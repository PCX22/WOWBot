"""Recibe un SavedVariables (por el sincronizador o por /sync subir), lo guarda y lo importa."""
from __future__ import annotations

import asyncio
import gzip
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .importers import read_all
from .importers.luasv import SavedVariablesError, decompress, parse
from .importers.merge import apply_results
from .models import Upload

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.\-]")


@dataclass
class UploadOutcome:
    ok: bool
    text: str


def new_token() -> str:
    return secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.strip().encode()).hexdigest()


async def import_bytes(sessions, discord_id: int, raw: bytes, guild_name: str | None) -> UploadOutcome:
    """Lee e importa un archivo ya guardado (no lo vuelve a guardar)."""
    try:
        variables = await asyncio.to_thread(parse, raw)
    except SavedVariablesError as e:
        return UploadOutcome(False, str(e))
    results = read_all(variables, guild_name)
    if not results:
        names = ", ".join(sorted(variables)[:10]) or "ninguna"
        return UploadOutcome(False, f"No reconozco el addon de este archivo (variables: {names}).")
    async with sessions() as session:
        summary = await apply_results(session, results)
        await session.commit()
    addons = ", ".join(r.addon for r in results)
    return UploadOutcome(True, f"{addons}: {summary.text()}")


async def handle_upload(sessions, uploads_dir: Path, discord_id: int, file_name: str, data: bytes, guild_name: str | None) -> UploadOutcome:
    try:
        raw = await asyncio.to_thread(decompress, data)
    except (SavedVariablesError, OSError, EOFError) as e:
        return UploadOutcome(False, f"El archivo no se puede abrir: {e}")
    digest = hashlib.sha256(raw).hexdigest()
    async with sessions() as session:
        seen = await session.scalar(select(Upload).where(Upload.sha256 == digest).limit(1))
    if seen is not None:
        return UploadOutcome(True, "Este archivo ya estaba subido; no hay nada nuevo.")

    uploads_dir.mkdir(parents=True, exist_ok=True)
    clean_name = SAFE_NAME.sub("_", Path(file_name).name)[:80] or "archivo.lua"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = uploads_dir / f"{stamp}_{discord_id}_{clean_name.removesuffix('.gz')}.gz"
    await asyncio.to_thread(path.write_bytes, gzip.compress(raw))

    outcome = await import_bytes(sessions, discord_id, raw, guild_name)
    async with sessions() as session:
        session.add(Upload(discord_id=discord_id, file_name=clean_name, sha256=digest, path=str(path), result=outcome.text))
        await session.commit()
    return outcome


async def reprocess_all(sessions, guild_name: str | None) -> int:
    """Vuelve a importar todos los archivos guardados, del más viejo al más nuevo."""
    async with sessions() as session:
        uploads = list(await session.scalars(select(Upload).order_by(Upload.created_at)))
    count = 0
    for upload in uploads:
        path = Path(upload.path)
        if not path.exists():
            continue
        raw = await asyncio.to_thread(lambda: gzip.decompress(path.read_bytes()))
        outcome = await import_bytes(sessions, upload.discord_id, raw, guild_name)
        async with sessions() as session:
            row = await session.get(Upload, upload.id)
            row.result = outcome.text
            await session.commit()
        count += 1
    return count
