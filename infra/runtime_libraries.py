"""Collect only external libraries needed by the Python service runtime."""

import re
import shutil
import subprocess
from pathlib import Path

# These optional interpreter extensions are unrelated to the service and pull in
# interactive terminal, NIS, GUI, or mount/UUID libraries. UUID has a stdlib fallback.
for pattern in ("_curses*", "readline*", "_uuid*", "nis*", "_tkinter*", "_crypt*", "_gdbm*", "_dbm*"):
    for path in Path("/usr/local/lib/python3.12/lib-dynload").glob(pattern):
        path.unlink()
for path in (Path("/usr/local/bin/uv"), Path("/usr/local/bin/uvx")):
    path.unlink(missing_ok=True)
for pattern in (
    "pip",
    "pip-*",
    "setuptools",
    "setuptools-*",
    "pkg_resources",
    "_distutils_hack",
    "distutils-precedence.pth",
):
    for path in Path("/usr/local/lib/python3.12/site-packages").glob(pattern):
        shutil.rmtree(path) if path.is_dir() else path.unlink()

# distroless/cc supplies the system loader, C/C++ runtime, OpenSSL and zlib.
provided = re.compile(r"^(ld-linux.*|lib(c|m|dl|pthread|rt|resolv|gcc_s|stdc\+\+|ssl|crypto|z)\.so.*)$")
packages = set()
files = [Path("/usr/local/bin/python3.12")]
files += list(Path("/usr/local").rglob("*.so*")) + list(Path("/opt/venv").rglob("*.so*"))
for path in files:
    result = subprocess.run(["ldd", str(path)], capture_output=True, text=True, check=False)
    for library in re.findall(r"(?:=>\s+)?(/[^\s()]+)", result.stdout):
        source = Path(library)
        if str(source).startswith(("/usr/local/", "/opt/venv/")) or provided.match(source.name):
            continue
        for candidate in (source, source.resolve()):
            owner = subprocess.run(["dpkg-query", "-S", str(candidate)], capture_output=True, text=True, check=False)
            for line in owner.stdout.splitlines():
                if ": /" in line:
                    packages.add(line.split(": /", 1)[0])
        destination = Path("/runtime-libs") / str(source.resolve().parent / source.name).lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
for path in ("app/tmp/uploads", "home/nonroot/.cache/huggingface"):
    (Path("/runtime-dirs") / path).mkdir(parents=True, exist_ok=True)

# Preserve package identity for vulnerability scanners; copied libraries must not
# become invisible just because the runtime has no package manager.
status = Path("/runtime-libs/var/lib/dpkg/status.d/python-runtime-libraries")
status.parent.mkdir(parents=True, exist_ok=True)
status.write_text("\n\n".join(subprocess.check_output(["dpkg-query", "-s", package], text=True).strip() for package in sorted(packages)) + "\n")

# Retain the distribution notices for the copied native libraries.
for package in sorted(packages):
    name = package.split(":", 1)[0]
    source = Path("/usr/share/doc") / name / "copyright"
    if source.is_file():
        destination = Path("/runtime-libs/usr/share/doc") / name / "copyright"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
