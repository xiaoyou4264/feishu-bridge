"""Message filtering and content parsing for Feishu events."""
import json


def should_respond(message, bot_open_id: str) -> bool:
    """
    Determine if the bot should respond to a message.

    Rules (per CONN-03):
      - P2P (chat_type == "p2p"): always respond.
      - Group (chat_type == "group"): respond only if bot is @mentioned.
      - Other chat types: do not respond.

    Args:
        message: EventMessage-like object with .chat_type and .mentions fields.
        bot_open_id: The bot's own open_id for @mention comparison.

    Returns:
        True if the bot should respond, False otherwise.
    """
    if message.chat_type == "p2p":
        return True

    if message.chat_type == "group":
        mentions = message.mentions or []
        return any(m.id.open_id == bot_open_id for m in mentions)

    return False


def parse_message_content(message) -> tuple[str, str]:
    """
    Parse message content into (text, message_type) tuple.

    Handles:
      - "text": extracts text field, strips @mention placeholders (e.g. "@_user_1").
      - "post": extracts plain text from rich text nodes.

    Per D-05: unsupported types (image, file, audio, video, sticker, etc.)
    raise ValueError. The caller is responsible for sending a friendly error reply.

    Args:
        message: EventMessage-like object with .message_type, .content, .mentions.

    Returns:
        (text, message_type) tuple where text is the extracted plain text.

    Raises:
        ValueError: For unsupported message types, with message "unsupported_type:{type}".
    """
    msg_type = message.message_type
    content = json.loads(message.content)

    if msg_type == "text":
        text = content.get("text", "")
        # Strip @mention placeholder keys (e.g. "@_user_1") from text
        mentions = message.mentions or []
        for mention in mentions:
            text = text.replace(mention.key, "").strip()
        return text, "text"

    if msg_type == "post":
        # Rich text: {"zh_cn": {"title": "...", "content": [[{"tag": "text", "text": "..."}]]}}
        lang_key = next(iter(content), None)
        if lang_key:
            post = content[lang_key]
            parts = []
            for line in post.get("content", []):
                for node in line:
                    if node.get("tag") == "text":
                        parts.append(node.get("text", ""))
            return " ".join(parts), "post"
        return "", "post"

    raise ValueError(f"unsupported_type:{msg_type}")
