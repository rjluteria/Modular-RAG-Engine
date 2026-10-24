"""LLM selection behind LangChain's own BaseChatModel interface."""

from typing import Protocol

from langchain_core.language_models import BaseChatModel


class LLMBuilder(Protocol):
    def build(self) -> BaseChatModel: ...
