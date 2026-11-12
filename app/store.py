"""Stockage en mémoire, thread-safe (suffisant pour un environnement de test éphémère)."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.masking import mask_pan
from app.schemas import Account, Payment, Transaction


class StoreError(Exception):
    """Erreur métier convertie en réponse HTTP par l'API."""

    status_code = 400


class NotFoundError(StoreError):
    status_code = 404


class BusinessRuleError(StoreError):
    status_code = 409


class CurrencyMismatchError(StoreError):
    status_code = 422


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class InMemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accounts: dict[str, tuple[str, Account]] = {}
        self._transactions: list[tuple[str, Transaction]] = []

    def create_account(
        self, owner: str, holder_name: str, currency: str, initial_balance: Decimal
    ) -> Account:
        account = Account(
            id=_new_id("acc"),
            holder_name=holder_name,
            currency=currency,
            balance=initial_balance,
            created_at=_now(),
        )
        with self._lock:
            self._accounts[account.id] = (owner, account)
            if initial_balance > 0:
                self._record(owner, account, "credit", initial_balance, "initial deposit")
        return account

    def list_accounts(self, owner: str) -> list[Account]:
        with self._lock:
            return [acc for acc_owner, acc in self._accounts.values() if acc_owner == owner]

    def _get_owned(self, owner: str, account_id: str) -> Account:
        entry = self._accounts.get(account_id)
        if entry is None or entry[0] != owner:
            raise NotFoundError(f"account {account_id} not found")
        return entry[1]

    def _record(
        self, owner: str, account: Account, kind: str, amount: Decimal, description: str
    ) -> Transaction:
        txn = Transaction(
            id=_new_id("txn"),
            account_id=account.id,
            type=kind,
            amount=amount,
            currency=account.currency,
            description=description,
            balance_after=account.balance,
            created_at=_now(),
        )
        self._transactions.append((owner, txn))
        return txn

    def create_payment(
        self,
        owner: str,
        account_id: str,
        amount: Decimal,
        currency: str,
        merchant: str,
        card_number: str,
    ) -> Payment:
        with self._lock:
            account = self._get_owned(owner, account_id)
            if account.currency != currency:
                raise CurrencyMismatchError(
                    f"payment currency {currency} does not match "
                    f"account currency {account.currency}"
                )
            if account.balance < amount:
                raise BusinessRuleError("insufficient funds")
            account.balance -= amount
            txn = self._record(owner, account, "debit", amount, f"payment to {merchant}")
        return Payment(
            id=txn.id,
            account_id=account.id,
            amount=amount,
            currency=account.currency,
            merchant=merchant,
            card_number=mask_pan(card_number),
            status="completed",
            created_at=txn.created_at,
        )

    def list_transactions(
        self, owner: str, account_id: str | None = None, limit: int = 50
    ) -> list[Transaction]:
        with self._lock:
            if account_id is not None:
                self._get_owned(owner, account_id)
            items = [
                txn
                for txn_owner, txn in self._transactions
                if txn_owner == owner and (account_id is None or txn.account_id == account_id)
            ]
        return list(reversed(items))[:limit]
