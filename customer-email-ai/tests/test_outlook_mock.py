"""Tests for mock Outlook mailbox behavior."""

from __future__ import annotations

import config
from services import graph_client
from services.outlook_email_service import list_mock_messages


def test_mock_outlook_inbox_loads() -> None:
    """Mock mode returns reusable Outlook messages for the single default user."""
    messages = graph_client.list_inbox_messages(config.DEFAULT_USER_ID, limit=50)

    assert len(messages) >= 3
    assert all(message.user_id == config.DEFAULT_USER_ID for message in messages)
    assert all(message.message_id for message in messages)


def test_mock_outlook_inbox_respects_limit() -> None:
    """Inbox loading keeps the requested business email limit."""
    messages = graph_client.list_inbox_messages(config.DEFAULT_USER_ID, limit=2)

    assert len(messages) == 2


def test_single_default_user_loads_correctly() -> None:
    """The configured app user email is separate from the fixed internal user id."""
    current_user = graph_client.get_current_user(config.DEFAULT_USER_ID)

    assert config.APP_USER_EMAIL == "boss@company.com"
    assert config.DEFAULT_USER_ID == "default_user"
    assert current_user["id"] == config.DEFAULT_USER_ID
    assert current_user["mail"] == config.APP_USER_EMAIL


def test_mock_messages_belong_to_single_default_user() -> None:
    """All mock Outlook data is scoped to the fixed internal user id."""
    message_ids = {message.message_id for message in list_mock_messages(config.DEFAULT_USER_ID, limit=50)}

    assert message_ids
    assert list_mock_messages("unknown_user") == []
