#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from storage import LocalStorage, StoredFile
from md2pdf.core import md2pdf


class PdfService:
    """
    Markdown -> PDF via md2pdf.

    Saves both the markdown source and resulting PDF via LocalStorage
    under storage/md2pdf/.
    """

    def __init__(
        self,
        storage: LocalStorage,
        storage_subdir: str = "md2pdf",
    ) -> None:
        self._storage = storage
        self._subdir = storage_subdir

    async def convert_markdown_text(
        self,
        md_text: str,
        css_file_id: Optional[str] = None,
        output_name: Optional[str] = None,
        base_url: Optional[str] = None,
        inferred_name: str = "document.md",
    ) -> Tuple[StoredFile, StoredFile]:
        """
        Convert in-memory Markdown text to PDF.
        Returns (stored_markdown, stored_pdf)
        """
        md_bytes = md_text.encode("utf-8")
        md_file = await self._storage.save_bytes(
            data=md_bytes,
            orig_name=f"{self._subdir}-{inferred_name}",
            mime_type="text/markdown",
        )
        pdf_file = await self._render_pdf_from_bytes(
            md_bytes,
            inferred_name,
            css_file_id,
            base_url,
            output_name,
        )
        return md_file, pdf_file

    async def convert_markdown_file_bytes(
        self,
        md_data: bytes,
        orig_filename: str,
        css_file_id: Optional[str] = None,
        output_name: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Tuple[StoredFile, StoredFile]:
        """
        Convert uploaded Markdown bytes to PDF.
        Returns (stored_markdown, stored_pdf)
        """
        safe_name = orig_filename if orig_filename.lower().endswith(".md") else f"{orig_filename}.md"
        md_file = await self._storage.save_bytes(
            data=md_data,
            orig_name=f"{self._subdir}-{safe_name}",
            mime_type="text/markdown",
        )
        pdf_file = await self._render_pdf_from_bytes(
            md_data,
            safe_name,
            css_file_id,
            base_url,
            output_name,
        )
        return md_file, pdf_file

    async def _render_pdf_from_bytes(
        self,
        md_data: bytes,
        markdown_name: str,
        css_file_id: Optional[str],
        base_url: Optional[str],
        output_name: Optional[str],
    ) -> StoredFile:
        """
        Render using md2pdf by writing a temp .md and producing a temp .pdf,
        then saving bytes to storage.
        """
        css_bytes: Optional[bytes] = None
        if css_file_id:
            css_bytes = await self._storage.read_bytes(css_file_id)
            if css_bytes is None:
                raise FileNotFoundError(f"CSS file {css_file_id} not found in storage")

        pdf_bytes = await asyncio.to_thread(
            self._md2pdf_via_disk, md_data, css_bytes, base_url
        )
        pdf_name = output_name or os.path.splitext(markdown_name)[0] + ".pdf"
        stored_pdf = await self._storage.save_bytes(
            data=pdf_bytes,
            orig_name=f"{self._subdir}-{pdf_name}",
            mime_type="application/pdf",
        )
        return stored_pdf


    def _md2pdf_via_disk(
        self, md_data: bytes, css_bytes: Optional[bytes], base_url: Optional[str]
    ) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            md_path = tdir / "input.md"
            pdf_path = tdir / "output.pdf"
            md_path.write_bytes(md_data)

            css_path = None
            if css_bytes:
                css_path = tdir / "style.css"
                css_path.write_bytes(css_bytes)

            css_path_str = str(css_path) if css_path else None
            css_list = [css_path_str] if css_path_str else None
            used = False
            base = base_url or str(md_path.parent)

            # Attempt 1: pdf_file_path/md_file_path
            try:
                md2pdf(
                    pdf_file_path=str(pdf_path),
                    md_file_path=str(md_path),
                    css_file_path=css_path_str,
                    base_url=base,
                )
                used = True
            except TypeError:
                pass

            # Attempt 2: keyword arguments (input_file/output_file)
            if not used:
                try:
                    md2pdf(
                        input_file=str(md_path),
                        output_file=str(pdf_path),
                        stylesheets=css_list,
                        base_url=base,
                    )
                    used = True
                except TypeError:
                    pass

            # Attempt 3: keyword arguments (input_file/output_path/css_file_path)
            if not used:
                try:
                    md2pdf(
                        input_file=str(md_path),
                        output_path=str(pdf_path),
                        css_file_path=css_list,
                        base_url=base,
                    )
                    used = True
                except TypeError:
                    pass

            # Attempt 4: positional (input, output)
            if not used:
                try:
                    if css_list:
                        md2pdf(str(md_path), str(pdf_path), stylesheets=css_list, base_url=base)
                    else:
                        md2pdf(str(md_path), str(pdf_path), base_url=base)
                    used = True
                except TypeError:
                    pass

            # Attempt 5: md_content -> output_path
            if not used:
                try:
                    content = [md_data.decode("utf-8")]
                    if css_list:
                        md2pdf(output_path=str(pdf_path), md_content=content, stylesheets=css_list, base_url=base)
                    else:
                        md2pdf(output_path=str(pdf_path), md_content=content, base_url=base)
                    used = True
                except TypeError as e:
                    raise TypeError(f"Incompatible md2pdf version/API: {e}")

            return pdf_path.read_bytes()

