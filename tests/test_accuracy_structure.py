"""Ground truth must preserve relationships, not just global word presence."""

from core.adapters.parsers.structured_adapter import StructuredParserAdapter
from core.chunking.strategy import split_documents
from scripts.accuracy_fixtures import AccuracyCase, build_accuracy_fixtures, matches


def test_varied_structure_preserves_qualified_rows(tmp_path):
    build_accuracy_fixtures(tmp_path)
    parser = StructuredParserAdapter()
    expected = {
        "materials.csv": ("Material: Opal", "Effective date: 2025-08-01", "Limit (mg/L): 0.7"),
        "routes.html": ("Route: Delta", "Day / Storm: 21", "Day / Normal: 64"),
        "equipment.docx": ("Item: Juniper", "2025 / Limit (kg): 27", "2024 / Limit (kg): 18"),
        "transport.pdf": ("Lane: South", "2025 capacity (t): 208", "2024 capacity (t): 197"),
    }
    for filename, values in expected.items():
        docs = parser.parse(str(tmp_path / filename), source_id="synthetic", use_case="synthetic")
        chunks = split_documents(docs)
        assert any(all(value in d.page_content for value in values) for d in chunks), filename
    docs = parser.parse(str(tmp_path / "transport.pdf"), source_id="synthetic", use_case="synthetic")
    assert {d.metadata["page_number"] for d in docs} == {1, 2}


def test_citations_cannot_satisfy_numeric_ground_truth():
    case = AccuracyCase("numeric", "A generic question", ("73",), (), "numeric")
    assert not matches(case, "42 units [734c9231-1234-4321-abcd-123456789012]")
    assert matches(case, "73 units [734c9231-1234-4321-abcd-123456789012]")


def test_numeric_ground_truth_accepts_sentence_punctuation():
    case = AccuracyCase("date", "A generic question", ("14", "March", "2024"), (), "date")
    assert matches(case, "Opened on 14 March 2024. [source]")
    case = AccuracyCase("decimal", "A generic question", ("0.7",), (), "numeric")
    assert matches(case, "The limit is 0.7.")
    assert not matches(case, "The limit is 0.75.")


def test_duplicate_csv_headers_do_not_discard_first_data_row(tmp_path):
    path = tmp_path / "duplicate.csv"
    path.write_text("Value,Value\n27,93\n18,90\n")
    docs = StructuredParserAdapter().parse(str(path), source_id="synthetic", use_case="synthetic")
    chunks = split_documents(docs)
    assert len(chunks) == 2
    assert "Value (column 1): 27; Value (column 2): 93" in chunks[0].page_content


def test_sparse_html_spans_cannot_expand_beyond_table_limit(tmp_path):
    import pytest

    from core.adapters.parsers.structured_adapter import StructuredParserAdapter

    source = tmp_path / "large-grid.html"
    source.write_text(
        "<table><tr><td colspan='500'>Header</td></tr>" + "<tr><td>Value</td></tr>" * 500 + "</table>"
    )
    with pytest.raises(ValueError, match="Table dimensions"):
        StructuredParserAdapter().parse(str(source), source_id="generic", use_case="synthetic")


def test_contradiction_oracle_requires_alternatives_and_no_precedence():
    from scripts.accuracy_fixtures import AccuracyCase, matches

    case = AccuracyCase(
        name="conflict", question="question", expected=("12", "16"), source_files=(), category="contradiction"
    )
    assert matches(case, "12 kg and 16 kg. No notice has priority over the other.")
    assert not matches(case, "12 kg and 16 kg.")
    assert not matches(case, "I don't know based on the available context.")


def test_sparse_csv_expanded_dimensions_are_bounded(tmp_path):
    import pytest

    path = tmp_path / "sparse.csv"
    path.write_text(",".join(f"Column {i}" for i in range(350)) + "\n" + "sample\n" * 300)
    with pytest.raises(ValueError, match="dimensions"):
        StructuredParserAdapter().parse(str(path), source_id="synthetic", use_case="synthetic")


def test_duplicate_csv_columns_retain_both_labeled_values(tmp_path):
    path = tmp_path / "duplicate-columns.csv"
    path.write_text("Value,Value,Unit\n4,9,kg\n")
    chunks = split_documents(
        StructuredParserAdapter().parse(str(path), source_id="synthetic", use_case="synthetic")
    )
    assert any(
        "Value (column 1): 4" in chunk.page_content and "Value (column 2): 9" in chunk.page_content
        for chunk in chunks
    )


def test_comparison_oracle_rejects_swapped_total_and_expense(tmp_path):
    from scripts.accuracy_fixtures import build_fresh_heldout_fixtures

    case = next(
        c for c in build_fresh_heldout_fixtures(tmp_path) if c.name == "fresh-commitments-versus-expense"
    )
    components = "Components are 12.4 million, 8.1 million and 6.7 million. "
    assert matches(
        case, components + "Total commitments are 27.2 million; the expense charge is 9.2 million."
    )
    assert not matches(
        case, components + "Total commitments are 9.2 million; the expense charge is 27.2 million."
    )


def test_compact_fixture_separates_breakdown_and_expense_across_passages(tmp_path):
    from scripts.accuracy_fixtures import build_fresh_heldout_fixtures

    build_fresh_heldout_fixtures(tmp_path)
    chunks = split_documents(
        StructuredParserAdapter().parse(
            str(tmp_path / "funding.docx"), source_id="funding", use_case="synthetic"
        )
    )
    breakdown = [d for d in chunks if "12.4" in d.page_content and "6.7" in d.page_content]
    expense = [d for d in chunks if "9.2" in d.page_content]
    assert len(chunks) <= 8 and len(breakdown) == len(expense) == 1
    assert breakdown[0].metadata["chunk_id"] != expense[0].metadata["chunk_id"]


def test_retrieved_source_identity_does_not_substitute_for_required_passage():
    from scripts.accuracy_fixtures import matches_context

    case = AccuracyCase(
        "joint",
        "Compare commitments and expense",
        (),
        ("funding",),
        "synthesis",
        context_expected=("12.4 million", "9.2 million"),
    )
    assert not matches_context(case, [{"source_id": "funding", "page_content": "Expense is 9.2 million."}])
    assert matches_context(
        case,
        [
            {"source_id": "funding", "page_content": "Commitment is 12.4 million."},
            {"source_id": "funding", "page_content": "Expense is 9.2 million."},
        ],
    )
