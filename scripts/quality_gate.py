#!/usr/bin/env python3
"""Quality Gate de PayFlex : classification Non-Critique (WARNING) / Critique (CRITICAL).

Sous-commandes :
  evaluate  Agrège les JSON de metrics/ et applique les seuils. Exit 1 si au moins une
            erreur CRITIQUE (fail fast), sinon 0 (les WARNING sont signalés sans bloquer).
            --scope limite l'évaluation à un job (ut, it, perf, static, confidentiality).
  report    Produit incident_report.md / incident_report.json à partir des métriques et
            du résultat des jobs GitHub Actions (contexte `needs`, passé en JSON).

Seuils (sujet du TP, étape 3) :
  Couverture        70 % <= C < 80 %        -> WARNING  | C < 70 %                -> CRITICAL
  Bugs / smells     code smells             -> WARNING  | bug bloquant / NPE pot. -> CRITICAL
  Perf k6           200 ms <= p95 <= 500 ms -> WARNING  | p95 > 500 ms            -> CRITICAL
                    1 % <= erreurs <= 2 %   -> WARNING  | erreurs > 2 %           -> CRITICAL
  Confidentialité   -                                   | taux < 100 %            -> CRITICAL
  Tests UT / IT     -                                   | au moins un échec       -> CRITICAL
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ci_common import annotate, append_summary, load_json, write_json
from confidentiality import confidentiality_rate

OK, WARNING, CRITICAL = "OK", "WARNING", "CRITICAL"
SEVERITY_ORDER = {OK: 0, WARNING: 1, CRITICAL: 2}
ICONS = {OK: "🟢", WARNING: "🟠", CRITICAL: "🔴"}

THRESHOLDS = {
    "coverage_target_pct": float(os.getenv("QG_COVERAGE_TARGET", "80")),
    "coverage_min_pct": float(os.getenv("QG_COVERAGE_MIN", "70")),
    "p95_warn_ms": float(os.getenv("QG_P95_WARN_MS", "200")),
    "p95_crit_ms": float(os.getenv("QG_P95_CRIT_MS", "500")),
    "error_warn_pct": float(os.getenv("QG_ERROR_WARN_PCT", "1")),
    "error_crit_pct": float(os.getenv("QG_ERROR_CRIT_PCT", "2")),
    "confidentiality_required_pct": 100.0,
    "max_tolerated_leaks": 0,
}

# Ordre d'exécution du pipeline (sert à identifier la cause racine d'un blocage).
JOBS = [
    ("secrets-scan", "Scan de secrets : code source, historique, configuration"),
    ("static-analysis", "Analyse statique (Ruff + Bandit)"),
    ("unit-tests", "Tests unitaires + couverture"),
    ("sonarqube", "SonarQube (optionnel)"),
    ("build-integration", "Build image + tests d'intégration (Newman)"),
    ("artifact-scan", "Scan de confidentialité des artefacts"),
    ("performance", "Tests de performance (k6)"),
    ("quality-gate", "Quality Gate global"),
    ("deploy", "Déploiement (simulé)"),
]
JOB_LABELS = dict(JOBS)
JOB_ORDER = [job for job, _ in JOBS]

ACTIONS = {
    "tests_ut": "Corriger la régression : voir reports/junit.xml et les logs du job unit-tests.",
    "coverage": "Ajouter des tests sur le code non couvert (voir le rapport HTML de couverture).",
    "tests_it": "Analyser l'assertion Newman en échec : contrat d'API ou scénario métier cassé.",
    "perf_latency": "Profiler les endpoints lents (latence p95), vérifier la configuration "
    "(ex. SIMULATED_LATENCY_MS) et les dépendances avant de relancer.",
    "perf_errors": "Analyser les erreurs HTTP sous charge dans les logs de l'API.",
    "static_blocking": "Corriger les bugs bloquants signalés (Bandit HIGH, Ruff F821/F823...).",
    "code_smells": "Planifier la résorption de la dette technique (non bloquant).",
    "sonar": "Corriger les issues SonarQube BLOCKER/CRITICAL (bugs / vulnérabilités).",
    "confidentiality": "Révoquer immédiatement le secret exposé, le supprimer du code ET de "
    "l'historique git (git filter-repo), puis relancer le pipeline.",
    "missing": "Le job n'a pas produit ses métriques : consulter ses logs.",
    "job_failed": "Consulter les logs du job en échec.",
}


def finding(
    indicator: str,
    job: str,
    classification: str,
    value: str,
    threshold: str,
    message: str,
    action_key: str,
) -> dict:
    return {
        "indicator": indicator,
        "job": job,
        "classification": classification,
        "value": value,
        "threshold": threshold,
        "message": message,
        "recommended_action": ACTIONS[action_key] if classification != OK else "-",
    }


# --------------------------------------------------------------------------- évaluateurs


def eval_unit_tests(ut: dict) -> list[dict]:
    out = []
    tests = ut.get("tests")
    if tests is None:
        out.append(
            finding(
                "Tests unitaires",
                "unit-tests",
                CRITICAL,
                "rapport absent",
                "0 échec",
                "Rapport JUnit des tests unitaires absent",
                "missing",
            )
        )
    else:
        broken = tests["failures"] + tests["errors"]
        if broken:
            names = ", ".join(c["test"] for c in tests["failed_cases"][:5])
            out.append(
                finding(
                    "Tests unitaires",
                    "unit-tests",
                    CRITICAL,
                    f"{broken} échec(s) / {tests['tests']}",
                    "0 échec",
                    f"{broken} test(s) unitaire(s) en échec : {names}",
                    "tests_ut",
                )
            )
        else:
            out.append(
                finding(
                    "Tests unitaires",
                    "unit-tests",
                    OK,
                    f"{tests['passed']}/{tests['tests']} OK",
                    "0 échec",
                    "Tous les tests unitaires passent",
                    "tests_ut",
                )
            )

    cov = ut.get("coverage")
    target, minimum = THRESHOLDS["coverage_target_pct"], THRESHOLDS["coverage_min_pct"]
    if cov is None:
        out.append(
            finding(
                "Couverture de code",
                "unit-tests",
                CRITICAL,
                "rapport absent",
                f">= {target:.0f} %",
                "Rapport de couverture absent",
                "missing",
            )
        )
        return out
    pct = cov["percent"]
    value = f"{pct:.2f} %"
    if pct < minimum:
        out.append(
            finding(
                "Couverture de code",
                "unit-tests",
                CRITICAL,
                value,
                f"< {minimum:.0f} % (critique)",
                f"Couverture à {pct:.1f} %, sous le minimum de {minimum:.0f} %",
                "coverage",
            )
        )
    elif pct < target:
        out.append(
            finding(
                "Couverture de code",
                "unit-tests",
                WARNING,
                value,
                f"{minimum:.0f} % <= C < {target:.0f} %",
                f"Attention, couverture à {pct:.1f} % (objectif {target:.0f} %)",
                "coverage",
            )
        )
    else:
        out.append(
            finding(
                "Couverture de code",
                "unit-tests",
                OK,
                value,
                f">= {target:.0f} %",
                f"Couverture à {pct:.1f} %",
                "coverage",
            )
        )
    return out


def eval_integration(it: dict) -> list[dict]:
    tests = it.get("assertions")
    if tests is None:
        return [
            finding(
                "Tests d'intégration",
                "build-integration",
                CRITICAL,
                "rapport absent",
                "0 échec",
                "Rapport JUnit Newman absent",
                "missing",
            )
        ]
    broken = tests["failures"] + tests["errors"]
    if broken:
        names = "; ".join(f"{c['test']} ({c['message']})" for c in tests["failed_cases"][:3])
        return [
            finding(
                "Tests d'intégration",
                "build-integration",
                CRITICAL,
                f"{broken} échec(s) / {tests['tests']}",
                "0 échec",
                f"{broken} assertion(s) Newman en échec : {names}",
                "tests_it",
            )
        ]
    return [
        finding(
            "Tests d'intégration",
            "build-integration",
            OK,
            f"{tests['passed']}/{tests['tests']} assertions OK",
            "0 échec",
            "Contrats d'API et scénario Newman validés",
            "tests_it",
        )
    ]


def eval_performance(perf: dict) -> list[dict]:
    p95, err = perf["p95_ms"], perf["error_rate_percent"]
    warn_ms, crit_ms = THRESHOLDS["p95_warn_ms"], THRESHOLDS["p95_crit_ms"]
    warn_err, crit_err = THRESHOLDS["error_warn_pct"], THRESHOLDS["error_crit_pct"]
    out = []
    if p95 > crit_ms:
        out.append(
            finding(
                "Performance : latence p95",
                "performance",
                CRITICAL,
                f"{p95:.0f} ms",
                f"> {crit_ms:.0f} ms (critique)",
                f"Latence p95 de {p95:.0f} ms, au-delà de {crit_ms:.0f} ms",
                "perf_latency",
            )
        )
    elif p95 >= warn_ms:
        out.append(
            finding(
                "Performance : latence p95",
                "performance",
                WARNING,
                f"{p95:.0f} ms",
                f"{warn_ms:.0f}-{crit_ms:.0f} ms",
                f"Attention, latence p95 à {p95:.0f} ms (cible < {warn_ms:.0f} ms)",
                "perf_latency",
            )
        )
    else:
        out.append(
            finding(
                "Performance : latence p95",
                "performance",
                OK,
                f"{p95:.1f} ms",
                f"< {warn_ms:.0f} ms",
                f"Latence p95 de {p95:.1f} ms",
                "perf_latency",
            )
        )
    if err > crit_err:
        out.append(
            finding(
                "Performance : taux d'erreur",
                "performance",
                CRITICAL,
                f"{err:.2f} %",
                f"> {crit_err:.0f} % (critique)",
                f"Taux d'erreur de {err:.2f} % sous charge",
                "perf_errors",
            )
        )
    elif err >= warn_err:
        out.append(
            finding(
                "Performance : taux d'erreur",
                "performance",
                WARNING,
                f"{err:.2f} %",
                f"{warn_err:.0f}-{crit_err:.0f} %",
                f"Attention, taux d'erreur à {err:.2f} % (cible < {warn_err:.0f} %)",
                "perf_errors",
            )
        )
    else:
        out.append(
            finding(
                "Performance : taux d'erreur",
                "performance",
                OK,
                f"{err:.2f} %",
                f"< {warn_err:.0f} %",
                f"Taux d'erreur de {err:.2f} %",
                "perf_errors",
            )
        )
    return out


def eval_static(static: dict) -> list[dict]:
    out = []
    if static["blocking_count"]:
        samples = "; ".join(
            f"{b['tool']} {b['code']} {b['file']}:{b['line']}" for b in static["blocking_bugs"][:5]
        )
        out.append(
            finding(
                "Bugs bloquants (code)",
                "static-analysis",
                CRITICAL,
                f"{static['blocking_count']} bug(s)",
                "0 bug bloquant",
                f"Bug bloquant / crash potentiel : {samples}",
                "static_blocking",
            )
        )
    else:
        out.append(
            finding(
                "Bugs bloquants (code)",
                "static-analysis",
                OK,
                "0",
                "0 bug bloquant",
                "Aucun bug bloquant (Bandit HIGH / Ruff F8xx-E9xx)",
                "static_blocking",
            )
        )
    smells = static["smells_count"]
    out.append(
        finding(
            "Code smells",
            "static-analysis",
            WARNING if smells else OK,
            str(smells),
            "0 (sinon avertissement)",
            f"{smells} code smell(s) / avertissement(s) Bandit" if smells else "Aucun code smell",
            "code_smells",
        )
    )
    return out


def eval_sonar(sonar: dict) -> list[dict]:
    out = []
    blocking = sonar["blocking_issues"]
    out.append(
        finding(
            "SonarQube : bugs/vulnérabilités BLOCKER-CRITICAL",
            "sonarqube",
            CRITICAL if blocking else OK,
            str(blocking),
            "0",
            f"{blocking} issue(s) BLOCKER/CRITICAL"
            if blocking
            else "Aucune issue bloquante SonarQube",
            "sonar",
        )
    )
    if sonar["code_smells"]:
        out.append(
            finding(
                "SonarQube : code smells",
                "sonarqube",
                WARNING,
                str(sonar["code_smells"]),
                "0 (sinon avertissement)",
                f"{sonar['code_smells']} code smell(s), dette "
                f"{sonar['technical_debt_min']} min",
                "code_smells",
            )
        )
    if sonar["quality_gate"] not in ("OK", "NONE"):
        out.append(
            finding(
                "SonarQube : Quality Gate",
                "sonarqube",
                WARNING,
                sonar["quality_gate"],
                "OK",
                "Quality Gate SonarQube non passé (voir le tableau de bord Sonar)",
                "sonar",
            )
        )
    return out


def eval_confidentiality(stages: list[dict]) -> list[dict]:
    leaks = sum(stage["leaks_total"] for stage in stages)
    missing = [m for stage in stages for m in stage.get("missing_reports", [])]
    rate = confidentiality_rate(leaks, THRESHOLDS["max_tolerated_leaks"])
    jobs = {"source": "secrets-scan", "artifacts": "artifact-scan"}
    if missing:
        job = jobs.get(stages[0]["stage"], "secrets-scan")
        return [
            finding(
                "Confidentialité",
                job,
                CRITICAL,
                "non vérifiable",
                "taux = 100 %",
                f"Rapport(s) Gitleaks absent(s) : {', '.join(missing)}",
                "missing",
            )
        ]
    if rate < 100:
        culprit = next(s for s in stages if s["leaks_total"])
        where = "; ".join(
            f"{f['rule']} dans {f['file']}:{f['line']} ({f['target']})"
            for s in stages
            for f in s["findings"][:5]
        )
        return [
            finding(
                "Confidentialité",
                jobs.get(culprit["stage"], "secrets-scan"),
                CRITICAL,
                f"taux = {rate:.0f} % ({leaks} fuite(s))",
                "taux = 100 % (0 fuite)",
                f"Fuite de donnée confidentielle détectée : {where}",
                "confidentiality",
            )
        ]
    scanned = ", ".join(t for s in stages for t in s["targets"])
    return [
        finding(
            "Confidentialité",
            "secrets-scan",
            OK,
            "taux = 100 % (0 fuite)",
            "taux = 100 %",
            f"Aucune fuite ({scanned})",
            "confidentiality",
        )
    ]


# --------------------------------------------------------------------------- agrégation


def load_metrics(metrics_dir: Path) -> dict:
    def opt(name: str) -> dict | None:
        path = metrics_dir / name
        return load_json(path) if path.is_file() else None

    return {
        "ut": opt("ut.json"),
        "it": opt("it.json"),
        "perf": opt("perf.json"),
        "static": opt("static.json"),
        "sonar": opt("sonar.json"),
        "confidentiality": [
            load_json(p) for p in sorted(metrics_dir.glob("confidentiality-*.json"))
        ],
    }


SCOPES = {
    "ut": [("ut", eval_unit_tests)],
    "it": [("it", eval_integration)],
    "perf": [("perf", eval_performance)],
    "static": [("static", eval_static), ("sonar", eval_sonar)],
    "confidentiality": [("confidentiality", eval_confidentiality)],
}
SCOPE_JOBS = {
    "ut": "unit-tests",
    "it": "build-integration",
    "perf": "performance",
    "static": "static-analysis",
    "confidentiality": "secrets-scan",
}


def evaluate(metrics: dict, scopes: list[str], required: list[str]) -> list[dict]:
    findings = []
    for scope in scopes:
        for key, evaluator in SCOPES[scope]:
            data = metrics.get(key)
            if data:
                findings.extend(evaluator(data))
            elif key in required:
                findings.append(
                    finding(
                        f"Métriques « {key} »",
                        SCOPE_JOBS.get(key, key),
                        CRITICAL,
                        "absentes",
                        "présentes",
                        f"Le fichier de métriques {key} est absent",
                        "missing",
                    )
                )
    return findings


def overall(findings: list[dict]) -> str:
    return max((f["classification"] for f in findings), key=SEVERITY_ORDER.get, default=OK)


def markdown_table(findings: list[dict]) -> str:
    rows = [
        f"| {ICONS[f['classification']]} {f['classification']} | {f['indicator']} | "
        f"`{f['job']}` | {f['value']} | {f['threshold']} | {f['message']} |"
        for f in sorted(findings, key=lambda f: -SEVERITY_ORDER[f["classification"]])
    ]
    header = (
        "| Classification | Indicateur | Job | Valeur mesurée | Seuil | Détail |\n"
        "|---|---|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def cmd_evaluate(args: argparse.Namespace) -> int:
    metrics = load_metrics(Path(args.metrics_dir))
    scopes = list(SCOPES) if args.scope == "all" else [args.scope]
    required = [r for r in args.require.split(",") if r] if args.require else []
    findings = evaluate(metrics, scopes, required)
    status = overall(findings)

    for f in findings:
        if f["classification"] == OK:
            annotate(
                "notice",
                f"{f['indicator']} = {f['value']} (seuil {f['threshold']})",
                title=f"OK - {f['indicator']}",
            )
        elif f["classification"] == WARNING:
            annotate("warning", f["message"], title=f"Non-critique - {f['indicator']}")
        elif f["classification"] == CRITICAL:
            annotate(
                "error",
                f"{f['message']} | action : {f['recommended_action']}",
                title=f"CRITIQUE - {f['indicator']}",
            )
    print()
    for f in findings:
        print(
            f"  [{f['classification']:<8}] {f['indicator']:<45} {f['value']:<28} "
            f"(seuil : {f['threshold']})"
        )
    verdict = {
        OK: "PASSED - tous les indicateurs sont au vert",
        WARNING: "PASSED WITH WARNINGS - erreurs non critiques, le pipeline continue",
        CRITICAL: "FAILED - erreur critique, arrêt du pipeline (fail fast)",
    }[status]
    print(f"\nQuality Gate [{args.scope}] : {verdict}")

    append_summary(
        f"### {ICONS[status]} Quality Gate `{args.scope}` : {verdict}\n\n"
        + markdown_table(findings)
    )
    if args.output:
        write_json(
            args.output,
            {
                "scope": args.scope,
                "status": status,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "thresholds": THRESHOLDS,
                "findings": findings,
            },
        )
    return 1 if status == CRITICAL else 0


# --------------------------------------------------------------------------- rapport


def git_context() -> dict:
    def git(*cmd: str) -> str | None:
        try:
            # Commande fixe (git) sans shell : uniquement pour le contexte en exécution locale.
            return subprocess.check_output(
                ["git", *cmd], text=True, stderr=subprocess.DEVNULL
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    server = os.getenv("GITHUB_SERVER_URL", "https://github.com")
    repo = os.getenv("GITHUB_REPOSITORY")
    run_id = os.getenv("GITHUB_RUN_ID")
    return {
        "repository": repo,
        "branch": os.getenv("GITHUB_HEAD_REF")
        or os.getenv("GITHUB_REF_NAME")
        or git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": os.getenv("GITHUB_SHA") or git("rev-parse", "HEAD"),
        "event": os.getenv("GITHUB_EVENT_NAME", "local"),
        "actor": os.getenv("GITHUB_ACTOR"),
        "workflow": os.getenv("GITHUB_WORKFLOW"),
        "run_url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_report(metrics: dict, needs: dict) -> dict:
    findings = evaluate(metrics, list(SCOPES), required=[])
    results = {job: (needs.get(job) or {}).get("result", "absent") for job in JOB_ORDER}
    failed_jobs = [job for job in JOB_ORDER if results[job] in ("failure", "cancelled")]

    critical_jobs = {f["job"] for f in findings if f["classification"] == CRITICAL}
    for job in failed_jobs:
        if job not in critical_jobs:
            findings.append(
                finding(
                    "Exécution du job",
                    job,
                    CRITICAL,
                    results[job],
                    "success",
                    f"Le job « {JOB_LABELS[job]} » a échoué ({results[job]})",
                    "job_failed",
                )
            )

    criticals = [f for f in findings if f["classification"] == CRITICAL]
    warnings = [f for f in findings if f["classification"] == WARNING]
    criticals.sort(key=lambda f: JOB_ORDER.index(f["job"]) if f["job"] in JOB_ORDER else 99)
    blocked = bool(criticals)
    root = criticals[0] if criticals else None
    return {
        "status": "BLOCKED" if blocked else ("SUCCESS_WITH_WARNINGS" if warnings else "SUCCESS"),
        "deployment": "annulé (fail fast)"
        if blocked
        else ("exécuté" if results.get("deploy") == "success" else results.get("deploy", "absent")),
        "context": git_context(),
        "root_cause": root,
        "critical_errors": criticals,
        "warnings": warnings,
        "passed_checks": [f for f in findings if f["classification"] == OK],
        "jobs": [
            {"job": job, "label": JOB_LABELS[job], "result": results[job]} for job in JOB_ORDER
        ],
        "skipped_jobs": [job for job in JOB_ORDER if results[job] == "skipped"],
        "thresholds": THRESHOLDS,
    }


RESULT_ICONS = {"success": "✅", "failure": "❌", "cancelled": "⛔", "skipped": "⏭️", "absent": "·"}


def render_markdown(report: dict) -> str:
    ctx = report["context"]
    status = report["status"]
    title = {
        "BLOCKED": "🔴 Rapport d'incident : déploiement BLOQUÉ",
        "SUCCESS_WITH_WARNINGS": "🟠 Rapport de pipeline : succès avec avertissements",
        "SUCCESS": "🟢 Rapport de pipeline : aucun incident",
    }[status]
    lines = [
        f"# {title}",
        "",
        "| Contexte | Valeur |",
        "|---|---|",
        f"| Branche | `{ctx['branch']}` |",
        f"| Commit | `{(ctx['commit'] or '')[:12]}` |",
        f"| Événement | {ctx['event']} |",
        f"| Exécution CI | {ctx['run_url'] or 'exécution locale'} |",
        f"| Généré le | {ctx['generated_at']} |",
        f"| Statut | **{status}** |",
        f"| Déploiement | **{report['deployment']}** |",
        "",
    ]
    root = report["root_cause"]
    if root:
        lines += [
            "## Cause exacte du blocage",
            "",
            f"- **Job fautif** : `{root['job']}` ({JOB_LABELS.get(root['job'], '')})",
            f"- **Indicateur** : {root['indicator']}",
            f"- **Valeur mesurée** : {root['value']}",
            f"- **Seuil violé** : {root['threshold']}",
            "- **Classification** : CRITIQUE (arrêt immédiat, pas de déploiement)",
            f"- **Détail** : {root['message']}",
            f"- **Action recommandée** : {root['recommended_action']}",
            "",
        ]
    if report["critical_errors"]:
        lines += [
            "## Erreurs critiques (bloquantes)",
            "",
            markdown_table(report["critical_errors"]),
            "",
        ]
    if report["warnings"]:
        lines += [
            "## Erreurs non critiques (avertissements)",
            "",
            markdown_table(report["warnings"]),
            "",
        ]
    if report["passed_checks"]:
        lines += ["## Indicateurs conformes", "", markdown_table(report["passed_checks"]), ""]
    lines += ["## État des jobs", "", "| Job | Étape | Résultat |", "|---|---|---|"]
    lines += [
        f"| `{j['job']}` | {j['label']} | {RESULT_ICONS.get(j['result'], '')} " f"{j['result']} |"
        for j in report["jobs"]
    ]
    if report["skipped_jobs"] and root:
        lines += ["", f"Jobs annulés par le fail fast : {', '.join(report['skipped_jobs'])}."]
    return "\n".join(lines) + "\n"


def read_needs(raw: str) -> dict:
    """Contexte `needs` : JSON en ligne (toJSON(needs) en CI) ou chemin d'un fichier JSON.

    Le JSON est testé en premier : passé tel quel à Path(), une longue chaîne provoque
    « File name too long » (ENAMETOOLONG) sous Linux.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("{"):
        return json.loads(raw)
    return json.loads(Path(raw).read_text(encoding="utf-8"))


