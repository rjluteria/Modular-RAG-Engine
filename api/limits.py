"""Shared request counter for authenticated API clients."""

import hashlib

from fastapi import HTTPException, Request
from redis import Redis

from core.config import get_settings


def enforce_rate_limit(request: Request, limit: int = 60) -> None:
    # The scheme is case-insensitive; it must not create a new counter for the same token.
    credential = request.headers.get("authorization", "").partition(" ")[2]
    fingerprint = hashlib.sha256(credential.encode()).hexdigest()
    if get_settings().shared_state_provider == "azure_blob":
        import time

        from core.adapters.state.blob_state import blob_state

        key = "rates/" + fingerprint + "/" + request.url.path.strip("/").replace("/", "-")
        minute = int(time.time() // 60)
        with blob_state() as state, state.lock(key):
            row = state.read(key, {})
            count = row.get("count", 0) + 1 if row.get("minute") == minute else 1
            state.write(key, {"minute": minute, "count": count})
        if count > limit:
            raise HTTPException(429, "Request limit exceeded", headers={"Retry-After": "60"})
        return
    with Redis.from_url(get_settings().redis_url, socket_timeout=3, socket_connect_timeout=3) as connection:
        count = connection.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
            1,
            "rag:requests:" + fingerprint,
        )
    if count > limit:
        raise HTTPException(429, "Request limit exceeded", headers={"Retry-After": "60"})
