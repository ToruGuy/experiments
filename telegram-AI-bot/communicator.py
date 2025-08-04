#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import io
import logging
import os
from typing import Awaitable, Protocol, Optional

from telegram import Update, InputFile
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import LocalStorage
from pdf_service import PdfService

log = logging.getLogger("communicator")

class ReplyFn(Protocol):
    def __call__(self, text: str, chat_id: int | str | None, user_id: int | None) -> Awaitable[str]:
        ...

class Communicator(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...

def guess_name(default: str, supplied: Optional[str], fallback_ext: Optional[str] = None) -> str:
    if supplied:
        return supplied
    if fallback_ext and not default.endswith(f".{fallback_ext.lstrip('.')}"):
        return f"{default}.{fallback_ext.lstrip('.')}"
    return default

class TelegramCommunicator:
    """
    v21+ bot with:
    - AI text replies
    - File storage
    - Markdown->PDF via md2pdf with CSS themes (/pdf, /pdf_toruai, /pdf_bentfly)
    Also accepts hyphenated variants typed by users by parsing text manually.
    """

    def __init__(self, token: str, reply_fn: ReplyFn, storage_dir: Optional[str] = "./storage") -> None:
        self._token = token
        self._reply_fn = reply_fn
        self._app: Application = Application.builder().token(self._token).build()
        self._storage = LocalStorage(storage_dir or "./storage")
        self._pdf = PdfService(self._storage, themes_dir="./css", storage_subdir="md2pdf")

        # Commands (use underscores only; hyphens are invalid) per PTB docs [docs.python-telegram-bot.org](https://docs.python-telegram-bot.org/en/v21.5/telegram.ext.commandhandler.html)
        self._app.add_handler(CommandHandler("start", self._start_cmd))
        self._app.add_handler(CommandHandler("help", self._help_cmd))
        self._app.add_handler(CommandHandler("files", self._files_cmd))
        self._app.add_handler(CommandHandler("get", self._get_cmd))
        self._app.add_handler(CommandHandler("del", self._del_cmd))
        self._app.add_handler(CommandHandler("see", self._see_cmd))

        self._app.add_handler(CommandHandler("pdf", self._pdf_cmd))
        self._app.add_handler(CommandHandler("pdf_toruai", self._pdf_toruai_cmd))
        self._app.add_handler(CommandHandler("pdf_bentfly", self._pdf_bentfly_cmd))

        # Files/media
        self._app.add_handler(MessageHandler(filters.Document.ALL, self._on_document))
        self._app.add_handler(MessageHandler(filters.PHOTO, self._on_photo))
        self._app.add_handler(MessageHandler(filters.VIDEO, self._on_video))
        self._app.add_handler(MessageHandler(filters.AUDIO, self._on_audio))
        self._app.add_handler(MessageHandler(filters.VOICE, self._on_voice))

        # Text: includes fallback for hyphenated “commands” typed by users
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text))
        # Additionally, capture messages that look like commands but with hyphens
        self._app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"^/(pdf\-toruai|pdf\-bentfly)\b"), self._on_hyphen_command))

        self._app.add_error_handler(self._on_error)

    async def _start_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user = update.effective_user
        await update.message.reply_html(
            f"Hi {user.mention_html()}!\n"
            "- Send a file to store it.\n"
            "- /files to list, /get <id> to retrieve, /del <id> to delete, /see <id>.\n"
            "- Markdown → PDF:\n"
            "    /pdf <markdown>\n"
            "    /pdf_toruai <markdown>\n"
            "    /pdf_bentfly <markdown>\n"
            "    Or upload a .md file to auto-convert.\n"
            "Tip: If you type /pdf-toruai, I’ll still handle it."
        )

    async def _help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Storage: /files, /get <id>, /del <id>, /see <id>\n"
            "Markdown → PDF:\n"
            "/pdf TEXT (default CSS)\n/pdf_toruai TEXT\n/pdf_bentfly TEXT\n"
            "Or upload a .md file and I’ll convert.\n"
            "Note: Telegram commands use letters/digits/underscores only per PTB [docs.python-telegram-bot.org](https://docs.python-telegram-bot.org/en/v21.5/telegram.ext.commandhandler.html)."
        )

    # ---------- Storage commands ----------
    async def _files_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        metas = await self._storage.list_files()
        if not metas:
            await update.message.reply_text("No files stored.")
            return
        lines = []
        for m in metas[:100]:
            lines.append(f"{m.file_id}  {m.orig_name}  {m.size} bytes")
        if len(metas) > 100:
            lines.append(f"... and {len(metas)-100} more")
        await update.message.reply_text("\n".join(lines))

    async def _get_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /get <file_id>")
            return
        fid = args[0]
        meta = await self._storage.get_meta(fid)
        if not meta:
            await update.message.reply_text("Not found.")
            return
        data = await self._storage.read_bytes(fid)
        if data is None:
            await update.message.reply_text("File is missing from storage.")
            return
        try:
            bio = io.BytesIO(data)
            bio.name = meta.orig_name or f"{fid}.bin"
            await update.message.reply_document(
                document=InputFile(bio, filename=bio.name),
                caption=f"{meta.orig_name} ({meta.size} bytes)",
            )
        except Exception as e:
            log.exception("Sending document failed")
            await update.message.reply_text(f"Failed to send: {e}")

    async def _del_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /del <file_id>")
            return
        fid = args[0]
        ok = await self._storage.delete(fid)
        await update.message.reply_text("Deleted." if ok else "Not found or could not delete.")

    async def _see_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /see <file_id>")
            return
        fid = args[0]
        meta = await self._storage.get_meta(fid)
        if not meta:
            await update.message.reply_text("Not found.")
            return
        msg = (
            f"id: {meta.file_id}\n"
            f"name: {meta.orig_name}\n"
            f"type: {meta.mime_type}\n"
            f"size: {meta.size}\n"
            f"created: {meta.created_ts}\n"
            f"path: {meta.path}"
        )
        await update.message.reply_text(msg)

    # ---------- PDF commands ----------
    async def _pdf_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_pdf_text(update, context, theme=None)

    async def _pdf_toruai_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_pdf_text(update, context, theme="toruai")

    async def _pdf_bentfly_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._handle_pdf_text(update, context, theme="bentfly")

    async def _handle_pdf_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, theme: Optional[str]) -> None:
        text = (update.message.text or "").strip()
        parts = text.split(maxsplit=1)
        md_text = parts[1] if len(parts) > 1 else ""
        if not md_text:
            await update.message.reply_text("Usage: /pdf <markdown text>\nOr upload a .md file.")
            return
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            md_file, pdf_file = await self._pdf.convert_markdown_text(
                md_text=md_text,
                theme_name=theme,
                base_url=os.getcwd(),
                inferred_name="pasted.md",
            )
        except Exception as e:
            log.exception("md2pdf conversion failed")
            await update.message.reply_text(f"PDF conversion failed: {e}")
            return
        await update.message.reply_text(f"Saved markdown: {md_file.file_id} ({md_file.orig_name})")
        await self._send_pdf(update, pdf_file)

    async def _on_hyphen_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        Handle user-typed hyphenated variants like '/pdf-toruai text' by redirecting to the underscore handler.
        """
        if not update.message or not update.message.text:
            return
        text = update.message.text
        if text.startswith("/pdf-toruai"):
            # Replace only the command part
            transformed = text.replace("/pdf-toruai", "/pdf_toruai", 1)
            update.message.text = transformed
            await self._pdf_toruai_cmd(update, context)
        elif text.startswith("/pdf-bentfly"):
            transformed = text.replace("/pdf-bentfly", "/pdf_bentfly", 1)
            update.message.text = transformed
            await self._pdf_bentfly_cmd(update, context)

    async def _send_pdf(self, update: Update, pdf_file_meta) -> None:
        data = await self._storage.read_bytes(pdf_file_meta.file_id)
        if data is None:
            await update.message.reply_text("PDF not found in storage.")
            return
        bio = io.BytesIO(data)
        bio.name = pdf_file_meta.orig_name or "document.pdf"
        await update.message.reply_document(
            document=InputFile(bio, filename=bio.name),
            caption=f"PDF ready: {pdf_file_meta.orig_name}",
        )

    # ---------- Incoming messages ----------
    async def _on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            return
        # If it's a hyphenated command without args handler catching it (e.g., bot mentioned), try to normalize
        if update.message.text.startswith("/pdf-toruai"):
            await self._on_hyphen_command(update, context)
            return
        if update.message.text.startswith("/pdf-bentfly"):
            await self._on_hyphen_command(update, context)
            return

        text = update.message.text
        chat_id = update.effective_chat.id if update.effective_chat else None
        user_id = update.effective_user.id if update.effective_user else None
        log.info("Text message from user_id=%s chat_id=%s: %r", user_id, chat_id, text)
        try:
            await update.message.chat.send_action(ChatAction.TYPING)
        except Exception:
            pass
        try:
            reply_text = await self._reply_fn(text, chat_id, user_id)
        except Exception as e:
            log.exception("reply_fn failed")
            reply_text = f"Agent error: {e}"
        await update.message.reply_text(reply_text)

    async def _on_document(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        doc = update.message.document if update.message else None
        if not doc:
            return
        tg_file = await doc.get_file()
        data = await tg_file.download_as_bytearray()
        fname = (doc.file_name or "file.bin")
        lower = fname.lower()
        if lower.endswith(".md") or doc.mime_type in {"text/markdown", "text/x-markdown"}:
            await update.message.reply_text("Markdown detected, converting to PDF...")
            try:
                md_file, pdf_file = await self._pdf.convert_markdown_file_bytes(
                    md_data=bytes(data),
                    orig_filename=fname,
                    theme_name=None,
                    base_url=os.getcwd(),
                )
            except Exception as e:
                log.exception("md2pdf conversion failed")
                await update.message.reply_text(f"PDF conversion failed: {e}")
                return
            await update.message.reply_text(f"Stored markdown: {md_file.file_id} ({md_file.orig_name})")
            await self._send_pdf(update, pdf_file)
        else:
            orig_name = guess_name(default="file.bin", supplied=fname)
            meta = await self._storage.save_bytes(bytes(data), orig_name=orig_name, mime_type=doc.mime_type)
            await update.message.reply_text(f"Stored file: {meta.file_id} ({meta.orig_name}, {meta.size} bytes)")

    async def _on_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        photos = update.message.photo if update.message else None
        if not photos:
            return
        photo = photos[-1]
        tg_file = await photo.get_file()
        data = await tg_file.download_as_bytearray()
        unique = getattr(photo, "file_unique_id", None) or "photo"
        orig_name = f"{unique}.jpg"
        meta = await self._storage.save_bytes(bytes(data), orig_name=orig_name, mime_type="image/jpeg")
        await update.message.reply_text(f"Stored photo: {meta.file_id} ({meta.orig_name}, {meta.size} bytes)")

    async def _on_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        vid = update.message.video if update.message else None
        if not vid:
            return
        tg_file = await vid.get_file()
        data = await tg_file.download_as_bytearray()
        name = guess_name(default="video.mp4", supplied=getattr(vid, "file_name", None))
        meta = await self._storage.save_bytes(bytes(data), orig_name=name, mime_type=vid.mime_type or "video/mp4")
        await update.message.reply_text(f"Stored video: {meta.file_id} ({meta.orig_name}, {meta.size} bytes)")

    async def _on_audio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        aud = update.message.audio if update.message else None
        if not aud:
            return
        tg_file = await aud.get_file()
        data = await tg_file.download_as_bytearray()
        name = guess_name(default="audio.mp3", supplied=getattr(aud, "file_name", None))
        meta = await self._storage.save_bytes(bytes(data), orig_name=name, mime_type=aud.mime_type or "audio/mpeg")
        await update.message.reply_text(f"Stored audio: {meta.file_id} ({meta.orig_name}, {meta.size} bytes)")

    async def _on_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        voice = update.message.voice if update.message else None
        if not voice:
            return
        tg_file = await voice.get_file()
        data = await tg_file.download_as_bytearray()
        unique = getattr(voice, "file_unique_id", None) or "voice"
        orig_name = f"{unique}.ogg"
        meta = await self._storage.save_bytes(bytes(data), orig_name=orig_name, mime_type=voice.mime_type or "audio/ogg")
        await update.message.reply_text(f"Stored voice: {meta.file_id} ({meta.orig_name}, {meta.size} bytes)")

    async def _on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Update caused error: %s", context.error)

    def start(self) -> None:
        log.info("Starting Telegram polling (run_polling)...")
        self._app.run_polling()

    def stop(self) -> None:
        pass