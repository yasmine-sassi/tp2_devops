"""Routes HTTP de PayFlex API."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app import __version__
from app.config import Settings
from app.masking import mask_pan
from app.schemas import (
    Account,
    AccountCreate,
    Payment,
    PaymentCreate,
    TokenRequest,
    TokenResponse,
    Transaction,
)
from app.security import check_credentials, create_token, current_user
from app.store import InMemoryStore

logger = logging.getLogger("payflex")
router = APIRouter()


async def get_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_store(request: Request) -> InMemoryStore:
    return request.app.state.store


User = Annotated[str, Depends(current_user)]
Store = Annotated[InMemoryStore, Depends(get_store)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"])
async def issue_token(body: TokenRequest, settings: AppSettings) -> TokenResponse:
    if not check_credentials(settings, body.username, body.password):
        logger.warning("échec d'authentification pour l'utilisateur %s", body.username)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return TokenResponse(
        access_token=create_token(settings, body.username),
        expires_in=settings.token_ttl_seconds,
    )


@router.get("/accounts", response_model=list[Account], tags=["accounts"])
async def list_accounts(user: User, db: Store) -> list[Account]:
    return db.list_accounts(user)


@router.post(
    "/accounts", response_model=Account, status_code=status.HTTP_201_CREATED, tags=["accounts"]
)
async def create_account(body: AccountCreate, user: User, db: Store) -> Account:
    account = db.create_account(user, body.holder_name, body.currency, body.initial_balance)
    logger.info("compte créé %s (%s)", account.id, account.currency)
    return account


@router.post(
    "/payments", response_model=Payment, status_code=status.HTTP_201_CREATED, tags=["payments"]
)
async def create_payment(body: PaymentCreate, user: User, db: Store) -> Payment:
    payment = db.create_payment(
        user, body.account_id, body.amount, body.currency, body.merchant, body.card_number
    )
    logger.info(
        "paiement %s compte=%s montant=%s %s carte=%s",
        payment.id,
        payment.account_id,
        payment.amount,
        payment.currency,
        mask_pan(body.card_number),
    )
    return payment


@router.get("/transactions", response_model=list[Transaction], tags=["payments"])
async def list_transactions(
    user: User,
    db: Store,
    account_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[Transaction]:
    return db.list_transactions(user, account_id, limit)
