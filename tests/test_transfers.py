def balance(client, acct):
    return client.get(f"/accounts/{acct}").json()["balance"]


def test_transfer_moves_money_and_balances(funded):
    r = funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 2550, "description": "lunch"})
    assert r.status_code == 201
    entry = r.json()
    assert entry["description"] == "lunch"
    assert [(p["account_id"], p["amount"]) for p in entry["postings"]] == [("alice", -2550), ("bob", 2550)]
    assert balance(funded, "alice") == 7450
    assert balance(funded, "bob") == 2550
    assert balance(funded, "external") == -10000


def test_overdraft_is_rejected_and_nothing_is_written(funded):
    r = funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 10001})
    assert r.status_code == 422
    assert r.json()["type"] == "InsufficientFunds"
    assert "balance 10000 is insufficient for 10001" in r.json()["error"]
    assert balance(funded, "alice") == 10000 and balance(funded, "bob") == 0
    assert funded.get("/reconcile").json()["entries"] == 1  # only the deposit


def test_exact_balance_transfer_is_allowed(funded):
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 10000}).status_code == 201
    assert balance(funded, "alice") == 0


def test_allow_negative_account_can_go_below_zero(funded):
    assert funded.post("/transfers", json={"from_account": "external", "to_account": "bob", "amount": 5}).status_code == 201
    assert balance(funded, "external") == -10005


def test_transfer_input_rules(funded):
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 0}).status_code == 422
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": -5}).status_code == 422
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "alice", "amount": 5}).status_code == 422
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "ghost", "amount": 5}).status_code == 404


def test_cross_currency_transfer_is_rejected(funded):
    funded.post("/accounts", json={"id": "euro", "name": "Euro wallet", "currency": "EUR"})
    r = funded.post("/transfers", json={"from_account": "alice", "to_account": "euro", "amount": 100})
    assert r.status_code == 422 and "one currency" in r.json()["error"]


def test_idempotent_replay_returns_same_entry_once(funded):
    body = {"from_account": "alice", "to_account": "bob", "amount": 1000, "description": "rent"}
    first = funded.post("/transfers", json=body, headers={"Idempotency-Key": "rent-oct"})
    second = funded.post("/transfers", json=body, headers={"Idempotency-Key": "rent-oct"})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json() == second.json()
    assert balance(funded, "bob") == 1000  # charged exactly once


def test_idempotency_key_reuse_with_different_request_is_409(funded):
    headers = {"Idempotency-Key": "k1"}
    assert funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 100}, headers=headers).status_code == 201
    r = funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 101}, headers=headers)
    assert r.status_code == 409 and "different request" in r.json()["error"]
    assert balance(funded, "bob") == 100


def test_entry_lookup(funded):
    entry = funded.post("/transfers", json={"from_account": "alice", "to_account": "bob", "amount": 1}).json()
    assert funded.get(f"/entries/{entry['id']}").json() == entry
    assert funded.get("/entries/999").status_code == 404
