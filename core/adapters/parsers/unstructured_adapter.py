"""Local parser: `unstructured`. Auto-detects file type via partition()."""

from langchain_core.documents import Document

_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".html", ".htm", ".txt", ".csv"}


class UnstructuredParserAdapter:
    def supports(self, filename: str, content_type: str | None) -> bool:
        return any(filename.lower().endswith(ext) for ext in _SUPPORTED_EXTENSIONS)

    def parse(self, file_path: str, *, source_id: str, use_case: str) -> list[Document]:
        import os

        os.environ.setdefault("DO_NOT_TRACK", "1")
        from unstructured.partition.auto import partition

        if file_path.lower().endswith(".pdf"):
            from unstructured.partition.pdf import partition_pdf

            elements = partition_pdf(filename=file_path, strategy="fast")
        else:
            elements = partition(filename=file_path)

        # Build a lookup of element_id -> title text, so parent_id can be
        # resolved to the actual heading instead of left as a raw hash.
        title_text_by_id = {
            element.id: str(element) for element in elements if type(element).__name__.lower() == "title"
        }

        documents: list[Document] = []
        for element in elements:
            metadata = element.metadata.to_dict() if element.metadata else {}
            parent_id = metadata.get("parent_id")
            documents.append(
                Document(
                    page_content=str(element),
                    metadata={
                        "source_id": source_id,
                        "use_case": use_case,
                        "page_number": metadata.get("page_number"),
                        "section_title": title_text_by_id.get(parent_id),
                        "element_type": type(element).__name__.lower(),
                        "source_uri": file_path,
                        # Preserved for table elements so row-aware chunking
                        # can split large tables without losing structure.
                        "text_as_html": metadata.get("text_as_html"),
                    },
                )
            )
        return documents
