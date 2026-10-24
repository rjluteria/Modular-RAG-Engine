# Testing and evaluation

[Back to the guide](../README.md)

Run commands from the repository root. Python 3.12 and uv are required for local development.

```sh
uv sync --frozen --extra dev --group runtime --python 3.12
uv run --group runtime ruff check .
uv run --group runtime mypy api core worker eval scripts/setup_search_index.py scripts/check_image_audit.py
uv run --group runtime pytest -q
```

Service tests are explicitly enabled with `RAG_SERVICE_TESTS=1` and isolated Redis/Qdrant URLs. They create disposable collections; never point them at an existing application database. CI runs synthetic tests without hosted-model credentials. Live evaluation is opt-in and requires an ingested isolated corpus and a local JSONL dataset with `question`, `reference_answer`, and per-row `use_case` fields.

```sh
EVAL_DATASET_PATH=/path/to/dataset.jsonl uv run --group runtime python -m eval.eval --report-only
```

The default retrieval depth is 8. Evaluate retrieval changes against source coverage and answer grounding before changing it; a larger context is not automatically better. Detailed scoring remains local. RAGAS retains faithfulness, answer relevancy, context precision, and context recall. Checkpoints include dataset, corpus metadata/text, code, model, and configuration identity and are saved only under ignored `.private/evaluation`. Each successful metric is saved separately so retries do not repeat generation or other valid metrics. Provider retries have bounded rounds and a total cooldown window. Shared request pacing queues concurrent consumers with a bounded wait and releases reservations on cancellation. Answer relevancy retains its native cosine range of −1 to 1; faithfulness, context precision and context recall use 0 to 1. Negative relevancy is not converted to zero. Cosine scores within eight floating-point ulps of either endpoint are corrected to that endpoint; raw metric values are retained in private checkpoints. Larger excursions and NaN remain invalid. See the [RAGAS definition](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/answer_relevance/). Non-finite or out-of-domain scores remain missing, with their raw numeric representation retained in private failure history; summaries report judge coverage. `--generate-only` captures answers and their actual contexts before scoring; a later run resumes those same checkpoints. Generation coverage is separate from metric coverage, and this mode cannot establish a baseline. A missing baseline is never a pass. After reviewing a complete run, `--write-baseline` explicitly establishes a private baseline. Later regression comparisons require the same dataset, corpus, models, and settings. The relative-drop check uses the absolute baseline magnitude, including negative relevancy baselines; any decrease from a zero baseline fails. Live evaluation is not part of ordinary credential-free CI. See [SECURITY.md](../SECURITY.md) and [vendor/ragas/README.md](../vendor/ragas/README.md) for the reproducible project-maintained RAGAS security patch and its mandatory verification; this is not an upstream patched release.

Evaluation fixtures must preserve document ownership independently of the question’s requested partition. A negative test that refers to a forbidden source must not upload that source into the tested partition. Verify corpus ownership and returned chunk provenance before scoring. Keep factual-reference metrics separate from authorization, scope-override and refusal checks; a policy expectation is not a source-supported factual answer. Report every task category and blocked case explicitly. LLM judge scores may vary on identical inputs; retain failed threshold checks and investigate their inputs instead of treating model disagreement as proof of a retrieval defect.


## Isolated service tests

Set `RAG_SERVICE_TESTS=1`, `REDIS_URL`, `QDRANT_URL` and a disposable `QDRANT_COLLECTION` explicitly. Use dedicated test services and a Redis database with no application state. Tests can clear state; never run them against production data. The normal Compose stack does not expose Redis/Qdrant host ports; host-side service tests require separately configured, loopback-only test services.

## Evaluate the running API

Set `EVAL_API_URL` and `EVAL_API_SOURCE_ID` along with the dataset and matching storage/model configuration. The source identity must attest the image and quality settings being evaluated. The runner uses the configured user token and records the actual source-first SSE contexts. Without an API URL, evaluation runs the local chain. Keep credentials and detailed results local; hosted evaluation sends the supplied questions, references and context to the configured judge.

## Run isolated storage tests

With Docker running, use a dedicated Compose project and disposable volumes. The extra file exposes backend ports on loopback only. Ensure ports 16379 and 16333 are available and do not reuse an application Redis database. Configure `.env.local` as described in [Configuration](configuration.md).

```sh
docker compose -p rag-tests -f infra/docker-compose.yml -f infra/compose.test.yml up --build -d redis qdrant
RAG_SERVICE_TESTS=1 REDIS_URL=redis://localhost:16379/15 QDRANT_URL=http://localhost:16333 \
  uv run --group runtime pytest -q
```

Synthetic embedding and model doubles let these tests exercise parsing, revision visibility, retrieval, citations, authentication, concurrency, and failure behavior without hosted inference. Ordinary tests skip service cases unless explicitly enabled. Individual tests generate their own vector collections. Use only disposable services because tests modify their state.

## Developer evaluation workflow

The [example JSONL](../eval/golden_set.example.jsonl) describes the input format. Create questions and source-supported reference answers for your own corpus, with the correct `use_case` and source ownership. Configure generation, judge, and embedding roles separately. Then run `--generate-only` to capture answers and contexts; run the same command without that flag to score the captured work. `--report-only` reports evaluation metrics without requiring a baseline comparison; it still runs the evaluation/resume path and can execute provider calls for missing work. It is not an offline checkpoint-viewing command.

```sh
EVAL_DATASET_PATH=/absolute/path/to/your/questions.jsonl \
  uv run --group runtime python -m eval.eval --generate-only
EVAL_DATASET_PATH=/absolute/path/to/your/questions.jsonl \
  uv run --group runtime python -m eval.eval
```

Generation and scoring execute your configured providers. Review data handling and provider access before running them. Do not use unrelated production data for tests. Report sample sizes, missing metrics, refusal/scope cases, latency distributions, and model/configuration identity separately; do not interpret a small successful sample as general accuracy or throughput.

## Image and dependency checks

```sh
uv run --group runtime python scripts/build_ragas_text.py --check
uv run --group runtime pytest -q tests/test_evaluation_dependency_security.py
uv run --group runtime pip-audit
docker compose -f infra/docker-compose.yml build
```

CI verifies the maintained wheel, compiles all root Bicep templates, runs the isolated service suite, checks five-format parsing in a network-disabled image, scans Git history for secrets, and scans API, worker, Redis, and Qdrant images. See [Security](../SECURITY.md) for scan gates and limitations. Keep your detailed outputs outside the distributable repository.
