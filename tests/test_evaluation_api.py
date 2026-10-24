import json

import httpx
import pytest

from eval.api_query import EvaluationAPIError, query_api


@pytest.mark.asyncio
async def test_api_evaluation_captures_exact_stream_context_and_answer():
    source = {
        "chunk_id": "row",
        "page_content": "Value: 42\nUnits: kg",
        "generation_context": "[row]\n[row:1] Value: 42\n[row:2] Units: kg",
    }
    body = (
        "event: sources\ndata: "
        + json.dumps({"sources": [source]})
        + "\n\nevent: token\ndata: "
        + json.dumps({"text": "42 kg"})
        + "\n\nevent: done\ndata: {}\n\n"
    )

    async def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-user"
        return httpx.Response(200, text=body)

    result = await query_api(
        "http://synthetic.invalid",
        "synthetic-user",
        "Question",
        "synthetic",
        transport=httpx.MockTransport(handler),
    )
    assert result == {"answer": "42 kg", "sources": [source]}


@pytest.mark.asyncio
async def test_api_evaluation_rejects_incomplete_stream_and_auth_failure():
    async def incomplete(request):
        return httpx.Response(200, text='event: sources\ndata: {"sources":[]}\n\n')

    with pytest.raises(EvaluationAPIError, match="incomplete_stream"):
        await query_api(
            "http://synthetic.invalid",
            "synthetic-user",
            "Question",
            "synthetic",
            transport=httpx.MockTransport(incomplete),
        )

    async def forbidden(request):
        return httpx.Response(403, json={"detail": "Forbidden"})

    with pytest.raises(EvaluationAPIError) as error:
        await query_api(
            "http://synthetic.invalid",
            "synthetic-user",
            "Question",
            "synthetic",
            transport=httpx.MockTransport(forbidden),
        )
    assert error.value.permanent


@pytest.mark.asyncio
async def test_api_evaluation_requires_the_actual_generation_context():
    async def missing(request):
        return httpx.Response(200, text='event: sources\ndata: {"sources":[{"page_content":"raw only"}]}\n\n')

    with pytest.raises(EvaluationAPIError) as error:
        await query_api(
            "http://synthetic.invalid",
            "synthetic-user",
            "Question",
            "synthetic",
            transport=httpx.MockTransport(missing),
        )
    assert error.value.code == "missing_generation_context" and error.value.permanent
