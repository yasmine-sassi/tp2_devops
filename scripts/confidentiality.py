#!/usr/bin/env python3
"""Calcul du Taux de Confidentialité à partir des rapports JSON de Gitleaks.

Formule du sujet :  Taux = (1 - N_fuites / S_max) x 100 %, avec S_max = 0.
Elle est indéterminée pour S_max = 0 (division par zéro). Convention retenue :

    Taux = max(0, 1 - N_fuites / max(S_max, 1)) x 100 %

=> avec S_max = 0 : 0 fuite -> 100 % ; au moins 1 fuite -> 0 %.
Le pipeline n'est validé que si Taux = 100 %. Tout taux < 100 % est une erreur CRITIQUE.

Usage :
    python scripts/confidentiality.py --stage source \
        --scan source=reports/gitleaks-source.json \
        --scan config=reports/gitleaks-config.json \
        --output metrics/confidentiality-source.json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ci_common import annotate, append_summary, load_json, write_json

EXIT_LEAK = 1
EXIT_MISSING_REPORT = 2


def confidentiality_rate(leaks: int, max_tolerated: int = 0) -> float:
    return max(0.0, 1 - leaks / max(max_tolerated, 1)) * 100


def parse_scans(values: list[str]) -> list[tuple[str, Path]]:
    scans = []
    for value in values:
        target, _, path = value.partition("=")
        if not target or not path:
            raise SystemExit(f"--scan attend la forme cible=chemin, reçu : {value!r}")
        scans.append((target, Path(path)))
    return scans


def normalize_path(path: str) -> str:
    """Chemin relatif au dépôt : retire la copie de travail .scan/<cible>/ du scan de config.

    Ainsi, une même fuite vue par le scan du code et par celui de la configuration n'est
    comptée qu'une seule fois.
    """
    return re.sub(r"^\.scan/[^/]+/", "", path.replace("\\", "/"))


def read_findings(target: str, report: Path) -> list[dict]:
    """Extrait les champs utiles (jamais le secret lui-même) d'un rapport Gitleaks."""
    findings = []
    for item in load_json(report) or []:
        findings.append(
            {
                "target": target,
                "rule": item.get("RuleID"),
                "description": item.get("Description"),
                "file": normalize_path(item.get("File") or ""),
                "line": item.get("StartLine"),
                "commit": (item.get("Commit") or "")[:8] or None,
                "author": item.get("Author") or None,
            }
        )
    return findings


def unique_leaks(findings: list[dict]) -> int:
    """Une fuite = un couple (règle, fichier, ligne), même si plusieurs scans la voient."""
    return len({(f["rule"], f["file"], f["line"]) for f in findings})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--stage", required=True, help="nom de l'étape (source, artifacts...)")
    parser.add_argument("--scan", action="append", required=True, help="cible=rapport.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-tolerated", type=int, default=0, help="S_max (défaut : 0)")
    args = parser.parse_args()

    targets: dict[str, dict] = {}
    findings: list[dict] = []
    missing: list[str] = []
    for target, report in parse_scans(args.scan):
        if not report.is_file():
            missing.append(f"{target} ({report})")
            targets[target] = {"report": str(report), "leaks": None, "status": "MISSING"}
            continue
        target_findings = read_findings(target, report)
        findings.extend(target_findings)
        targets[target] = {"report": str(report), "leaks": unique_leaks(target_findings)}

    leaks = unique_leaks(findings)
    rate = confidentiality_rate(leaks, args.max_tolerated)
    critical = rate < 100 or bool(missing)
    result = {
        "indicator": "confidentiality",
        "stage": args.stage,
        "formula": "max(0, 1 - N / max(S_max, 1)) x 100",
        "max_tolerated": args.max_tolerated,
        "leaks_total": leaks,
        "rate_percent": round(rate, 2),
        "targets": targets,
        "missing_reports": missing,
        "findings": findings,
        "status": "CRITICAL" if critical else "OK",
    }
    write_json(args.output, result)

    rows = "\n".join(
        f"| {name} | {info['leaks'] if info['leaks'] is not None else 'rapport absent'} |"
        for name, info in targets.items()
    )
    icon = "🔴" if critical else "🟢"
    append_summary(
        f"### {icon} Confidentialité ({args.stage}) : taux = {rate:.0f} %\n\n"
        f"| Cible scannée | Fuites |\n|---|---|\n{rows}\n\n"
        f"Fuites uniques : **{leaks}** / seuil toléré : **{args.max_tolerated}**"
    )

    for finding in findings:
        annotate(
            "error",
            f"{finding['description']} ({finding['rule']}) détecté dans {finding['target']}",
            title="Fuite de donnée confidentielle",
            file=finding["file"],
            line=finding["line"],
        )
    print(
        f"Taux de confidentialité [{args.stage}] = {rate:.2f} % "
        f"({leaks} fuite(s), seuil toléré {args.max_tolerated})"
    )
    if missing:
        annotate(
            "error",
            f"rapport(s) Gitleaks absent(s) : {', '.join(missing)}",
            title="Confidentialité non vérifiable",
        )
        return EXIT_MISSING_REPORT
    per_target = ", ".join(f"{name} : {info['leaks']}" for name, info in targets.items())
    if rate < 100:
        annotate(
            "error",
            f"Taux de confidentialité {rate:.0f} % < 100 % ({leaks} fuite(s) ; {per_target}) "
            ": pipeline bloqué",
            title="Erreur CRITIQUE - confidentialité",
        )
        return EXIT_LEAK
    annotate(
        "notice",
        f"Taux de confidentialité = {rate:.0f} % (0 fuite ; {per_target})",
        title=f"OK - confidentialité ({args.stage})",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
