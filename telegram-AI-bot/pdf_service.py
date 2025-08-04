#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from storage import LocalStorage, StoredFile

# md2pdf relies on WeasyPrint
from md2pdf.core import md2pdf

@dataclass
class CssTheme:
    name: str
    css_path: Optional[Path]  # None = no custom CSS

class PdfService:
    """
    Markdown -> PDF via md2pdf with CSS themes.
    Saves both the markdown source and resulting PDF via LocalStorage under storage/md2pdf/.
    """

    def __init__(
        self,
        storage: LocalStorage,
        themes_dir: str | Path = "./css",
        storage_subdir: str = "md2pdf",
        default_theme_name: str = "default",
    ) -> None:
        self._storage = storage
        self._themes_dir = Path(themes_dir)
        self._subdir = storage_subdir
        self._themes: dict[str, CssTheme] = self._load_themes(default_theme_name)
        self._default_theme_name = default_theme_name

    def _load_themes(self, default_theme_name: str) -> dict[str, CssTheme]:
        mapping = {
            "default": None,                 # use md2pdf/weasyprint defaults
            "toruai": self._themes_dir / "toruai.css",
            "bentfly": self._themes_dir / "bentfly.css",
        }
        themes: dict[str, CssTheme] = {}
        for name, path in mapping.items():
            css_path = Path(path) if path else None
            if css_path is not None and not css_path.exists():
                css_path = None  # if CSS missing, fall back gracefully
            themes[name] = CssTheme(name=name, css_path=css_path)
        if default_theme_name not in themes:
            themes[default_theme_name] = CssTheme(name=default_theme_name, css_path=None)
        return themes

    def _pick_theme(self, theme_name: Optional[str]) -> CssTheme:
        if not theme_name:
            return self._themes[self._default_theme_name]
        return self._themes.get(theme_name, self._themes[self._default_theme_name])

    async def convert_markdown_text(
        self,
        md_text: str,
        theme_name: Optional[str],
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
        pdf_file = await self._render_pdf_from_bytes(md_bytes, md_file.orig_name, theme_name, base_url)
        return md_file, pdf_file

    async def convert_markdown_file_bytes(
        self,
        md_data: bytes,
        orig_filename: str,
        theme_name: Optional[str],
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
        pdf_file = await self._render_pdf_from_bytes(md_data, md_file.orig_name, theme_name, base_url)
        return md_file, pdf_file

    async def _render_pdf_from_bytes(
        self,
        md_data: bytes,
        markdown_name: str,
        theme_name: Optional[str],
        base_url: Optional[str],
    ) -> StoredFile:
        """
        Render using md2pdf by writing a temp .md and producing a temp .pdf, then saving bytes to storage.
        This avoids relying on md2pdf's function signature variations.
        """
        theme = self._pick_theme(theme_name)
        pdf_bytes = await asyncio.to_thread(self._md2pdf_via_disk, md_data, theme, base_url)
        pdf_name = os.path.splitext(markdown_name)[0] + ".pdf"
        stored_pdf = await self._storage.save_bytes(
            data=pdf_bytes,
            orig_name=f"{self._subdir}-{pdf_name}",
            mime_type="application/pdf",
        )
        return stored_pdf

    def _md2pdf_via_disk(self, md_data: bytes, theme: CssTheme, base_url: Optional[str]) -> bytes:
        # Prepare temp files
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            md_path = tdir / "input.md"
            pdf_path = tdir / "output.pdf"
            md_path.write_bytes(md_data)

            # Try calling md2pdf with different supported signatures:
            # Newer versions: md2pdf(input_file=..., output_file=..., stylesheets=[...], base_url=...)
            # Some versions:  md2pdf(input_file=..., output_path=..., css_file_path=[...], base_url=...)
            css_list = [str(theme.css_path)] if theme.css_path else None
            used = False

            # Attempt 1: input_file/output_file
            try:
                md2pdf(
                    input_file=str(md_path),
                    output_file=str(pdf_path),
                    stylesheets=css_list,
                    base_url=base_url or str(md_path.parent),
                )
                used = True
            except TypeError:
                pass

            # Attempt 2: input_file/output_path/css_file_path
            if not used:
                try:
                    md2pdf(
                        input_file=str(md_path),
                        output_path=str(pdf_path),
                        css_file_path=css_list,
                        base_url=base_url or str(md_path.parent),
                    )
                    used = True
                except TypeError:
                    pass

            # Attempt 3: positional (input, output)
            if not used:
                try:
                    if css_list:
                        md2pdf(str(md_path), str(pdf_path), stylesheets=css_list, base_url=base_url or str(md_path.parent))
                    else:
                        md2pdf(str(md_path), str(pdf_path), base_url=base_url or str(md_path.parent))
                    used = True
                except TypeError:
                    pass

            if not used:
                # Last resort: md_content -> output_path
                try:
                    if css_list:
                        md2pdf(output_path=str(pdf_path), md_content=[md_data.decode("utf-8")], stylesheets=css_list, base_url=base_url or str(md_path.parent))
                    else:
                        md2pdf(output_path=str(pdf_path), md_content=[md_data.decode("utf-8")], base_url=base_url or str(md_path.parent))
                    used = True
                except TypeError as e:
                    raise TypeError(f"Incompatible md2pdf version/API: {e}")

            return pdf_path.read_bytes()