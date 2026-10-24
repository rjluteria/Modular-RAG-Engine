"""Explicit opt-in RAGAS evaluation. Detailed evidence stays local and ignored."""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
GOLDEN_SET_PATH = Path(os.getenv("EVAL_DATASET_PATH", "eval/golden_set.jsonl"))
BASELINE_SCORES_PATH = Path(os.getenv("EVAL_BASELINE_PATH", ".private/evaluation-baseline.json"))


def load_golden_set(use_case: str | None = None) -> list[dict]:
    rows = [json.loads(line) for line in GOLDEN_SET_PATH.read_text().splitlines() if line.strip()]
    rows = [row for row in rows if use_case is None or row["use_case"] == use_case]
    if not rows or any(
        not all(row.get(key) for key in ("question", "reference_answer", "use_case")) for row in rows
    ):
        raise ValueError("Evaluation requires valid question, reference_answer and use_case fields")
    return rows


def _valid_score(value: Any, metric: str) -> bool:
    lower = -1 if metric == "answer_relevancy" else 0
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and lower <= value <= 1
    )


def _normalize_metric_score(value: float, metric: str) -> float:
    # Cosine arithmetic can overshoot its mathematical bounds by a few ulps.
    # Restrict correction to this metric and eight ulps at either endpoint.
    if metric == "answer_relevancy" and math.isfinite(value):
        tolerance = 8 * math.ulp(1.0)
        if 1 < value <= 1 + tolerance:
            return 1.0
        if -1 - tolerance <= value < -1:
            return -1.0
    return value


def aggregate_metric_rows(rows: list[dict], expected: int) -> dict:
    scores = {}
    coverage = {}
    for name in METRICS:
        valid = [float(row[name]) for row in rows if _valid_score(row.get(name), name)]
        coverage[name] = {"successful": len(valid), "failed": expected - len(valid)}
        scores[name] = sum(valid) / len(valid) if valid else None
    successful_rows = sum(all(_valid_score(row.get(name), name) for name in METRICS) for row in rows)
    return {
        "failed_rows": expected - successful_rows,
        "scores": scores,
        "coverage": coverage,
        "complete": expected > 0 and all(item["failed"] == 0 for item in coverage.values()),
    }


def _permanent_failure(error: Exception) -> str | None:
    from core.adapters.llms.errors import public_model_error

    if getattr(error, "permanent", False):
        return getattr(error, "code", type(error).__name__)
    code = public_model_error(error)["code"]
    return code if code in {"provider_configuration", "provider_credentials", "model_unavailable"} else None


def _record_failure(evidence: dict, error: Exception, phase: str) -> None:
    from core.adapters.llms.errors import public_model_error
    from eval.api_query import EvaluationAPIError

    code = error.code if isinstance(error, EvaluationAPIError) else public_model_error(error)["code"]
    evidence["failure_type"] = type(error).__name__
    evidence.setdefault("failures", []).append({"phase": phase, "type": type(error).__name__, "code": code})


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False))
    temporary.replace(path)