def cmd_report(args: argparse.Namespace) -> int:
    needs = read_needs(args.needs)
    report = build_report(load_metrics(Path(args.metrics_dir)), needs)
    out_dir = Path(args.output_dir)
    write_json(out_dir / "incident_report.json", report)
    markdown = render_markdown(report)
    (out_dir / "incident_report.md").write_text(markdown, encoding="utf-8", newline="\n")
    append_summary(markdown)
    print(markdown)
    root = report["root_cause"]
    if root:
        annotate(
            "error",
            f"Job {root['job']} | {root['indicator']} = {root['value']} | seuil violé : "
            f"{root['threshold']} | {root['message']} | action : {root['recommended_action']}",
            title=f"Rapport d'incident - déploiement {report['deployment']}",
        )
    else:
        annotate(
            "notice",
            f"Statut {report['status']} - {len(report['warnings'])} avertissement(s) - "
            f"déploiement {report['deployment']}",
            title="Rapport de pipeline",
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Quality Gate PayFlex")
    sub = parser.add_subparsers(dest="command", required=True)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--metrics-dir", default="metrics")
    ev.add_argument("--scope", default="all", choices=["all", *SCOPES])
    ev.add_argument("--require", default="", help="métriques obligatoires, ex. ut,it,perf")
    ev.add_argument("--output")
    rp = sub.add_parser("report")
    rp.add_argument("--metrics-dir", default="metrics")
    rp.add_argument(
        "--needs",
        default=os.getenv("NEEDS_JSON", ""),
        help="JSON du contexte `needs` (ou chemin d'un fichier)",
    )
    rp.add_argument("--output-dir", default="reports")
    args = parser.parse_args()
    return cmd_evaluate(args) if args.command == "evaluate" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
