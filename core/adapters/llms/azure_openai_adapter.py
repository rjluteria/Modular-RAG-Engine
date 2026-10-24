"""Independent Azure generation/judge deployments with managed identity."""

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from core.config import get_settings


class AzureOpenAILLMBuilder:
    def build(self, *, judge: bool = False) -> BaseChatModel:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from langchain_openai import AzureChatOpenAI

        from core.adapters.llms.azure_gate import AzureProviderGate
        from core.adapters.llms.errors import ProviderConfigurationError

        settings = get_settings()
        deployment = (
            settings.azure_openai_judge_deployment if judge else settings.azure_openai_chat_deployment
        )
        if not settings.azure_openai_endpoint or not deployment:
            raise ProviderConfigurationError("Selected model role requires configuration")
        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=SecretStr(settings.azure_openai_api_key) if settings.azure_openai_api_key else None,
            azure_ad_token_provider=None
            if settings.azure_openai_api_key
            else get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            ),
            api_version=settings.azure_openai_api_version,
            azure_deployment=deployment,
            temperature=0,
            max_completion_tokens=settings.max_model_output_tokens,
            timeout=settings.model_timeout_seconds,
            max_retries=0,  # Keep provider retries at the application boundary.
            callbacks=[AzureProviderGate()] if settings.shared_state_provider == "azure_blob" else [],
        )
