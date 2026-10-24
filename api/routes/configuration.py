"""Deployment-wide administrator settings; no query-history functionality."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_admin
from core.adapters.state.blob_state import blob_state
from core.config import get_settings

router = APIRouter(tags=["configuration"], dependencies=[Depends(require_admin)])


class Configuration(BaseModel):
    approved_use_cases: list[str] = Field(default_factory=list, max_length=100)
    originals_retention_days: Literal[0, 1, 7, 30] = 0


@router.put("/configuration")
def update_configuration(configuration: Configuration):
    if get_settings().shared_state_provider != "azure_blob":
        raise HTTPException(501, "Runtime configuration requires shared Azure state")
    if any(not s.strip() or len(s) > 128 for s in configuration.approved_use_cases):
        raise HTTPException(422, "Collection scopes must contain 1 to 128 characters")
    with blob_state() as state, state.lock("configuration"):
        state.write("configuration", configuration.model_dump())
    return {"data": configuration.model_dump()}
