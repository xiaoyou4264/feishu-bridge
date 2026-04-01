"""Shared test fixtures for feishu-bridge tests."""
import types
import pytest


def make_event_message(**overrides):
    """
    Factory for mock EventMessage-like objects.

    Defaults:
        message_id="msg_001"
        chat_id="chat_001"
        chat_type="p2p"
        message_type="text"
        content='{"text":"hello"}'
        mentions=None
    """
    defaults = {
        "message_id": "msg_001",
        "chat_id": "chat_001",
        "chat_type": "p2p",
        "message_type": "text",
        "content": '{"text":"hello"}',
        "mentions": None,
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def make_event_data(message=None, event_id="evt_001", sender_open_id="ou_sender_001"):
    """
    Factory for mock P2ImMessageReceiveV1-like objects.

    Fields:
        .header.event_id
        .event.message
        .event.sender.sender_id.open_id
    """
    if message is None:
        message = make_event_message()

    sender_id = types.SimpleNamespace(open_id=sender_open_id)
    sender = types.SimpleNamespace(sender_id=sender_id, sender_type="user")
    event = types.SimpleNamespace(message=message, sender=sender)
    header = types.SimpleNamespace(event_id=event_id)

    return types.SimpleNamespace(header=header, event=event)
