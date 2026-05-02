"""Connector interface — the contract every messaging adapter implements.

Connectors are LLM-neutral. They translate platform-specific events
(Telegram updates, WhatsApp messages, HTTP requests) into a uniform
InboundMessage and pass them to the agent runtime.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass(frozen=True)
class InboundMessage:
    chat_id: str            # opaque, connector-scoped
    user_id: str
    text: str
    attachments: list[str]  # local file paths (downloaded, mounted volume)
    metadata: dict[str, str]


@dataclass(frozen=True)
class OutboundMessage:
    chat_id: str
    text: str
    files: list[str]


# Handler signature an agent runtime exposes to a connector.
Handler = Callable[[InboundMessage], Awaitable[OutboundMessage]]


class Connector(ABC):
    """Lifecycle: configure → start (long-running) → stop."""

    @abstractmethod
    async def start(self, handler: Handler) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None: ...
