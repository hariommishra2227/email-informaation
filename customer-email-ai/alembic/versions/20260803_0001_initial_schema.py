"""initial production schema

Revision ID: 20260803_0001
Revises:
Create Date: 2026-08-03

Downgrade warning: dropping these tables destroys application data. Do not run a
production downgrade without a tested backup/restore plan.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260803_0001"
down_revision = None
branch_labels = None
depends_on = None


def _pk_type() -> sa.TypeEngine:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "application_users",
        sa.Column("id", _pk_type(), primary_key=True, autoincrement=True),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("external_user_id", name="uq_application_users_external_user_id"),
    )
    op.create_index("ix_application_users_external_user_id", "application_users", ["external_user_id"])

    op.create_table(
        "connected_mailboxes",
        sa.Column("id", _pk_type(), primary_key=True, autoincrement=True),
        sa.Column("user_id", _pk_type(), sa.ForeignKey("application_users.id"), nullable=False),
        sa.Column("graph_user_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("email_address", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_connected_mailboxes_user_id", "connected_mailboxes", ["user_id"])
    op.create_index("ix_connected_mailboxes_email_address", "connected_mailboxes", ["email_address"])

    op.create_table(
        "emails",
        sa.Column("id", _pk_type(), primary_key=True, autoincrement=True),
        sa.Column("graph_message_id", sa.String(512), nullable=False),
        sa.Column("mailbox_id", _pk_type(), sa.ForeignKey("connected_mailboxes.id"), nullable=False),
        sa.Column("internet_message_id", sa.String(998), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("sender_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_datetime", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extraction_status", sa.String(64), nullable=False, server_default="Pending"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retry_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("mailbox_id", "graph_message_id", name="uq_emails_mailbox_graph_message"),
    )
    for name, column in (
        ("ix_emails_graph_message_id", "graph_message_id"),
        ("ix_emails_mailbox_id", "mailbox_id"),
        ("ix_emails_sender_email", "sender_email"),
        ("ix_emails_received_datetime", "received_datetime"),
        ("ix_emails_extraction_status", "extraction_status"),
    ):
        op.create_index(name, "emails", [column])

    op.create_table(
        "extracted_contacts",
        sa.Column("id", _pk_type(), primary_key=True, autoincrement=True),
        sa.Column("mailbox_id", _pk_type(), sa.ForeignKey("connected_mailboxes.id"), nullable=False),
        sa.Column("person_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("email_address", sa.String(320), nullable=False, server_default=""),
        sa.Column("normalized_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("organisation_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("mobile_phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("normalized_phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("designation", sa.String(255), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("location", sa.String(255), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_date", sa.String(64), nullable=False, server_default=""),
        sa.Column("source", sa.String(64), nullable=False, server_default="Manual"),
        sa.Column("source_message_id", sa.String(512), nullable=False, server_default=""),
        sa.Column("extraction_confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(64), nullable=False, server_default="Unique"),
        sa.Column("name_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("name_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("name_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("email_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("email_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("organisation_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("organisation_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("organisation_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("mobile_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("mobile_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("mobile_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("designation_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("designation_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("designation_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("address_source", sa.String(64), nullable=False, server_default=""),
        sa.Column("address_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("address_evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_method", sa.String(64), nullable=False, server_default="regex_spacy"),
        sa.Column("llm_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("llm_model", sa.String(255), nullable=False, server_default=""),
        sa.Column("llm_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("review_status", sa.String(64), nullable=False, server_default="Needs Review"),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("correction_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("internet_message_id", sa.String(998), nullable=False, server_default=""),
        sa.Column("sender_email", sa.String(320), nullable=False, server_default=""),
        sa.Column("sender_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("receiver_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("sender_domain", sa.String(255), nullable=False, server_default=""),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("mailbox_id", "normalized_email", name="uq_contacts_mailbox_normalized_email"),
    )
    for name, column in (
        ("ix_extracted_contacts_mailbox_id", "mailbox_id"),
        ("ix_contacts_organisation_name", "organisation_name"),
        ("ix_contacts_normalized_email", "normalized_email"),
        ("ix_contacts_sender_email", "sender_email"),
        ("ix_contacts_review_status", "review_status"),
    ):
        op.create_index(name, "extracted_contacts", [column])

    op.create_table("email_contacts", sa.Column("id", _pk_type(), primary_key=True, autoincrement=True), sa.Column("email_id", _pk_type(), sa.ForeignKey("emails.id"), nullable=False), sa.Column("contact_id", _pk_type(), sa.ForeignKey("extracted_contacts.id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("email_id", "contact_id", name="uq_email_contacts_email_contact"))
    op.create_index("ix_email_contacts_email_id", "email_contacts", ["email_id"])
    op.create_index("ix_email_contacts_contact_id", "email_contacts", ["contact_id"])

    op.create_table("review_audit", sa.Column("id", _pk_type(), primary_key=True, autoincrement=True), sa.Column("record_id", _pk_type(), sa.ForeignKey("extracted_contacts.id"), nullable=False), sa.Column("field_name", sa.String(128), nullable=False), sa.Column("old_value", sa.Text(), nullable=False, server_default=""), sa.Column("new_value", sa.Text(), nullable=False, server_default=""), sa.Column("old_source", sa.String(128), nullable=False, server_default=""), sa.Column("new_source", sa.String(128), nullable=False, server_default=""), sa.Column("reviewed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("reviewed_by", sa.String(255), nullable=False, server_default=""), sa.Column("notes", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_review_audit_record_id", "review_audit", ["record_id"])

    op.create_table("attachments", sa.Column("id", _pk_type(), primary_key=True, autoincrement=True), sa.Column("email_id", _pk_type(), sa.ForeignKey("emails.id"), nullable=False), sa.Column("original_filename", sa.String(255), nullable=False), sa.Column("safe_filename", sa.String(255), nullable=False), sa.Column("content_type", sa.String(255), nullable=False, server_default=""), sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"), sa.Column("storage_key", sa.String(1024), nullable=False), sa.Column("checksum", sa.String(128), nullable=False), sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("email_id", "checksum", name="uq_attachments_email_checksum"))
    op.create_index("ix_attachments_email_id", "attachments", ["email_id"])
    op.create_index("ix_attachments_checksum", "attachments", ["checksum"])

    op.create_table("mailbox_sync_state", sa.Column("mailbox_id", _pk_type(), sa.ForeignKey("connected_mailboxes.id"), primary_key=True), sa.Column("delta_link", sa.Text(), nullable=False, server_default=""), sa.Column("sync_cursor", sa.Text(), nullable=False, server_default=""), sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True), sa.Column("current_status", sa.String(64), nullable=False, server_default="Never"), sa.Column("error_message", sa.Text(), nullable=False, server_default=""), sa.Column("processed_records", sa.Integer(), nullable=False, server_default="0"), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))

    op.create_table("processing_jobs", sa.Column("id", _pk_type(), primary_key=True, autoincrement=True), sa.Column("job_type", sa.String(64), nullable=False), sa.Column("status", sa.String(64), nullable=False, server_default="Queued"), sa.Column("mailbox_id", _pk_type(), sa.ForeignKey("connected_mailboxes.id"), nullable=True), sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("error_message", sa.Text(), nullable=False, server_default=""), sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"), sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_processing_jobs_job_type", "processing_jobs", ["job_type"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])
    op.create_index("ix_processing_jobs_mailbox_id", "processing_jobs", ["mailbox_id"])

    op.create_table("failed_processing_records", sa.Column("id", _pk_type(), primary_key=True, autoincrement=True), sa.Column("job_id", _pk_type(), sa.ForeignKey("processing_jobs.id"), nullable=True), sa.Column("mailbox_id", _pk_type(), sa.ForeignKey("connected_mailboxes.id"), nullable=True), sa.Column("email_id", _pk_type(), sa.ForeignKey("emails.id"), nullable=True), sa.Column("graph_message_id", sa.String(512), nullable=False, server_default=""), sa.Column("error_code", sa.String(128), nullable=False, server_default=""), sa.Column("error_message", sa.Text(), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_failed_processing_records_job_id", "failed_processing_records", ["job_id"])
    op.create_index("ix_failed_processing_records_mailbox_id", "failed_processing_records", ["mailbox_id"])
    op.create_index("ix_failed_processing_records_email_id", "failed_processing_records", ["email_id"])

    op.create_table("oauth_auth_flows", sa.Column("flow_id", sa.String(255), primary_key=True), sa.Column("flow_json", sa.Text(), nullable=False), sa.Column("created_at", sa.BigInteger(), nullable=False), sa.Column("expires_at", sa.BigInteger(), nullable=False))
    op.create_index("ix_oauth_auth_flows_expires_at", "oauth_auth_flows", ["expires_at"])

    op.create_table("oauth_token_caches", sa.Column("cache_owner", sa.String(255), primary_key=True), sa.Column("cache_json", sa.Text(), nullable=False), sa.Column("account_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("updated_at", sa.BigInteger(), nullable=False))


def downgrade() -> None:
    for table in (
        "oauth_token_caches",
        "oauth_auth_flows",
        "failed_processing_records",
        "processing_jobs",
        "mailbox_sync_state",
        "attachments",
        "review_audit",
        "email_contacts",
        "extracted_contacts",
        "emails",
        "connected_mailboxes",
        "application_users",
    ):
        op.drop_table(table)
