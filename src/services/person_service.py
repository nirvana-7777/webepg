"""
Person registry and read-side enrichment.

The ``persons`` table (migration v11) is filled in two steps:

  1. ``register`` -- called by detail enrichment; stores id + name as soon as
     a programme's ``*_details`` credits are known (no extra API call).
  2. image/roles -- fetched later by UltimateBackendPersonEnrichmentService.

On the read side, ``attach_to_programs`` / ``attach_to_dicts`` join that data
onto programmes as a ``credits`` structure, so API and XMLTV consumers get
the complete EPG including person images.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from ..database.connection import get_db
from ..utils.credits import CREDIT_ROLES, extract_person_info, normalize_credits

logger = logging.getLogger(__name__)

# SQLite allows 999 bound parameters by default; leave room for provider_id.
_IN_BATCH = 500


class PersonService:
    @staticmethod
    def register(provider_id: int, *credit_lists: Optional[list]) -> int:
        """Register persons (id + name) found in parsed credit lists.

        If the credit entries already carry ``image`` and/or ``roles`` (inline
        ``*_details`` from providers without a ``/persons`` endpoint), those
        are stored right away and the person is marked ``ok`` so the person
        enrichment job never has to ask the backend for it. Existing image or
        roles are only replaced by non-empty new values; an already ``ok``
        row is never downgraded.
        """
        found: Dict[str, Dict[str, Any]] = {}
        for credit_list in credit_lists:
            # Inline data first: richer entries win over plain id+name ones.
            for info in extract_person_info(credit_list):
                current = found.setdefault(info["id"], {"name": info["name"]})
                if info.get("image"):
                    current.setdefault("image", info["image"])
                if info.get("roles"):
                    current.setdefault("roles", info["roles"])
            for person in normalize_credits(credit_list):
                if person.get("id"):
                    found.setdefault(person["id"], {"name": person["name"]})

        if not found:
            return 0

        now = datetime.now(timezone.utc).isoformat()
        rows = []
        for pid, info in found.items():
            inline = bool(info.get("image") or info.get("roles"))
            rows.append(
                (
                    provider_id,
                    pid,
                    info["name"],
                    info.get("image"),
                    json.dumps(info["roles"]) if info.get("roles") else None,
                    "ok" if inline else "pending",
                    now if inline else None,
                )
            )

        get_db().executemany(
            """
            INSERT INTO persons
                (provider_id, person_id, name, image_url, roles, status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id, person_id) DO UPDATE SET
                image_url  = COALESCE(excluded.image_url, persons.image_url),
                roles      = COALESCE(excluded.roles, persons.roles),
                status     = CASE WHEN excluded.status = 'ok'
                                  THEN 'ok' ELSE persons.status END,
                fetched_at = COALESCE(excluded.fetched_at, persons.fetched_at)
            """,
            rows,
        )
        return len(found)

    @staticmethod
    def _load(wanted: Dict[int, Set[str]]) -> Dict[Tuple[int, str], Dict[str, Any]]:
        """Fetch image/roles for {provider_id: {person_id, ...}}."""
        db = get_db()
        result: Dict[Tuple[int, str], Dict[str, Any]] = {}

        for provider_id, ids in wanted.items():
            id_list = sorted(ids)
            for i in range(0, len(id_list), _IN_BATCH):
                chunk = id_list[i : i + _IN_BATCH]
                placeholders = ",".join("?" * len(chunk))
                rows = db.fetchall(
                    f"""
                    SELECT person_id, image_url, roles
                    FROM persons
                    WHERE provider_id = ? AND person_id IN ({placeholders})
                    """,
                    (provider_id, *chunk),
                )
                for row in rows:
                    try:
                        roles = json.loads(row["roles"]) if row["roles"] else None
                    except (TypeError, ValueError):
                        roles = None
                    result[(provider_id, row["person_id"])] = {
                        "image": row["image_url"],
                        "roles": roles,
                    }
        return result

    @staticmethod
    def _parse(value: Any) -> list:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return []
        return value if isinstance(value, list) else []

    @classmethod
    def _build(
        cls,
        raw_by_role: Dict[str, Any],
        provider_id: Optional[int],
        persons: Dict[Tuple[int, str], Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        credits: Dict[str, List[Dict[str, Any]]] = {}
        for role in CREDIT_ROLES:
            entries = []
            for person in normalize_credits(cls._parse(raw_by_role.get(role))):
                extra = persons.get((provider_id, person.get("id")))
                if extra:
                    if extra["image"]:
                        person["image"] = extra["image"]
                    if extra["roles"]:
                        person["roles"] = extra["roles"]
                entries.append(person)
            if entries:
                credits[role] = entries
        return credits

    @classmethod
    def _attach(cls, items: list, get_role, get_provider, set_credits) -> None:
        # Pass 1: collect ids so persons are loaded in a few batched queries.
        wanted: Dict[int, Set[str]] = {}
        for item in items:
            provider_id = get_provider(item)
            for role in CREDIT_ROLES:
                for person in normalize_credits(cls._parse(get_role(item, role))):
                    if person.get("id"):
                        wanted.setdefault(provider_id, set()).add(person["id"])

        try:
            persons = cls._load(wanted) if wanted else {}
        except Exception as e:  # never let person data break the EPG itself
            logger.warning(f"Could not load person data: {e}")
            persons = {}

        for item in items:
            raw = {role: get_role(item, role) for role in CREDIT_ROLES}
            set_credits(item, cls._build(raw, get_provider(item), persons))

    @classmethod
    def attach_to_programs(cls, programs: Iterable) -> None:
        """Set ``program.credits`` on Program model objects."""
        cls._attach(
            list(programs),
            get_role=lambda p, role: getattr(p, role, None),
            get_provider=lambda p: p.provider_id,
            set_credits=lambda p, credits: setattr(p, "credits", credits),
        )

    @classmethod
    def attach_to_dicts(cls, programs: List[Dict[str, Any]]) -> None:
        """Set ``program["credits"]`` on program dicts (XMLTV export path)."""
        cls._attach(
            programs,
            get_role=lambda p, role: p.get(role),
            get_provider=lambda p: p.get("provider_id"),
            set_credits=lambda p, credits: p.__setitem__("credits", credits),
        )
