from langchain_core.language_models import BaseChatModel

from core.config import get_settings


class OllamaLLMBuilder:
    def build(self) -> BaseChatModel:
        from langchain_ollama import ChatOllama

        settings = get_settings()
        return ChatOllama(base_url=settings.ollama_base_url, model=settings.ollama_model, temperature=0.1)
