"""The local dependency patch must be verified even when PyPI cannot audit it."""

import ast
import hashlib
import importlib.metadata
import importlib.util
import inspect
import json
import zipfile
from pathlib import Path

import pytest


def test_installed_evaluation_dependency_matches_reviewed_wheel():
    import ragas.cache
    import ragas.llms.base
    import ragas.metrics.collections.multi_modal_faithfulness.util
    import ragas.prompt.multi_modal_prompt

    root = Path(__file__).resolve().parents[1]
    provenance = json.loads((root / "vendor/ragas/provenance.json").read_text())
    wheel = root / "vendor/ragas" / f"ragas-{provenance['patched_version']}-py3-none-any.whl"
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() == provenance["patched_wheel_sha256"]
    assert importlib.metadata.version("ragas") == provenance["patched_version"]
    modules = [
        ragas.cache,
        ragas.llms.base,
        ragas.metrics.collections.multi_modal_faithfulness.util,
        ragas.prompt.multi_modal_prompt,
    ]
    with zipfile.ZipFile(wheel) as archive:
        for module in modules:
            path = module.__name__.replace(".", "/") + ".py"
            assert Path(module.__file__).read_bytes() == archive.read(path)
        assert any(name.endswith("/LICENSE") for name in archive.namelist())
    assert not any(r.lower().startswith("diskcache") for r in importlib.metadata.requires("ragas"))
    assert importlib.util.find_spec("diskcache") is None


def test_multimodal_context_cannot_read_files_or_fetch_urls(tmp_path, monkeypatch):
    import builtins

    from ragas.metrics.collections.multi_modal_faithfulness import util

    sentinel = tmp_path / "sentinel.png"
    sentinel.write_bytes(b"private synthetic sentinel")
    attempted = []

    def forbidden_io(*args, **kwargs):
        attempted.append(args)
        raise AssertionError("External resource loading attempted")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden_io)
        patch.setattr(util.requests, "get", forbidden_io)
        for target in (
            "http://127.0.0.1/internal.png",
            "http://169.254.169.254/latest/meta-data/",
            "https://example.invalid/redirect.png",
            str(sentinel),
        ):
            assert util.process_image_to_base64(target) is None
    assert attempted == []


def test_legacy_multimodal_resource_helpers_are_disabled():
    from ragas.prompt.multi_modal_prompt import ImageTextPromptValue

    for name in (
        "_try_process_allowed_url",
        "_download_validate_and_encode",
        "_try_process_local_file",
        "_is_safe_url_target",
    ):
        source = inspect.getsource(getattr(ImageTextPromptValue, name))
        import textwrap

        tree = ast.parse(textwrap.dedent(source))
        assert not any(isinstance(node, ast.Call) for node in ast.walk(tree))


def test_pickle_cache_is_unavailable_and_text_metrics_remain():
    from ragas.cache import DiskCacheBackend
    from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness

    with pytest.raises(RuntimeError, match="DiskCache is disabled"):
        DiskCacheBackend("untrusted-directory")
    assert {m.name for m in (faithfulness, answer_relevancy, context_precision, context_recall)} == {
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    }
