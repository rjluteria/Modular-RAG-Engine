"""Synthetic table-only values must survive independently of the prose."""

import pytest

from core.adapters.parsers.structured_adapter import StructuredParserAdapter
from core.chunking.strategy import split_documents
from scripts.format_smoke import build_fixtures


@pytest.fixture(scope="module")
def format_fixtures(tmp_path_factory):
    folder = tmp_path_factory.mktemp("formats")
    build_fixtures(folder)
    return folder


@pytest.mark.parametrize("extension", ["pdf", "docx", "txt", "html", "csv"])
def test_answer_values_survive_parsing_and_chunking(format_fixtures, extension):
    path = format_fixtures / ("sample." + extension)
    documents = StructuredParserAdapter().parse(str(path), source_id="synthetic", use_case="synthetic")
    chunks = split_documents(documents)
    content = " ".join(chunk.page_content for chunk in chunks)
    assert "42" in content
    assert "shipping" in content.lower()
    if extension != "txt":
        assert "Synthetic item" in content
        assert "73" in content
    assert all(chunk.metadata["source_id"] == "synthetic" for chunk in chunks)


def test_optional_document_client_and_stream_close_on_provider_failure(monkeypatch, tmp_path):
    import azure.ai.documentintelligence
    import pytest

    from core.adapters.parsers.doc_intelligence_adapter import DocIntelligenceParserAdapter
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "azure_doc_intelligence_endpoint", "https://example.invalid")
    monkeypatch.setattr(settings, "azure_doc_intelligence_api_key", "synthetic-test-only")
    observed = {}

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            observed["client_closed"] = True

        def begin_analyze_document(self, model, *, body):
            observed["stream"] = body
            raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr(azure.ai.documentintelligence, "DocumentIntelligenceClient", Client)
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-synthetic")
    with pytest.raises(RuntimeError, match="Synthetic provider failure"):
        DocIntelligenceParserAdapter().parse(str(source), source_id="sample", use_case="synthetic")
    assert observed["client_closed"]
    assert observed["stream"].closed
