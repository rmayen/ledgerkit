# ledgerkit

A small double-entry ledger service: accounts, transfers, multi-leg journal entries, idempotent writes,
overdraft protection, statements with running balances, and a reconciliation endpoint that proves the
books balance. FastAPI + SQLite, no ORM.

I built it to understand what actually makes money movement correct: integer minor units, balanced entries,
one write lock around the check-then-insert, and a key that makes retries safe.

## Guarantees

- **Every entry sums to zero.** A journal entry is two or more postings in signed minor units. If they do
  not net to zero the request is a `422` and nothing is written. `/reconcile` re-verifies this over the whole ledger.
- **No silent overdrafts.** An account without `allow_negative` cannot go below zero. The check and the insert
  happen inside one `BEGIN IMMEDIATE` transaction, so two concurrent transfers cannot both pass the check.
- **Retries are safe.** Send `Idempotency-Key: <key>` on `POST /transfers` or `POST /entries`. The same key with
  the same request returns the original entry with `200` and charges nothing again. The same key with a
  different request is a `409`.
- **Money is integers.** Amounts are minor units (cents) with a three-letter currency. Entries cannot mix currencies.
- **Nothing is deleted.** Mistakes are corrected with a new reversing entry, so the audit trail is complete.

## Run it

```bash
pip install -e ".[dev]"
uvicorn ledgerkit.api:app --reload      # http://localhost:8000, interactive docs at /docs
pytest -q                                # 20 tests
```

Set `LEDGER_DB=/path/to/ledger.db` to choose the database file (default `ledger.db` in the working directory).

## Walkthrough

```bash
H='Content-Type: application/json'
curl -X POST localhost:8000/accounts -H "$H" -d '{"id":"bank","name":"Bank","currency":"USD","allow_negative":true}'
curl -X POST localhost:8000/accounts -H "$H" -d '{"id":"alice","name":"Alice wallet","currency":"USD"}'
curl -X POST localhost:8000/accounts -H "$H" -d '{"id":"bob","name":"Bob wallet","currency":"USD"}'

# fund Alice with $50.00; the key makes this safe to retry
curl -X POST localhost:8000/transfers -H "$H" -H 'Idempotency-Key: dep-001' \
  -d '{"from_account":"bank","to_account":"alice","amount":5000,"description":"deposit"}'

# Alice pays Bob $12.50
curl -X POST localhost:8000/transfers -H "$H" -d '{"from_account":"alice","to_account":"bob","amount":1250}'

# a split payment: $30 from Alice, $27 to Bob, $3 platform fee
curl -X POST localhost:8000/entries -H "$H" -d '{"description":"payment + fee","postings":[
  {"account_id":"alice","amount":-3000},{"account_id":"bob","amount":2700},{"account_id":"fees","amount":300}]}'

curl localhost:8000/accounts/alice            # {"id":"alice", ..., "balance":750}
curl localhost:8000/accounts/alice/entries    # statement, newest first, with balance_after on each line
curl localhost:8000/reconcile                 # {"ok":true, "net_by_currency":{"USD":0}, ...}
```

An overdraft attempt:

```
POST /transfers {"from_account":"alice","to_account":"bob","amount":99999}
422 {"error":"account 'alice' balance 750 is insufficient for 99999 (would be -99249)","type":"InsufficientFunds"}
```

## API

| Method | Path                             | Purpose                                                     |
| ------ | -------------------------------- | ----------------------------------------------------------- |
| POST   | `/accounts`                      | create (`id`, `name`, `currency`, `allow_negative`)         |
| GET    | `/accounts/{id}`                 | account with current `balance`                              |
| GET    | `/accounts/{id}/entries`         | statement, newest first; `limit`, `before_id` cursor        |
| POST   | `/transfers`                     | two-leg entry; `Idempotency-Key` header optional            |
| POST   | `/entries`                       | n-leg entry; postings must sum to zero                      |
| GET    | `/entries/{id}`                  | entry with postings                                         |
| GET    | `/reconcile`                     | unbalanced entries, overdrawn accounts, net by currency     |
| GET    | `/health`                        |                                                             |

Errors are `{"error": "...", "type": "NotFound" | "Conflict" | "Unprocessable" | "InsufficientFunds"}` with
`404`, `409`, `422`, `422` respectively. Request-shape errors from validation are FastAPI's standard `422`.

## Tests

20 tests exercise: account creation and validation, transfers and balances, exact-balance and overdraft cases,
`allow_negative`, cross-currency rejection, idempotent replay and key conflict, multi-leg entries, net-effect
checking when one account appears twice, statement pagination with running balances, a clean reconcile, and a
reconcile over deliberately corrupted rows to prove it catches both an unbalanced entry and an overdrawn account.

## Layout

```
src/ledgerkit/
  db.py       schema + connection (WAL, foreign keys)
  ledger.py   all ledger logic and invariants; raises typed LedgerError subclasses
  api.py      FastAPI routes and error mapping; create_app(conn) for tests
tests/        conftest.py (in-memory app + funded fixture), test_accounts, test_transfers, test_entries, test_statement_and_reconcile
```

## Limitations

- Single SQLite file, single process. The write lock gives correctness, not throughput.
- No authentication; this is the ledger core, not the public edge.
- Idempotency keys are global, not per client. A real deployment would scope them to the caller.
