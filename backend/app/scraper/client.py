"""Сбер Подбор API client: one 'lane' per ATS account, each with its own proxy and token.

Ported from the Node exporter. The four behaviours below are not incidental — each one
cost a real incident during the original 40-hour crawl, so they are called out where
they are implemented:

1. Login happens INSIDE the retry guard. A proxy blip during token refresh used to raise
   out of the request helper and silently drop the whole work item (82 622 profiles lost).
2. A hard failure never checkpoints. Callers must leave the item unmarked so a later run
   retries it, rather than storing an empty row that looks complete.
3. HTTP 403 is a final answer ("no data"), not a failure. Some contact records are
   permanently forbidden; treating them as retryable deferred them forever.
4. Every request carries an explicit timeout. Without one a hung proxy stalls for
   httpx's default, turning an outage into a silent 45-minute crawl.

The server throttles by latency, not by a request counter, and the anti-abuse 401 block is
triggered by rapid LOGINS. So: few logins, modest per-lane pacing, and never a login storm.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("scraper.client")


class Sentinel:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self.name}>"


#: The record does not exist. A complete answer — safe to checkpoint.
NOT_FOUND = Sentinel("not_found")
#: The server refuses this record and will keep refusing it. Also a complete answer (fix 3).
FORBIDDEN = Sentinel("forbidden")
#: Retries exhausted. NOT a complete answer — the caller must not checkpoint (fix 2).
GAVE_UP = Sentinel("gave_up")

Result = dict[str, Any] | Sentinel


@dataclass
class Lane:
    """One ATS account. `account_id` ties stats back to the ats_accounts row."""

    index: int
    account_id: int
    email: str
    password: str
    proxy: str | None = None

    token: str | None = None
    token_expiry: float = 0.0
    last_login: float = 0.0
    # Incremented on every successful login. A worker that got a 401 only forces a
    # re-login if nobody else already refreshed the token underneath it — otherwise
    # 14 concurrent workers each trigger a login and walk straight into the block.
    token_generation: int = 0

    block_s: float = field(default_factory=lambda: float(settings.block_cooldown_s))
    _next_slot: float = 0.0

    requests_ok: int = 0
    requests_failed: int = 0
    last_error: str | None = None

    _login_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _pace_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    @property
    def proxy_label(self) -> str:
        if not self.proxy:
            return "direct"
        scheme, _, rest = self.proxy.partition("://")
        _, _, host = rest.rpartition("@")
        return f"{scheme}://***@{host}" if "@" in rest else self.proxy

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.api_base,
                proxy=self.proxy or None,
                timeout=httpx.Timeout(settings.req_timeout_s),  # fix 4
                follow_redirects=False,
                headers={"accept": "application/json, text/plain, */*"},
                limits=httpx.Limits(max_connections=settings.lane_concurrency * 2),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class LaneBlocked(RuntimeError):
    """The account is under the anti-abuse block and re-login did not clear it."""


class ApiClient:
    """Drives a pool of lanes. Work is pulled from a shared cursor, so a slow lane
    simply takes fewer items instead of stranding its shard."""

    def __init__(self, lanes: list[Lane]) -> None:
        if not lanes:
            raise ValueError("no active ATS accounts configured — add one in the admin panel")
        self.lanes = lanes

    async def aclose(self) -> None:
        await asyncio.gather(*(lane.aclose() for lane in self.lanes), return_exceptions=True)

    # ── auth ────────────────────────────────────────────────────────────────
    async def _login(self, lane: Lane) -> str:
        """Full /v2/auth/login. This is also the only thing that clears the 401 block."""
        wait = lane.last_login + settings.min_relogin_s - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

        r = await lane.client().post(
            "/v2/auth/login",
            headers={"content-type": "application/vnd.api+json"},
            json={"data": {"attributes": {"login": lane.email, "password": lane.password}}},
            timeout=httpx.Timeout(settings.req_timeout_s),
        )
        if r.status_code not in (200, 201):
            raise LaneBlocked(f"login {lane.email} failed: HTTP {r.status_code}")
        attrs = r.json()["data"]["attributes"]

        lane.token = attrs["accessToken"]
        expiry = attrs.get("accessTokenExpiryAt")
        lane.token_expiry = (
            datetime.fromisoformat(expiry).timestamp() if expiry else time.time() + 7 * 3600
        )
        lane.last_login = time.monotonic()
        lane.token_generation += 1
        log.info("[lane %s %s via %s] logged in until %s", lane.index, lane.email, lane.proxy_label, expiry)
        return lane.token

    async def _ensure_token(self, lane: Lane) -> str:
        if lane.token and time.time() < lane.token_expiry - 300:
            return lane.token
        async with lane._login_lock:
            # Someone may have refreshed while we waited for the lock.
            if lane.token and time.time() < lane.token_expiry - 300:
                return lane.token
            return await self._login(lane)

    async def _pace(self, lane: Lane) -> None:
        """Minimum gap between request starts on this lane."""
        async with lane._pace_lock:
            now = time.monotonic()
            slot = max(now, lane._next_slot)
            lane._next_slot = slot + settings.min_interval_ms / 1000
            delay = slot - now
        if delay > 0:
            await asyncio.sleep(delay)

    # ── requests ────────────────────────────────────────────────────────────
    async def request(
        self,
        lane: Lane,
        path: str,
        *,
        method: str = "GET",
        json_body: dict | None = None,
        json_api: bool = False,
    ) -> Result:
        for attempt in range(settings.max_retries_per_req):
            try:
                # fix 1: token refresh is inside the guard, so a proxy blip during login
                # becomes a retry instead of an exception that kills the work item.
                token = await self._ensure_token(lane)
                generation = lane.token_generation
                await self._pace(lane)

                headers = {"authorization": f"Bearer {token}"}
                if json_body is not None:
                    headers["content-type"] = (
                        "application/vnd.api+json" if json_api else "application/json"
                    )
                r = await lane.client().request(method, path, headers=headers, json=json_body)
                text = r.text
            except (httpx.HTTPError, LaneBlocked, KeyError, ValueError) as exc:
                lane.requests_failed += 1
                lane.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("[lane %s] net error %s %s", lane.index, path, lane.last_error)
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            if r.status_code == 200:
                lane.requests_ok += 1
                lane.block_s = float(settings.block_cooldown_s)
                if not text:
                    return NOT_FOUND
                try:
                    return r.json()
                except ValueError:
                    log.warning("[lane %s] non-JSON 200 on %s", lane.index, path)
                    return GAVE_UP

            if r.status_code == 404:
                return NOT_FOUND

            # fix 3: forbidden is final, not a failure.
            if r.status_code == 403:
                log.info("[lane %s] 403 forbidden (no-data) %s", lane.index, path)
                return FORBIDDEN

            if r.status_code == 401:
                await self._handle_401(lane, generation, path)
                continue

            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

            lane.requests_failed += 1
            lane.last_error = f"HTTP {r.status_code}"
            log.warning("[lane %s] unexpected %s %s %s", lane.index, r.status_code, path, text[:120])
            return GAVE_UP

        log.warning("[lane %s] GAVE UP %s", lane.index, path)
        lane.requests_failed += 1
        return GAVE_UP

    async def _handle_401(self, lane: Lane, generation: int, path: str) -> None:
        """Anti-abuse block. Re-login once per generation, then back off exponentially.

        Only the worker holding the stale generation re-logins; the other in-flight
        workers pick up the fresh token. Logging in from each of them is what triggers
        the block in the first place.
        """
        async with lane._login_lock:
            if lane.token_generation != generation:
                return  # somebody already refreshed; just retry
            log.warning(
                "[lane %s] 401 on %s — re-login + back-off %.0fs", lane.index, path, lane.block_s
            )
            lane.token = None
            try:
                await self._login(lane)
            except Exception as exc:  # noqa: BLE001 - reported, then backed off
                lane.last_error = f"re-login failed: {exc}"
                log.error("[lane %s] re-login error: %s", lane.index, exc)
        await asyncio.sleep(lane.block_s)
        lane.block_s = min(lane.block_s * 2, settings.block_cooldown_max_s)

    # ── work distribution ───────────────────────────────────────────────────
    async def shard(self, items: list[Any], worker, *, on_progress=None) -> None:
        """Run `worker(lane, item)` over items, `lane_concurrency` runners per lane.

        A shared cursor (not a per-lane slice) means one degraded lane cannot strand
        the items assigned to it.
        """
        cursor = iter(items)
        lock = asyncio.Lock()
        done = 0

        async def take() -> Any:
            async with lock:
                return next(cursor, None)

        async def runner(lane: Lane) -> None:
            nonlocal done
            while True:
                item = await take()
                if item is None:
                    return
                try:
                    await worker(lane, item)
                except Exception as exc:  # noqa: BLE001 - one bad item must not kill the runner
                    log.exception("[lane %s] worker error on %r: %s", lane.index, item, exc)
                done += 1
                if on_progress and done % 500 == 0:
                    await on_progress(done)

        runners = [
            asyncio.create_task(runner(lane))
            for lane in self.lanes
            for _ in range(settings.lane_concurrency)
        ]
        try:
            await asyncio.gather(*runners)
        finally:
            for task in runners:
                task.cancel()
        if on_progress:
            await on_progress(done)
