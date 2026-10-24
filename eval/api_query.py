"""Evaluate the actual API image using its documented source/answer SSE events."""

import json

import httpx

from core.adapters.llms.errors import ProviderRateLimitError


class EvaluationAPIError(RuntimeError):
    def __init__(self, code: str, *, permanent: bool = False):
        super().__init__(code)
        self.code = code
        self.permanent = permanent


def _error(code: str) -> Exception:
    if code == "provider_rate_limited":
        return ProviderRateLimitError("Hosted capacity is busy")
    return EvaluationAPIError(
        code,
        permanent=code
        in {"provider_credentials", "provider_configuration", "model_unavailable", "api_authorization"},
    )


async def query_api(
    url: str, token: str, question: str, use_case: str, *, timeout=1200, transport=None
) -> dict:
    if not token:
        raise EvaluationAPIError("api_authorization", permanent=True)
    sources = None
    parts: list[str] = []
    done = False
    event = "message"
    data = []
    async with (
        httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": "Bearer " + token},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        ) as client,
        client.stream(
            "POST", "/api/v1/query", json={"query": question, "use_case": use_case, "stream": True}
        ) as response,
    ):
        if response.status_code >= 300:
            await response.aread()
            code = "api_authorization" if response.status_code in (401, 403) else "api_unavailable"
            try:
                detail = response.json().get("detail", {})
                if isinstance(detail, dict):
                    code = detail.get("code", code)
            except (ValueError, AttributeError):
                pass
            raise _error(code)
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
            elif not line and data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError as exc:
                    raise EvaluationAPIError("invalid_stream") from exc
                if event == "sources":
                    if sources is not None or parts:
                        raise EvaluationAPIError("invalid_stream")
                    sources = payload.get("sources")
                    if not isinstance(sources, list) or any(
                        not isinstance(s, dict)
                        or not isinstance(s.get("page_content"), str)
                        or not isinstance(s.get("generation_context"), str)
                        or not s["generation_context"].strip()
                        for s in sources
                    ):
                        raise EvaluationAPIError("missing_generation_context", permanent=True)
                elif event == "token":
                    if sources is None or not isinstance(payload.get("text"), str):
                        raise EvaluationAPIError("invalid_stream")
                    parts.append(payload["text"])
                elif event == "error":
                    raise _error(payload.get("code", "generation_failed"))
                elif event == "done":
                    done = True
                    break
                event = "message"
                data = []
    if not done or sources is None or not parts:
        raise EvaluationAPIError("incomplete_stream")
    return {"answer": "".join(parts), "sources": sources}
