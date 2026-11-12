"""Content-addressed media store: ``media/ab/cd/<sha256>.<ext>``.

Files are written to ``tmp/`` first and moved into place, so a crash never
leaves a half-written file under its final name. Identical content is stored
once.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    #: Relative to the media root, with forward slashes.
    rel_path: str
    size_bytes: int
    existed: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class MediaStore:
    def __init__(self, root: Path, tmp: Path) -> None:
        self.root = root
        self.tmp = tmp

    def temp_path(self, suffix: str) -> Path:
        self.tmp.mkdir(parents=True, exist_ok=True)
        return self.tmp / f"{uuid.uuid4().hex}{suffix}"

    def put(self, source: Path, ext: str) -> StoredFile:
        """Move ``source`` (a file under ``tmp``) into the store."""
        ext = ext.lower().lstrip(".")
        digest = sha256_file(source)
        rel = f"{digest[:2]}/{digest[2:4]}/{digest}.{ext}"
        dest = self.root / rel
        size = source.stat().st_size
        if dest.exists():
            source.unlink(missing_ok=True)
            return StoredFile(digest, rel, size, existed=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, dest)
        return StoredFile(digest, rel, size, existed=False)

    def resolve(self, rel_path: str) -> Path:
        """Absolute path of a stored file; refuses anything outside the store."""
        candidate = (self.root / rel_path).resolve()
        root = self.root.resolve()
        if root not in candidate.parents:
            raise ValueError("path escapes the media store")
        return candidate