async def run_eval(
    use_case: str | None = None,
    *,
    max_rounds: int = 3,
    max_cooldown_seconds: int = 300,
    generate_only: bool = False,
) -> dict:
    os.environ["RAGAS_DO_NOT_TRACK"] = "true"
    from datasets import Dataset
    from ragas import aevaluate
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
    from ragas.run_config import RunConfig

    from core.chains.grounding import format_context
    from core.chains.retrieval_chain import build_retrieval_chain
    from core.config import get_settings
    from core.registry import get_embedder, get_judge_llm, get_retrieval_corpus

    started = time.monotonic()
    api_url = os.getenv("EVAL_API_URL", "")
    api_identity = os.getenv("EVAL_API_SOURCE_ID", "")
    if api_url and not api_identity:
        raise ValueError("API evaluation requires a verified EVAL_API_SOURCE_ID")
    settings = get_settings()
    if settings.llm_provider == "azure_openai" and not all((
        settings.azure_generation_model_identity, settings.azure_judge_model_identity,
        settings.azure_embedding_model_identity,
    )):
        raise ValueError("Azure evaluation requires explicit model/version identities")
    rows = load_golden_set(use_case)
    configuration = {
        key: getattr(settings, key)
        for key in (
            "parser_provider",
            "embedder_provider",
            "vector_store_provider",
            "azure_openai_endpoint",
            "azure_openai_chat_deployment",
            "azure_openai_judge_deployment",
            "azure_openai_embedding_deployment",
            "azure_embedding_dimensions",
            "azure_generation_model_identity",
            "azure_judge_model_identity",
            "azure_embedding_model_identity",
            "max_model_output_tokens",
            "llm_provider",
            "nvidia_nim_model",
            "nvidia_judge_model",
            "nvidia_generation_options",
            "nvidia_structured_output_method",
            "nvidia_judge_options",
            "sentence_transformers_model",
            "retrieval_top_k",
            "chunk_size",
            "chunk_overlap",
            "hybrid_bm25_weight",
            "hybrid_dense_weight",
            "reranker_enabled",
        )
    }
    evaluated_use_cases = {row["use_case"] for row in rows}
    corpus = sorted(
        json.dumps({"text": doc.page_content, "metadata": doc.metadata}, sort_keys=True)
        for doc in get_retrieval_corpus()
        if doc.metadata.get("use_case") in evaluated_use_cases
    )
    configuration["execution"] = "api" if api_url else "local"
    comparison_id = _digest([rows, configuration, corpus])
    files = sorted(
        path
        for directory in ("core", "api", "worker", "eval")
        for path in (PROJECT_ROOT / directory).rglob("*.py")
    )
    code_identity = _digest(
        [(str(path.relative_to(PROJECT_ROOT)), path.read_text()) for path in files]
        + [(name, (PROJECT_ROOT / name).read_text()) for name in ("pyproject.toml", "uv.lock")]
    )
    run_id = _digest([comparison_id, code_identity, api_identity])
    directory = Path(".private/evaluation") / run_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    judge = None if generate_only else get_judge_llm()
    embeddings = None if generate_only else get_embedder()
    if not generate_only and (judge is None or embeddings is None):
        raise ValueError("Evaluation requires explicit judge and embedding clients")
    metrics = [faithfulness, answer_relevancy, context_precision, context_recall]
    from core.adapters.llms.provider_gate import remaining_cooldown

    cooldown_spent = 0
    permanent_error = None
    rounds = 0
    for round_index in range(max_rounds):
        rounds = round_index + 1
        rate_limited = False
        for index, row in enumerate(rows):
            path = directory / (str(index) + ".json")
            evidence = json.loads(path.read_text()) if path.exists() else {}
            if evidence.get("complete"):
                continue
            row_started = time.monotonic()
            try:
                if "answer" not in evidence:
                    if api_url:
                        from eval.api_query import query_api

                        result = await query_api(
                            api_url, settings.api_user_token, row["question"], row["use_case"]
                        )
                    else:
                        result = await build_retrieval_chain(use_case=row["use_case"]).ainvoke_with_sources(
                            row["question"]
                        )
                    evidence = {
                        "question": row["question"],
                        "use_case": row["use_case"],
                        "reference": row["reference_answer"],
                        "answer": result["answer"],
                        "sources": result["sources"],
                        "run_id": run_id,
                        "failures": evidence.get("failures", []),
                    }
                    _save(path, evidence)
                if generate_only:
                    evidence["duration_seconds"] = (
                        evidence.get("duration_seconds", 0) + time.monotonic() - row_started
                    )
                    _save(path, evidence)
                    print(f"Generated answers saved: {index + 1}/{len(rows)}", flush=True)
                    continue
                dataset = Dataset.from_list(
                    [
                        {
                            "question": row["question"],
                            "answer": evidence["answer"],
                            "contexts": [
                                source["generation_context"]
                                if "generation_context" in source
                                else format_context(source["page_content"], source.get("chunk_id"))
                                for source in evidence["sources"]
                            ],
                            "reference": row["reference_answer"],
                        }
                    ]
                )
                scores = evidence.setdefault("metrics", {})
                for metric, name in zip(metrics, METRICS, strict=True):
                    if _valid_score(scores.get(name), name):
                        continue
                    try:
                        evaluated: Any = await aevaluate(
                            dataset,
                            metrics=[metric],
                            llm=judge,
                            embeddings=embeddings,
                            run_config=RunConfig(max_workers=1, max_retries=0, timeout=900),
                            raise_exceptions=True,
                        )
                        value = float(evaluated.to_pandas().iloc[0][name])
                        normalized = _normalize_metric_score(value, name)
                        evidence.setdefault("raw_metric_values", {})[name] = repr(value)
                        scores[name] = normalized if _valid_score(normalized, name) else None
                        if scores[name] is None:
                            evidence.setdefault("failures", []).append(
                                {
                                    "phase": name,
                                    "type": "InvalidMetricScore",
                                    "code": "invalid_metric_score",
                                    "raw_value": repr(value),
                                }
                            )
                    except Exception as exc:  # noqa: BLE001 -- preserve failed row evidence  # noqa: BLE001 -- preserve private per-metric failure coverage
                        scores[name] = None
                        _record_failure(evidence, exc, name)
                        permanent_error = _permanent_failure(exc)
                    evidence["complete"] = aggregate_metric_rows([scores], 1)["complete"]
                    evidence["duration_seconds"] = (
                        evidence.get("duration_seconds", 0) + time.monotonic() - row_started
                    )
                    row_started = time.monotonic()
                    _save(path, evidence)  # Successful metrics survive interruption independently.
                    if permanent_error:
                        break
                    if await asyncio.to_thread(remaining_cooldown) > 0:
                        rate_limited = True
                        break
            except Exception as exc:  # noqa: BLE001 -- preserve failed row evidence
                _record_failure(evidence, exc, "generation" if "answer" not in evidence else "scoring")
                evidence.update(complete=False, run_id=run_id)
                rate_limited = await asyncio.to_thread(remaining_cooldown) > 0
                permanent_error = _permanent_failure(exc)
            if evidence.get("complete"):
                evidence.pop("failure_type", None)
            _save(path, evidence)
            print(f"Evaluation progress: {index + 1}/{len(rows)} (round {rounds})", flush=True)
            if rate_limited or permanent_error:
                break
        saved = [json.loads(path.read_text()) for path in directory.glob("*.json") if path.stem.isdigit()]
        generated = sum(isinstance(row.get("answer"), str) and bool(row["answer"].strip()) for row in saved)
        if (
            permanent_error
            or (generate_only and generated == len(rows))
            or sum(bool(row.get("complete")) for row in saved) == len(rows)
        ):
            break
        cooldown = await asyncio.to_thread(remaining_cooldown)
        if round_index + 1 >= max_rounds or cooldown_spent + cooldown > max_cooldown_seconds:
            break
        if cooldown:
            print(
                f"Hosted provider cooldown: {cooldown} seconds; no new calls during this interval", flush=True
            )
            cooldown_spent += cooldown + 1
            for interval in range(0, cooldown + 1, 30):
                await asyncio.sleep(min(30, cooldown + 1 - interval))
    evidence_rows = [
        json.loads((directory / f"{i}.json").read_text()) if (directory / f"{i}.json").exists() else {}
        for i in range(len(rows))
    ]
    metric_rows = [row.get("metrics", {}) for row in evidence_rows]
    failures = sum(bool(row.get("failure_type")) and not row.get("complete") for row in evidence_rows)
    summary = aggregate_metric_rows(metric_rows, len(rows))
    summary.update(
        comparison_id=comparison_id,
        run_id=run_id,
        configuration=configuration,
        duration_seconds=time.monotonic() - started,
        exception_rows=failures,
        failure_attempts=sum(len(row.get("failures", [])) for row in evidence_rows),
        generated_rows=sum(
            isinstance(row.get("answer"), str) and bool(row["answer"].strip()) for row in evidence_rows
        ),
        generation_complete=all(
            isinstance(row.get("answer"), str) and bool(row["answer"].strip()) for row in evidence_rows
        ),
        blocked_reason=permanent_error,
        api_source_identity=api_identity or None,
        rounds=rounds,
        cooldown_seconds=cooldown_spent,
    )
    attempts_path = directory / "attempts.json"
    attempts = json.loads(attempts_path.read_text()).get("attempts", []) if attempts_path.exists() else []
    attempts.append(
        {
            "duration_seconds": summary["duration_seconds"],
            "complete": summary["complete"],
            "failed_rows": summary["failed_rows"],
            "rounds": rounds,
            "cooldown_seconds": cooldown_spent,
        }
    )
    _save(attempts_path, {"attempts": attempts})
    summary["cumulative_duration_seconds"] = sum(attempt["duration_seconds"] for attempt in attempts)
    _save(directory / "summary.json", summary)
    return summary


