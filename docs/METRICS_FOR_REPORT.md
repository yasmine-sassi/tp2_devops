# Métriques mesurées pour le rapport de synthèse

Toutes les valeurs ci-dessous proviennent d'**exécutions réelles**. Aucune n'est estimée.

* **CI** : runs GitHub Actions du 12/11/2026 (runners `ubuntu-latest`). Les valeurs sont lues dans les annotations publiées par `quality_gate.py` / `confidentiality.py` sur chaque run.
* **Local** : `scripts/run_local_pipeline.sh` (Windows 11 + Docker Desktop), mêmes commandes que la CI. Valeurs relevées dans Prometheus (bonus observabilité).

## 1. Résultats CI par scénario

| | `main` | `demo/warning-coverage` | `demo/perf-fail` | `demo/leak` |
|---|---|---|---|---|
| Commit | `1857fab` | `e0eafd6` | `a03148c` | `676d021` |
| Tests unitaires | 32/32 OK | 36/36 OK | 32/32 OK | 32/32 OK |
| Couverture (lignes + branches) | **100.00 %** | **76.0 %** 🟠 | 100.00 % | 100.00 % |
| Tests d'intégration (Newman) | 24/24 assertions | 24/24 assertions | 24/24 assertions | non exécuté (fail fast) |
| Latence p95 (k6) | **1.6 ms** | 1.9 ms | **604 ms** 🔴 | non exécuté (fail fast) |
| Taux d'erreur HTTP (k6) | 0.00 % | 0.00 % | 0.00 % | non exécuté (fail fast) |
| Bugs bloquants / code smells | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| Fuites détectées (code + historique / config / artefacts) | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | **2** / 2 / non exécuté 🔴 |
| Taux de confidentialité | 100 % | 100 % | 100 % | **0 %** 🔴 |
| Classification | aucune erreur | 1 erreur **non critique** | 1 erreur **critique** | 1 erreur **critique** |
| Job fautif | - | (`unit-tests`, avertissement) | `performance` (k6 exit 99) | `secrets-scan` |
| Jobs annulés (fail fast) | - | - | quality-gate, deploy | build-integration, artifact-scan, performance, quality-gate, deploy |
| Décision | ✅ **déployé** | 🟠 **déployé avec avertissement** | 🔴 **bloqué**, pas de déploiement | 🔴 **bloqué**, pas de déploiement |
| Run CI | [43212339911](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212339911) | [43212346706](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212346706) | [43212355133](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212355133) | [43212363578](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212363578) |

Détail des 2 fuites de `demo/leak` (fichier `config/payment-gateway.properties`) :

| Règle Gitleaks | Ligne | Contenu (masqué par `--redact`) | Vu par |
|---|---|---|---|
| `payflex-api-token` | 6 | jeton `pfx_live_…` factice | scan code + historique **et** scan configuration |
| `pii-credit-card-pan` | 7 | numéro Visa de test (se terminant par 1111) | scan code + historique **et** scan configuration |

Calcul : N = 2 fuites uniques (règle, fichier, ligne) et S_max = 0, d'où Taux = max(0, 1 − 2 / max(0, 1)) × 100 = **0 %**.

Messages réellement émis par le pipeline (annotations des runs) :

* `demo/warning-coverage` : `warning` « **Attention, couverture à 76.0 % (objectif 80 %)** ». Rapport : « Statut SUCCESS_WITH_WARNINGS - 1 avertissement(s) - déploiement exécuté ».
* `demo/perf-fail` : `error` « Job performance | Performance : latence p95 = 604 ms | seuil violé : > 500 ms (critique) | Latence p95 de 604 ms, au-delà de 500 ms | action : Profiler les endpoints lents (latence p95), vérifier la configuration (ex. SIMULATED_LATENCY_MS) et les dépendances avant de relancer. »
* `demo/leak` : `error` « Taux de confidentialité 0 % < 100 % (2 fuite(s) ; source : 2, config : 2) : pipeline bloqué » et une annotation par fuite.

## 2. Résultats locaux (mêmes scénarios, `scripts/run_local_pipeline.sh`)

| | `main` | `demo/warning-coverage` | `demo/perf-fail` | `demo/leak` |
|---|---|---|---|---|
| Couverture | 100 % | 76.05 % | 100 % | 100 % |
| Latence p95 (k6, 10 VUs, 30 s) | 5.48 ms | 5.27 ms | 608.27 ms | non exécuté |
| Requêtes k6 / taux d'erreur | 11 002 / 0 % | 11 038 / 0 % | 642 / 0 % | non exécuté |
| Fuites / taux de confidentialité | 0 / 100 % | 0 / 100 % | 0 / 100 % | 2 / 0 % |
| Erreurs critiques / avertissements | 0 / 0 | 0 / 1 | 1 / 0 | 1 / 0 |
| Décision | déployé | déployé avec avertissement | bloqué | bloqué |

Autres mesures locales utiles pour le rapport :

