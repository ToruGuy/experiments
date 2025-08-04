#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import atexit
import asyncio
import logging

import httpx
from openai import AsyncOpenAI

log = logging.getLogger(__name__)

class PersonalAgent:
    def __init__(self, openrouter_api_key: str) -> None:
        if not openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required")

        self._http = httpx.AsyncClient(timeout=60.0)
        self._client = AsyncOpenAI(
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            http_client=self._http,
        )
        atexit.register(self._close_httpx_on_exit)

    async def reply(self, text: str, chat_id: int | str | None, user_id: int | None) -> str:
        try:
            resp = await self._client.chat.completions.create(
                model="openrouter/horizon-beta",
                messages=[
                    {"role": "system", "content": "You are a helpful assistant for Telegram."},
                    {"role": "user", "content": text},
                ],
                temperature=0.7,
            )
            content = resp.choices[0].message.content or ""
            return content.strip() if content else "(no content)"
        except Exception as e:
            log.exception("Model call failed")
            return f"Error calling model: {e}"

    def _close_httpx_on_exit(self) -> None:
        try:
            if hasattr(self._http, "aclose"):
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    loop.create_task(self._http.aclose())
                else:
                    asyncio.run(self._http.aclose())
        except Exception:
            pass