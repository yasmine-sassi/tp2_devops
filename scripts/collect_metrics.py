#!/usr/bin/env python3
"""Normalise les rapports bruts des outils en fichiers JSON de métriques (un par job).

    ut      : JUnit PyTest + coverage.xml          -> metrics/ut.json
    it      : JUnit Newman                         -> metrics/it.json
    perf    : résumé k6 (--summary-export)         -> metrics/perf.json
    static  : Ruff (JSON) + Bandit (JSON)          -> metrics/static.json
    sonar   : API SonarQube (measures + issues)    -> metrics/sonar.json

Ces fichiers sont publiés comme artefacts et agrégés par scripts/quality_gate.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # noqa: S405 - rapports locaux produits par le pipeline
from collections import Counter
from pathlib import Path

from ci_common import load_json, write_json

# Codes Ruff assimilés à un « bug bloquant » (équivalent Python d'une NullPointerException
# potentielle : nom indéfini, variable utilisée avant affectation, erreur de syntaxe...).
RUFF_BLOCKING_PREFIXES = ("F821", "F822", "F823", "F63", "F7", "E9")


def _junit_totals(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()  # noqa: S314 - fichier généré par le pipeline
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    totals = Counter()
    failures = []
    for suite in suites:
        for key in ("tests", "failures", "errors", "skipped"):
            totals[key] += int(suite.get(key, 0) or 0)
        for case in suite.iter("testcase"):
            for kind in ("failure", "error"):
                node = case.find(kind)
                if node is not None:
                    name = f"{suite.get('name', '')} :: {case.get('name', '')}".strip(" :")
                    failures.append({"test": name, "message": (node.get("message") or "")[:300]})
    totals_dict = {k: totals[k] for k in ("tests", "failures", "errors", "skipped")}
    totals_dict["passed"] = (
        totals_dict["tests"]
        - totals_dict["failures"]
        - totals_dict["errors"]
        - totals_dict["skipped"]
    )
    return {**totals_dict, "failed_cases": failures[:20]}


def collect_ut(args: argparse.Namespace) -> dict:
    tests = _junit_totals(Path(args.junit)) if Path(args.junit).is_file() else None
    coverage = None
    if Path(args.coverage).is_file():
        root = ET.parse(args.coverage).getroot()  # noqa: S314
        lines_valid = int(root.get("lines-valid", 0))
        lines_covered = int(root.get("lines-covered", 0))
        branches_valid = int(root.get("branches-valid", 0))
        branches_covered = int(root.get("branches-covered", 0))
        total = lines_valid + branches_valid
        # Même calcul que la ligne TOTAL de coverage.py (lignes + branches).
        percent = 100.0 * (lines_covered + branches_covered) / total if total else 0.0
        coverage = {
            "percent": round(percent, 2),
            "line_rate_percent": round(100 * float(root.get("line-rate", 0)), 2),
            "branch_rate_percent": round(100 * float(root.get("branch-rate", 0)), 2),
            "lines_valid": lines_valid,
            "lines_covered": lines_covered,
            "branches_valid": branches_valid,
            "branches_covered": branches_covered,
        }
    return {"indicator": "unit_tests", "job": "unit-tests", "tests": tests, "coverage": coverage}


def collect_it(args: argparse.Namespace) -> dict:
    tests = _junit_totals(Path(args.junit)) if Path(args.junit).is_file() else None
    return {"indicator": "integration_tests", "job": "build-integration", "assertions": tests}


def collect_perf(args: argparse.Namespace) -> dict:
    summary = load_json(args.summary)
    metrics = summary["metrics"]
    duration = metrics["http_req_duration"]
    failed = metrics.get("http_req_failed", {})
    reqs = metrics.get("http_reqs", {})
    checks = metrics.get("checks", {})
    return {
        "indicator": "performance",
        "job": "performance",
        "p95_ms": round(duration["p(95)"], 2),
        "p90_ms": round(duration.get("p(90)", 0), 2),
        "avg_ms": round(duration.get("avg", 0), 2),
        "max_ms": round(duration.get("max", 0), 2),
        "error_rate_percent": round(100 * failed.get("value", 0), 3),
        "requests": int(reqs.get("count", 0)),
        "rps": round(reqs.get("rate", 0), 2),
        "checks_rate_percent": round(100 * checks.get("value", 0), 2) if checks else None,
        "vus_max": int(
            metrics.get("vus_max", {}).get("max", metrics.get("vus_max", {}).get("value", 0))
        ),
    }


def collect_static(args: argparse.Namespace) -> dict:
    ruff_items = load_json(args.ruff) if Path(args.ruff).is_file() else []
    bandit = load_json(args.bandit) if Path(args.bandit).is_file() else {"results": []}

    blocking, smells = [], []
    for item in ruff_items:
        code = item.get("code") or "SYNTAX"
        entry = {
            "tool": "ruff",
            "code": code,
            "message": item.get("message"),
            "file": item.get("filename"),
            "line": (item.get("location") or {}).get("row"),
        }
        is_blocking = code == "SYNTAX" or code.startswith(RUFF_BLOCKING_PREFIXES)
        (blocking if is_blocking else smells).append(entry)

    severities = Counter()
    bandit_high = []
    for issue in bandit.get("results", []):
        severity = issue.get("issue_severity", "LOW").upper()
        severities[severity] += 1
        entry = {
            "tool": "bandit",
            "code": issue.get("test_id"),
            "message": issue.get("issue_text"),
            "file": issue.get("filename"),
            "line": issue.get("line_number"),
            "severity": severity,
        }
        (bandit_high if severity == "HIGH" else smells).append(entry)

    cwd = str(Path.cwd())
    for entry in blocking + bandit_high + smells:
        if entry["file"] and entry["file"].startswith(cwd):
            entry["file"] = str(Path(entry["file"]).relative_to(cwd)).replace("\\", "/")
    return {
        "indicator": "static_analysis",
        "job": "static-analysis",
        "ruff_total": len(ruff_items),
        "bandit": {
            "high": severities["HIGH"],
            "medium": severities["MEDIUM"],
            "low": severities["LOW"],
        },
        "blocking_bugs": blocking + bandit_high,
        "code_smells": smells,
        "blocking_count": len(blocking) + len(bandit_high),
        "smells_count": len(smells),
    }


def _sonar_get(host: str, token: str, path: str, params: dict) -> dict:
    url = f"{host.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"URL SonarQube invalide : {host}")
    request = urllib.request.Request(url)  # noqa: S310 - schéma vérifié ci-dessus
    auth = base64.b64encode(f"{token}:".encode()).decode()
    request.add_header("Authorization", f"Basic {auth}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def collect_sonar(args: argparse.Namespace) -> dict:
    key = args.project_key
    measures = _sonar_get(
        args.host,
        args.token,
        "/api/measures/component",
        {
            "component": key,
            "metricKeys": "bugs,vulnerabilities,code_smells,coverage,sqale_index,"
            "reliability_rating,security_rating",
        },
    )
    values = {m["metric"]: m.get("value") for m in measures["component"]["measures"]}
    blocking = _sonar_get(
        args.host,
        args.token,
        "/api/issues/search",
        {
            "componentKeys": key,
            "types": "BUG,VULNERABILITY",
            "severities": "BLOCKER,CRITICAL",
            "resolved": "false",
            "ps": 50,
        },
    )
    gate = _sonar_get(
        args.host, args.token, "/api/qualitygates/project_status", {"projectKey": key}
    )
    return {
        "indicator": "sonarqube",
        "job": "sonarqube",
        "quality_gate": gate["projectStatus"]["status"],
        "bugs": int(values.get("bugs") or 0),
        "vulnerabilities": int(values.get("vulnerabilities") or 0),
        "code_smells": int(values.get("code_smells") or 0),
        "coverage_percent": float(values.get("coverage") or 0),
        "technical_debt_min": int(values.get("sqale_index") or 0),
        "blocking_issues": blocking.get("total", 0),
        "blocking_samples": [
            {
                "rule": i["rule"],
                "message": i["message"],
                "component": i["component"],
                "line": i.get("line"),
                "severity": i["severity"],
            }
            for i in blocking.get("issues", [])[:10]
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalisation des métriques du pipeline")
    sub = parser.add_subparsers(dest="kind", required=True)
    ut = sub.add_parser("ut")
    ut.add_argument("--junit", default="reports/junit.xml")
    ut.add_argument("--coverage", default="coverage.xml")
    it = sub.add_parser("it")
    it.add_argument("--junit", default="reports/newman-junit.xml")
    perf = sub.add_parser("perf")
    perf.add_argument("--summary", default="reports/k6-summary.json")
    static = sub.add_parser("static")
    static.add_argument("--ruff", default="reports/ruff.json")
    static.add_argument("--bandit", default="reports/bandit.json")
    sonar = sub.add_parser("sonar")
    sonar.add_argument("--host", required=True)
    sonar.add_argument("--token", required=True)
    sonar.add_argument("--project-key", default="payflex-api")
    for p in (ut, it, perf, static, sonar):
        p.add_argument("--output", required=True)
    args = parser.parse_args()

    collectors = {
        "ut": collect_ut,
        "it": collect_it,
        "perf": collect_perf,
        "static": collect_static,
        "sonar": collect_sonar,
    }
    data = collectors[args.kind](args)
    write_json(args.output, data)
    print(
        json.dumps(
            {k: v for k, v in data.items() if not isinstance(v, list)}, indent=2, ensure_ascii=False
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
