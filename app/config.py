"""Configuration chargée exclusivement depuis l'environnement (aucun secret en dur)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    jwt_secret: str
    token_ttl_seconds: int
    demo_user: str
    demo_password: str | None
    simulated_latency_ms: int


def load_settings() -> Settings:
    """Construit la configuration à partir des variables d'environnement.

    - PAYFLEX_JWT_SECRET : clé de signature des jetons (aléatoire par processus si absente).
    - PAYFLEX_DEMO_USER / PAYFLEX_DEMO_PASSWORD : identifiants du client de démonstration.
      Sans mot de passe défini, toute authentification est refusée.
    - SIMULATED_LATENCY_MS : latence artificielle (scénario de démo « perf-fail »).
    """
    return Settings(
        jwt_secret=os.getenv("PAYFLEX_JWT_SECRET") or secrets.token_urlsafe(32),
        token_ttl_seconds=int(os.getenv("PAYFLEX_TOKEN_TTL", "900")),
        demo_user=os.getenv("PAYFLEX_DEMO_USER", "demo"),
        demo_password=os.getenv("PAYFLEX_DEMO_PASSWORD") or None,
        simulated_latency_ms=max(0, int(os.getenv("SIMULATED_LATENCY_MS", "0"))),
    )
