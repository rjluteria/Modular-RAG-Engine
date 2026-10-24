import pytest

from eval.eval import METRICS, aggregate_metric_rows, check_regression


def test_failed_judge_coverage_cannot_pass_on_good_average():
    valid = dict.fromkeys(METRICS, 1.0)
    failed = dict.fromkeys(METRICS, float("nan"))
    report = aggregate_metric_rows([valid, failed], 2)
    assert not report["complete"]
    assert report["failed_rows"] == 1
    assert report["coverage"]["faithfulness"] == {"successful": 1, "failed": 1}
    assert report["scores"]["faithfulness"] == 1.0


def test_missing_baseline_never_becomes_an_automatic_pass(tmp_path, monkeypatch):
    path = tmp_path / "missing.json"
    monkeypatch.setattr("eval.eval.BASELINE_SCORES_PATH", path)
    assert not check_regression({"complete": True}, 0.05)
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_score", [float("nan"), 1.25])
async def test_resume_reuses_generation_and_only_successful_metrics(tmp_path, monkeypatch, invalid_score):
    import json
    from collections import Counter

    import pandas as pd
    import ragas
    from langchain_core.documents import Document

    import eval.eval as evaluation

    data = tmp_path / "synthetic.jsonl"
    data.write_text(
        json.dumps({"question": "Synthetic limit?", "reference_answer": "42", "use_case": "synthetic"}) + "\n"
    )
    monkeypatch.setattr(evaluation, "GOLDEN_SET_PATH", data)
    monkeypatch.chdir(tmp_path)
    calls = Counter()

    class Chain:
        async def ainvoke_with_sources(self, question):
            calls["generation"] += 1
            return {
                "answer": "42 [row]",
                "sources": [
                    {
                        "chunk_id": "row",
                        "page_content": "The limit is 42.",
                        "generation_context": "Captured context supplied by the server: limit 42",
                    }
                ],
            }

    async def score(dataset, *, metrics, **kwargs):
        name = metrics[0].name
        calls[name] += 1
        assert dataset[0]["contexts"] == ["Captured context supplied by the server: limit 42"]
        value = invalid_score if name == "faithfulness" and calls[name] == 1 else 1.0
        if name == "answer_relevancy":
            value = -0.25

        class Result:
            def to_pandas(self):
                return pd.DataFrame([{name: value}])

        return Result()

    monkeypatch.setattr("core.chains.retrieval_chain.build_retrieval_chain", lambda **kwargs: Chain())
    monkeypatch.setattr("core.registry.get_judge_llm", lambda: object())
    monkeypatch.setattr("core.registry.get_embedder", lambda: object())
    monkeypatch.setattr(
        "core.registry.get_retrieval_corpus",
        lambda: [
            Document(page_content="The limit is 42.", metadata={"chunk_id": "row", "use_case": "synthetic"})
        ],
    )
    monkeypatch.setattr("core.adapters.llms.provider_gate.remaining_cooldown", lambda: 0)
    monkeypatch.setattr(ragas, "aevaluate", score)
    generated = await evaluation.run_eval(max_rounds=1, generate_only=True)
    assert generated["generation_complete"] and generated["generated_rows"] == 1
    assert not generated["complete"] and generated["coverage"]["faithfulness"]["successful"] == 0
    assert calls["generation"] == 1 and not any(calls[name] for name in METRICS)
    first = await evaluation.run_eval(max_rounds=1)
    assert not first["complete"] and first["failed_rows"] == 1
    second = await evaluation.run_eval(max_rounds=1)
    assert second["complete"] and second["failed_rows"] == 0
    assert second["failure_attempts"] == 1
    assert second["scores"]["answer_relevancy"] == -0.25
    assert calls["generation"] == 1 and calls["faithfulness"] == 2
    assert all(calls[name] == 1 for name in ("answer_relevancy", "context_precision", "context_recall"))
    checkpoint = next((tmp_path / ".private/evaluation").glob("*/0.json"))
    assert json.loads(checkpoint.read_text())["failures"][0]["raw_value"] == repr(invalid_score)


def test_negative_relevancy_is_valid_without_changing_its_value():
    row = dict.fromkeys(METRICS, 1.0)
    row["answer_relevancy"] = -0.25
    report = aggregate_metric_rows([row], 1)
    assert report["complete"] and report["failed_rows"] == 0
    assert report["scores"]["answer_relevancy"] == -0.25


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("faithfulness", -0.1),
        ("context_precision", -0.1),
        ("context_recall", -0.1),
        ("answer_relevancy", -1.01),
        ("answer_relevancy", 1.01),
    ],
)
def test_metric_domains_do_not_accept_unrelated_out_of_range_scores(metric, value):
    row = dict.fromkeys(METRICS, 1.0)
    row[metric] = value
    report = aggregate_metric_rows([row], 1)
    assert not report["complete"] and report["failed_rows"] == 1
    assert report["coverage"][metric] == {"successful": 0, "failed": 1}
    assert report["scores"][metric] is None


@pytest.mark.parametrize(
    ("base", "value", "passed"),
    [
        (-0.4, -0.45, False),
        (-0.4, -0.41, True),
        (-0.4, -0.2, True),
        (0.0, -0.001, False),
        (0.0, 0.0, True),
        (0.8, 0.75, False),
        (0.8, 0.77, True),
    ],
)
def test_regression_handles_negative_and_zero_relevancy_baselines(tmp_path, monkeypatch, base, value, passed):
    import json

    baseline = {"complete": True, "comparison_id": "synthetic", "scores": dict.fromkeys(METRICS, 1.0)}
    baseline["scores"]["answer_relevancy"] = base
    report = {**baseline, "scores": {**baseline["scores"], "answer_relevancy": value}}
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(baseline))
    monkeypatch.setattr("eval.eval.BASELINE_SCORES_PATH", path)
    assert check_regression(report, 0.05) is passed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 410])
