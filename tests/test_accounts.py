def test_create_and_get_account(client):
    r = client.post("/accounts", json={"id": "acct-1", "name": "Main", "currency": "usd"})
    assert r.status_code == 201
    assert r.json() == {**r.json(), "id": "acct-1", "currency": "USD", "allow_negative": False, "balance": 0}
    assert client.get("/accounts/acct-1").json()["balance"] == 0


def test_duplicate_account_is_409(client):
    body = {"id": "a", "name": "A", "currency": "USD"}
    assert client.post("/accounts", json=body).status_code == 201
    r = client.post("/accounts", json=body)
    assert r.status_code == 409 and "already exists" in r.json()["error"]


def test_unknown_account_is_404(client):
    assert client.get("/accounts/nope").status_code == 404
    assert client.get("/accounts/nope/entries").status_code == 404


def test_account_validation(client):
    assert client.post("/accounts", json={"id": "bad id!", "name": "x", "currency": "USD"}).status_code == 422
    assert client.post("/accounts", json={"id": "ok", "name": "x", "currency": "US"}).status_code == 422
