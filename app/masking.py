"""Masquage des numéros de carte (PAN) dans les réponses et les logs."""

from __future__ import annotations

import logging
import re

# Toute séquence de 13 à 19 chiffres (séparateurs espace/tiret tolérés) est traitée
# comme un PAN potentiel : on préfère sur-masquer plutôt que fuiter.
PAN_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")


def digits_only(value: str) -> str:
    return re.sub(r"\D", "", value)


def luhn_valid(number: str) -> bool:
    digits = digits_only(number)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        digit = int(char)
        if index % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def mask_pan(pan: str) -> str:
    """Ne conserve que les 4 derniers chiffres : « **** **** **** 1234 »."""
    digits = digits_only(pan)
    if len(digits) < 4:
        return "****"
    return f"**** **** **** {digits[-4:]}"


def redact_text(text: str) -> str:
    return PAN_PATTERN.sub(lambda match: mask_pan(match.group(0)), text)


class PanRedactingFilter(logging.Filter):
    """Filtre de logging : réécrit chaque message pour masquer les PAN."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        return True
