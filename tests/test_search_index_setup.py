from types import SimpleNamespace

import pytest

from scripts import setup_search_index as setup


def test_index_setup_uses_selected_dimensions_and_closes_credential(monkeypatch):
    observed = {}
    credential = SimpleNamespace(close=lambda: observed.update(closed=True))

    class Client:
        def __init__(self, endpoint, supplied):
            assert endpoint == "https://example.invalid"
            assert supplied is credential

        def __enter__(self):
            return self

        def __exit__(self, *args):
            observed["client_closed"] = True

        def create_or_update_index(self, index):
            observed["index"] = index
            raise ConnectionError("Synthetic administration failure")

    monkeypatch.setattr(setup, "DefaultAzureCredential", lambda: credential)
    monkeypatch.setattr(setup, "SearchIndexClient", Client)
    with pytest.raises(ConnectionError):
        setup.setup_index("https://example.invalid", 42, index_name="synthetic")
    assert observed["index"].name == "synthetic"
    vector = next(field for field in observed["index"].fields if field.name == "vector")
    assert vector.vector_search_dimensions == 42
    assert observed["client_closed"] and observed["closed"]


def test_invalid_dimensions_fail_before_creating_credentials(monkeypatch):
    def unexpected():
        raise AssertionError("Credential should not be created")

    monkeypatch.setattr(setup, "DefaultAzureCredential", unexpected)
    with pytest.raises(ValueError, match="positive"):
        setup.setup_index("https://example.invalid", 0)
