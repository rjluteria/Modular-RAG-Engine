"""Server-side bearer authentication; ingest requires an administrator."""

import secrets
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.config import get_settings

bearer = HTTPBearer(auto_error=False)


def authenticate(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
    settings = get_settings()
    if credentials and credentials.scheme.lower() == "bearer":
        token = credentials.credentials.encode("utf-8")
        if settings.api_admin_token and secrets.compare_digest(
            token, settings.api_admin_token.encode("utf-8")
        ):
            return "admin"
        if settings.api_user_token and secrets.compare_digest(token, settings.api_user_token.encode("utf-8")):
            return "user"
    raise HTTPException(401, "Valid bearer credentials required", headers={"WWW-Authenticate": "Bearer"})


def require_admin(role: Annotated[str, Depends(authenticate)]) -> str:
    if role != "admin":
        raise HTTPException(403, "Administrator privileges required")
    return role


def authorize_use_case(role: str, use_case: str) -> None:
    settings = get_settings()
    scopes = settings.api_user_use_cases
    if settings.shared_state_provider == "azure_blob":
        from core.adapters.state.blob_state import blob_state

        with blob_state() as state:
            scopes = state.read("configuration", {}).get("approved_use_cases", scopes)
    if role != "admin" and use_case not in scopes:
        raise HTTPException(403, "Credential is not authorized for this use case")
