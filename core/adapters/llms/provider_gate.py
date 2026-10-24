"""Shared Redis lease keeps generation and evaluation within one provider concurrency slot."""

import asyncio
import math
import threading
import time
import uuid
from datetime import UTC

from langchain_core.callbacks import AsyncCallbackHandler

from core.config import get_settings

_RELEASE = """
if redis.call('get', KEYS[1]) ~= ARGV[1] then return 0 end
if ARGV[2] ~= '' then redis.call('set', KEYS[2], ARGV[2], 'EX', ARGV[3]) end
if tonumber(ARGV[4]) > 0 then redis.call('set', KEYS[3], '1', 'EX', ARGV[4]) end
return redis.call('del', KEYS[1])
"""


class ProviderGate(AsyncCallbackHandler):
    raise_error = True

    def __init__(self):
        self._leases = {}
        self._mutex = threading.Lock()

    @staticmethod
    def _connection():
        from redis.asyncio import Redis

        return Redis.from_url(get_settings().redis_url, socket_connect_timeout=5, socket_timeout=5)

    async def _release(self, token, *, finished=False, error=None):
        settings = get_settings()
        interval = settings.provider_min_interval_seconds
        cooldown = 0
        if getattr(error, "status_code", None) == 429:
            response = getattr(error, "response", None)
            cooldown = retry_delay(getattr(response, "headers", {}), settings.provider_cooldown_seconds)
        async with self._connection() as connection:
            await connection.eval(
                _RELEASE,
                3,
                "rag:provider:slot",
                "rag:provider:not-before",
                "rag:provider:cooldown",
                token,
                str(time.time() + interval) if finished else "",
                max(1, math.ceil(interval + 60)),
                cooldown,
            )

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        from core.adapters.llms.errors import ProviderRateLimitError

        settings = get_settings()
        token = uuid.uuid4().hex
        try:
            async with self._connection() as connection:
                if await connection.exists("rag:provider:cooldown"):
                    raise ProviderRateLimitError("Provider cooldown is active")
                lease = connection.lock(
                    "rag:provider:slot",
                    timeout=settings.model_timeout_seconds * (settings.model_max_retries + 1) + 300,
                    blocking_timeout=settings.provider_wait_timeout_seconds,
                    thread_local=False,
                )
                if not await lease.acquire(token=token):
                    raise TimeoutError("Provider capacity is busy")
                if await connection.exists("rag:provider:cooldown"):
                    raise ProviderRateLimitError("Provider cooldown is active")
                not_before = await connection.get("rag:provider:not-before")
                if not_before:
                    await asyncio.sleep(max(0, float(not_before) - time.time()))
                with self._mutex:
                    self._leases[run_id] = token
        except BaseException:
            with self._mutex:
                self._leases.pop(run_id, None)
            # The known token also cleans up cancellation during the acquire reply.
            await asyncio.shield(self._release(token))
            raise

    async def _finish(self, run_id, error=None):
        with self._mutex:
            token = self._leases.pop(run_id, None)
        if token is not None:
            # Fresh connections support both synchronous and asynchronous model callbacks.
            await asyncio.shield(self._release(token, finished=True, error=error))

    async def on_llm_end(self, response, *, run_id, **kwargs):
        await self._finish(run_id)

    async def on_llm_error(self, error, *, run_id, **kwargs):
        await self._finish(run_id, error)


def remaining_cooldown() -> int:
    if get_settings().shared_state_provider == "azure_blob":
        from core.adapters.state.blob_state import blob_state

        with blob_state() as state:
            row = state.read("provider-slot", {})
        return max(0, math.ceil(row.get("not_before", 0) - time.time()))
    from redis import Redis

    with Redis.from_url(get_settings().redis_url, socket_connect_timeout=5, socket_timeout=5) as connection:
        return max(0, connection.ttl("rag:provider:cooldown"))


def retry_delay(headers, fallback: int) -> int:
    import math
    from datetime import datetime
    from email.utils import parsedate_to_datetime

    value = headers.get("retry-after")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            seconds = deadline.timestamp() - datetime.now(UTC).timestamp()
        except (TypeError, ValueError, IndexError):
            try:
                seconds = float(headers.get("retry-after-ms")) / 1000
            except (TypeError, ValueError):
                seconds = fallback
    return max(1, min(86400, math.ceil(seconds) if math.isfinite(seconds) else fallback))
