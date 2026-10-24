"""Ordinary tests must never rely on local hosted credentials or auth scopes."""

import pytest


@pytest.fixture(autouse=True)
def isolate_local_credentials(monkeypatch):
    from core.config import get_settings

    settings = get_settings()
    for name, value in {
        "api_admin_token": "",
        "api_user_token": "",
        "api_user_use_cases": [],
        "nvidia_api_key": None,
    }.items():
        monkeypatch.setattr(settings, name, value)
