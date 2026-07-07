import datetime
import enum
import uuid

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class DocStatus(enum.StrEnum):
    pending = 'pending'
    processing = 'processing'
    done = 'done'
    error = 'error'


class Document(Base):
    __tablename__ = 'documents'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    filename: Mapped[str] = mapped_column(Text)  # Original-Dateiname
    stored_name: Mapped[str] = mapped_column(Text)  # Dateiname in data/originals
    result_stem: Mapped[str | None] = mapped_column(Text)  # Basisname in ergebnisse/
    mime: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    # SHA-256 des Originals — erkennt erneute Uploads derselben Datei
    sha256: Mapped[str | None] = mapped_column(Text, index=True)
    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus, name='doc_status'), default=DocStatus.pending
    )
    error: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Feste Tags (z.B. Ordnerpfad aus einem ZIP-Upload): bleiben bei
    # jeder (Neu-)Verarbeitung erhalten, das Modell ergänzt nur weitere.
    fixed_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default='{}'
    )
    summary: Mapped[str | None] = mapped_column(Text)
    doc_date: Mapped[datetime.date | None] = mapped_column(Date)
    uploaded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    pages: Mapped[list[Page]] = relationship(
        back_populates='document', cascade='all, delete-orphan', order_by='Page.page_no'
    )


class Page(Base):
    __tablename__ = 'pages'
    __table_args__ = (UniqueConstraint('document_id', 'page_no'),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey('documents.id', ondelete='CASCADE')
    )
    page_no: Mapped[int] = mapped_column(Integer)
    content_md: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates='pages')
