"""Hosted generation and dedicated judge clients for the current model provider."""

from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from core.adapters.llms.errors import ProviderConfigurationError
from core.config import get_settings


class NvidiaNIMLLMBuilder:
    def build(self, *, judge: bool = False) -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        from core.adapters.llms.provider_gate import ProviderGate

        settings = get_settings()
        if not settings.nvidia_api_key or not settings.nvidia_api_key.strip():
            raise ProviderConfigurationError("Selected hosted provider requires NVIDIA_API_KEY")
        return ChatOpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=SecretStr(settings.nvidia_api_key) if settings.nvidia_api_key else None,
            model=settings.nvidia_judge_model if judge else settings.nvidia_nim_model,
            temperature=1.0,
            top_p=0.95,
            extra_body=settings.nvidia_judge_options if judge else settings.nvidia_generation_options,
            callbacks=[ProviderGate()],
            timeout=settings.model_timeout_seconds,
            max_retries=settings.model_max_retries,
        )

    def bind_grounded(self, model, schema):
        method = get_settings().nvidia_structured_output_method
        options = {"strict": True} if method == "json_schema" else {}
        return model.with_structured_output(schema, method=method, **options)
