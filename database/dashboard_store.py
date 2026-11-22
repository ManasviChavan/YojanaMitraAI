from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

from config.settings import DATABASE_PATH


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS guest_preferences (
            guest_id TEXT PRIMARY KEY, preferred_language TEXT NOT NULL DEFAULT 'English'
        );
        CREATE TABLE IF NOT EXISTS guest_searches (
            id INTEGER PRIMARY KEY, guest_id TEXT NOT NULL, query TEXT NOT NULL,
            language TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS saved_schemes (
            id INTEGER PRIMARY KEY, guest_id TEXT NOT NULL, title TEXT NOT NULL,
            url TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(guest_id, url)
        );
        CREATE TABLE IF NOT EXISTS saved_assessments (
            id INTEGER PRIMARY KEY, guest_id TEXT NOT NULL, scheme TEXT NOT NULL,
            payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def get_preferred_language(guest_id: str) -> str | None:
    with _connect() as db:
        row = db.execute("SELECT preferred_language FROM guest_preferences WHERE guest_id=?", (guest_id,)).fetchone()
    return row[0] if row else None


def set_preferred_language(guest_id: str, language: str) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO guest_preferences(guest_id, preferred_language) VALUES (?, ?) "
            "ON CONFLICT(guest_id) DO UPDATE SET preferred_language=excluded.preferred_language",
            (guest_id, language),
        )


def save_search(guest_id: str, query: str, language: str) -> None:
    with _connect() as db:
        db.execute("INSERT INTO guest_searches(guest_id, query, language) VALUES (?, ?, ?)", (guest_id, query, language))


def save_scheme(guest_id: str, title: str, url: str, retrieved_at: str) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO saved_schemes(guest_id, title, url, retrieved_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guest_id, url) DO UPDATE SET title=excluded.title, retrieved_at=excluded.retrieved_at",
            (guest_id, title, url, retrieved_at),
        )


def save_assessment(guest_id: str, scheme: str, payload: dict[str, Any]) -> None:
    with _connect() as db:
        db.execute(
            "INSERT INTO saved_assessments(guest_id, scheme, payload) VALUES (?, ?, ?)",
            (guest_id, scheme, json.dumps(payload, ensure_ascii=False)),
        )


def dashboard_data(guest_id: str) -> dict[str, list[dict]]:
    with _connect() as db:
        searches = db.execute(
            "SELECT query, language, created_at FROM guest_searches WHERE guest_id=? ORDER BY id DESC LIMIT 20", (guest_id,)
        ).fetchall()
        schemes = db.execute(
            "SELECT title, url, retrieved_at, saved_at FROM saved_schemes WHERE guest_id=? ORDER BY id DESC", (guest_id,)
        ).fetchall()
        assessments = db.execute(
            "SELECT scheme, payload, created_at FROM saved_assessments WHERE guest_id=? ORDER BY id DESC LIMIT 20", (guest_id,)
        ).fetchall()
    return {
        "searches": [dict(row) for row in searches],
        "schemes": [dict(row) for row in schemes],
        "assessments": [{**dict(row), "payload": json.loads(row["payload"])} for row in assessments],
    }


def clear_guest_data(guest_id: str) -> None:
    with _connect() as db:
        for table in ("guest_searches", "saved_schemes", "saved_assessments", "guest_preferences"):
            db.execute(f"DELETE FROM {table} WHERE guest_id=?", (guest_id,))
