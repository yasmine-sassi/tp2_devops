"""Contrats d'API (Pydantic v2)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.masking import luhn_valid

Currency = Literal["EUR", "USD", "TND"]


class TokenRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - type OAuth2, pas un secret
    expires_in: int


class AccountCreate(BaseModel):
    holder_name: str = Field(min_length=1, max_length=100)
    currency: Currency
    initial_balance: Decimal = Field(default=Decimal("0"), ge=0, max_digits=14, decimal_places=2)


class Account(BaseModel):
    id: str
    holder_name: str
    currency: Currency
    balance: Decimal
    created_at: datetime


class PaymentCreate(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    amount: Decimal = Field(gt=0, le=Decimal("100000"), max_digits=12, decimal_places=2)
    currency: Currency
    merchant: str = Field(min_length=1, max_length=100)
    card_number: str = Field(min_length=13, max_length=23, pattern=r"^[0-9 -]+$")

    @field_validator("card_number")
    @classmethod
    def card_must_pass_luhn(cls, value: str) -> str:
        if not luhn_valid(value):
            raise ValueError("invalid card number")
        return value


class Payment(BaseModel):
    id: str
    account_id: str
    amount: Decimal
    currency: Currency
    merchant: str
    card_number: str = Field(description="PAN masqué : seuls les 4 derniers chiffres sont visibles")
    status: Literal["completed"]
    created_at: datetime


class Transaction(BaseModel):
    id: str
    account_id: str
    type: Literal["credit", "debit"]
    amount: Decimal
    currency: Currency
    description: str
    balance_after: Decimal
    created_at: datetime
