"""Select source excerpts by ID; render only text supplied by the server."""

import json
import re
import unicodedata
from dataclasses import dataclass

from langchain_core.documents import Document
from pydantic import BaseModel, Field, model_validator

from core.adapters.llms.errors import GroundingError

REFUSAL = "I don't know based on the available context."


class GroundedAnswer(BaseModel):
    supported: bool = Field(
        description=(
            "True only when evidence supports at least one fact actually requested by the question. "
            "A neighboring attribute, statistic, event, or period does not establish support. A calculation requires sufficient observations or an explicit source-defined relationship; unrelated aggregates do not establish an unstated statistic."
        ),
    )
    evidence: list[str] = Field(max_length=50, description="Selection IDs supporting the requested facts")
    answer: str = Field(min_length=1, max_length=16000)
    units: str = Field(
        max_length=128,
        description=(
            "Only the literal measurement unit of an answered numeric quantity, copied exactly from cited text. "
            "Use an empty string for names, identifiers, counts without an explicit unit, dates, nonnumeric "
            "answers, or when no literal measurement unit is present. Never invent or expand a unit label."
        ),
    )
    qualifications: list[str] = Field(
        max_length=16,
        description=(
            "Selection IDs for necessary additional restrictions on applicability, scope, effective period, "
            "or measurement basis of the requested fact. Exclude independent measurements, other rows or "
            "periods, and facts already expressed in the answer. Empty when no additional restriction applies."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_refusal(cls, value):
        if isinstance(value, dict) and value.get("supported") is False:
            return {"answer": REFUSAL, "units": "", "qualifications": [], "supported": False, "evidence": []}
        return value


@dataclass(frozen=True)
class Selection:
    identifier: str
    chunk_id: str
    text: str
    document: Document


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _parts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+|[\n;]+", text) if part.strip()]


def selections(document: Document) -> list[Selection]:
    chunk_id = str(document.metadata.get("chunk_id"))
    parts = _parts(document.page_content)
    cells = document.metadata.get("table_cells")
    if (
        document.metadata.get("element_type") == "table"
        and isinstance(cells, list)
        and cells
        and all(isinstance(cell, str) for cell in cells)
    ):
        title = document.metadata.get("section_title")
        title = title if isinstance(title, str) else ""
        canonical = (title + "\n" if title else "") + "; ".join(cells)
        if _normalize(canonical) == _normalize(document.page_content):
            # Keep semicolons inside a complete parsed cell from creating columns.
            parts = _parts(title) + cells
    # Selection IDs are internal prompt handles. Keep full chunk IDs for returned citations,
    # while avoiding repeated 256-bit IDs in context and both schema enum arrays.
    handle = chunk_id[:16]
    return [Selection(f"{handle}:{i}", chunk_id, text, document) for i, text in enumerate(parts, 1)]


def format_context(page_content: str, chunk_id: str | None, metadata: dict | None = None) -> str:
    document = Document(page_content=page_content, metadata={**(metadata or {}), "chunk_id": chunk_id})
    source_id = document.metadata.get("source_id")
    identity = ""
    if isinstance(source_id, str) and source_id:
        identity = " Source metadata: " + json.dumps({"source_id": source_id}, ensure_ascii=False)
    return f"[{chunk_id}]{identity}\n" + "\n".join(
        f"[{item.identifier}] {item.text}" for item in selections(document)
    )


def answer_schema(documents: list[Document]) -> dict:
    identifiers = [item.identifier for document in documents for item in selections(document)]
    if len(set(identifiers)) != len(identifiers):
        raise GroundingError("Evidence selection identifiers are not unique")
    schema = GroundedAnswer.model_json_schema()
    for name in ("evidence", "qualifications"):
        schema["properties"][name]["items"] = {"type": "string", "enum": identifiers}
    return schema


def render_grounded(response: GroundedAnswer, documents: list[Document]) -> str:
    if not response.supported:
        return REFUSAL
    if not response.evidence:
        raise GroundingError("An answer requires supporting evidence")
    available = {}
    for document in documents:
        for item in selections(document):
            if item.identifier in available:
                raise GroundingError("Evidence selection identifiers are not unique")
            available[item.identifier] = item
    chosen = response.evidence + response.qualifications
    if any(identifier not in available for identifier in chosen):
        raise GroundingError("Selected evidence does not match the retrieved context")
    references = list(dict.fromkeys(available[identifier].chunk_id for identifier in chosen))
    cited = [available[identifier].document for identifier in chosen]
    answer = response.answer.strip()
    if response.units:
        pattern = r"(?<![^\W\d_])" + re.escape(_normalize(response.units)) + r"(?![^\W\d_])"
        if not any(re.search(pattern, _normalize(document.page_content)) for document in cited):
            raise GroundingError("Units do not match cited context")
        if not re.search(pattern, _normalize(answer), re.IGNORECASE):
            answer += "\nUnits: " + response.units
    conditions = [available[identifier].text for identifier in response.qualifications]
    conditions = [
        text
        for text in dict.fromkeys(conditions)
        if _normalize(text).casefold() not in _normalize(answer).casefold()
    ]
    if conditions:
        answer += "\nConditions: " + "; ".join(conditions)
    return answer + "\n\nSources: " + ", ".join(f"[{reference}]" for reference in references)
