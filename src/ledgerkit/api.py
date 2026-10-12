"""HTTP layer. Thin: parse, call the ledger, map errors to status codes."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ledger
from .db import connect


class AccountIn(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    allow_negative: bool = False


class TransferIn(BaseModel):
    from_account: str
    to_account: str
    amount: int = Field(gt=0, description="minor units, e.g. cents")
    description: str = Field(default="", max_length=240)


class PostingIn(BaseModel):
    account_id: str
    amount: int = Field(description="signed minor units; the entry must sum to zero")


class EntryIn(BaseModel):
    postings: list[PostingIn] = Field(min_length=2)
    description: str = Field(default="", max_length=240)


def create_app(conn: sqlite3.Connection | None = None) -> FastAPI:
    """Build the app around a connection. Tests pass an in-memory connection; production uses LEDGER_DB."""
    db = conn or connect(os.environ.get("LEDGER_DB", "ledger.db"))
    app = FastAPI(title="ledgerkit", version="0.1.0")

    def get_db() -> sqlite3.Connection:
        return db

    DB = Depends(get_db)
    IDEM = Header(default=None, alias="Idempotency-Key", max_length=128)

    @app.exception_handler(ledger.LedgerError)
    async def ledger_error(_req: Request, exc: ledger.LedgerError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content={"error": str(exc), "type": type(exc).__name__})

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/accounts", status_code=201)
    def create_account(body: AccountIn, db: sqlite3.Connection = DB) -> dict[str, Any]:
        return ledger.create_account(db, body.id, body.name, body.currency, body.allow_negative)

    @app.get("/accounts/{account_id}")
    def get_account(account_id: str, db: sqlite3.Connection = DB) -> dict[str, Any]:
        return ledger.get_account(db, account_id)

    @app.get("/accounts/{account_id}/entries")
    def account_entries(
        account_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        before_id: int | None = Query(default=None, ge=1),
        db: sqlite3.Connection = DB,
    ) -> list[dict[str, Any]]:
        return ledger.account_entries(db, account_id, limit=limit, before_id=before_id)

    @app.post("/transfers")
    def create_transfer(body: TransferIn, db: sqlite3.Connection = DB, idempotency_key: str | None = IDEM) -> JSONResponse:
        entry, created = ledger.transfer(
            db, body.from_account, body.to_account, body.amount, body.description, idempotency_key
        )
        return JSONResponse(status_code=201 if created else 200, content=entry)

    @app.post("/entries")
    def create_entry(body: EntryIn, db: sqlite3.Connection = DB, idempotency_key: str | None = IDEM) -> JSONResponse:
        postings = [ledger.Posting(p.account_id, p.amount) for p in body.postings]
        entry, created = ledger.post_entry(db, postings, body.description, idempotency_key)
        return JSONResponse(status_code=201 if created else 200, content=entry)

    @app.get("/entries/{entry_id}")
    def get_entry(entry_id: int, db: sqlite3.Connection = DB) -> dict[str, Any]:
        return ledger.get_entry(db, entry_id)

    @app.get("/reconcile")
    def reconcile(db: sqlite3.Connection = DB) -> dict[str, Any]:
        return ledger.reconcile(db)

    return app


app = create_app()
