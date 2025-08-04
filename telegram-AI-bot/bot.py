#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import logging
from dotenv import load_dotenv

from communicator import TelegramCommunicator
from agent import PersonalAgent

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tg-ai-bot")

def main() -> None:
    load_dotenv()

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    storage_dir = os.getenv("BOT_STORAGE_DIR", "./storage")

    if not telegram_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")
    if not openrouter_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY in .env")

    agent = PersonalAgent(openrouter_key)
    comm = TelegramCommunicator(telegram_token, reply_fn=agent.reply, storage_dir=storage_dir)

    log.info("Bot starting...")
    comm.start()

if __name__ == "__main__":
    main()