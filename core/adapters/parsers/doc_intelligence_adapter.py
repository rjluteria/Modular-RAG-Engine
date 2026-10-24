"""Prod parser: Azure AI Document Intelligence (prebuilt-layout model).
Returns the same Document shape as UnstructuredParserAdapter so downstream
chunking/retrieval code never knows which one ran."""

from langchain_core.documents import Document

from core.config import get_settings

_SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".png", ".jpg", ".jpeg", ".tiff"}


class DocIntelligenceParserAdapter:
    def supports(self, filename: str, content_type: str | None) -> bool:
        return any(filename.lower().endswith(ext) for ext in _SUPPORTED_EXTENSIONS)

    def parse(self, file_path: str, *, source_id: str, use_case: str) -> list[Document]:
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.core.credentials import AzureKeyCredential

        settings = get_settings()
        if not settings.azure_doc_intelligence_endpoint:
            raise ValueError("Selected Azure adapter requires configuration")
        if not settings.azure_doc_intelligence_api_key:
            raise ValueError("Selected Azure adapter requires configuration")
        with DocumentIntelligenceClient(
            endpoint=settings.azure_doc_intelligence_endpoint,
            credential=AzureKeyCredential(settings.azure_doc_intelligence_api_key),
        ) as client, open(file_path, "rb") as source:
            poller = client.begin_analyze_document("prebuilt-layout", body=source)
            result = poller.result()

        documents: list[Document] = []
        for paragraph in result.paragraphs or []:
            page_number = paragraph.bounding_regions[0].page_number if paragraph.bounding_regions else None
            documents.append(
                Document(
                    page_content=paragraph.content,
                    metadata={
                        "source_id": source_id,
                        "use_case": use_case,
                        "page_number": page_number,
                        "section_title": getattr(paragraph, "role", None),
                        "element_type": "narrative_text",
                        "source_uri": file_path,
                    },
                )
            )
        for table in result.tables or []:
            page_number = table.bounding_regions[0].page_number if table.bounding_regions else None
            table_text = "\n".join(cell.content for cell in table.cells)
            documents.append(
                Document(
                    page_content=table_text,
                    metadata={
                        "source_id": source_id,
                        "use_case": use_case,
                        "page_number": page_number,
                        "section_title": None,
                        "element_type": "table",
                        "source_uri": file_path,
                    },
                )
            )
        return documents
