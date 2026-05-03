"""PydanticAI runtime wrapper — thin async LLM call path with prompt-injection framing."""
from __future__ import annotations

from typing import Literal, TypedDict


class Turn(TypedDict):
    """A single conversation turn, compatible with the L3 memory layer's dict shape."""

    role: Literal["user", "assistant"]
    content: str


def _frame_user_text(text: str, chat_id: int) -> str:
    """Wrap user text in a prompt-injection framing envelope.

    chat_id is typed int as the attribute-injection defense: a crafted string
    like '1" system="injected' cannot reach this function because Python's type
    system and the Telegram Update.effective_chat.id field both guarantee an
    integer. No string-based chat_id ever reaches the envelope attribute.

    The escape targets the exact lowercase literal </user_message>. Mixed-case
    variants (e.g. </USER_MESSAGE>) are intentionally left unescaped and pass
    through as plain text inside the envelope body — they are syntactically
    distinct strings that do not break the envelope boundary, because the system
    prompt instructs the LLM to honor the exact lowercase boundary tag it was
    given. If the envelope tag name ever changes, the escape rule must change
    with it.

    The chat_id type is also enforced at runtime via isinstance — the type hint
    alone is not a defense (codex-review P1: bool is a subclass of int but a
    misuse like _frame_user_text(t, True) should fail loudly, and any future
    caller passing a string must hit a TypeError, not silently produce a broken
    envelope).
    """
    if type(chat_id) is not int:
        raise TypeError(
            f"chat_id must be int, got {type(chat_id).__name__}: {chat_id!r}"
        )
    escaped = text.replace("</user_message>", r"<\/user_message>")
    return f'<user_message chat_id="{chat_id}">\n{escaped}\n</user_message>'
