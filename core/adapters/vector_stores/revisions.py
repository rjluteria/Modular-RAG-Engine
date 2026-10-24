"""A durable visibility pointer publishes only fully stored source revisions."""

import hashlib
import json

from redis import Redis

from core.config import get_settings


def source_key(use_case: str, source_id: str) -> str:
    return hashlib.sha256(json.dumps([use_case, source_id]).encode()).hexdigest()


def manifest_key(collection: str) -> str:
    return "rag:visible:" + collection


def read_lock(connection, collection: str):
    # Readers hold this only while copying contexts; staging embeddings needs no reader lock.
    return connection.lock("rag:publish:" + collection, timeout=120, blocking_timeout=30)


def redis_connection():
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=5, socket_timeout=5)


def visible_revisions(connection, collection: str) -> list[str]:
    return [value.decode() for value in connection.hvals(manifest_key(collection))]
