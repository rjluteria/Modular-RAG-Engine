import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.config import get_settings


def test_auth_roles_and_query_validation(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_admin_token", "synthetic-admin")
    monkeypatch.setattr(settings, "api_user_token", "synthetic-user")
    client = TestClient(app)
    assert client.post("/api/v1/query", json={"query": "test", "use_case": "synthetic"}).status_code == 401
    user = {"Authorization": "Bearer synthetic-user"}
    assert client.get("/api/v1/jobs/test", headers=user).status_code == 403
    assert (
        client.post(
            "/api/v1/query", headers=user, json={"query": "test", "use_case": "synthetic", "top_k": 0}
        ).status_code
        == 422
    )


def test_upload_limit_cleans_partial_file(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_admin_token", "synthetic-admin")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_upload_bytes", 5)
    result = TestClient(app).post(
        "/api/v1/ingest",
        headers={"Authorization": "Bearer synthetic-admin"},
        data={"use_case": "synthetic"},
        files={"file": ("../../test.txt", b"123456")},
    )
    assert result.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_declared_oversized_request_is_rejected_before_authentication(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 5)
    result = TestClient(app).post("/api/v1/ingest", content=b"", headers={"Content-Length": "30000000"})
    assert result.status_code == 413


def test_chunked_oversized_multipart_is_rejected_before_spooling(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 5)

    def body():
        yield b'--synthetic\r\nContent-Disposition: form-data; name="file"; filename="test.txt"\r\n\r\n'
        yield b"x" * (1024 * 1024 + 6)
        yield b"\r\n--synthetic--\r\n"

    result = TestClient(app).post(
        "/api/v1/ingest", content=body(), headers={"Content-Type": "multipart/form-data; boundary=synthetic"}
    )
    assert result.status_code == 413


def test_missing_provider_key_never_falls_back_to_unrelated_sdk_credentials(monkeypatch):
    import pytest

    from core.adapters.llms.errors import ProviderConfigurationError
    from core.adapters.llms.nvidia_nim_adapter import NvidiaNIMLLMBuilder

    monkeypatch.setattr(get_settings(), "nvidia_api_key", None)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-unrelated-credential")
    with pytest.raises(ProviderConfigurationError):
        NvidiaNIMLLMBuilder().build()


@pytest.mark.parametrize(
    "filename,content,expected",
    [
        ("unsupported.exe", b"sample", 415),
        ("corrupt.pdf", b"not a PDF", 422),
        ("corrupt.docx", b"PKinvalid", 422),
        ("binary.csv", b"\x00binary", 422),
    ],
)
def test_invalid_uploads_fail_clearly_and_leave_no_files(tmp_path, monkeypatch, filename, content, expected):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_admin_token", "synthetic-admin")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    result = TestClient(app).post(
        "/api/v1/ingest",
        headers={"Authorization": "Bearer synthetic-admin"},
        data={"use_case": "synthetic"},
        files={"file": (filename, content)},
    )
    assert result.status_code == expected
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "status,code",
    [
        (400, "provider_configuration"),
        (422, "provider_configuration"),
        (401, "provider_credentials"),
        (410, "model_unavailable"),
        (429, "provider_rate_limited"),
        (503, "provider_unavailable"),
    ],
)
def test_provider_failures_are_distinct_and_sanitized(monkeypatch, status, code):
    class UpstreamFailure(Exception):
        status_code = status

    class Failure:
        async def ainvoke_with_sources(self, question):
            raise UpstreamFailure("synthetic sensitive upstream response")

    monkeypatch.setattr(get_settings(), "api_user_token", "synthetic-user")
    monkeypatch.setattr(get_settings(), "api_user_use_cases", ["synthetic"])
    monkeypatch.setattr("api.routes.query.enforce_rate_limit", lambda request: None)
    monkeypatch.setattr("api.routes.query.build_retrieval_chain", lambda **kwargs: Failure())
    response = TestClient(app).post(
        "/api/v1/query",
        headers={"Authorization": "Bearer synthetic-user"},
        json={"query": "test", "use_case": "synthetic"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == code
    assert "sensitive upstream" not in response.text


def test_user_partition_authorization_runs_before_retrieval(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_user_token", "synthetic-user")
    monkeypatch.setattr(settings, "api_user_use_cases", ["permitted"])
    monkeypatch.setattr(
        "api.routes.query.build_retrieval_chain",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("Unauthorized retrieval executed")),
    )
    for stream in (False, True):
        response = TestClient(app).post(
            "/api/v1/query",
            headers={"Authorization": "Bearer synthetic-user"},
            json={"query": "test", "use_case": "other-partition", "stream": stream},
        )
        assert response.status_code == 403


def test_user_scope_defaults_deny_access(monkeypatch):
    from fastapi import HTTPException

    from api.auth import authorize_use_case

    monkeypatch.setattr(get_settings(), "api_user_use_cases", [])
    with pytest.raises(HTTPException):
        authorize_use_case("user", "synthetic")
    authorize_use_case("admin", "synthetic")


def test_equal_privileged_credentials_are_rejected_without_echoing_inputs():
    from pydantic import ValidationError

    from core.config import Settings

    with pytest.raises(ValidationError) as error:
        Settings(
            _env_file=None,
            api_admin_token="synthetic-sensitive-marker",
            api_user_token="synthetic-sensitive-marker",
        )
    assert "synthetic-sensitive-marker" not in str(error.value)


@pytest.mark.parametrize("encoding,bom", [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")])
def test_utf16_csv_upload_reaches_queue_and_preserves_content(tmp_path, monkeypatch, encoding, bom):
    from core.adapters.parsers.structured_adapter import StructuredParserAdapter

    settings = get_settings()
    monkeypatch.setattr(settings, "api_admin_token", "synthetic-admin")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("api.routes.ingest.enforce_rate_limit", lambda *args, **kwargs: None)
    captured = []

    class Queue:
        def enqueue(self, kind, payload):
            captured.append(payload)
            return "synthetic-job"

    monkeypatch.setattr("api.routes.ingest.get_job_queue", Queue)
    content = bom + "Entity,Period,Value,Units\nSable,2025,31,kg\n".encode(encoding)
    response = TestClient(app).post(
        "/api/v1/ingest",
        headers={"Authorization": "Bearer synthetic-admin"},
        data={"use_case": "synthetic"},
        files={"file": ("sample.csv", content)},
    )
    assert response.status_code == 202
    documents = StructuredParserAdapter().parse(
        captured[0]["file_path"], source_id="sample.csv", use_case="synthetic"
    )
    assert any("31" in d.page_content and "kg" in d.page_content for d in documents)


def test_csv_bom_does_not_allow_binary_null_content(tmp_path):
    from core.uploads import validate_document

    path = tmp_path / "binary.csv"
    path.write_bytes(b"\xff\xfe" + "Header\n".encode("utf-16-le") + b"\0\0")
    with pytest.raises(ValueError):
        validate_document(path)


def test_non_ascii_invalid_bearer_is_unauthorized(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_admin_token", "synthetic-admin")
    monkeypatch.setattr(settings, "api_user_token", "synthetic-user")
    response = TestClient(app).post(
        "/api/v1/query",
        headers={b"authorization": b"Bearer \xe9"},
        json={"query": "Synthetic question", "use_case": "synthetic"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("job_type,handler", [("ingest", "_run_ingest_job"), ("query", "_run_query_job")])
def test_worker_failure_traceback_does_not_expose_upstream_content(monkeypatch, job_type, handler, caplog):
    import traceback

    import worker.rq_worker as worker

    def fail(payload):
        raise ValueError("synthetic confidential source or provider payload")

    monkeypatch.setattr(worker, handler, fail)
    with pytest.raises(RuntimeError) as result:
        worker.run_job(job_type, {})
    rendered = "".join(traceback.format_exception(result.value))
    assert "confidential source" not in rendered + caplog.text
    assert "ValueError" in rendered


def test_full_async_query_queue_returns_retryable_status(monkeypatch):
    from fastapi.testclient import TestClient

    from api.main import app
    from api.routes import query
    from core.config import get_settings

    class FullQueue:
        def enqueue(self, *args):
            raise OverflowError("private broker message")

    monkeypatch.setattr(get_settings(), "api_admin_token", "test-admin")
    monkeypatch.setattr(query, "enforce_rate_limit", lambda *args, **kwargs: None)
    monkeypatch.setattr(query, "get_job_queue", lambda: FullQueue())
    response = TestClient(app).post(
        "/api/v1/query/async", headers={"Authorization": "Bearer test-admin"},
        json={"query": "Question", "use_case": "example"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert "private broker message" not in response.text
