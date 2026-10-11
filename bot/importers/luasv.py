"""Lee un archivo SavedVariables de WoW (código Lua con asignaciones) y lo pasa a dicts de Python.

El archivo se ejecuta en un Lua real pero aislado: sin librerías (ni os, ni io, ni load), solo texto
(no bytecode), con límite de memoria y de instrucciones. Llega del PC de otra persona: no es de fiar.
"""
from __future__ import annotations

import gzip
import io

from lupa import LuaError, LuaRuntime

MAX_FILE = 64 * 1024 * 1024        # tamaño máximo descomprimido
MAX_MEMORY = 512 * 1024 * 1024      # memoria de Lua
MAX_INSTRUCTIONS = 400_000_000
MAX_DEPTH = 64

_RUNNER = """
local source, limit = ...
local env = {}
local chunk, err = load(source, "=SavedVariables", "t", env)
if not chunk then error(err, 0) end
debug.sethook(function() error("el archivo tarda demasiado en leerse", 0) end, "", limit)
local ok, result = pcall(chunk)
debug.sethook()
if not ok then error(result, 0) end
return env
"""


class SavedVariablesError(ValueError):
    pass


def decompress(data: bytes) -> bytes:
    """Quita el gzip si lo trae, sin dejar que un archivo pequeño se convierta en uno enorme."""
    if data[:2] != b"\x1f\x8b":
        if len(data) > MAX_FILE:
            raise SavedVariablesError("El archivo es demasiado grande.")
        return data
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        while chunk := f.read(1024 * 1024):
            out.write(chunk)
            if out.tell() > MAX_FILE:
                raise SavedVariablesError("El archivo es demasiado grande.")
    return out.getvalue()


def _to_python(value, depth: int = 0):
    if depth > MAX_DEPTH:
        raise SavedVariablesError("El archivo tiene tablas demasiado anidadas.")
    if hasattr(value, "items") and callable(getattr(value, "items")):
        result = {}
        for key, item in value.items():
            if isinstance(key, bytes):
                key = key.decode("utf-8", "replace")
            elif isinstance(key, float) and key.is_integer():
                key = int(key)
            result[key] = _to_python(item, depth + 1)
        return result
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, float) and value.is_integer() and abs(value) < 2**53:
        return int(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # Funciones u otras cosas raras: un SavedVariables no las tiene.
    return None


def parse(data: bytes) -> dict:
    """Las variables globales que define el archivo: {"GuildRecipesDB": {...}, ...}."""
    source = decompress(data)
    try:
        lua = LuaRuntime(register_eval=False, register_builtins=False, encoding=None, max_memory=MAX_MEMORY)
    except TypeError:  # lupa sin soporte de max_memory
        lua = LuaRuntime(register_eval=False, register_builtins=False, encoding=None)
    try:
        runner = lua.execute("return function(...) " + _RUNNER + " end")
        env = runner(source, MAX_INSTRUCTIONS)
    except LuaError as e:
        message = e.args[0] if e.args else e
        if isinstance(message, bytes):
            message = message.decode("utf-8", "replace")
        raise SavedVariablesError(f"No se puede leer el archivo: {message}") from None
    except MemoryError:
        raise SavedVariablesError("El archivo necesita demasiada memoria.") from None
    result = _to_python(env)
    return {k: v for k, v in result.items() if isinstance(k, str)}


def as_list(value) -> list:
    """Una tabla Lua usada como lista ({1=..., 2=...}) como lista de Python."""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return []
    items = []
    i = 1
    while i in value:
        items.append(value[i])
        i += 1
    return items
