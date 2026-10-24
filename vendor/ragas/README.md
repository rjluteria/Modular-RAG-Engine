# Project-maintained RAGAS text-evaluation build

This wheel is RAGAS 0.4.3 with explicit source changes, not an upstream patched release. The Apache-2.0 license and upstream attribution are retained in its wheel metadata. The modified modules include notices identifying the project patch.

`provenance.json` pins the upstream wheel URL/SHA-256 and the resulting wheel hash. `scripts/build_ragas_text.py` verifies the upstream bytes before changing anything, rebuilds deterministically, and regenerates the wheel RECORD. Run `uv run python scripts/build_ragas_text.py --check` to verify that the committed wheel is exactly reproducible. CI requires this verification and the dependency security tests before accepting the dependency audit.

The changes disable URL and local-file image loading in both multimodal paths, disable the unused pickle-backed DiskCache backend, remove its dependency, and replace the removed optional Vertex import with an unavailable marker type. The required text metric implementations are unchanged from upstream 0.4.3. Persistent checkpoints in this project remain private JSON files.

Multimodal resource loading, DiskCache, and the optional Vertex integration are unsupported in this build. This build does not add a general RAGAS API. The evaluation runner remains restricted to configured text metrics and trusted local datasets.

PyPI auditing cannot assess this local wheel. A skipped lookup is not a clean audit: source reproducibility, installed-file integrity checks, negative file/network-access tests, and retained text-metric checks are mandatory evidence. Upstream advisories remain documented in SECURITY.md; other dependencies are still audited normally.
