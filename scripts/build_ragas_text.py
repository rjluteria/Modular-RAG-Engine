"""Rebuild a hash-pinned RAGAS wheel with unused unsafe I/O features disabled.

RAGAS is Apache-2.0 licensed; its license/attribution files remain in the wheel.
This is a project-maintained patch, not a claim of an upstream security release.
"""

import argparse
import ast
import base64
import csv
import hashlib
import io
import json
import tempfile
import urllib.request
import zipfile
from pathlib import Path

UPSTREAM_URL = "https://files.pythonhosted.org/packages/4d/e0/1fecd22c93d3ed66453cbbdefd05528331af4d33b2b76a370d751231912c/ragas-0.4.3-py3-none-any.whl"
UPSTREAM_SHA256 = "ef1d75f674c294e9a6e7d8e9ad261b6bf4697dad1c9cbd1a756ba7a6b4849a38"
VERSION = "0.4.3.post2"
PATCH_ID = "text-evaluation-no-resource-loading-no-diskcache-v1"


def replace_nodes(source, names):
    lines = source.splitlines(keepends=True)
    changes = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in changes} == set(names), "Upstream patch targets changed"
    for node in sorted(changes, key=lambda n: n.lineno, reverse=True):
        indent = " " * node.col_offset
        replacement = names[node.name]
        if replacement is None:
            replacement = f'def {node.name}(*args, **kwargs):\n    """Project security patch: external resources are disabled."""\n    return None\n'
        lines[node.lineno - 1 : node.end_lineno] = [
            indent + line + "\n" for line in replacement.rstrip().splitlines()
        ]
    return "# Modified by Modular RAG Engine: " + PATCH_ID + "\n" + "".join(lines)


def build(data: bytes, output: Path) -> str:
    if hashlib.sha256(data).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("Upstream RAGAS wheel hash mismatch")
    source = zipfile.ZipFile(io.BytesIO(data))
    entries = {info.filename: source.read(info) for info in source.infolist() if not info.is_dir()}
    new_util = "ragas/metrics/collections/multi_modal_faithfulness/util.py"
    entries[new_util] = replace_nodes(
        entries[new_util].decode(), {"_try_process_url": None, "_try_process_local_file": None}
    ).encode()
    prompt = "ragas/prompt/multi_modal_prompt.py"
    entries[prompt] = replace_nodes(
        entries[prompt].decode(),
        {
            "_try_process_allowed_url": None,
            "_download_validate_and_encode": None,
            "_try_process_local_file": None,
            "_is_safe_url_target": "def _is_safe_url_target(*args, **kwargs):\n    return False\n",
        },
    ).encode()
    cache = "ragas/cache.py"
    entries[cache] = replace_nodes(
        entries[cache].decode(),
        {
            "DiskCacheBackend": '''class DiskCacheBackend(CacheInterface):
    """Project patch: persistent pickle caching is not included in this build."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("DiskCache is disabled in this text-evaluation build")
    def get(self, key):
        raise RuntimeError("DiskCache is disabled")
    def set(self, key, value):
        raise RuntimeError("DiskCache is disabled")
    def has_key(self, key):
        raise RuntimeError("DiskCache is disabled")
'''
        },
    ).encode()
    llm_base = "ragas/llms/base.py"
    llm_source = entries[llm_base].decode()
    removed = "from langchain_community.chat_models.vertexai import ChatVertexAI"
    assert removed in llm_source
    llm_source = llm_source.replace(
        removed,
        'class ChatVertexAI:\n    """Optional Vertex integration is unavailable in this evaluation build."""\n',
    )
    entries[llm_base] = (
        "# Modified by Modular RAG Engine: optional Vertex import compatibility.\n" + llm_source
    ).encode()
    old_prefix, new_prefix = "ragas-0.4.3.dist-info/", f"ragas-{VERSION}.dist-info/"
    entries = {
        name.replace(old_prefix, new_prefix): content
        for name, content in entries.items()
        if not name.endswith("/RECORD")
    }
    meta_name = new_prefix + "METADATA"
    metadata = entries[meta_name].decode().replace("Version: 0.4.3\n", "Version: " + VERSION + "\n")
    # The only consumer of diskcache was removed above; installing it would restore needless risk.
    metadata = "\n".join(
        line for line in metadata.split("\n") if not line.lower().startswith("requires-dist: diskcache")
    )
    entries[meta_name] = metadata.encode()
    provenance = {
        "patch_id": PATCH_ID,
        "upstream_version": "0.4.3",
        "upstream_sha256": UPSTREAM_SHA256,
        "patched_version": VERSION,
        "modified_files": [new_util, prompt, cache, llm_base],
        "resolved_advisories": ["CVE-2026-6587", "CVE-2025-69872"],
        "limitations": [
            "No remote/local image loading",
            "No DiskCache backend",
            "Project-maintained dependency patch; not an upstream release",
        ],
    }
    entries["ragas/project_security_patch.json"] = json.dumps(provenance, indent=2).encode()
    records = io.StringIO(newline="")
    writer = csv.writer(records, lineterminator="\n")
    for name, content in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([new_prefix + "RECORD", "", ""])
    entries[new_prefix + "RECORD"] = records.getvalue().encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            wheel.writestr(info, content)
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    request = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "modular-rag-dependency-build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    output = Path("vendor/ragas") / f"ragas-{VERSION}-py3-none-any.whl"
    if args.check:
        with tempfile.TemporaryDirectory() as directory:
            rebuilt = Path(directory) / output.name
            digest = build(data, rebuilt)
        if digest != hashlib.sha256(output.read_bytes()).hexdigest():
            raise SystemExit("Vendored RAGAS wheel is not reproducible from the pinned source and patches")
        print("Verified reproducible evaluation dependency patch:", digest)
        raise SystemExit(0)
    digest = build(data, output)
    Path("vendor/ragas/provenance.json").write_text(
        json.dumps(
            {
                "upstream_url": UPSTREAM_URL,
                "upstream_sha256": UPSTREAM_SHA256,
                "patched_wheel_sha256": digest,
                "patch_id": PATCH_ID,
                "patched_version": VERSION,
            },
            indent=2,
        )
        + "\n"
    )
    print("Built hash-verified project-patched RAGAS:", digest)
