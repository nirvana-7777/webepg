"""
Credit-list helpers shared by models, services and the XMLTV serializer.

``programs.actors`` / ``directors`` / ``writers`` / ``producers`` /
``presenters`` hold JSON arrays. Depending on how a row was written the
entries are either plain name strings (XMLTV import, grid import, older
enrichment) or dicts from the Ultimate Backend ``*_details`` lists::

    {"id": "gnp_230637", "name": "Kat Dennings", "character_name": "Max Black"}

These helpers accept both shapes so no consumer has to care.
"""

from typing import Any, Dict, List

CREDIT_ROLES = ("actors", "directors", "writers", "producers", "presenters")


def normalize_credits(raw: Any) -> List[Dict[str, Any]]:
    """Return ``[{"name", "id"?, "character_name"?}, ...]`` for a parsed list."""
    if not isinstance(raw, list):
        return []

    result: List[Dict[str, Any]] = []
    for entry in raw:
        if isinstance(entry, str):
            name = entry.strip()
            if name:
                result.append({"name": name})
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            if not name:
                continue
            person: Dict[str, Any] = {"name": name}
            if entry.get("id"):
                person["id"] = str(entry["id"])
            if entry.get("character_name"):
                person["character_name"] = entry["character_name"]
            result.append(person)
    return result


def credit_names(raw: Any) -> List[str]:
    """Plain name list (legacy ``actors``/``directors`` shape)."""
    return [person["name"] for person in normalize_credits(raw)]


def extract_person_info(raw: Any) -> List[Dict[str, Any]]:
    """Return ``[{"id", "name", "image"?, "roles"?}, ...]`` for entries that
    carry a person id.

    Providers without a ``/persons`` endpoint (e.g. magentaeu_*) deliver
    ``image`` and ``roles`` inline in the ``*_details`` lists of the programme
    details. ``normalize_credits`` deliberately drops those (programme rows
    keep only id/name/character_name), so they are collected here and stored
    once in the ``persons`` table instead.
    """
    if not isinstance(raw, list):
        return []

    result: List[Dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        person_id = entry.get("id")
        if not name or not person_id:
            continue
        info: Dict[str, Any] = {"id": str(person_id), "name": name}
        image = entry.get("image")
        if isinstance(image, str) and image.strip():
            info["image"] = image.strip()
        roles = entry.get("roles")
        if isinstance(roles, list) and roles:
            info["roles"] = [str(r) for r in roles if r]
        result.append(info)
    return result
