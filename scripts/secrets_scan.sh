#!/usr/bin/env bash
# Lance Gitleaks sur les trois périmètres exigés et produit des rapports JSON (secrets masqués).
#
#   scripts/secrets_scan.sh source            -> code source + historique git, fichiers de config
#   scripts/secrets_scan.sh artifacts <dir>   -> artefacts du build (FS de l'image, logs, rapports)
#
# GITLEAKS peut pointer vers le binaire (CI) ou vers une image Docker (local), ex. :
#   GITLEAKS="docker run --rm -v $PWD:/repo -w /repo zricethezav/gitleaks:v8.21.2"
# Le script n'échoue pas lui-même sur une fuite : c'est scripts/confidentiality.py qui
# calcule le taux et décide (exit 1 si taux < 100 %). Il échoue si Gitleaks plante.
set -euo pipefail

GITLEAKS=${GITLEAKS:-gitleaks}
COMMON=(--config .gitleaks.toml --redact --report-format json --exit-code 0 --no-banner)
mkdir -p reports

case "${1:-}" in
  source)
    # Historique de la branche courante uniquement (les autres branches ont leur propre run).
    $GITLEAKS git . "${COMMON[@]}" --log-opts="--full-history HEAD" \
      --report-path reports/gitleaks-source.json

    # Fichiers de configuration (y compris non suivis par git, ex. .env) copiés à part.
    rm -rf .scan/config && mkdir -p .scan/config
    shopt -s nullglob dotglob
    for path in Dockerfile docker-compose*.yml .gitleaks.toml sonar-project.properties \
                pyproject.toml requirements*.txt .env* *.env *.properties *.ini *.cfg *.conf \
                *.yml *.yaml config .github postman k6 monitoring app/config.py; do
      [ -e "$path" ] && cp -r --parents "$path" .scan/config/
    done
    $GITLEAKS dir .scan/config "${COMMON[@]}" --report-path reports/gitleaks-config.json
    ;;
  artifacts)
    target=${2:?usage: secrets_scan.sh artifacts <dir>}
    $GITLEAKS dir "$target" "${COMMON[@]}" --report-path reports/gitleaks-artifacts.json
    ;;
  *)
    echo "usage: $0 source | artifacts <dir>" >&2
    exit 64
    ;;
esac
