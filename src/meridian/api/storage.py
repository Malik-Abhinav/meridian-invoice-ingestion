"""Content-addressed local PDF storage with atomic, no-replace publication."""

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from pypdf import PdfReader


class StorageError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def safe_filename(name: str | None) -> str:
    if (not name or len(name) > 255 or name in (".", "..")
            or "/" in name or "\\" in name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)):
        raise StorageError("invalid_filename", "Upload filename must be a plain filename of at most 255 characters.")
    return name


def validate_pdf(data: bytes, max_pages: int) -> None:
    if not data.startswith(b"%PDF-"):
        raise StorageError("invalid_pdf", "Uploaded content is not a PDF.")
    try:
        reader = PdfReader(BytesIO(data), strict=True)
        if reader.is_encrypted or not 1 <= len(reader.pages) <= max_pages:
            raise ValueError("encrypted, empty, or over page limit")
        for page in reader.pages:
            _ = page.mediabox
    except Exception as exc:
        raise StorageError("invalid_pdf", "PDF structure is invalid or unsupported.") from exc


class LocalPdfStorage:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise StorageError("invalid_document", "Document hash is invalid.")
        target = self.root / digest[:2] / f"{digest}.pdf"
        if target.parent.is_symlink() or target.is_symlink():
            raise StorageError("storage_conflict", "Document storage path must not be a symbolic link.")
        return target

    async def store(self, upload: UploadFile, *, max_bytes: int, max_pages: int) -> tuple[str, int, bool]:
        safe_filename(upload.filename)
        pieces = bytearray()
        while chunk := await upload.read(min(1024 * 1024, max_bytes + 1 - len(pieces))):
            pieces.extend(chunk)
            if len(pieces) > max_bytes:
                raise StorageError("upload_too_large", "PDF exceeds the configured upload limit.")
        data = bytes(pieces)
        validate_pdf(data, max_pages)
        digest = hashlib.sha256(data).hexdigest()
        target = self._path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent.is_symlink():
            raise StorageError("storage_conflict", "Document storage path must not be a symbolic link.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".upload-", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                created = False
            if not created and hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise StorageError("storage_conflict", "Existing content-addressed PDF failed integrity check.")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return digest, len(data), created

    def read(self, digest: str) -> bytes:
        try:
            data = self._path(digest).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("document_missing", "Stored PDF is unavailable.") from exc
        if hashlib.sha256(data).hexdigest() != digest:
            raise StorageError("storage_conflict", "Stored PDF failed integrity check.")
        return data
