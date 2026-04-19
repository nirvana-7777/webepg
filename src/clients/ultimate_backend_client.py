"""
REST client for Ultimate Backend API.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientTimeout

from .base import EPGClient
from .models import (EPGWindow, UltimateBackendChannel,
                     UltimateBackendChannelList, UltimateBackendProgram,
                     UltimateBackendProvider)

logger = logging.getLogger(__name__)


class RateLimiter:
    """Simple rate limiter for API requests."""

    def __init__(self, requests_per_second: float):
        self.requests_per_second = requests_per_second
        self.interval = 1.0 / requests_per_second
        self._last_request_time = 0

    async def acquire(self):
        """Wait if needed to respect rate limit."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        if elapsed < self.interval:
            await asyncio.sleep(self.interval - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()


class UltimateBackendClient(EPGClient):
    """REST client for Ultimate Backend API."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout_seconds: int = 30,
        max_retries: int = 3,
        requests_per_second: float = 5.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = ClientTimeout(total=timeout_seconds)
        self.max_retries = max_retries
        self.rate_limiter = RateLimiter(requests_per_second)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """
        Get or create an aiohttp session.

        Uses force_close=True on the connector so that connections are never
        returned to a keep-alive pool. This prevents stale connections being
        reused when the client is called from a fresh asyncio event loop
        (which happens when APScheduler jobs use asyncio.run()).
        """
        if self._session is None or self._session.closed:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            connector = aiohttp.TCPConnector(force_close=True)
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=self.timeout,
                connector=connector,
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        retry_count: int = 0,
    ) -> Dict:
        """Make an HTTP request with retries."""
        await self.rate_limiter.acquire()

        url = f"{self.base_url}{path}"
        session = await self._get_session()

        try:
            async with session.request(method, url, params=params) as response:
                if response.status == 429:  # Rate limit
                    retry_after = int(response.headers.get("Retry-After", 5))
                    logger.warning(f"Rate limited, waiting {retry_after}s")
                    await asyncio.sleep(retry_after)
                    if retry_count < self.max_retries:
                        return await self._request(
                            method, path, params, retry_count + 1
                        )

                response.raise_for_status()
                return await response.json()

        except ClientResponseError as e:
            if e.status >= 500 and retry_count < self.max_retries:
                wait_time = 2 ** retry_count
                logger.warning(f"Server error {e.status}, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
                return await self._request(method, path, params, retry_count + 1)
            raise

        except ClientError as e:
            if retry_count < self.max_retries:
                wait_time = 2 ** retry_count
                logger.warning(f"Request failed: {e}, retrying in {wait_time}s")
                await asyncio.sleep(wait_time)
                return await self._request(method, path, params, retry_count + 1)
            raise

    async def get_providers(self) -> List[Dict]:
        """
        GET /api/providers
        Returns list of all providers.

        Real response shape:
            {"providers": [{
                "name": "magentatv",
                "label": "MagentaTV",
                "logo": "...",
                "country": "DE",
                "enabled": true,
                "requires_credentials": false,
                "auth": {...},
                "instance_ready": true,
                ...
            }]}
        """
        data = await self._request("GET", "/api/providers")
        return data.get("providers", [])

    async def get_channels(self, provider_name: str) -> UltimateBackendChannelList:
        """
        GET /api/providers/{provider}/channels
        Returns list of channels for a provider along with EPG window info.

        Real response shape:
            {"provider": "magenta2",
             "country": "de",
             "catchup_window_hours": 4,
             "epg_window": {"past_days": 7, "future_days": 3, "implements_epg": true},
             "channels": [{
                "Name": "Moviedome",
                "Id": "Zf4PyxjqUvbe",
                ...
             }]}
        """
        data = await self._request("GET", f"/api/providers/{provider_name}/channels")
        return UltimateBackendChannelList.from_api_response(data)

    async def get_epg(
        self,
        provider_name: str,
        channel_id: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[Dict]:
        """
        GET /api/providers/{provider}/channels/{channel_id}/epg
        Returns EPG programs for a channel within time range.

        Real response shape:
            {"provider": "movetv",
             "channel_id": "211458",
             "start_time": "...",
             "end_time": "...",
             "count": 25,
             "programs": [{
                "epg_id": 2045056482,
                "schedule_id": "211458:2045056482",
                "title": "...",
                "start": "2026-04-07T11:00:00+00:00",  ← tz-aware ISO 8601
                "end":   "2026-04-07T12:09:00+00:00",
                "genre": "MAGAZIN",
                "categories": ["Ljudi"],
                "category_ids": [300],
                "rating": 0,        ← 0 means "no rating", not rated-0
                "cast": [],
                "images": {"background": "..."},
                ...
             }]}

        Args:
            provider_name: Provider name (e.g., "movetv")
            channel_id: Channel ID as returned by get_channels() — may be a
                        string token like "Zf4PyxjqUvbe" or a numeric string
                        like "211458" depending on the provider.
            start_time: UTC datetime (tz-naive)
            end_time: UTC datetime (tz-naive); max 24 h recommended per call

        Returns:
            List of raw program dictionaries (parsed by
            UltimateBackendProgram.from_api_response).
        """
        params = {
            "start": int(start_time.timestamp()),
            "end": int(end_time.timestamp()),
        }

        data = await self._request(
            "GET",
            f"/api/providers/{provider_name}/channels/{channel_id}/epg",
            params=params,
        )

        return data.get("programs", [])

    async def has_epg(self, provider_name: str) -> bool:
        """
        Check if provider has EPG by fetching the /channels endpoint
        and checking the implements_epg flag.

        Returns:
            True if the endpoint returns 200 and implements_epg is True.
        """
        try:
            channel_list = await self.get_channels(provider_name)
            return channel_list.epg_window.implements_epg
        except ClientResponseError as e:
            if e.status == 404:
                return False
            logger.warning(f"Unexpected error checking EPG for {provider_name}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Error checking EPG support for {provider_name}: {e}")
            return False

    async def close(self):
        """
        Close the aiohttp session.

        IMPORTANT: always call this at the end of a job run (inside the same
        coroutine passed to asyncio.run()) so that the next job starts with a
        clean session on a fresh event loop.
        """
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None