from langchain_core.embeddings import Embeddings

from core.config import get_settings


class SentenceTransformersEmbedderBuilder:
    def build(self) -> Embeddings:
        from langchain_huggingface import HuggingFaceEmbeddings

        settings = get_settings()
        return HuggingFaceEmbeddings(model_name=settings.sentence_transformers_model)
