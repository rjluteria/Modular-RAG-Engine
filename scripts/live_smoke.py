"""Opt-in hosted evaluation using only generic synthetic fixtures."""
import asyncio
import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    private = Path(".private")
    private.mkdir(exist_ok=True)
    dataset = private / "synthetic-live.jsonl"
    rows = [
        {"question": "What is the shipping limit?", "reference_answer": "The shipping limit is 42 units.", "use_case": "synthetic-live"},
        {"question": "What is the launch code?", "reference_answer": "The launch code is amber.", "use_case": "synthetic-live"},
    ]
    dataset.write_text("".join(json.dumps(row) + "\n" for row in rows))
    os.environ["EVAL_DATASET_PATH"] = str(dataset)
    from core.chains.ingestion_chain import ingest_file
    from eval.eval import run_eval
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "guide.txt"
        source.write_text("The shipping limit is 42 units. The launch code is amber.")
        ingest_file(str(source), source_id="synthetic-live-guide", use_case="synthetic-live")
    report = asyncio.run(run_eval())
    print(json.dumps({key: report[key] for key in ("scores", "coverage", "complete", "duration_seconds")}, indent=2))
    raise SystemExit(0 if report["complete"] else 1)


if __name__ == "__main__":
    main()
