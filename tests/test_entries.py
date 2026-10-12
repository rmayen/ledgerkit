def test_multi_leg_entry_splits_a_payment(funded):
    # Alice pays $30: $27 to Bob, $3 to a fees account.
    funded.post("/accounts", json={"id": "fees", "name": "Platform fees", "currency": "USD"})
    r = funded.post("/entries", json={
        "description": "payment with fee",
        "postings": [
            {"account_id": "alice", "amount": -3000},
            {"account_id": "bob", "amount": 2700},
            {"account_id": "fees", "amount": 300},
        ],
    })
    assert r.status_code == 201
    assert funded.get("/accounts/fees").json()["balance"] == 300
    assert funded.get("/accounts/alice").json()["balance"] == 7000


def test_unbalanced_entry_is_rejected(funded):
    r = funded.post("/entries", json={"postings": [{"account_id": "alice", "amount": -100}, {"account_id": "bob", "amount": 99}]})
    assert r.status_code == 422 and "sum to zero" in r.json()["error"]


def test_zero_posting_and_too_few_postings(funded):
    assert funded.post("/entries", json={"postings": [{"account_id": "alice", "amount": 0}, {"account_id": "bob", "amount": 0}]}).status_code == 422
    assert funded.post("/entries", json={"postings": [{"account_id": "alice", "amount": -1}]}).status_code == 422


def test_net_effect_is_checked_per_account(funded):
    # Alice appears twice; her net is -100, which she can afford even though one leg is -10100.
    r = funded.post("/entries", json={"postings": [
        {"account_id": "alice", "amount": -10100},
        {"account_id": "alice", "amount": 10000},
        {"account_id": "bob", "amount": 100},
    ]})
    assert r.status_code == 201
    assert funded.get("/accounts/alice").json()["balance"] == 9900
