#!/usr/bin/env python3
"""Bonus observabilité : exporte les indicateurs du pipeline au format Prometheus.

Lit metrics/*.json et reports/incident_report.json, écrit un fichier texte au format
d'exposition Prometheus et, si --push-url est fourni, le pousse vers une Pushgateway
(groupement job=payflex_pipeline, branch=<branche>) pour visualisation dans Grafana.

    python scripts/export_prometheus.py --output reports/pipeline_metrics.prom \
        --push-url http://localhost:9091
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ci_common import load_json
from quality_gate import git_context, load_metrics

PREFIX = "payflex_pipeline"


def _label_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _optional(path: Path) -> dict | None:
    return load_json(path) if path.is_file() else None


class Series(list):
    """Liste de séries (nom, aide, valeur, labels supplémentaires)."""

    def add(self, name: str, help_text: str, value: float | None, **labels: str) -> None:
        if value is not None:
            self.append((f"{PREFIX}_{name}", help_text, float(value), labels))


def add_tests(out: Series, ut: dict | None, it: dict | None) -> None:
    if ut:
        tests, cov = ut.get("tests") or {}, ut.get("coverage") or {}
        out.add("unit_tests_total", "Nombre de tests unitaires", tests.get("tests"))
        failed = tests.get("failures", 0) + tests.get("errors", 0) if tests else None
        out.add("unit_tests_failed", "Tests unitaires en échec", failed)
        out.add("coverage_percent", "Couverture de code (lignes + branches, %)", cov.get("percent"))
    if it and it.get("assertions"):
        a = it["assertions"]
        out.add("integration_assertions_total", "Assertions Newman", a["tests"])
        out.add(
            "integration_assertions_failed",
            "Assertions Newman en échec",
            a["failures"] + a["errors"],
        )


def add_performance(out: Series, perf: dict | None) -> None:
    if not perf:
        return
    out.add("latency_p95_ms", "Latence p95 sous charge k6 (ms)", perf["p95_ms"])
    out.add("latency_avg_ms", "Latence moyenne sous charge k6 (ms)", perf["avg_ms"])
    out.add(
        "error_rate_percent", "Taux d'erreur HTTP sous charge k6 (%)", perf["error_rate_percent"]
    )
    out.add("load_requests_total", "Requêtes envoyées par k6", perf["requests"])
    out.add("load_rps", "Débit k6 (requêtes/s)", perf["rps"])


def add_quality(out: Series, static: dict | None, sonar: dict | None) -> None:
    if static:
        out.add(
            "blocking_bugs",
            "Bugs bloquants (Bandit HIGH, Ruff F8xx/E9xx)",
            static["blocking_count"],
        )
        out.add("code_smells", "Code smells (Ruff, Bandit LOW/MEDIUM)", static["smells_count"])
    if sonar:
        out.add("sonar_bugs", "Bugs SonarQube", sonar["bugs"])
        out.add("sonar_code_smells", "Code smells SonarQube", sonar["code_smells"])


def add_confidentiality(out: Series, stages: list[dict]) -> None:
    if not stages:
        return
    leaks = sum(stage["leaks_total"] for stage in stages)
    out.add("leaks_total", "Fuites de données confidentielles détectées", leaks)
    out.add("confidentiality_rate_percent", "Taux de confidentialité (%)", 0 if leaks else 100)
    for stage in stages:
        out.add(
            "leaks_by_stage",
            "Fuites par périmètre scanné",
            stage["leaks_total"],
            stage=stage["stage"],
        )


def add_report(out: Series, report: dict | None) -> None:
    if not report:
        return
    out.add("critical_errors", "Erreurs critiques (bloquantes)", len(report["critical_errors"]))
    out.add("warnings", "Erreurs non critiques (avertissements)", len(report["warnings"]))
    out.add("blocked", "1 si le déploiement a été bloqué", report["status"] == "BLOCKED")
    out.add("deployed", "1 si le déploiement a été exécuté", report["deployment"] == "exécuté")
    for status in ("SUCCESS", "SUCCESS_WITH_WARNINGS", "BLOCKED"):
        out.add("status", "Statut du pipeline", report["status"] == status, status=status)
    for job in report["jobs"]:
        out.add(
            "job_success",
            "1 si le job a réussi",
            job["result"] == "success",
            job_name=job["job"],
            result=job["result"],
        )


def collect(metrics_dir: Path, report: dict | None) -> Series:
    m = load_metrics(metrics_dir)
    out = Series()
    add_tests(out, m["ut"], m["it"])
    add_performance(out, m["perf"])
    add_quality(out, m["static"], m["sonar"])
    add_confidentiality(out, m["confidentiality"])
    add_report(out, report)
    out.add("last_run_timestamp_seconds", "Horodatage de la dernière exécution", time.time())
    return out


def render(series: list[tuple[str, str, float, dict]], base_labels: dict) -> str:
    lines, seen = [], set()
    for name, help_text, value, labels in series:
        if name not in seen:
            lines += [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
            seen.add(name)
        all_labels = {**base_labels, **labels}
        label_text = ",".join(f'{k}="{_label_value(v)}"' for k, v in all_labels.items())
        lines.append(f"{name}{{{label_text}}} {value:g}")
    return "\n".join(lines) + "\n"


def push(url: str, body: str, branch: str) -> None:
    target = (
        f"{url.rstrip('/')}/metrics/job/{PREFIX}/branch/" f"{urllib.parse.quote(branch, safe='')}"
    )
    if not target.startswith(("http://", "https://")):
        raise SystemExit(f"URL Pushgateway invalide : {url}")
    request = urllib.request.Request(target, data=body.encode(), method="PUT")  # noqa: S310
    request.add_header("Content-Type", "text/plain; version=0.0.4")
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        print(f"Pushgateway : HTTP {response.status} -> {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--metrics-dir", default="metrics")
    parser.add_argument("--report", default="reports/incident_report.json")
    parser.add_argument("--output", default="reports/pipeline_metrics.prom")
    parser.add_argument("--push-url", default="")
    parser.add_argument("--branch", default="")
    args = parser.parse_args()

    ctx = git_context()
    branch = args.branch or ctx["branch"] or "unknown"
    base = {"branch": branch, "commit": (ctx["commit"] or "")[:8]}
    body = render(collect(Path(args.metrics_dir), _optional(Path(args.report))), base)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(body, encoding="utf-8", newline="\n")
    print(f"{body.count(chr(10)) - body.count('# ')} séries écrites dans {args.output}")
    if args.push_url:
        push(args.push_url, body, branch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
