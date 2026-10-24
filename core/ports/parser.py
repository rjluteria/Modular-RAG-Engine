"""The seam that makes ingestion file-type agnostic.

New file types / new use cases are handled by adding an adapter and
registering it in core/registry.py — never by branching pipeline code.
"""

from typing import Protocol, runtime_checkable

from langchain_core.documents import Document


@runtime_checkable
class DocumentParser(Protocol):
    """Turns raw bytes on disk into LangChain Documents with page/element
    metadata. Parsing only — no chunking here (see core/chunking)."""

    def supports(self, filename: str, content_type: str | None) -> bool:
        """Whether this adapter can handle the given file."""
        ...

    def parse(self, file_path: str, *, source_id: str, use_case: str) -> list[Document]:
        """Returns Documents with metadata:
        source_id, page_number, section_title, element_type, source_uri, use_case.
        """
        ...
