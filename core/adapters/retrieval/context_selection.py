"""Complete compact sources without discarding strong or diverse retrieval anchors."""

from collections import defaultdict

from langchain_core.documents import Document


def _source(document: Document):
    source = document.metadata.get("source_id")
    scope = document.metadata.get("use_case")
    if isinstance(source, str) and source and (scope is None or isinstance(scope, str)):
        return scope, source
    return None


def complete_source_context(ranked: list[Document], corpus: list[Document], limit: int) -> list[Document]:
    """Use only the caller's visible, filtered snapshot; never exceed its context limit.

    Keep the strongest half of the hits and an anchor for every represented
    source. Use remaining space to complete sources that fit in full, then
    retain remaining ranked hits. Large sources are not expanded partially.
    """
    if limit <= 0:
        return []
    ranked = ranked[:limit]
    selected = list(ranked[: (limit + 1) // 2])
    seen = {d.metadata.get("chunk_id") for d in selected}
    represented = {_source(d) for d in selected}
    for document in ranked[len(selected) :]:
        source = _source(document)
        if source is None or source not in represented:
            selected.append(document)
            seen.add(document.metadata.get("chunk_id"))
            represented.add(source)
    anchors = list(selected)
    groups = defaultdict(list)
    for document in corpus:
        source = _source(document)
        if source is not None:
            groups[source].append(document)
    considered = set()
    for anchor in anchors:
        source = _source(anchor)
        if source is None or source in considered:
            continue
        considered.add(source)
        group = groups[source]
        if len(group) > limit:
            continue
        missing = [d for d in group if d.metadata.get("chunk_id") not in seen]
        if len(missing) <= limit - len(selected):
            selected.extend(missing)
            seen.update(d.metadata.get("chunk_id") for d in missing)
    for document in ranked:
        if len(selected) == limit:
            break
        if document.metadata.get("chunk_id") not in seen:
            selected.append(document)
            seen.add(document.metadata.get("chunk_id"))
    return selected
