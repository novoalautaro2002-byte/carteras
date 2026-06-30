"""
Capa de caché en SQLite. Evita pegarle a yfinance/FMP en cada request:
guarda el JSON de cada respuesta con timestamp y lo sirve si no venció el TTL.
"""
import sqlite3
import json
import time
from contextlib import contextmanager
from typing import Any, Optional

from app.config import CACHE_DB_PATH


def _init_db():
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                fetched_at REAL NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def _connect():
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    try:
        yield conn
    finally:
        conn.close()


def get(key: str, ttl_hours: float) -> Optional[Any]:
    """Devuelve el valor cacheado si existe y no venció el TTL, sino None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT value, fetched_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if row is None:
        return None
    value, fetched_at = row
    age_hours = (time.time() - fetched_at) / 3600
    if age_hours > ttl_hours:
        return None
    return json.loads(value)


def set(key: str, value: Any) -> None:
    payload = json.dumps(value)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO cache (key, value, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
            (key, payload, time.time()),
        )
        conn.commit()


def clear(prefix: str = "") -> int:
    """Borra entradas de caché. Si se pasa prefix, borra solo las que matchean."""
    with _connect() as conn:
        if prefix:
            cur = conn.execute("DELETE FROM cache WHERE key LIKE ?", (f"{prefix}%",))
        else:
            cur = conn.execute("DELETE FROM cache")
        conn.commit()
        return cur.rowcount


_init_db()
