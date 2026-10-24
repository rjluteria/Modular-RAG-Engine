"""Validate bounded uploads before enqueueing parser work."""

import codecs
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def validate_document(path: Path) -> None:
    suffix = path.suffix.lower()
    with path.open("rb") as stream:
        prefix = stream.read(8192)
    if suffix == ".pdf":
        if not prefix.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF document")
    elif suffix == ".docx":
        try:
            with ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 1000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                    raise ValueError("Document archive exceeds expanded size limit")
                if "word/document.xml" not in archive.namelist() or any(
                    item.flag_bits & 1 for item in entries
                ):
                    raise ValueError("Invalid or encrypted DOCX document")
        except BadZipFile as exc:
            raise ValueError("Invalid DOCX document") from exc
    elif suffix == ".csv" and prefix.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            text = codecs.getincrementaldecoder("utf-16")().decode(prefix, final=False)
        except UnicodeError as exc:
            raise ValueError("Invalid UTF-16 CSV document") from exc
        if "\0" in text:
            raise ValueError("Expected a text document")
    elif b"\0" in prefix:
        raise ValueError("Expected a text document")
