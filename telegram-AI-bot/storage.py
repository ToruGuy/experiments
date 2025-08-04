#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

@dataclass
class StoredFile:
    file_id: str
    orig_name: str
    mime_type: Optional[str]
    size: int
    path: str
    created_ts: float

class LocalStorage:
    """
    Simple local filesystem storage.
    base/
      files/<file_id>
      meta/<file_id>.json
    """

    def __init__(self, base_dir: str | Path = "./storage") -> None:
        self.base = Path(base_dir)
        self.files_dir = self.base / "files"
        self.meta_dir = self.base / "meta"
        self._lock = asyncio.Lock()
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

    async def save_bytes(self, data: bytes, orig_name: str, mime_type: Optional[str]) -> StoredFile:
        async with self._lock:
            created = time.time()
            h = hashlib.sha256()
            h.update(data)
            h.update(str(created).encode())
            fid = h.hexdigest()[:16]
            fpath = self.files_dir / fid

            await asyncio.to_thread(self._write_bytes, fpath, data)
            meta = StoredFile(
                file_id=fid,
                orig_name=orig_name,
                mime_type=mime_type,
                size=len(data),
                path=str(fpath),
                created_ts=created,
            )
            await asyncio.to_thread(self._write_meta, fid, meta)
            return meta

    def _write_bytes(self, path: Path, data: bytes) -> None:
        with open(path, "wb") as f:
            f.write(data)

    def _write_meta(self, fid: str, meta: StoredFile) -> None:
        mpath = self.meta_dir / f"{fid}.json"
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(asdict(meta), f, ensure_ascii=False, indent=2)

    async def list_files(self) -> list[StoredFile]:
        metas: list[StoredFile] = []
        for mfile in sorted(self.meta_dir.glob("*.json")):
            try:
                meta = await asyncio.to_thread(self._read_meta_file, mfile)
                metas.append(meta)
            except Exception:
                continue
        return metas

    def _read_meta_file(self, path: Path) -> StoredFile:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return StoredFile(**obj)

    async def get_meta(self, file_id: str) -> Optional[StoredFile]:
        mpath = self.meta_dir / f"{file_id}.json"
        if not mpath.exists():
            return None
        return await asyncio.to_thread(self._read_meta_file, mpath)

    async def read_bytes(self, file_id: str) -> Optional[bytes]:
        meta = await self.get_meta(file_id)
        if not meta:
            return None
        fpath = Path(meta.path)
        if not fpath.exists():
            return None
        return await asyncio.to_thread(fpath.read_bytes)

    async def delete(self, file_id: str) -> bool:
        async with self._lock:
            meta = await self.get_meta(file_id)
            if not meta:
                return False
            ok = True
            try:
                Path(meta.path).unlink(missing_ok=True)
            except Exception:
                ok = False
            try:
                (self.meta_dir / f"{file_id}.json").unlink(missing_ok=True)
            except Exception:
                ok = False
            return ok