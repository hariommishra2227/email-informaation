from __future__ import annotations

from services.graph_client import _receiver_names_from_graph_item


def test_receiver_names_are_unique_and_ordered() -> None:
    item = {"toRecipients": [
        {"emailAddress": {"name": "Hariom Mishra"}},
        {"emailAddress": {"name": "Hariom Mishra"}},
        {"emailAddress": {"name": "Rahul Gupta"}},
        {"emailAddress": {"address": "blank@example.com"}},
    ]}
    assert _receiver_names_from_graph_item(item) == "Hariom Mishra; Rahul Gupta"


def test_missing_graph_recipient_metadata_is_blank() -> None:
    assert _receiver_names_from_graph_item({}) == ""
    assert _receiver_names_from_graph_item({"toRecipients": None}) == ""
