import json
import sqlite3
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                important INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                done_at TEXT
            );

            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                type TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_config (
                chat_id INTEGER PRIMARY KEY,
                allowed_commands TEXT
            );
        """)


# --- notes ---

def add_note(text: str, important: bool = False) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notes (text, important, created_at) VALUES (?, ?, ?)",
            (text, int(important), datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_important_notes(limit: int = 10) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE important=1 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()


# --- todos ---

def add_todo(text: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO todos (text, done, created_at) VALUES (?, 0, ?)",
            (text, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def list_todos_since(since: str) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM todos WHERE done=0 AND created_at > ? ORDER BY created_at ASC", (since,)
        ).fetchall()


def get_important_notes_since(since: str, limit: int = 5) -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM notes WHERE important=1 AND created_at > ? ORDER BY created_at DESC LIMIT ?",
            (since, limit),
        ).fetchall()


def list_todos(include_done: bool = False) -> list:
    with get_conn() as conn:
        if include_done:
            return conn.execute("SELECT * FROM todos ORDER BY created_at DESC").fetchall()
        return conn.execute(
            "SELECT * FROM todos WHERE done=0 ORDER BY created_at ASC"
        ).fetchall()


def mark_done(todo_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE todos SET done=1, done_at=? WHERE id=? AND done=0",
            (datetime.utcnow().isoformat(), todo_id),
        )
        return cur.rowcount > 0


def delete_todo(todo_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        return cur.rowcount > 0


# --- chats ---

def track_chat(chat_id: int, title: str, chat_type: str):
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO chats (chat_id, title, type, message_count, first_seen, last_seen)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                message_count = message_count + 1,
                last_seen = excluded.last_seen
        """, (chat_id, title or str(chat_id), chat_type, now, now))


def list_chats() -> list:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM chats ORDER BY last_seen DESC"
        ).fetchall()


# --- config ---

def get_config(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None


def set_config(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# --- chat config ---

def get_chat_commands(chat_id: int) -> list | None:
    """Returns list of allowed commands, or None meaning all commands allowed."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT allowed_commands FROM chat_config WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["allowed_commands"]) if row["allowed_commands"] else None


def set_chat_commands(chat_id: int, commands: list):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_config (chat_id, allowed_commands) VALUES (?,?)"
            " ON CONFLICT(chat_id) DO UPDATE SET allowed_commands=excluded.allowed_commands",
            (chat_id, json.dumps(commands)),
        )
