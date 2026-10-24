"""Qdrant storage with staged revisions and a durable atomic visibility switch."""

import hashlib
import json
import uuid

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from core.adapters.vector_stores.revisions import manifest_key, read_lock, redis_connection, source_key
from core.config import get_settings


class QdrantVectorStoreBuilder:
    def check_ready(self) -> None:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=get_settings().qdrant_url, timeout=3)
        try:
            # Readiness checks connectivity without allocating model weights or
            # creating a collection. Queries/ingestion initialize it lazily.
            client.get_collections()
        finally:
            client.close()

    def build(self, embedder: Embeddings) -> VectorStore:
        from langchain_qdrant import QdrantVectorStore
        from qdrant_client import QdrantClient
        from qdrant_client.http import models

        settings = get_settings()
        client = QdrantClient(url=settings.qdrant_url, timeout=30)
        if not client.collection_exists(settings.qdrant_collection):
            vector = embedder.embed_query("dimension probe")
            client.create_collection(
                settings.qdrant_collection,
                vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE),
            )

        class ManagedQdrantStore(QdrantVectorStore):
            @staticmethod
            def _selection(source_id, use_case):
                return models.Filter(
                    must=[
                        models.FieldCondition(
                            key="metadata.source_id", match=models.MatchValue(value=source_id)
                        ),
                        models.FieldCondition(
                            key="metadata.use_case", match=models.MatchValue(value=use_case)
                        ),
                    ]
                )

            def replace_source(self, documents, *, source_id: str, use_case: str):
                if not documents:
                    raise ValueError("A source revision must contain documents")
                key = source_key(use_case, source_id)
                selection = self._selection(source_id, use_case)
                content = [
                    (
                        d.page_content,
                        {
                            k: v
                            for k, v in d.metadata.items()
                            if k not in {"chunk_id", "source_uri", "_revision"}
                        },
                    )
                    for d in documents
                ]
                revision = hashlib.sha256(
                    json.dumps([use_case, source_id, content], sort_keys=True).encode()
                ).hexdigest()
                ids = [
                    str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps([revision, i])))
                    for i in range(len(documents))
                ]
                with (
                    redis_connection() as connection,
                    connection.lock(
                        "rag:source:" + self.collection_name + ":" + key, timeout=1800, blocking_timeout=30
                    ),
                ):
                    # Adopt legacy chunks before publishing a replacement. Untagged chunks remain
                    # readable during an interrupted migration, along with the tagged legacy revision.
                    with read_lock(connection, self.collection_name):
                        legacy = models.Filter(
                            must=[
                                selection,
                                models.IsEmptyCondition(
                                    is_empty=models.PayloadField(key="metadata._revision")
                                ),
                            ]
                        )
                        if self.client.count(self.collection_name, count_filter=legacy, exact=True).count:
                            old_revision = "legacy:" + self.collection_name + ":" + key
                            connection.hset(manifest_key(self.collection_name), key, old_revision)
                            self.client.set_payload(
                                self.collection_name,
                                payload={"_revision": old_revision},
                                key="metadata",
                                points=legacy,
                                wait=True,
                            )
                    staged = []
                    for doc, point_id in zip(documents, ids, strict=True):
                        doc.metadata["chunk_id"] = point_id
                        staged.append(doc.model_copy(deep=True))
                        staged[-1].metadata["_revision"] = revision
                    # Any incomplete batch remains invisible. The previous revision stays intact.
                    self.add_documents(staged, ids=ids)
                    with read_lock(connection, self.collection_name):
                        with connection.pipeline(transaction=True) as transaction:
                            transaction.hset(manifest_key(self.collection_name), key, revision)
                            checksum = documents[0].metadata.get("upload_checksum")
                            if checksum:
                                transaction.set("rag:revision:" + self.collection_name + ":" + key, checksum)
                            transaction.execute()
                        # Context readers have finished copying the old revision before superseded-revision removal.
                        stale = models.Filter(
                            must=[selection],
                            must_not=[
                                models.FieldCondition(
                                    key="metadata._revision", match=models.MatchValue(value=revision)
                                )
                            ],
                        )
                        self.client.delete(
                            self.collection_name,
                            points_selector=models.FilterSelector(filter=stale),
                            wait=True,
                        )
                return ids

            def delete_source(self, *, source_id: str, use_case: str):
                key = source_key(use_case, source_id)
                with (
                    redis_connection() as connection,
                    connection.lock(
                        "rag:source:" + self.collection_name + ":" + key, timeout=1800, blocking_timeout=30
                    ),
                    read_lock(connection, self.collection_name),
                ):
                    selection = self._selection(source_id, use_case)
                    legacy = models.Filter(
                        must=[
                            selection,
                            models.IsEmptyCondition(is_empty=models.PayloadField(key="metadata._revision")),
                        ]
                    )
                    if self.client.count(self.collection_name, count_filter=legacy, exact=True).count:
                        old_revision = "legacy:" + self.collection_name + ":" + key
                        connection.hset(manifest_key(self.collection_name), key, old_revision)
                        self.client.set_payload(
                            self.collection_name,
                            payload={"_revision": old_revision},
                            key="metadata",
                            points=legacy,
                            wait=True,
                        )
                    with connection.pipeline(transaction=True) as transaction:
                        transaction.hset(
                            manifest_key(self.collection_name), key, "deleted:" + str(uuid.uuid4())
                        )
                        transaction.delete("rag:revision:" + self.collection_name + ":" + key)
                        transaction.execute()
                    self.client.delete(
                        self.collection_name,
                        points_selector=models.FilterSelector(filter=selection),
                        wait=True,
                    )

        return ManagedQdrantStore(
            client=client, collection_name=settings.qdrant_collection, embedding=embedder
        )
