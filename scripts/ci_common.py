"""Utilitaires partagés par les scripts du pipeline (annotations GitHub, résumé, JSON)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def in_github_actions() -> bool:
    return os.getenv("GITHUB_ACTIONS") == "true"


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(
    level: str,
    message: str,
    title: str | None = None,
    file: str | None = None,
    line: int | None = None,
) -> None:
    """Affiche une annotation GitHub (::warning:: / ::error:: / ::notice::) ou un log lisible."""
    if in_github_actions():
        props = []
        if title:
            props.append(f"title={_escape(title).replace(',', '%2C').replace(':', '%3A')}")
        if file:
            props.append(f"file={file}")
        if line:
            props.append(f"line={line}")
        suffix = " " + ",".join(props) if props else ""
        print(f"::{level}{suffix}::{_escape(message)}")
    else:
        prefix = {"error": "[CRITIQUE]", "warning": "[WARNING]", "notice": "[INFO]"}.get(level, "")
        location = f" ({file}:{line})" if file else ""
        print(f"{prefix} {title + ' - ' if title else ''}{message}{location}")


def append_summary(markdown: str) -> None:
    """Ajoute du Markdown au résumé du job (GITHUB_STEP_SUMMARY), s'il existe."""
    target = os.getenv("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(markdown.rstrip() + "\n\n")


def load_json(path: str | Path) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
