"""Ledger operations. No HTTP here; everything takes a connection and raises LedgerError subclasses."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Any


class LedgerError(Exception):
    status = 400


class NotFound(LedgerError):
    status = 404


class Conflict(LedgerError):
    status = 409


class Unprocessable(LedgerError):
    status = 422


class InsufficientFunds(Unprocessable):
    pass


@dataclass(frozen=True)
class Posting:
    account_id: str
    amount: int  # minor units; positive increases the account balance, negative decreases it


def create_account(conn: sqlite3.Connection, id: str, name: str, currency: str, allow_negative: bool = False) -> dict[str, Any]:
    currency = currency.upper()
    try:
        conn.execute(
            "INSERT INTO accounts (id, name, currency, allow_negative) VALUES (?, ?, ?, ?)",
            (id, name, currency, int(allow_negative)),
        )
    except sqlite3.IntegrityError as e:
        raise Conflict(f"account '{id}' already exists") from e
    return get_account(conn, id)


def get_account(conn: sqlite3.Connection, id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (id,)).fetchone()
    if row is None:
        raise NotFound(f"account '{id}' not found")
    d = dict(row)
    d["allow_negative"] = bool(d["allow_negative"])
    d["balance"] = balance(conn, id)
    return d


def balance(conn: sqlite3.Connection, account_id: str) -> int:
    row = conn.execute("SELECT COALESCE(SUM(amount), 0) AS b FROM postings WHERE account_id = ?", (account_id,)).fetchone()
    return int(row["b"])


def _request_hash(postings: list[Posting], description: str) -> str:
    payload = json.dumps({"d": description, "p": [[p.account_id, p.amount] for p in postings]}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def get_entry(conn: sqlite3.Connection, entry_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT id, idempotency_key, description, created_at FROM entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotFound(f"entry {entry_id} not found")
    postings = conn.execute(
        "SELECT account_id, amount, currency FROM postings WHERE entry_id = ? ORDER BY id", (entry_id,)
    ).fetchall()
    return {**dict(row), "postings": [dict(p) for p in postings]}


def post_entry(
    conn: sqlite3.Connection,
    postings: list[Posting],
    description: str = "",
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """Write one balanced journal entry atomically.

    Returns (entry, created). If the idempotency key was seen before with the same request, the stored
    entry is returned with created=False. The same key with a different request is a 409.
    """
    if len(postings) < 2:
        raise Unprocessable("an entry needs at least two postings")
    if any(p.amount == 0 for p in postings):
        raise Unprocessable("posting amounts must be non-zero")
    if sum(p.amount for p in postings) != 0:
        raise Unprocessable(f"postings must sum to zero (got {sum(p.amount for p in postings)})")

    req_hash = _request_hash(postings, description)

    # BEGIN IMMEDIATE takes the write lock up front, so the balance checks below cannot be invalidated
    # by a concurrent writer between the read and the insert.
    conn.execute("BEGIN IMMEDIATE")
    try:
        if idempotency_key is not None:
            prior = conn.execute(
                "SELECT id, request_hash FROM entries WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if prior is not None:
                if prior["request_hash"] != req_hash:
                    raise Conflict(f"idempotency key '{idempotency_key}' was already used for a different request")
                conn.execute("COMMIT")
                return get_entry(conn, prior["id"]), False

        accounts: dict[str, sqlite3.Row] = {}
        for p in postings:
            if p.account_id not in accounts:
                row = conn.execute("SELECT * FROM accounts WHERE id = ?", (p.account_id,)).fetchone()
                if row is None:
                    raise NotFound(f"account '{p.account_id}' not found")
                accounts[p.account_id] = row
        currencies = {accounts[p.account_id]["currency"] for p in postings}
        if len(currencies) != 1:
            raise Unprocessable(f"all postings in an entry must share one currency (got {', '.join(sorted(currencies))})")
        currency = currencies.pop()

        # Net effect per account, so an entry touching the same account twice is checked once.
        net: dict[str, int] = {}
        for p in postings:
            net[p.account_id] = net.get(p.account_id, 0) + p.amount
        for account_id, delta in net.items():
            if delta < 0 and not accounts[account_id]["allow_negative"]:
                current = balance(conn, account_id)
                if current + delta < 0:
                    raise InsufficientFunds(
                        f"account '{account_id}' balance {current} is insufficient for {-delta} (would be {current + delta})"
                    )

        cur = conn.execute(
            "INSERT INTO entries (idempotency_key, request_hash, description) VALUES (?, ?, ?)",
            (idempotency_key, req_hash, description),
        )
        entry_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO postings (entry_id, account_id, amount, currency) VALUES (?, ?, ?, ?)",
            [(entry_id, p.account_id, p.amount, currency) for p in postings],
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get_entry(conn, entry_id), True


def transfer(
    conn: sqlite3.Connection,
    from_account: str,
    to_account: str,
    amount: int,
    description: str = "",
    idempotency_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if amount <= 0:
        raise Unprocessable("transfer amount must be positive")
    if from_account == to_account:
        raise Unprocessable("cannot transfer an account to itself")
    return post_entry(
        conn,
        [Posting(from_account, -amount), Posting(to_account, amount)],
        description=description,
        idempotency_key=idempotency_key,
    )


def account_entries(conn: sqlite3.Connection, account_id: str, limit: int = 50, before_id: int | None = None) -> list[dict[str, Any]]:
    get_account(conn, account_id)
    sql = """
        SELECT e.id, e.description, e.created_at, p.amount, p.currency
        FROM postings p JOIN entries e ON e.id = p.entry_id
        WHERE p.account_id = ? {cursor}
        ORDER BY e.id DESC LIMIT ?
    """
    params: list[Any] = [account_id]
    cursor = ""
    if before_id is not None:
        cursor = "AND e.id < ?"
        params.append(before_id)
    params.append(limit)
    rows = conn.execute(sql.format(cursor=cursor), params).fetchall()
    # Running balance is computed newest-first, so walk from the oldest shown row back up.
    out = [dict(r) for r in rows]
    running = balance(conn, account_id)
    if before_id is not None:
        newer = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM postings WHERE account_id = ? AND entry_id >= ?", (account_id, before_id)
        ).fetchone()["s"]
        running -= int(newer)
    for r in out:
        r["balance_after"] = running
        running -= r["amount"]
    return out


def reconcile(conn: sqlite3.Connection) -> dict[str, Any]:
    """Prove the books balance. Every entry must sum to zero and so must the whole ledger."""
    unbalanced = [
        dict(r)
        for r in conn.execute(
            "SELECT entry_id, SUM(amount) AS total FROM postings GROUP BY entry_id HAVING SUM(amount) != 0"
        ).fetchall()
    ]
    overdrawn = [
        dict(r)
        for r in conn.execute(
            """SELECT a.id AS account_id, COALESCE(SUM(p.amount), 0) AS balance
               FROM accounts a LEFT JOIN postings p ON p.account_id = a.id
               WHERE a.allow_negative = 0 GROUP BY a.id HAVING COALESCE(SUM(p.amount), 0) < 0"""
        ).fetchall()
    ]
    totals = conn.execute(
        "SELECT COUNT(*) AS entries, COALESCE(SUM(amount), 0) AS net FROM postings"
    ).fetchone()
    by_currency = {
        r["currency"]: int(r["net"])
        for r in conn.execute("SELECT currency, SUM(amount) AS net FROM postings GROUP BY currency").fetchall()
    }
    ok = not unbalanced and not overdrawn and all(v == 0 for v in by_currency.values())
    return {
        "ok": ok,
        "accounts": conn.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"],
        "entries": conn.execute("SELECT COUNT(*) AS n FROM entries").fetchone()["n"],
        "postings": int(totals["entries"]),
        "net_by_currency": by_currency,
        "unbalanced_entries": unbalanced,
        "overdrawn_accounts": overdrawn,
    }
