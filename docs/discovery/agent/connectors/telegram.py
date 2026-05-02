"""Telegram connector (stub).

Implements the Connector contract. Wired up in stage 1 MVP. The connector
holds zero LLM-specific knowledge — it's pure I/O translation.
"""

from __future__ import annotations

import os

from agent.connectors.base import Connector, Handler, OutboundMessage


class TelegramConnector(Connector):
    def __init__(self, token_env: str = "TELEGRAM_BOT_TOKEN") -> None:
        token = os.environ.get(token_env)
        if not token:
            raise RuntimeError(f"{token_env} not set")
        self._token = token
        self._app = None  # python-telegram-bot Application, built in start()

    async def start(self, handler: Handler) -> None:
        # Stage 1 implementation goal:
        #   from telegram.ext import ApplicationBuilder, MessageHandler, filters
        #   app = ApplicationBuilder().token(self._token).build()
        #   app.add_handler(MessageHandler(filters.TEXT, _wrap(handler)))
        #   await app.run_polling()
        raise NotImplementedError("stage 1 task")

    async def stop(self) -> None:
        raise NotImplementedError("stage 1 task")

    async def send(self, msg: OutboundMessage) -> None:
        raise NotImplementedError("stage 1 task")
