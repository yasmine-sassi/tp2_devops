#!/usr/bin/env bash
# Rejoue localement le pipeline CI (mêmes commandes, même ordre, même fail fast).
#
#   bash scripts/run_local_pipeline.sh
#
# Prérequis : Python 3.10+ avec requirements-dev.txt, Docker (Compose v2).
# Gitleaks, Newman et k6 sont exécutés via leurs images Docker officielles.
# Variables utiles : PYTHON (interpréteur), SIMULATED_LATENCY_MS, K6_DURATION, K6_VUS.
set -uo pipefail
cd "$(dirname "$0")/.."

if [ -z "${PYTHON:-}" ]; then
  for candidate in .venv/bin/python .venv/Scripts/python python3 python; do
    if [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then PYTHON=$candidate; break; fi
  done
fi
export PYTHONIOENCODING=utf-8
export GITLEAKS=${GITLEAKS:-scripts/gitleaks_docker.sh}
export PAYFLEX_DEMO_PASSWORD=${PAYFLEX_DEMO_PASSWORD:-$($PYTHON -c 'import secrets; print(secrets.token_hex(16))')}
PY="$PYTHON"

rm -rf reports metrics artifacts .scan coverage.xml
mkdir -p reports metrics artifacts/logs artifacts/reports

declare -A RESULT
ORDER=(secrets-scan static-analysis unit-tests sonarqube build-integration artifact-scan performance quality-gate deploy)
for job in "${ORDER[@]}"; do RESULT[$job]=skipped; done
RESULT[sonarqube]=success   # optionnel : job neutre sans serveur SonarQube configuré

banner() { printf '\n\033[1;34m=== %s ===\033[0m\n' "$1"; }

run_job() {  # run_job <id> <fonction>
  banner "$1"
  if "$2"; then RESULT[$1]=success; else RESULT[$1]=failure; fi
  echo "--> $1 : ${RESULT[$1]}"
}

secrets_scan() {
  bash scripts/secrets_scan.sh source || return 1
  $PY scripts/confidentiality.py --stage source \
    --scan source=reports/gitleaks-source.json --scan config=reports/gitleaks-config.json \
    --output metrics/confidentiality-source.json
}

static_analysis() {
  $PY -m ruff check . --output-format json --exit-zero > reports/ruff.json
  $PY -m bandit -q -r app -c pyproject.toml -f json -o reports/bandit.json --exit-zero
  $PY scripts/collect_metrics.py static --output metrics/static.json > /dev/null
  $PY scripts/quality_gate.py evaluate --scope static --require static
}

unit_tests() {
  $PY -m pytest --cov=app --cov-report=term --cov-report=xml:coverage.xml \
    --cov-report=html:reports/coverage-html --junitxml=reports/junit.xml --cov-fail-under=70
  local rc=$?
  $PY scripts/collect_metrics.py ut --output metrics/ut.json > /dev/null
  $PY scripts/quality_gate.py evaluate --scope ut --require ut || rc=1
  return $rc
}

build_integration() {
  local rc=0
  docker compose build api || return 1
  docker image inspect payflex-api:local --format '{{json .Config}}' > artifacts/image-config.json
  if docker compose up -d --wait api; then
    docker compose --profile it run --rm newman || rc=1
    mkdir -p artifacts/image-fs
    docker export "$(docker compose ps -q api)" | tar -x -C artifacts/image-fs app
  else
    rc=1
  fi
  docker compose logs --no-color api > artifacts/logs/api-integration.log
  docker compose down -v --remove-orphans   # destruction systématique (if: always() en CI)
  $PY scripts/collect_metrics.py it --output metrics/it.json > /dev/null
  $PY scripts/quality_gate.py evaluate --scope it --require it || rc=1
  return $rc
}

artifact_scan() {
  cp -r reports/. artifacts/reports/
  bash scripts/secrets_scan.sh artifacts artifacts || return 1
  $PY scripts/confidentiality.py --stage artifacts \
    --scan artifacts=reports/gitleaks-artifacts.json \
    --output metrics/confidentiality-artifacts.json
}

performance() {
  local rc=0
  docker compose up -d --wait api || return 1
  docker compose --profile perf run --rm k6 || rc=1
  docker compose logs --no-color api > artifacts/logs/api-performance.log
  docker compose down -v --remove-orphans
  [ -f reports/k6-summary.json ] && $PY scripts/collect_metrics.py perf --output metrics/perf.json > /dev/null
  $PY scripts/quality_gate.py evaluate --scope perf --require perf || rc=1
  return $rc
}

quality_gate() {
  $PY scripts/quality_gate.py evaluate --require ut,it,perf,static,confidentiality \
    --output reports/quality_gate.json
}

deploy() {
  echo "Déploiement simulé de payflex-api:local ($(git rev-parse --short HEAD))"
}

# Étape 1 : jobs indépendants (parallèles en CI)
run_job secrets-scan secrets_scan
run_job static-analysis static_analysis
run_job unit-tests unit_tests
stage1_ok=true
for job in secrets-scan static-analysis unit-tests; do
  [ "${RESULT[$job]}" = success ] || stage1_ok=false
done

# Étapes suivantes : fail fast (chaque job a besoin du succès du précédent)
if $stage1_ok; then run_job build-integration build_integration; fi
[ "${RESULT[build-integration]}" = success ] && run_job artifact-scan artifact_scan
[ "${RESULT[artifact-scan]}" = success ] && run_job performance performance
[ "${RESULT[performance]}" = success ] && run_job quality-gate quality_gate
[ "${RESULT[quality-gate]}" = success ] && run_job deploy deploy

banner "incident-report (toujours exécuté)"
needs="{"
for job in "${ORDER[@]}"; do needs+="\"$job\": {\"result\": \"${RESULT[$job]}\"},"; done
needs="${needs%,}}"
$PY scripts/quality_gate.py report --needs "$needs" --output-dir reports > /dev/null
echo "Rapport : reports/incident_report.md"
$PY -c "import json; r = json.load(open('reports/incident_report.json', encoding='utf-8')); print('Statut :', r['status'], '| Déploiement :', r['deployment'])"
[ "${RESULT[deploy]}" = success ]
