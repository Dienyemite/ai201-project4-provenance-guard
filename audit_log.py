"""Structured audit log for Provenance Guard, backed by SQLite.

Every attribution decision (and any later appeal against it) lives in a single
row in the `submissions` table, so the full lifecycle of a piece of content is
always visible in one place via get_log().
"""

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "provenance_guard.db"

_local = threading.local()


def _get_conn():
    """One SQLite connection per thread (Flask's dev server is multi-threaded)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def init_db():
    conn = _get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            content_id        TEXT PRIMARY KEY,
            creator_id         TEXT NOT NULL,
            text               TEXT NOT NULL,
            timestamp          TEXT NOT NULL,
            llm_score          REAL,
            stylometric_score  REAL,
            combined_score     REAL,
            confidence         REAL,
            attribution        TEXT,
            label              TEXT,
            status             TEXT NOT NULL DEFAULT 'classified',
            appeal_reasoning   TEXT,
            appeal_timestamp   TEXT
        )
        """
    )
    conn.commit()


def log_submission(entry: dict) -> None:
    """Write a brand-new attribution decision to the audit log."""
    conn = _get_conn()
    conn.execute(
        """
        INSERT INTO submissions (
            content_id, creator_id, text, timestamp,
            llm_score, stylometric_score, combined_score,
            confidence, attribution, label, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry["content_id"],
            entry["creator_id"],
            entry["text"],
            entry["timestamp"],
            entry["llm_score"],
            entry["stylometric_score"],
            entry["combined_score"],
            entry["confidence"],
            entry["attribution"],
            entry["label"],
            entry.get("status", "classified"),
        ),
    )
    conn.commit()


def get_entry(content_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
    ).fetchone()
    return dict(row) if row else None


def log_appeal(content_id: str, creator_reasoning: str) -> dict | None:
    """Update an existing row in place: status -> under_review, plus the
    creator's reasoning and an appeal timestamp, kept alongside the original
    decision rather than in a separate table.
    """
    conn = _get_conn()
    existing = get_entry(content_id)
    if existing is None:
        return None

    appeal_timestamp = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE submissions
        SET status = 'under_review',
            appeal_reasoning = ?,
            appeal_timestamp = ?
        WHERE content_id = ?
        """,
        (creator_reasoning, appeal_timestamp, content_id),
    )
    conn.commit()
    return get_entry(content_id)


def get_log(limit: int = 50) -> list[dict]:
    """Return the most recent audit log entries, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM submissions ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