def check_regression(report: dict, threshold: float) -> bool:
    if not report.get("complete") or not BASELINE_SCORES_PATH.exists():
        return False
    baseline = json.loads(BASELINE_SCORES_PATH.read_text())
    if not baseline.get("complete") or baseline.get("comparison_id") != report.get("comparison_id"):
        return False
    for name in METRICS:
        value, base = report["scores"].get(name), baseline["scores"].get(name)
        if not all(_valid_score(score, name) for score in (value, base)):
            return False
        if value < base and (base == 0 or (base - value) / abs(base) > threshold):
            return False
    return True


def _safe_log_record(*args, **kwargs):
    record = logging.LogRecord(*args, **kwargs)
    error = record.exc_info[1] if record.exc_info else None
    status = getattr(error, "status_code", None)
    status = status if isinstance(status, int) else None
    record.name = "evaluation." + record.module
    record.msg = f"Dependency event ({type(error).__name__ if error else 'no exception'}; status {status})"
    record.args = ()
    record.exc_info = record.exc_text = record.stack_info = None
    return record


def _configure_private_evaluation():
    from langchain_core.globals import set_debug, set_verbose

    for key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ[key] = "false"
    set_debug(False)
    set_verbose(False)
    logging.setLogRecordFactory(_safe_log_record)
    logging.captureWarnings(True)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s", force=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-case")
    parser.add_argument("--threshold", type=float, default=0.05)
    parser.add_argument("--report-only", action="store_true")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument(
        "--generate-only",
        action="store_true",
        help="Save answers and actual contexts without scoring; never establishes a baseline",
    )
    parser.add_argument("--max-rounds", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--max-cooldown-seconds", type=int, default=300)
    args = parser.parse_args()
    if args.generate_only and args.write_baseline:
        parser.error("Generation-only capture cannot establish an evaluation baseline")
    _configure_private_evaluation()
    report = asyncio.run(
        run_eval(
            args.use_case,
            max_rounds=args.max_rounds,
            max_cooldown_seconds=args.max_cooldown_seconds,
            generate_only=args.generate_only,
        )
    )
    if args.write_baseline and report["complete"]:
        BASELINE_SCORES_PATH.parent.mkdir(parents=True, exist_ok=True)
        _save(BASELINE_SCORES_PATH, report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "scores",
                    "coverage",
                    "complete",
                    "generated_rows",
                    "generation_complete",
                    "failure_attempts",
                    "duration_seconds",
                )
            },
            indent=2,
        )
    )
    passed = (
        report["generation_complete"]
        if args.generate_only
        else (
            report["complete"]
            if args.report_only or args.write_baseline
            else check_regression(report, args.threshold)
        )
    )
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