* **Optimisation des performances** : le premier tir k6 donnait **p95 = 241.65 ms**, dans la zone d'avertissement. Après passage des handlers en `async`, remplacement de `BaseHTTPMiddleware` par un middleware ASGI pur et adoption de `uvicorn[standard]`, le p95 est descendu à **9.38 ms**. Le débit est passé de 81 à 355 requêtes/s (commit `dcee84e`).
* **SonarQube Community 10.7** (local) : Quality Gate `OK`, 0 bug, 0 vulnérabilité, 0 code smell, couverture importée 100 %.
* **Auto-test des règles Gitleaks** : 5/5 vrais positifs détectés (3 PAN Visa/Mastercard/Amex, 1 jeton PayFlex, 1 clé privée RSA). Le PAN masqué `**** **** **** 1111` et un timestamp à 13 chiffres ne sont pas signalés.
* **Faux positif traité** : `GPG_KEY` dans la configuration de l'image `python:3.11-slim` (empreinte publique de la clé CPython), détecté par la règle `generic-api-key` lors du scan des artefacts. Il est mis en liste blanche de façon ciblée dans `.gitleaks.toml`.

## 3. Extrait fidèle du workflow (`.github/workflows/main.yml`)

### Déclencheurs et variables globales (lignes 16-37)

```yaml
on:
  push:
    branches: [main, "demo/**"]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PYTHON_VERSION: "3.11"
  GITLEAKS_VERSION: "8.21.2"
  # Latence artificielle injectée dans l'API (ms). Scénario demo/perf-fail : 600.
  SIMULATED_LATENCY_MS: "0"
  K6_VUS: "10"
  K6_DURATION: "30s"
  PYTHONIOENCODING: utf-8
```

### Job de performance : seuils critiques k6, Quality Gate de périmètre, nettoyage en `if: always()` (lignes 311-364)

```yaml
  performance:
    name: "4 · Tests de performance (k6)"
    needs: artifact-scan
    runs-on: ubuntu-latest
    env:
      IMAGE_TAG: ci
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      - uses: actions/download-artifact@v8
        with:
          name: docker-image
      - name: Charger l'image testée en intégration
        run: gunzip -c payflex-api-image.tar.gz | docker load
      - name: Mot de passe éphémère du client de test
        run: |
          password=$(openssl rand -hex 16)
          echo "::add-mask::$password"
          echo "PAYFLEX_DEMO_PASSWORD=$password" >> "$GITHUB_ENV"
      - name: Lancement du conteneur éphémère
        run: |
          mkdir -p reports metrics && chmod 777 reports
          echo "SIMULATED_LATENCY_MS=${SIMULATED_LATENCY_MS}"
          docker compose up --no-build -d --wait api
      - name: Tir de charge k6 (seuils critiques p95 <= 500 ms, erreurs <= 2 %)
        run: docker compose --profile perf run --rm k6
      - name: Logs et destruction de l'environnement
        if: always()
        run: |
          docker compose logs --no-color api > reports/api-performance.log || true
          docker compose down -v --remove-orphans
      - name: Métriques + Quality Gate (perf) - avertissement si 200 ms <= p95 <= 500 ms
        if: always()
        run: |
          if [ -f reports/k6-summary.json ]; then
            python scripts/collect_metrics.py perf --output metrics/perf.json > /dev/null
          fi
          python scripts/quality_gate.py evaluate --scope perf --require perf
      - name: Publier les métriques
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: metrics-performance
          path: metrics/
          if-no-files-found: ignore
      - name: Publier les rapports k6
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: reports-performance
          path: reports/
          if-no-files-found: ignore
```

### Déploiement conditionné au Quality Gate et rapport d'incident toujours exécuté (lignes 394-459)

```yaml
  deploy:
    name: "6 · Déploiement (simulé)"
    needs: quality-gate
    if: github.event_name != 'pull_request'
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: Déploiement simulé
        run: |
          echo "Déploiement de payflex-api:${GITHUB_SHA::12} sur l'environnement production (simulé)"
          {
            echo "### 🚀 Déploiement simulé"
            echo ""
            echo "| Élément | Valeur |"
            echo "|---|---|"
            echo "| Image | \`payflex-api:${GITHUB_SHA::12}\` |"
            echo "| Branche | \`${GITHUB_REF_NAME}\` |"
            echo "| Environnement | production (simulé) |"
          } >> "$GITHUB_STEP_SUMMARY"

  # ------------------------------------------------------------------ toujours
  incident-report:
    name: "Rapport d'incident / de pipeline"
    needs:
      - secrets-scan
      - static-analysis
      - unit-tests
      - sonarqube
      - build-integration
      - artifact-scan
      - performance
      - quality-gate
      - deploy
    if: always()
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: ${{ env.PYTHON_VERSION }}
      - uses: actions/download-artifact@v8
        continue-on-error: true
        with:
          pattern: metrics-*
          path: metrics
          merge-multiple: true
      - name: Classification des erreurs et rapport
        env:
          NEEDS_JSON: ${{ toJSON(needs) }}
        run: python scripts/quality_gate.py report --metrics-dir metrics --output-dir reports
      - name: Export Prometheus des indicateurs (bonus observabilité)
        if: always()
        env:
          PUSHGATEWAY_URL: ${{ secrets.PUSHGATEWAY_URL }}
        run: |
          python scripts/export_prometheus.py --output reports/pipeline_metrics.prom \
            ${PUSHGATEWAY_URL:+--push-url "$PUSHGATEWAY_URL"}
      - name: Publier le rapport
        if: always()
        uses: actions/upload-artifact@v7
        with:
          name: incident-report
          path: |
            reports/incident_report.md
            reports/incident_report.json
            reports/pipeline_metrics.prom
```
