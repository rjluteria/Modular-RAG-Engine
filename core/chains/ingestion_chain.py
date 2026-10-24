"""Ingestion as one sequence, reusing the same registry-selected parser,
embedder, and vector store as the retrieval chain. Runs inline for small
files or inside a worker job for batch ingestion (see worker/)."""

from collections.abc import Callable

from core.chunking.strategy import split_documents
from core.registry import get_parser, get_vector_store


def ingest_file(
    file_path: str,
    *,
    source_id: str,
    use_case: str,
    checksum: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    notify = progress or (lambda stage: None)
    notify("initializing")
    parser = get_parser()
    vector_store = get_vector_store()

    notify("parsing")
    documents = parser.parse(file_path, source_id=source_id, use_case=use_case)
    notify("chunking")
    chunks = split_documents(documents)
    if not chunks or not any(chunk.page_content.strip() for chunk in chunks):
        raise ValueError("No extractable text found")
    for chunk in chunks:
        chunk.metadata["upload_checksum"] = checksum
    replace = getattr(vector_store, "replace_source", None)
    if replace is None:
        raise NotImplementedError("Selected storage adapter does not support safe source replacement")
    notify("embedding_and_storing")
    ids = replace(chunks, source_id=source_id, use_case=use_case)

    notify("complete")
    return {"source_id": source_id, "use_case": use_case, "chunks_ingested": len(chunks), "ids": ids}
