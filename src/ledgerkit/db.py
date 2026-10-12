"""SQLite connection and schema.

Money is stored as integers in minor units (cents) with a currency code. Floats never touch a balance.
Every journal entry is a set of postings whose signed amounts sum to zero; that invariant is checked in
Python inside the same transaction that writes the rows, and re-checked by /reconcile.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  currency       TEXT NOT NULL CHECK (length(currency) = 3),
  allow_negative INTEGER NOT NULL DEFAULT 0 CHECK (allow_negative IN (0, 1)),
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS entries (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  idempotency_key TEXT UNIQUE,
  request_hash    TEXT,
  description     TEXT NOT NULL DEFAULT '',
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS postings (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id   INTEGER NOT NULL REFERENCES entries(id),
  account_id TEXT    NOT NULL REFERENCES accounts(id),
  amount     INTEGER NOT NULL CHECK (amount != 0),
  currency   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_postings_account ON postings(account_id, id);
CREATE INDEX IF NOT EXISTS idx_postings_entry   ON postings(entry_id);
"""


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    return conn
