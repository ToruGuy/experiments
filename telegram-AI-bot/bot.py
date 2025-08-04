#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Telegram AI Bot using OpenRouter via the OpenAI SDK (OpenAI Python client).

Features:
- Loads config from .env
- Replies with AI to any incoming text message
- Lets you set a default chat/channel target via /set_target and send AI replies via /send
- Compatible with python-telegram-bot v22.3
- Uses a shared httpx.AsyncClient and closes it at process exit via atexit

Docs:
  Telegram core types/methods: https://docs.python-telegram-bot.org/en/stable/telegram.html
  OpenRouter quickstart: https://openrouter.docs.buildwithfern.com/docs/quickstart
  OpenRouter frameworks (OpenAI SDK usage): https://openrouter.ai/docs/frameworks
"""

import os
import atexit
import asyncio
import logging
from typing import Optional

import httpx
from dotenv import load_dotenv
from openai import AsyncOpenAI

from telegram import Update, ForceReply
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------- Logging ----------
logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("tg-ai-bot")

# ---------- Env ----------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")
if not OPENROUTER_API_KEY:
    raise RuntimeError("Missing OPENROUTER_API_KEY in .env")

# ---------- OpenRouter client via OpenAI SDK ----------
def make_openrouter_client() -> AsyncOpenAI:
    # No optional attribution headers per user request
    http_client = httpx.AsyncClient(timeout=60.0)
    client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        http_client=http_client,
    )
    return client

openrouter_client: Optional[AsyncOpenAI] = make_openrouter_client()

# Graceful(ish) shutdown for the underlying AsyncClient on process exit
def _close_openrouter_client() -> None:
    try:
        if openrouter_client and hasattr(openrouter_client, "_client"):
            client = getattr(openrouter_client, "_client")
            if hasattr(client, "aclose"):
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    loop.create_task(client.aclose())  # fire-and-forget
                else:
                    asyncio.run(client.aclose())
    except Exception:
        pass

atexit.register(_close_openrouter_client)

# ---------- App state ----------
TARGET_KEY = "default_target"  # stores either int chat_id or str username (e.g., @channel)

# ---------- AI call ----------
async def ai_complete(prompt: str) -> str:
    if not openrouter_client:
        return "OpenRouter client not initialized."
    try:
        resp = await openrouter_client.chat.completions.create(
            model="openrouter/horizon-beta",
            messages=[
                {"role": "system", "content": "You are a helpful assistant for Telegram."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        content = resp.choices[0].message.content or ""
        return content.strip() if content else "(no content)"
    except Exception as e:
        log.exception("OpenRouter request failed")
        return f"Error calling model: {e}"

# ---------- Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    target = context.application.bot_data.get(TARGET_KEY)
    target_info = f"Current default target: {target}" if target else "No default target set."
    text = (
        f"Hi {user.mention_html()}!\n\n"
        "I reply with AI to any message you send me.\n\n"
        "To set a default chat/channel for /send, use:\n"
        "  /set_target <chat_id_or_username>\n"
        "Examples:\n"
        "  /set_target -1001234567890     (supergroup/channel ID)\n"
        "  /set_target @yourchannel       (public channel username)\n\n"
        "Send to target:\n"
        "  /send Your message here\n\n"
        f"{target_info}"
    )
    await update.message.reply_html(text, reply_markup=ForceReply(selective=True))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Commands:\n"
        "/start - intro and status\n"
        "/help - this help\n"
        "/set_target <chat_id_or_username> - set default target chat/channel\n"
        "/get_target - show current target\n"
        "/send <text> - send text to the default target via AI\n\n"
        "Tip: The bot must be allowed to post in the target. For channels, add it as an admin. "
        "For public channels use @username; for private/supergroups use numeric chat_id (-100...)."
    )

async def set_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text(
            "Usage: /set_target <chat_id_or_username>\n"
            "Example: /set_target -1001234567890 or /set_target @mychannel"
        )
        return

    raw = context.args[0]
    try:
        target = int(raw)  # numeric chat_id
    except ValueError:
        target = raw.strip()  # e.g., @channelusername

    context.application.bot_data[TARGET_KEY] = target
    await update.message.reply_text(f"Default target set to: {target}")

async def get_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = context.application.bot_data.get(TARGET_KEY)
    if target is None:
        await update.message.reply_text("No default target set. Use /set_target <chat_id_or_username>")
    else:
        await update.message.reply_text(f"Current default target: {target}")

async def send_to_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    target = context.application.bot_data.get(TARGET_KEY)
    if target is None:
        await update.message.reply_text("No default target set. Use /set_target <chat_id_or_username>")
        return
    if not context.args:
        await update.message.reply_text("Usage: /send <text to send via AI>")
        return

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text("Please provide text to send.")
        return

    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception:
        pass

    ai_text = await ai_complete(prompt)
    try:
        await context.bot.send_message(chat_id=target, text=ai_text)
        await update.message.reply_text("Sent to target.")
    except Exception as e:
        log.exception("Failed sending to target")
        await update.message.reply_text(f"Failed to send to target: {e}")

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_text = update.message.text or ""
    if not user_text.strip():
        return
    try:
        await update.message.chat.send_action(ChatAction.TYPING)
    except Exception:
        pass
    reply = await ai_complete(user_text)
    await update.message.reply_text(reply)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Update caused error: %s", context.error)

def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("set_target", set_target))
    application.add_handler(CommandHandler("get_target", get_target))
    application.add_handler(CommandHandler("send", send_to_target))

    # Any text message
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Error handler
    application.add_error_handler(on_error)

    logging.getLogger(__name__).info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()