"""Émission et vérification des jetons d'accès (JWT HS256)."""

from __future__ import annotations

import hmac
import time
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


def check_credentials(settings: Settings, username: str, password: str) -> bool:
    if settings.demo_password is None:
        return False
    user_ok = hmac.compare_digest(username.encode(), settings.demo_user.encode())
    password_ok = hmac.compare_digest(password.encode(), settings.demo_password.encode())
    return user_ok and password_ok


def create_token(settings: Settings, subject: str) -> str:
    now = int(time.time())
    claims = {"sub": subject, "iat": now, "exp": now + settings.token_ttl_seconds}
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(settings: Settings, token: str) -> str:
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc
    return str(claims["sub"])


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_token(request.app.state.settings, credentials.credentials)
