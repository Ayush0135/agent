import sqlite3
import asyncio
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "local_research.db"

def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    """Create tables if they don't exist."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS research_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                query       TEXT NOT NULL,
                format      TEXT NOT NULL DEFAULT 'detailed report',
                result      TEXT,
                download_url TEXT,
                created_at  TEXT NOT NULL
            )
        """)
        conn.commit()

# Initialize on import
_init_db()

async def save_research(user_id: str, query: str, format_type: str, result: str, download_url: str = None):
    """Asynchronously save a research result to the local SQLite DB."""
    def _insert():
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO research_history (user_id, query, format, result, download_url, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, query, format_type, result, download_url, datetime.utcnow().isoformat())
            )
            conn.commit()
    await asyncio.to_thread(_insert)

async def get_history(user_id: str, limit: int = 20) -> list[dict]:
    """Fetch recent research history for a user from SQLite."""
    def _fetch():
        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT id, query, format, result, download_url, created_at
                   FROM research_history
                   WHERE user_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (user_id, limit)
            ).fetchall()
        return [dict(r) for r in rows]
    return await asyncio.to_thread(_fetch)

async def delete_history_item(item_id: int, user_id: str):
    """Delete a specific history item (user-scoped for security)."""
    def _delete():
        with _get_conn() as conn:
            conn.execute(
                "DELETE FROM research_history WHERE id = ? AND user_id = ?",
                (item_id, user_id)
            )
            conn.commit()
    await asyncio.to_thread(_delete)
