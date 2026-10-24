from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

from core.config import get_settings


class AzureOpenAIEmbedderBuilder:
    def build(self) -> Embeddings:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from langchain_openai import AzureOpenAIEmbeddings

        settings = get_settings()
        model = AzureOpenAIEmbeddings(
            model=settings.azure_embedding_model_identity.partition(":")[0] or "text-embedding-3-small",
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=SecretStr(settings.azure_openai_api_key) if settings.azure_openai_api_key else None,
            azure_ad_token_provider=None
            if settings.azure_openai_api_key
            else get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            ),
            dimensions=settings.azure_embedding_dimensions,
            max_retries=0,
            timeout=settings.model_timeout_seconds,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_embedding_deployment,
        )

        return model
