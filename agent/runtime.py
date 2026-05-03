"""PydanticAI runtime wrapper — thin async LLM call path with prompt-injection framing."""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from agent.config import ConfigError
from agent.logging import get_logger
from agent.registry import ResolvedProvider

_log = get_logger("agent.runtime")


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


async def reply(
    provider: ResolvedProvider,
    system_prompt: str,
    history: list[Turn],
    user_text: str,
    chat_id: int,
) -> str:
    """Call the LLM and return the assistant reply as a plain string.

    Steps (in order per PLAN-L2 Step 2):
      1. Frame the user text in the prompt-injection envelope.
      2. Build PydanticAI model from provider (openai_compatible only; anything
         else raises ConfigError — anthropic is already rejected at L1 resolve).
      3. Construct a fresh Agent per call (no module-level cache in 0.1).
      4. Translate history list[Turn] into PydanticAI message_history.
      5. Await agent.run(); extract .output string.
      6. Log INFO routing metadata (no api_key, no user/reply text).
      7. On exception: log ERROR via scrubbing logger, then re-raise.
    """
    framed = _frame_user_text(user_text, chat_id)

    if provider.kind == "openai_compatible":
        model = OpenAIChatModel(
            model_name=provider.model_id,
            provider=OpenAIProvider(
                base_url=provider.base_url,
                api_key=provider.api_key,
            ),
        )
    else:
        raise ConfigError(f"Unknown provider kind: {provider.kind!r}")

    agent: Agent[None, str] = Agent(model, system_prompt=system_prompt)

    message_history = []
    for turn in history:
        if turn["role"] == "user":
            message_history.append(ModelRequest(parts=[UserPromptPart(content=turn["content"])]))
        else:
            message_history.append(ModelResponse(parts=[TextPart(content=turn["content"])]))

    try:
        result = await agent.run(framed, message_history=message_history)
        _log.info(
            "provider-call: provider=%s model=%s",
            provider.provider_name,
            provider.model_id,
        )
        return result.output
    except Exception:
        _log.error(
            "provider-call failed: provider=%s model=%s",
            provider.provider_name,
            provider.model_id,
            exc_info=True,
        )
        raise
