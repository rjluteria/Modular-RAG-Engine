import json

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.config import get_settings


@pytest.mark.parametrize("failure", [False, True])
def test_stream_framing_sources_and_errors(monkeypatch, failure):
    class Stream:
        async def astream_events(self, question):
            yield "sources", {"sources": [{"chunk_id": "synthetic"}]}
            if failure:
                raise RuntimeError("private upstream message")
            yield "token", {"text": 'line one\n"line two"'}
            yield "done", {}

    settings = get_settings()
    monkeypatch.setattr(settings, "api_user_token", "synthetic-user")
    monkeypatch.setattr(settings, "api_user_use_cases", ["synthetic"])
    monkeypatch.setattr("api.routes.query.enforce_rate_limit", lambda request: None)
    monkeypatch.setattr("api.routes.query.build_retrieval_chain", lambda **kwargs: Stream())
    response = TestClient(app).post(
        "/api/v1/query",
        headers={"Authorization": "Bearer synthetic-user"},
        json={"query": "test", "use_case": "synthetic", "stream": True},
    )
    assert response.status_code == 200
    events = []
    for block in response.text.strip().split("\n\n"):
        name, data = block.split("\n")
        events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    assert events[0] == ("sources", {"sources": [{"chunk_id": "synthetic"}]})
    assert events[-1][0] == ("error" if failure else "done")
    assert "private upstream message" not in response.text
    if not failure:
        assert events[1][1]["text"] == 'line one\n"line two"'
