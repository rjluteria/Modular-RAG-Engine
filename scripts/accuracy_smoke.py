"""Opt-in generic accuracy regression; never use an existing application collection."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from langchain_core.exceptions import OutputParserException

from core.adapters.llms.errors import GroundingError
from core.adapters.llms.provider_gate import remaining_cooldown
from core.chains.ingestion_chain import ingest_file
from core.chains.retrieval_chain import build_retrieval_chain
from core.config import get_settings
from core.registry import get_retrieval_corpus
from scripts.accuracy_fixtures import (
    build_accuracy_fixtures,
    build_fresh_heldout_fixtures,
    build_heldout_fixtures,
    matches,
    matches_context,
)


def code_identity() -> str:
    files = sorted(p for folder in ("api", "core", "worker") for p in Path(folder).rglob("*.py"))
    files += [Path("scripts/accuracy_fixtures.py"), Path(__file__), Path("pyproject.toml"), Path("uv.lock")]
    return hashlib.sha256(
        json.dumps([(str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in files]).encode()
    ).hexdigest()


def save(path: Path, value) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, allow_nan=False))
    temp.replace(path)


async def run(trials=2, max_provider_rounds=3, held_out=False, fresh_held_out=False):
    settings = get_settings()
    if not settings.qdrant_collection.startswith("verification_"):
        raise ValueError("Generic live checks require an explicitly isolated verification_ collection")
    if fresh_held_out:
        use_case, folder = "synthetic-fresh-heldout", Path(".private/generic-fresh-heldout-fixtures")
        cases = build_fresh_heldout_fixtures(folder)
    elif held_out:
        use_case, folder = "synthetic-heldout", Path(".private/generic-heldout-fixtures")
        cases = build_heldout_fixtures(folder)
    else:
        use_case, folder = "synthetic-accuracy", Path(".private/generic-accuracy-fixtures")
        cases = build_accuracy_fixtures(folder)
    for path in sorted(folder.iterdir()):
        ingest_file(str(path), source_id=path.name, use_case=use_case)
    code = code_identity()
    configuration = {
        name: getattr(settings, name)
        for name in (
            "parser_provider",
            "nvidia_nim_model",
            "nvidia_generation_options",
            "nvidia_structured_output_method",
            "sentence_transformers_model",
            "retrieval_top_k",
            "chunk_size",
            "chunk_overlap",
            "hybrid_bm25_weight",
            "hybrid_dense_weight",
        )
    }
    corpus = sorted((d.metadata["chunk_id"], d.page_content) for d in get_retrieval_corpus(use_case))
    run_id = hashlib.sha256(
        json.dumps([code, configuration, corpus, [vars(c) for c in cases], trials], sort_keys=True).encode()
    ).hexdigest()
    directory = Path(".private/accuracy") / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    cooldown_spent = 0
    for provider_round in range(max_provider_rounds):
        provider_failed = False
        for index, case in enumerate(cases):
            for trial in range(trials):
                path = directory / f"{index}-{trial}.json"
                previous = json.loads(path.read_text()) if path.exists() else {}
                if previous.get("status") in {"answered", "grounding_failed", "format_failed"}:
                    continue
                row = {
                    "case": case.name,
                    "category": case.category,
                    "trial": trial + 1,
                    "question": case.question,
                    "expected": case.expected,
                    "expected_source_files": case.source_files,
                    "run_id": run_id,
                }
                call_started = time.monotonic()
                try:
                    result = await build_retrieval_chain(use_case=use_case).ainvoke_with_sources(
                        case.question
                    )
                    row.update(
                        status="answered",
                        answer=result["answer"],
                        sources=result["sources"],
                        correct=matches(case, result["answer"]),
                        expected_context_present=matches_context(case, result["sources"]),
                        expected_sources_present=set(case.source_files)
                        <= {s["source_id"] for s in result["sources"]},
                    )
                except (GroundingError, OutputParserException) as exc:
                    row.update(
                        status="grounding_failed" if isinstance(exc, GroundingError) else "format_failed",
                        correct=False,
                        failure_type=type(exc).__name__,
                        invalid_output=getattr(exc, "llm_output", None),
                        validation_details=getattr(exc, "validation_response", None),
                    )
                except Exception as exc:  # noqa: BLE001 -- separate infrastructure coverage from accuracy
                    row.update(
                        status="provider_or_service_failure", correct=None, failure_type=type(exc).__name__
                    )
                    provider_failed = True
                row["duration_seconds"] = time.monotonic() - call_started
                save(path, row)
                print(case.name, trial + 1, row["status"], "correct", row["correct"], flush=True)
                if provider_failed:
                    break
            if provider_failed:
                break
        if not provider_failed:
            break
        delay = await asyncio.to_thread(remaining_cooldown)
        if provider_round + 1 >= max_provider_rounds or not delay or cooldown_spent + delay > 300:
            break
        cooldown_spent += delay + 1
        print("Shared provider cooldown:", delay, "seconds", flush=True)
        for interval in range(0, delay + 1, 30):
            await asyncio.sleep(min(30, delay + 1 - interval))
    rows = [json.loads(p.read_text()) for p in sorted(directory.glob("*-*.json"))]
    unchanged = code == code_identity()
    summary = {
        "run_id": run_id,
        "configuration": configuration,
        "code_identity": code,
        "code_unchanged": unchanged,
        "expected_trials": len(cases) * trials,
        "answered_trials": sum(r["status"] == "answered" for r in rows),
        "correct_trials": sum(
            r.get("correct") is True
            and r.get("expected_sources_present")
            and r.get("expected_context_present")
            for r in rows
        ),
        "unjudged_trials": len(cases) * trials - sum(r.get("correct") is not None for r in rows),
        "duration_seconds": time.monotonic() - started,
        "cooldown_seconds": cooldown_spent,
    }
    summary["complete"] = unchanged and summary["correct_trials"] == summary["expected_trials"]
    save(directory / "summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--held-out", action="store_true")
    selection.add_argument("--fresh-held-out", action="store_true")
    parser.add_argument("--trials", type=int, choices=range(2, 6), default=2)
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live approval is required for hosted calls")
    result = asyncio.run(run(trials=args.trials, held_out=args.held_out, fresh_held_out=args.fresh_held_out))
    raise SystemExit(0 if result["complete"] else 1)
