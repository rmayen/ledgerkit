import pytest
from fastapi.testclient import TestClient

from ledgerkit.api import create_app
from ledgerkit.db import connect


@pytest.fixture
def client():
    app = create_app(connect(":memory:"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def funded(client):
    """Three USD accounts: a bank-style 'external' account that may go negative, and two wallets with $100 and $0."""
    client.post("/accounts", json={"id": "external", "name": "External funding", "currency": "usd", "allow_negative": True})
    client.post("/accounts", json={"id": "alice", "name": "Alice wallet", "currency": "USD"})
    client.post("/accounts", json={"id": "bob", "name": "Bob wallet", "currency": "USD"})
    r = client.post("/transfers", json={"from_account": "external", "to_account": "alice", "amount": 10000, "description": "deposit"})
    assert r.status_code == 201
    return client
