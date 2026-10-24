import pytest
from langchain_core.documents import Document

from core.chains.retrieval_chain import RAGChain


class Retriever:
    calls = 0

    async def ainvoke(self, question):
        self.calls += 1
        return [
            Document(page_content="The code is amber.", metadata={"chunk_id": "a"}),
            Document(page_content="Another fact", metadata={"chunk_id": "b"}),
        ]


class Generator:
    async def ainvoke(self, value):
        assert "[a]\n[a:1] The code is amber." == value["context"]
        from core.chains.grounding import GroundedAnswer

        return GroundedAnswer(
            qualifications=[],
            units="",
            answer="amber",
            supported=True,
            evidence=["a:1"],
        )


@pytest.mark.asyncio
async def test_generation_uses_exact_sources_and_retrieves_once():
    retriever = Retriever()
    chain = RAGChain(Generator(), retriever, 1)
    result = await chain.ainvoke_with_sources("What code?")
    assert retriever.calls == 1
    assert result["answer"] == "amber\n\nSources: [a]"
    assert [s["chunk_id"] for s in result["sources"]] == ["a"]


@pytest.mark.asyncio
async def test_empty_context_refuses_without_generation():
    class Empty:
        async def ainvoke(self, question):
            return []

    result = await RAGChain(None, Empty(), 3).ainvoke_with_sources("Unknown?")
    assert "don't know" in result["answer"]
    assert result["sources"] == []


def test_keyword_scroll_reads_every_page(monkeypatch):
    from types import SimpleNamespace

    offsets = []

    def scroll(**kwargs):
        offsets.append(kwargs["offset"])
        point = SimpleNamespace(payload={"page_content": "test", "metadata": {}})
        return [point], ("next" if kwargs["offset"] is None else None)

    store = SimpleNamespace(client=SimpleNamespace(scroll=scroll), collection_name="synthetic")
    monkeypatch.setattr("core.registry.get_vector_store", lambda: store)
    from core.adapters.retrieval.qdrant_adapter import _scroll_corpus

    assert len(_scroll_corpus(store, None)) == 2
    assert offsets == [None, "next"]


def _context_doc(identifier, source, text="Generic passage", scope="alpha"):
    return Document(
        page_content=text, metadata={"chunk_id": identifier, "source_id": source, "use_case": scope}
    )


def test_compact_source_completion_recovers_breakdown_beside_accounting_charge():
    from core.adapters.retrieval.context_selection import complete_source_context

    report = [
        _context_doc(f"report-{i}", "funding", text)
        for i, text in enumerate(
            [
                "The accounting expense is 9.2 million.",
                "The three commitments are 12.4, 8.1, and 6.7 million.",
                "The agreement is effective in June 2027.",
                "Funding and accounting measures have different purposes.",
                "The program covers habitat, waterways, and community support.",
            ]
        )
    ]
    other = [_context_doc(f"annual-{i}", "annual", "Annual accounting discussion") for i in range(20)]
    ranked = [report[0], *other[:5], report[2], other[5]]
    result = complete_source_context(ranked, report + other, 8)
    assert result[:4] == ranked[:4]
    assert {d.metadata["chunk_id"] for d in report} <= {d.metadata["chunk_id"] for d in result}
    assert len(result) == 8


def test_source_completion_preserves_other_sources_needed_for_synthesis():
    from core.adapters.retrieval.context_selection import complete_source_context

    main = [_context_doc(f"main-{i}", "main") for i in range(5)]
    others = [_context_doc(f"other-{i}", f"other-source-{i}") for i in range(3)]
    ranked = [*main, *others]
    result = complete_source_context(ranked, ranked, 8)
    assert {d.metadata["source_id"] for d in result} == {d.metadata["source_id"] for d in ranked}
    assert len(result) == 8


def test_source_completion_respects_scope_and_small_context_limits():
    from core.adapters.retrieval.context_selection import complete_source_context

    first = _context_doc("alpha-one", "shared", "Public first passage")
    second = _context_doc("alpha-two", "shared", "Public second passage")
    hidden = _context_doc("beta-one", "shared", "Other partition", scope="beta")
    assert complete_source_context([first], [first, second, hidden], 1) == [first]
    assert complete_source_context([first], [first, second, hidden], 2) == [first, second]


def test_large_sources_keep_ranked_context_when_completion_cannot_fit():
    from core.adapters.retrieval.context_selection import complete_source_context

    corpus = [_context_doc(f"large-{i}", "large") for i in range(20)]
    assert complete_source_context(corpus[:4], corpus, 4) == corpus[:4]
