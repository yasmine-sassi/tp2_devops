"""Outils de test.

Aucun numéro de carte n'est écrit en clair dans le dépôt : les PAN de test sont
générés à l'exécution (préfixe + chiffre de contrôle de Luhn). Le scanner Gitleaks
reste ainsi strict sur la branche principale.
"""

from __future__ import annotations


def luhn_check_digit(partial: str) -> str:
    total = 0
    for index, char in enumerate(reversed(partial)):
        digit = int(char)
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str((10 - total % 10) % 10)


def make_pan(prefix: str = "400000", length: int = 16, fill: str = "0") -> str:
    body = prefix + fill * (length - len(prefix) - 1)
    return body + luhn_check_digit(body)


def break_luhn(pan: str) -> str:
    last = int(pan[-1])
    return pan[:-1] + str((last + 1) % 10)
