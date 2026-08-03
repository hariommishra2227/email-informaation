"""SQLAlchemy ORM models for Outlook extraction persistence."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.connection import Base


BigIntPk = BigInteger().with_variant(Integer, "sqlite")


class ApplicationUser(Base):
    __tablename__ = "application_users"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    external_user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ConnectedMailbox(Base):
    __tablename__ = "connected_mailboxes"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("application_users.id"), nullable=False, index=True)
    graph_user_id: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped[ApplicationUser] = relationship()


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "graph_message_id", name="uq_emails_mailbox_graph_message"),
        Index("ix_emails_graph_message_id", "graph_message_id"),
        Index("ix_emails_mailbox_id", "mailbox_id"),
        Index("ix_emails_sender_email", "sender_email"),
        Index("ix_emails_received_datetime", "received_datetime"),
        Index("ix_emails_extraction_status", "extraction_status"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    graph_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("connected_mailboxes.id"), nullable=False)
    internet_message_id: Mapped[str] = mapped_column(String(998), default="", nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sender_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_preview: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    received_datetime: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(64), default="Pending", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retry_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    last_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    next_retry_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    mailbox: Mapped[ConnectedMailbox] = relationship()


class ExtractedContact(Base):
    __tablename__ = "extracted_contacts"
    __table_args__ = (
        UniqueConstraint("mailbox_id", "normalized_email", name="uq_contacts_mailbox_normalized_email"),
        Index("ix_contacts_organisation_name", "organisation_name"),
        Index("ix_contacts_normalized_email", "normalized_email"),
    )

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    mailbox_id: Mapped[int] = mapped_column(ForeignKey("connected_mailboxes.id"), nullable=False, index=True)
    person_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    normalized_email: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    organisation_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    mobile_phone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    normalized_phone: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    designation: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    location: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="", nullable=False)
    email_date: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="Manual", nullable=False)
    source_message_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    extraction_confidence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(64), default="Unique", nullable=False)
    name_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    name_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    name_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    email_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    email_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    email_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    organisation_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    organisation_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    organisation_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mobile_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    mobile_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    mobile_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    designation_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    designation_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    designation_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    address_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    address_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    address_evidence: Mapped[str] = mapped_column(Text, default="", nullable=False)
    extraction_method: Mapped[str] = mapped_column(String(64), default="regex_spacy", nullable=False)
    llm_used: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    llm_error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    review_status: Mapped[str] = mapped_column(String(64), default="Needs Review", nullable=False, index=True)
    reviewed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    correction_notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    internet_message_id: Mapped[str] = mapped_column(String(998), default="", nullable=False)
    sender_email: Mapped[str] = mapped_column(String(320), default="", nullable=False, index=True)
    sender_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    receiver_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    sender_domain: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    processed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class EmailContact(Base):
    __tablename__ = "email_contacts"
    __table_args__ = (UniqueConstraint("email_id", "contact_id", name="uq_email_contacts_email_contact"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), nullable=False, index=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("extracted_contacts.id"), nullable=False, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ReviewAudit(Base):
    __tablename__ = "review_audit"
    __table_args__ = (Index("ix_review_audit_record_id", "record_id"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("extracted_contacts.id"), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    old_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    new_value: Mapped[str] = mapped_column(Text, default="", nullable=False)
    old_source: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    new_source: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    reviewed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("email_id", "checksum", name="uq_attachments_email_checksum"),)

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id"), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    uploaded_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MailboxSyncState(Base):
    __tablename__ = "mailbox_sync_state"

    mailbox_id: Mapped[int] = mapped_column(ForeignKey("connected_mailboxes.id"), primary_key=True)
    delta_link: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sync_cursor: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_successful_sync_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=True)
    current_status: Mapped[str] = mapped_column(String(64), default="Never", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    processed_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(64), default="Queued", nullable=False, index=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("connected_mailboxes.id"), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class FailedProcessingRecord(Base):
    __tablename__ = "failed_processing_records"

    id: Mapped[int] = mapped_column(BigIntPk, primary_key=True, autoincrement=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("processing_jobs.id"), nullable=True, index=True)
    mailbox_id: Mapped[int | None] = mapped_column(ForeignKey("connected_mailboxes.id"), nullable=True, index=True)
    email_id: Mapped[int | None] = mapped_column(ForeignKey("emails.id"), nullable=True, index=True)
    graph_message_id: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    error_code: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class OAuthAuthFlow(Base):
    __tablename__ = "oauth_auth_flows"

    flow_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    flow_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)


class OAuthTokenCache(Base):
    __tablename__ = "oauth_token_caches"

    cache_owner: Mapped[str] = mapped_column(String(255), primary_key=True)
    cache_json: Mapped[str] = mapped_column(Text, nullable=False)
    account_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
