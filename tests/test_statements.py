from __future__ import annotations

import pytest

from app.statements import categorize


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("payment to Monoprix", "grocery"),
        ("payment to Uber", "transport"),
        ("initial deposit", "deposit"),
        ("payment to Unknown Ltd", "other"),
    ],
)
def test_categorize(description: str, expected: str) -> None:
    assert categorize(description) == expected
