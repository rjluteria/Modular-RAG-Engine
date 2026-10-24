"""Azure Search chunks become visible only after a complete revision is stored."""

import hashlib
import json
import time

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from core.adapters.state.blob_state import blob_state
from core.config import get_settings


def source_key(use_case: str, source_id: str) -> str:
    return hashlib.sha256(json.dumps([use_case, source_id]).encode()).hexdigest()


def search_client():
    from azure.identity import DefaultAzureCredential
    from azure.search.documents import SearchClient

    settings = get_settings()
    return SearchClient(settings.azure_search_endpoint, settings.azure_search_index, DefaultAzureCredential())


def corpus(state, use_case=None, filters=None) -> list[Document]:
    manifest = state.read("manifest", {})
    result = []
    for entry in manifest.values():
        if entry.get("deleted") or (use_case is not None and entry["use_case"] != use_case):
            continue
        for row in state.read("revisions/" + entry["source_key"] + "/" + entry["revision"], []):
            if all(row["metadata"].get(k) == v for k, v in (filters or {}).items()):
                result.append(Document(**row))
    return result


class AzurePublishedStore(VectorStore):
    def __init__(self, embedder):
        self.embedder = embedder

    def similarity_search(self, query: str, k: int = 4, **kwargs) -> list[Document]:
        from core.adapters.retrieval.azure_search_adapter import AzurePublishedRetriever

        return AzurePublishedRetriever(store=self, top_k=k, **kwargs).invoke(query)

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs):
        raise NotImplementedError("Use source-scoped atomic replacement")

    def replace_source(self, documents, *, source_id: str, use_case: str):
        if not documents:
            raise ValueError("A source revision must contain documents")
        settings = get_settings()
        key = source_key(use_case, source_id)
        # All writers and readers copy a consistent manifest/corpus under the same lease.
        with blob_state() as state, state.lock("manifest") as lost:
            manifest = state.read("manifest", {})
            previous = manifest.get(key, {})
            content = [[d.page_content, {k: v for k, v in d.metadata.items()
                                        if k not in {"chunk_id", "source_uri", "_revision"}}]
                       for d in documents]
            revision = hashlib.sha256(json.dumps([use_case, source_id, content], sort_keys=True).encode()).hexdigest()
            other_count = sum(v.get("count", 0) for k, v in manifest.items() if k != key)
            if other_count + len(documents) > settings.azure_max_corpus_chunks:
                raise ValueError("Configured corpus capacity exceeded")
            vectors = self.embedder.embed_documents([d.page_content for d in documents])
            rows = []
            payload = []
            for i, (doc, vector) in enumerate(zip(documents, vectors, strict=True)):
                doc = doc.model_copy(deep=True)
                point_id = hashlib.sha256(f"{key}:{revision}:{i}".encode()).hexdigest()
                doc.metadata.update(chunk_id=point_id, _revision=revision)
                rows.append({"page_content": doc.page_content, "metadata": doc.metadata})
                payload.append({"id": point_id, "content": doc.page_content, "vector": vector,
                                "source_key": key, "revision": revision, "use_case": use_case})
            client = search_client()
            try:
                for start in range(0, len(payload), 100):
                    results = client.upload_documents(payload[start:start + 100])
                    if not all(r.succeeded for r in results):
                        raise RuntimeError("Search indexing did not complete")
                # Upload acceptance alone is not proof that every chunk is queryable.
                expected = {row["id"] for row in payload}
                deadline = time.monotonic() + 60
                while True:
                    visible = {row["id"] for row in client.search(
                        search_text="*", filter="revision eq '" + revision + "'", select=["id"]
                    )}
                    if expected <= visible:
                        break
                    if time.monotonic() >= deadline or lost.is_set():
                        raise RuntimeError("Search revision is not fully queryable")
                    time.sleep(0.5)
            finally:
                client.close()
            state.write("revisions/" + key + "/" + revision, rows)
            if lost.is_set():
                raise RuntimeError("Distributed lease was lost before publication")
            manifest[key] = {"revision": revision, "source_key": key, "use_case": use_case, "count": len(rows),
                             "ids": [r["id"] for r in payload],
                             "checksum": documents[0].metadata.get("upload_checksum")}
            state.write("manifest", manifest)
            # Stale entries remain filtered by the manifest even if physical superseded-revision removal fails.
            if previous.get("ids") and previous.get("revision") != revision:
                self._delete_ids(previous["ids"])
                state.delete("revisions/" + key + "/" + previous["revision"])
            return manifest[key]["ids"]

    @staticmethod
    def _delete_ids(ids):
        client = search_client()
        try:
            for start in range(0, len(ids), 100):
                results = client.delete_documents([{"id": i} for i in ids[start:start + 100]])
                if not all(r.succeeded for r in results):
                    raise RuntimeError("Search superseded-revision removal did not complete")
        finally:
            client.close()

    def delete_source(self, *, source_id: str, use_case: str):
        key = source_key(use_case, source_id)
        with blob_state() as state, state.lock("manifest"):
            manifest = state.read("manifest", {})
            previous = manifest.get(key, {})
            manifest[key] = {**previous, "deleted": True, "count": 0, "use_case": use_case}
            state.write("manifest", manifest)
            if previous.get("ids"):
                self._delete_ids(previous["ids"])
            client = search_client()
            try:
                staged_ids = [r["id"] for r in client.search(
                    search_text="*", filter="source_key eq '" + key + "'", select=["id"]
                )]
                if staged_ids:
                    self._delete_ids(staged_ids)
            finally:
                client.close()
            for blob in state.container.list_blobs(name_starts_with="revisions/" + key + "/"):
                state.delete(blob.name)


class AzureSearchVectorStoreBuilder:
    def build(self, embedder: Embeddings) -> VectorStore:
        return AzurePublishedStore(embedder)

    def check_ready(self) -> None:
        client = search_client()
        try:
            client.get_document_count()
        finally:
            client.close()
