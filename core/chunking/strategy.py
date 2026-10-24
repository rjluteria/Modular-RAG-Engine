"""Chunking is a utility layered on top of LangChain's own splitters, not a
bespoke chunk service. Structure-aware splitting is picked per document
type from metadata already set by the parser adapters."""

from bs4 import BeautifulSoup
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from core.config import get_settings

# Conservative character limit for table row-groups. all-MiniLM-L6-v2 has a
# 256-token limit; text past that is silently truncated at embed time, not
# rejected — so this stays well under that to leave headroom for tokenization
# overhead (subword splitting means tokens != characters 1:1).
_TABLE_CHUNK_CHAR_LIMIT = 800


def _prepend_section_header(chunk: Document) -> Document:
    """Contextual header prepending: cheap, improves retrieval by default."""
    title = chunk.metadata.get("section_title")
    if title and not chunk.page_content.startswith(title):
        chunk.page_content = f"{title}\n\n{chunk.page_content}"
    return chunk


def _split_table_document(doc: Document) -> list[Document]:
    """Row-aware splitting for table elements. Reformats table content into
    clean pipe-delimited rows regardless of size, and splits into multiple
    row-group chunks (header repeated in each) only when the table is too
    large to stay under the embedder's token window as a single chunk."""
    headers, rows = doc.metadata.get("table_headers"), doc.metadata.get("table_rows")
    if headers is not None and rows is not None:
        # Each complete row carries its own column labels and qualifications.
        # Oversized rows are kept intact rather than dropping a later column.
        chunks = []
        for index, row in enumerate(rows):
            cells = [
                f"{header}: {value if value else '[not specified]'}"
                for header, value in zip(headers, row, strict=True)
            ]
            context = doc.metadata.get("section_title")
            content = (str(context) + "\n" if context else "") + "; ".join(cells)
            metadata = {
                key: value
                for key, value in doc.metadata.items()
                if key not in {"table_rows", "table_headers"}
            }
            chunks.append(
                Document(
                    page_content=content, metadata={**metadata, "table_row": index + 1, "table_cells": cells}
                )
            )
        return chunks or [doc]

    html = doc.metadata.get("text_as_html")
    if not html:
        return [doc]

    soup = BeautifulSoup(html, "html.parser")
    rows = soup.find_all("tr")
    if not rows:
        return [doc]

    header_cells = rows[0].find_all(["th", "td"])
    header_line = " | ".join(c.get_text(strip=True) for c in header_cells)

    data_rows = [" | ".join(c.get_text(strip=True) for c in row.find_all(["td", "th"])) for row in rows[1:]]
    data_rows = [r for r in data_rows if r]

    split_docs: list[Document] = []
    current_rows: list[str] = []
    current_len = len(header_line)

    def _flush():
        if not current_rows:
            return
        content = header_line + "\n" + "\n".join(current_rows)
        split_docs.append(Document(page_content=content, metadata=dict(doc.metadata)))

    for row_line in data_rows:
        if current_rows and current_len + len(row_line) > _TABLE_CHUNK_CHAR_LIMIT:
            _flush()
            current_rows = []
            current_len = len(header_line)
        current_rows.append(row_line)
        current_len += len(row_line)

    _flush()
    return split_docs or [doc]


def split_documents(documents: list[Document]) -> list[Document]:
    settings = get_settings()

    # Tables/figures are kept intact as a chunk unless too large — never
    # merged into surrounding prose, which would destroy their structure.
    tables = [d for d in documents if d.metadata.get("element_type") == "table"]
    prose = [d for d in documents if d.metadata.get("element_type") != "table"]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    chunks = splitter.split_documents(prose)
    chunks = [_prepend_section_header(c) for c in chunks]

    table_chunks: list[Document] = []
    for table in tables:
        table_chunks.extend(_split_table_document(table))

    all_chunks = chunks + table_chunks
    for idx, chunk in enumerate(all_chunks):
        chunk.metadata["chunk_id"] = f"{chunk.metadata.get('source_id', 'doc')}_{idx}"

    return all_chunks
