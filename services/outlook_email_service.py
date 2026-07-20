"""Outlook mailbox access with safe local mock data."""

from __future__ import annotations

from models import OutlookMessage
import config


MOCK_OUTLOOK_MESSAGES: list[OutlookMessage] = [
    OutlookMessage(
        message_id="mock-emp1-001",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Rajesh Kumar",
        sender_email="rajesh.kumar@abctech.com",
        subject="Request for Endpoint Security Quotation",
        body="""
        <p>Rajesh Kumar</p>
        <p>IT Manager<br>ABC Technologies Pvt. Ltd.</p>
        <p>Email: rajesh.kumar@abctech.com<br>Mobile: +91 9876543210</p>
        <p>Address: Sector 62, Noida, Uttar Pradesh</p>
        """,
        received_datetime="2026-07-16T09:30:00+05:30",
        is_read=False,
        has_attachments=True,
        attachment_names=["endpoint-requirements.pdf", "site-list.xlsx"],
    ),
    OutlookMessage(
        message_id="mock-emp1-002",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Neha Shah",
        sender_email="neha.shah@orbitalsystems.com",
        subject="Company contact for pricing",
        body="Hello, please share pricing with Orbital Systems. Email: neha.shah@orbitalsystems.com",
        received_datetime="2026-07-15T16:10:00+05:30",
        is_read=True,
    ),
    OutlookMessage(
        message_id="mock-emp1-003",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Amit Verma",
        sender_email="amit.verma@northstartech.in",
        subject="Firewall renewal discussion",
        body="""
        Subject: Firewall renewal discussion
        Amit Verma
        Network Engineer
        Northstar Technologies Pvt. Ltd.
        Mobile: +91-99887-66554
        Email: amit.verma@northstartech.in
        Address: Hinjewadi Phase 1, Pune, Maharashtra
        """,
        received_datetime="2026-07-15T10:05:00+05:30",
        is_read=False,
    ),
    OutlookMessage(
        message_id="mock-emp1-004",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Rajesh Kumar",
        sender_email="rajesh.kumar@abctech.com",
        subject="Follow up on quotation",
        body="""
        Hi team,
        This is Rajesh Kumar from ABC Technologies Pvt. Ltd.
        Email: rajesh.kumar@abctech.com
        Mobile: +91 9876543210
        """,
        received_datetime="2026-07-14T13:45:00+05:30",
        is_read=False,
        has_attachments=True,
        attachment_names=["old-quote.pdf"],
    ),
    OutlookMessage(
        message_id="mock-emp1-005",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Pooja Mehta",
        sender_email="pooja@greenfieldretail.com",
        subject="Need endpoint demo",
        body="Please arrange a demo for Greenfield Retail. You can reply to this email.",
        received_datetime="2026-07-13T12:20:00+05:30",
        is_read=False,
    ),
    OutlookMessage(
        message_id="mock-emp1-006",
        user_id=config.DEFAULT_USER_ID,
        sender_name="Daniel Lee",
        sender_email="daniel.lee@northwindretail.com",
        subject="Support renewal",
        body="""
        Daniel Lee
        Northwind Retail
        Email: daniel.lee@northwindretail.com
        """,
        received_datetime="2026-07-12T11:00:00+05:30",
        is_read=True,
    ),
]


def list_mock_messages(user_id: str, limit: int | None = None) -> list[OutlookMessage]:
    """Return mock messages scoped to the single configured application user."""
    messages = [message for message in MOCK_OUTLOOK_MESSAGES if message.user_id == user_id]
    return messages[:limit] if limit is not None else messages


def get_mock_message(user_id: str, message_id: str) -> OutlookMessage | None:
    """Return one mock message for the single configured application user."""
    return next(
        (
            message
            for message in MOCK_OUTLOOK_MESSAGES
            if message.user_id == user_id and message.message_id == message_id
        ),
        None,
    )
