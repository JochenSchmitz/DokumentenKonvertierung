import datetime
import uuid

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime: str
    size_bytes: int
    status: str
    error: str | None
    page_count: int | None
    tags: list[str]
    summary: str | None
    doc_date: datetime.date | None
    uploaded_at: datetime.datetime
    processed_at: datetime.datetime | None


class DocumentUpdate(BaseModel):
    """Vom Nutzer editierbare Felder eines Dokuments (bisher nur Schlagworte)."""

    tags: list[str]


class UploadSkipped(BaseModel):
    """Beim Upload abgelehnte Datei samt kurzem Grund (z.B. Duplikat)."""

    filename: str
    reason: str


class UploadResult(BaseModel):
    created: list[DocumentOut]
    skipped: list[UploadSkipped]


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_no: int
    content_md: str


class DocumentEntityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    kind: str | None
    name: str | None
    company: str | None
    address: str | None
    phone: str | None
    email: str | None


class DocumentDetail(DocumentOut):
    pages: list[PageOut]
    entities: list[DocumentEntityOut]
