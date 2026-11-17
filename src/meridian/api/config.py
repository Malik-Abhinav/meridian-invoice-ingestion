"""API configuration read at application creation, without connecting to services."""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class ApiSettings:
    storage_root: Path = Path(".meridian-storage")
    max_upload_bytes: int = 12 * 1024 * 1024
    max_pdf_pages: int = 30
    provider: str = "mock"
    openai_model: str | None = None
    dev_auth_enabled: bool = False
    dev_staff_token: str | None = None
    dev_staff_id: str = "local-reviewer"
    dev_token_expires_at: datetime | None = None
    processing_lease_seconds: int = 300

    @classmethod
    def from_env(cls) -> "ApiSettings":
        return cls(
            storage_root=Path(os.getenv("MERIDIAN_STORAGE_ROOT", ".meridian-storage")),
            max_upload_bytes=int(os.getenv("MERIDIAN_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024))),
            max_pdf_pages=int(os.getenv("MERIDIAN_MAX_PDF_PAGES", "30")),
            provider=os.getenv("MERIDIAN_EXTRACTION_PROVIDER", "mock"),
            openai_model=os.getenv("MERIDIAN_OPENAI_MODEL"),
            dev_auth_enabled=os.getenv("MERIDIAN_DEV_AUTH", "0") == "1",
            dev_staff_token=os.getenv("MERIDIAN_DEV_STAFF_TOKEN"),
            dev_staff_id=os.getenv("MERIDIAN_DEV_STAFF_ID", "local-reviewer"),
            dev_token_expires_at=(datetime.fromisoformat(os.environ["MERIDIAN_DEV_TOKEN_EXPIRES_AT"]
                                  .replace("Z", "+00:00"))
                                  if os.getenv("MERIDIAN_DEV_TOKEN_EXPIRES_AT") else None),
            processing_lease_seconds=int(os.getenv("MERIDIAN_PROCESSING_LEASE_SECONDS", "300")),
        )

    def __post_init__(self) -> None:
        if self.max_upload_bytes <= 0 or self.max_pdf_pages <= 0 or self.processing_lease_seconds <= 0:
            raise ValueError("upload, PDF page, and processing lease limits must be positive")
        if self.provider not in ("mock", "openai"):
            raise ValueError("MERIDIAN_EXTRACTION_PROVIDER must be mock or openai")
        if self.dev_auth_enabled and (not self.dev_staff_token or not self.dev_staff_id.strip()):
            raise ValueError("development authentication requires a token and staff ID")
        if self.dev_token_expires_at is not None and self.dev_token_expires_at.utcoffset() is None:
            raise ValueError("development token expiry must include a timezone")