async def test_permanent_judge_failure_stops_before_other_metrics_or_rows(tmp_path, monkeypatch, status):
    import json

    import ragas
    from langchain_core.documents import Document

    import eval.eval as evaluation

    data = tmp_path / "synthetic.jsonl"
    data.write_text(
        "".join(
            json.dumps({"question": f"Limit {i}?", "reference_answer": "42", "use_case": "synthetic"}) + "\n"
            for i in range(2)
        )
    )
    monkeypatch.setattr(evaluation, "GOLDEN_SET_PATH", data)
    monkeypatch.chdir(tmp_path)
    calls = []

    class Chain:
        async def ainvoke_with_sources(self, question):
            return {"answer": "42", "sources": [{"chunk_id": "row", "page_content": "Limit 42"}]}

    class Failure(RuntimeError):
        status_code = status

    async def score(*args, **kwargs):
        assert kwargs["raise_exceptions"] is True
        calls.append(1)
        raise Failure("Sanitized synthetic provider failure")

    monkeypatch.setattr("core.chains.retrieval_chain.build_retrieval_chain", lambda **kwargs: Chain())
    monkeypatch.setattr("core.registry.get_judge_llm", lambda: object())
    monkeypatch.setattr("core.registry.get_embedder", lambda: object())
    monkeypatch.setattr(
        "core.registry.get_retrieval_corpus",
        lambda: [Document(page_content="Limit 42", metadata={"use_case": "synthetic"})],
    )
    monkeypatch.setattr("core.adapters.llms.provider_gate.remaining_cooldown", lambda: 0)
    monkeypatch.setattr(ragas, "aevaluate", score)
    report = await evaluation.run_eval()
    assert not report["complete"] and report["failed_rows"] == 2
    assert report["blocked_reason"] in {"provider_credentials", "model_unavailable"}
    assert len(calls) == 1


def test_generation_capture_cannot_establish_a_scoring_baseline(tmp_path, monkeypatch):
    import sys

    import eval.eval as evaluation

    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(evaluation, "BASELINE_SCORES_PATH", baseline)
    monkeypatch.setattr(sys, "argv", ["evaluation", "--generate-only", "--write-baseline"])
    with pytest.raises(SystemExit) as error:
        evaluation.main()
    assert error.value.code == 2 and not baseline.exists()


def test_evaluation_logs_keep_error_categories_without_private_payloads():
    import logging

    from eval.eval import _safe_log_record

    class Failure(RuntimeError):
        status_code = 401

    error = Failure("synthetic confidential response")
    record = _safe_log_record(
        "private-label",
        logging.ERROR,
        "evaluation.py",
        1,
        "source: %s",
        ("synthetic confidential source",),
        (Failure, error, None),
    )
    assert "Failure" in record.getMessage() and "401" in record.getMessage()
    assert "confidential" not in record.getMessage() and record.name == "evaluation.evaluation"
    assert not record.exc_info and not record.args


def test_cli_generation_only_reaches_capture_phase(monkeypatch):
    import sys

    import eval.eval as evaluation

    calls = []

    async def run(*args, **kwargs):
        calls.append(kwargs)
        return {
            "scores": {},
            "coverage": {},
            "complete": False,
            "generated_rows": 1,
            "generation_complete": True,
            "failure_attempts": 0,
            "duration_seconds": 0,
        }

    monkeypatch.setattr(sys, "argv", ["eval", "--generate-only"])
    monkeypatch.setattr(evaluation, "run_eval", run)
    monkeypatch.setattr(evaluation, "_configure_private_evaluation", lambda: None)
    with pytest.raises(SystemExit) as result:
        evaluation.main()
    assert result.value.code == 0
    assert calls[0]["generate_only"] is True


def test_failure_history_retains_safe_api_codes_without_raw_payloads():
    from eval.api_query import EvaluationAPIError
    from eval.eval import _record_failure

    evidence = {}
    _record_failure(evidence, EvaluationAPIError("answer_not_grounded"), "generation")
    assert evidence["failures"] == [
        {"phase": "generation", "type": "EvaluationAPIError", "code": "answer_not_grounded"}
    ]

    class ProviderFailure(Exception):
        status_code = 401

    _record_failure(evidence, ProviderFailure("synthetic sensitive upstream payload"), "faithfulness")
    assert len(evidence["failures"]) == 2
    assert evidence["failures"][1]["code"] == "provider_credentials"
    assert "sensitive" not in str(evidence)


@pytest.mark.parametrize(
    "value, expected",
    [(1.0000000000000002, 1.0), (-1.0000000000000002, -1.0), (-0.25, -0.25), (1.000000001, 1.000000001)],
)
def test_cosine_roundoff_correction_is_bounded_and_metric_specific(value, expected):
    from eval.eval import _normalize_metric_score

    assert _normalize_metric_score(value, "answer_relevancy") == expected
    assert _normalize_metric_score(value, "faithfulness") == value


def test_cosine_roundoff_does_not_turn_nan_into_a_score():
    import math

    from eval.eval import _normalize_metric_score

    assert math.isnan(_normalize_metric_score(float("nan"), "answer_relevancy"))
