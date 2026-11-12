from __future__ import annotations

import io
import logging

import pytest
from fastapi.testclient import TestClient

from app.masking import PanRedactingFilter, luhn_valid, mask_pan, redact_text
from tests.helpers import break_luhn, make_pan


def test_luhn_accepts_generated_numbers() -> None:
    for prefix, length in (("400000", 16), ("510000", 16), ("340000", 15), ("601100", 16)):
        assert luhn_valid(make_pan(prefix, length))


def test_luhn_rejects_bad_check_digit_and_bad_length() -> None:
    assert not luhn_valid(break_luhn(make_pan()))
    assert not luhn_valid("42")


@pytest.mark.parametrize(("value", "expected"), [("12", "****"), ("98765", "**** **** **** 8765")])
def test_mask_pan(value: str, expected: str) -> None:
    assert mask_pan(value) == expected


def test_redact_text_masks_raw_and_spaced_pan() -> None:
    pan = make_pan()
    spaced = " ".join(pan[i : i + 4] for i in range(0, 16, 4))
    text = f"card={pan} other={spaced} order=12345"
    redacted = redact_text(text)
    assert pan not in redacted
    assert spaced not in redacted
    assert redacted.count(f"**** **** **** {pan[-4:]}") == 2
    assert "order=12345" in redacted


def test_log_filter_redacts_message_and_args() -> None:
    pan = make_pan()
    record = logging.LogRecord("payflex", logging.INFO, __file__, 1, "pan %s", (pan,), None)
    assert PanRedactingFilter().filter(record) is True
    assert pan not in record.getMessage()


def test_application_logs_never_contain_clear_pan(
    client: TestClient, auth_headers: dict, account: dict
) -> None:
    pan = make_pan()
    logger = logging.getLogger("payflex")
    handler = logger.handlers[0]  # handler réel configuré par l'application
    buffer = io.StringIO()
    previous = handler.setStream(buffer)
    try:
        logger.info("tentative de log brut %s", pan)
        client.post(
            "/payments",
            json={
                "account_id": account["id"],
                "amount": "5.00",
                "currency": "EUR",
                "merchant": "Test",
                "card_number": pan,
            },
            headers=auth_headers,
        )
    finally:
        handler.setStream(previous)
    out = buffer.getvalue()
    assert "paiement" in out
    assert pan not in out
