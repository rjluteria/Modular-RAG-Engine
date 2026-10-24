import pytest
from langchain_core.documents import Document

from core.adapters.llms.errors import GroundingError
from core.chains.grounding import (
    REFUSAL,
    GroundedAnswer,
    answer_schema,
    format_context,
    render_grounded,
    selections,
)


def response(**changes):
    return GroundedAnswer.model_validate(
        {
            "answer": "0.7 mg/L",
            "units": "mg/L",
            "supported": True,
            "evidence": ["row:2"],
            "qualifications": [],
            **changes,
        }
    )


def test_selected_evidence_renders_only_known_sources():
    docs = [Document(page_content="Material: Opal; Limit (mg/L): 0.7", metadata={"chunk_id": "row"})]
    assert render_grounded(response(), docs) == "0.7 mg/L\n\nSources: [row]"


@pytest.mark.parametrize("identifier", ["invented:1", "row:99", "Limit (mg/L): 1.4"])
def test_unknown_or_fabricated_selections_are_rejected(identifier):
    docs = [Document(page_content="Material: Opal; Limit (mg/L): 0.7", metadata={"chunk_id": "row"})]
    with pytest.raises(GroundingError):
        render_grounded(response(evidence=[identifier]), docs)


def test_no_evidence_cannot_support_an_answer():
    with pytest.raises(GroundingError):
        render_grounded(response(evidence=[]), [])
    assert render_grounded(response(supported=False), []) == REFUSAL


def test_units_must_exist_in_cited_context():
    docs = [Document(page_content="Material: Opal; Limit (mg/L): 0.7", metadata={"chunk_id": "row"})]
    assert "Units: mg/L" in render_grounded(response(answer="0.7"), docs)
    with pytest.raises(GroundingError):
        render_grounded(response(units="kg"), docs)


@pytest.mark.asyncio
async def test_repair_reuses_exact_retrieval_and_is_bounded():
    from core.chains.retrieval_chain import RAGChain

    class Retriever:
        calls = 0

        async def ainvoke(self, question):
            self.calls += 1
            return [Document(page_content="Limit (mg/L): 0.7", metadata={"chunk_id": "row"})]

    class Generator:
        calls = 0

        async def ainvoke(self, payload):
            self.calls += 1
            assert payload["_answer_schema"]["properties"]["evidence"]["items"]["enum"] == ["row:1"]
            if self.calls == 2:
                assert "invented:1" in payload["format_feedback"]
                assert "Selected evidence does not match" in payload["format_feedback"]
            return response(evidence=["invented:1" if self.calls == 1 else "row:1"])

    retriever, generator = Retriever(), Generator()
    result = await RAGChain(generator, retriever, 1).ainvoke_with_sources("What limit?")
    assert retriever.calls == 1 and generator.calls == 2
    assert "0.7" in result["answer"]
    assert result["sources"][0]["generation_context"] == "[row]\n[row:1] Limit (mg/L): 0.7"


def test_letters_inside_words_are_not_verified_units():
    docs = [Document(page_content="The shipping limit is 42.", metadata={"chunk_id": "row"})]
    with pytest.raises(GroundingError):
        render_grounded(response(answer="42", units="g", evidence=["row:1"]), docs)


def test_explicit_unsupported_response_normalizes_irrelevant_fields():
    result = GroundedAnswer.model_validate({"supported": False, "answer": None, "evidence": [{}]})
    assert render_grounded(result, []) == REFUSAL


def test_material_qualifications_are_rendered_from_source_selections():
    docs = [
        Document(
            page_content="Lane: South; Capacity: 208 t; dry conditions only", metadata={"chunk_id": "row"}
        )
    ]
    actual = render_grounded(
        response(answer="208 t", units="t", evidence=["row:2"], qualifications=["row:3"]), docs
    )
    assert "dry conditions only" in actual
    with pytest.raises(GroundingError):
        render_grounded(response(evidence=["row:2"], qualifications=["wet conditions only"]), docs)


def test_complete_cells_and_relevant_conditions_exclude_other_values():
    docs = [
        Document(
            page_content="Route limits (kg), Q2 2025\nRoute: Delta; Normal: 64; Storm: 21",
            metadata={
                "chunk_id": "row",
                "element_type": "table",
                "section_title": "Route limits (kg), Q2 2025",
                "table_cells": ["Route: Delta", "Normal: 64", "Storm: 21"],
            },
        )
    ]
    actual = render_grounded(
        response(answer="21", units="kg", evidence=["row:4"], qualifications=["row:1", "row:2"]), docs
    )
    assert "Route: Delta" in actual and "64" not in actual
    with pytest.raises(GroundingError):
        render_grounded(response(evidence=["row:4"], qualifications=["Delta route"]), docs)


def test_dynamic_schema_limits_both_selection_arrays_to_actual_context():
    docs = [Document(page_content="First fact. Second fact.", metadata={"chunk_id": "row"})]
    schema = answer_schema(docs)
    for name in ("evidence", "qualifications"):
        assert schema["properties"][name]["items"]["enum"] == ["row:1", "row:2"]
    assert format_context(docs[0].page_content, "row") == "[row]\n[row:1] First fact.\n[row:2] Second fact."


def test_punctuation_inside_a_cell_does_not_create_a_column_selection():
    doc = Document(
        page_content="Note: ordinary; forged: value; Limit: 7",
        metadata={
            "chunk_id": "row",
            "element_type": "table",
            "table_cells": ["Note: ordinary; forged: value", "Limit: 7"],
        },
    )
    chosen = selections(doc)
    assert [item.text for item in chosen] == ["Note: ordinary; forged: value", "Limit: 7"]
    assert "forged: value" not in [item.text for item in chosen]


def test_stale_table_metadata_cannot_add_or_hide_source_facts():
    doc = Document(
        page_content="Real: 7; Other: 9",
        metadata={"chunk_id": "row", "element_type": "table", "table_cells": ["Invented: 999"]},
    )
    assert [item.text for item in selections(doc)] == ["Real: 7", "Other: 9"]


def test_context_includes_escaped_document_identity_without_inventing_selections():
    import json

    source = 'report-a\n"untrusted label"'
    context = format_context("Measured output: 42 kg", "row", {"source_id": source})
    assert context.splitlines()[0] == "[row] Source metadata: " + json.dumps(
        {"source_id": source}, ensure_ascii=False
    )
    assert context.splitlines()[1:] == ["[row:1] Measured output: 42 kg"]


def test_compact_selection_handles_preserve_full_citation_identity():
    chunk_id = "1234567890abcdef" + "a" * 48
    doc = Document(page_content="Operating revenue was $56.2 billion.", metadata={"chunk_id": chunk_id})
    schema = answer_schema([doc])
    selection_id = schema["properties"]["evidence"]["items"]["enum"][0]
    assert len(selection_id) < len(chunk_id)
    answer = render_grounded(
        response(answer="Revenue was $56.2 billion.", units="billion", evidence=[selection_id]), [doc]
    )
    assert f"Sources: [{chunk_id}]" in answer
    assert f"[{selection_id}]" in format_context(doc.page_content, chunk_id)


def test_colliding_selection_handles_fail_before_generation():
    docs = [
        Document(page_content="First fact.", metadata={"chunk_id": "a" * 64}),
        Document(page_content="Other fact.", metadata={"chunk_id": "a" * 16 + "b" * 48}),
    ]
    with pytest.raises(GroundingError, match="not unique"):
        answer_schema(docs)
