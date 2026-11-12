from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import break_luhn, make_pan


def pay(client: TestClient, headers: dict, account_id: str, **overrides) -> object:
    payload = {
        "account_id": account_id,
        "amount": "25.50",
        "currency": "EUR",
        "merchant": "Librairie du Centre",
        "card_number": make_pan(),
    }
    payload.update(overrides)
    return client.post("/payments", json=payload, headers=headers)


def test_create_and_list_accounts(client: TestClient, auth_headers: dict, account: dict) -> None:
    assert account["id"].startswith("acc_")
    assert account["balance"] == "1000.00"
    listed = client.get("/accounts", headers=auth_headers).json()
    assert [acc["id"] for acc in listed] == [account["id"]]


def test_account_validation(client: TestClient, auth_headers: dict) -> None:
    bad = [
        {"holder_name": "", "currency": "EUR"},
        {"holder_name": "Bob", "currency": "GBP"},
        {"holder_name": "Bob", "currency": "EUR", "initial_balance": "-1"},
    ]
    for payload in bad:
        assert client.post("/accounts", json=payload, headers=auth_headers).status_code == 422


def test_account_without_initial_balance_has_no_transaction(
    client: TestClient, auth_headers: dict
) -> None:
    created = client.post(
        "/accounts", json={"holder_name": "Bob", "currency": "USD"}, headers=auth_headers
    ).json()
    assert created["balance"] == "0"
    txns = client.get(
        "/transactions", params={"account_id": created["id"]}, headers=auth_headers
    ).json()
    assert txns == []


def test_payment_debits_account_and_masks_card(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    pan = make_pan()
    response = pay(client, auth_headers, account["id"], card_number=pan)
    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "completed"
    assert body["card_number"] == f"**** **** **** {pan[-4:]}"
    assert pan not in response.text
    balance = client.get("/accounts", headers=auth_headers).json()[0]["balance"]
    assert balance == "974.50"


def test_payment_accepts_spaced_card_number(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    pan = make_pan(prefix="510000")
    spaced = " ".join(pan[i : i + 4] for i in range(0, 16, 4))
    response = pay(client, auth_headers, account["id"], card_number=spaced)
    assert response.status_code == 201
    assert response.json()["card_number"].endswith(pan[-4:])


def test_payment_rejects_invalid_card_without_echoing_it(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    invalid = break_luhn(make_pan())
    response = pay(client, auth_headers, account["id"], card_number=invalid)
    assert response.status_code == 422
    assert invalid not in response.text
    assert "invalid card number" in response.text


def test_payment_unknown_account(client: TestClient, auth_headers: dict) -> None:
    response = pay(client, auth_headers, "acc_does_not_exist")
    assert response.status_code == 404


def test_payment_insufficient_funds(client: TestClient, auth_headers: dict, account: dict) -> None:
    response = pay(client, auth_headers, account["id"], amount="1000.01")
    assert response.status_code == 409
    assert response.json()["detail"] == "insufficient funds"


def test_payment_currency_mismatch(client: TestClient, auth_headers: dict, account: dict) -> None:
    response = pay(client, auth_headers, account["id"], currency="USD")
    assert response.status_code == 422


def test_payment_amount_must_be_positive(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    assert pay(client, auth_headers, account["id"], amount="0").status_code == 422


def test_transactions_history_newest_first_with_limit(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    for amount in ("1.00", "2.00", "3.00"):
        assert pay(client, auth_headers, account["id"], amount=amount).status_code == 201
    txns = client.get("/transactions", headers=auth_headers).json()
    assert [t["type"] for t in txns] == ["debit", "debit", "debit", "credit"]
    assert txns[0]["amount"] == "3.00"
    assert txns[0]["balance_after"] == "994.00"
    limited = client.get("/transactions", params={"limit": 2}, headers=auth_headers).json()
    assert len(limited) == 2


def test_transactions_filter_unknown_account(client: TestClient, auth_headers: dict) -> None:
    response = client.get(
        "/transactions", params={"account_id": "acc_missing"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_accounts_are_isolated_per_user(client: TestClient, settings, account: dict) -> None:
    from app.security import create_token

    other = {"Authorization": f"Bearer {create_token(settings, 'mallory')}"}
    assert client.get("/accounts", headers=other).json() == []
    assert pay(client, other, account["id"]).status_code == 404
