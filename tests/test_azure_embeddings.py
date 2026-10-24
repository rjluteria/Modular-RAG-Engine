from types import SimpleNamespace


def test_azure_embedding_builder_preserves_provider_with_shared_state(monkeypatch):
    import langchain_openai

    from core.adapters.embedders.azure_openai_adapter import AzureOpenAIEmbedderBuilder
    from core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "shared_state_provider", "azure_blob")
    monkeypatch.setattr(settings, "azure_openai_api_key", "synthetic-test-only")
    monkeypatch.setattr(settings, "azure_openai_endpoint", "https://example.invalid")
    monkeypatch.setattr(settings, "azure_openai_embedding_deployment", "synthetic")
    observed = {}
    provider = SimpleNamespace(embed_documents=lambda texts: [[float(len(text))] for text in texts])

    def build(**kwargs):
        observed.update(kwargs)
        return provider

    def unexpected():
        raise AssertionError("Embedding construction must not require publication records")

    monkeypatch.setattr(langchain_openai, "AzureOpenAIEmbeddings", build)
    monkeypatch.setattr("core.adapters.state.blob_state.blob_state", unexpected)
    model = AzureOpenAIEmbedderBuilder().build()
    assert model is provider
    assert model.embed_documents(["hello"]) == [[5.0]]
    assert observed["azure_deployment"] == "synthetic"
    assert observed["dimensions"] == settings.azure_embedding_dimensions
