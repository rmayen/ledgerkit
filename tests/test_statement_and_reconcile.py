def test_account_entries_with_running_balance_and_cursor(funded):
    for amt in (100, 200, 300):
        funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": amt})
    rows = funded.get("/accounts/alice/entries").json()
    assert [r["amount"] for r in rows] == [-300, -200, -100, 10000]
    assert [r["balance_after"] for r in rows] == [9400, 9700, 9900, 10000]

    page = funded.get("/accounts/alice/entries", params={"limit": 2}).json()
    assert [r["amount"] for r in page] == [-300, -200]
    nxt = funded.get("/accounts/alice/entries", params={"limit": 2, "before_id": page[-1]["id"]}).json()
    assert [r["amount"] for r in nxt] == [-100, 10000]
    assert [r["balance_after"] for r in nxt] == [9900, 10000]


def test_reconcile_reports_a_balanced_ledger(funded):
    funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 4200})
    rep = funded.get("/reconcile").json()
    assert rep["ok"] is True
    assert rep["accounts"] == 3 and rep["entries"] == 2 and rep["postings"] == 4
    assert rep["net_by_currency"] == {"USD": 0}
    assert rep["unbalanced_entries"] == [] and rep["overdrawn_accounts"] == []


def test_reconcile_catches_corruption(client):
    """Bypass the API and damage the data directly; reconcile must notice."""
    from ledgerkit.db import connect
    from ledgerkit.api import create_app
    from fastapi.testclient import TestClient

    conn = connect(":memory:")
    with TestClient(create_app(conn)) as c:
        c.post("/accounts", json={"id": "a", "name": "A", "currency": "USD", "allow_negative": True})
        c.post("/accounts", json={"id": "b", "name": "B", "currency": "USD"})
        c.post("/transfers", json={"from_account": "a", "to_account": "b", "amount": 500})
        conn.execute("UPDATE postings SET amount = 499 WHERE account_id = 'b'")
        conn.execute("INSERT INTO entries (description) VALUES ('rogue')")
        conn.execute("INSERT INTO postings (entry_id, account_id, amount, currency) VALUES (2, 'b', -1000, 'USD')")
        rep = c.get("/reconcile").json()
        assert rep["ok"] is False
        assert rep["unbalanced_entries"] == [{"entry_id": 1, "total": -1}, {"entry_id": 2, "total": -1000}]
        assert rep["overdrawn_accounts"] == [{"account_id": "b", "balance": -501}]
