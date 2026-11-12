# PayFlex API : pipeline CI/CD intelligent

[![PayFlex CI/CD](https://github.com/yasmine-sassi/tp2_devops/actions/workflows/main.yml/badge.svg?branch=main)](https://github.com/yasmine-sassi/tp2_devops/actions/workflows/main.yml)

API REST de services financiers (FastAPI) servant de cible à un pipeline GitHub Actions. Le pipeline enchaîne :

* tests multi-niveaux : unitaires, intégration, performance ;
* détection de fuites de données confidentielles et calcul du **taux de confidentialité** ;
* **Quality Gate** qui classe automatiquement les erreurs en **non critiques** (avertissement, le pipeline continue) et **critiques** (fail fast, pas de déploiement, rapport d'incident).

Conception détaillée : schéma du pipeline et logique de classification dans [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Valeurs mesurées : [docs/METRICS_FOR_REPORT.md](docs/METRICS_FOR_REPORT.md).

## Stack

| Rôle | Outil |
|---|---|
| Orchestrateur | GitHub Actions ([.github/workflows/main.yml](.github/workflows/main.yml)) |
| API | Python 3.11, FastAPI, Uvicorn, PyJWT |
| Tests unitaires + couverture | PyTest + pytest-cov (`coverage.xml`, rapport HTML, JUnit) |
| Tests d'intégration | Docker Compose (conteneur éphémère + healthcheck) + Postman/Newman |
| Performance | k6 (`--summary-export`) |
| Qualité | Ruff + Bandit (par défaut), SonarQube Community (local + job CI optionnel) |
| Confidentialité | Gitleaks + [.gitleaks.toml](.gitleaks.toml) (PII carte bancaire, jeton PayFlex) |
| Quality Gate | [scripts/quality_gate.py](scripts/quality_gate.py), [scripts/confidentiality.py](scripts/confidentiality.py) |

## API

| Méthode | Route | Auth | Description |
|---|---|---|---|
| GET | `/health` | - | État du service |
| POST | `/auth/token` | - | Jeton Bearer (JWT HS256) à partir de `username` / `password` |
| GET | `/accounts` | Bearer | Comptes de l'utilisateur |
| POST | `/accounts` | Bearer | Création de compte (`holder_name`, `currency` EUR/USD/TND, `initial_balance`) |
| POST | `/payments` | Bearer | Paiement par carte (contrôle de Luhn, solde, devise). Le PAN est **masqué** dans la réponse |
| GET | `/transactions` | Bearer | Historique (`account_id`, `limit`), du plus récent au plus ancien |

Documentation OpenAPI : `http://localhost:8000/docs`.

Les numéros de carte ne sont jamais renvoyés ni journalisés en clair (`**** **** **** 1234`), y compris dans les erreurs de validation 422. Les secrets proviennent uniquement de l'environnement :

| Variable | Rôle |
|---|---|
| `PAYFLEX_DEMO_PASSWORD` | Mot de passe du client de démonstration (sans valeur, toute authentification est refusée) |
| `PAYFLEX_DEMO_USER` | Utilisateur de démonstration, `demo` par défaut |
| `PAYFLEX_JWT_SECRET` | Clé de signature des jetons, aléatoire par défaut |
| `SIMULATED_LATENCY_MS` | Latence artificielle, utilisée par le scénario de démo |

## Lancement en local

Prérequis : Python 3.10+, Docker avec Compose v2, Git Bash sous Windows. Newman, k6 et Gitleaks sont lancés via leurs images Docker, aucune installation n'est nécessaire.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : source .venv/Scripts/activate
pip install -r requirements-dev.txt

# Tests unitaires + couverture (échec sous 70 %)
pytest --cov=app --cov-report=term --cov-report=xml:coverage.xml \
       --cov-report=html:reports/coverage-html --junitxml=reports/junit.xml --cov-fail-under=70

# Environnement éphémère + tests d'intégration + tir de charge
export PAYFLEX_DEMO_PASSWORD=$(python -c "import secrets; print(secrets.token_hex(16))")
docker compose up -d --build --wait api          # attend le healthcheck
docker compose --profile it run --rm newman      # tests d'intégration Newman
docker compose --profile perf run --rm k6        # k6, résumé dans reports/k6-summary.json
docker compose down -v                           # destruction de l'environnement

# Détection de fuites (Gitleaks via Docker)
GITLEAKS=scripts/gitleaks_docker.sh bash scripts/secrets_scan.sh source

# Le pipeline complet, dans le même ordre que la CI (fail fast + rapport d'incident)
bash scripts/run_local_pipeline.sh
SIMULATED_LATENCY_MS=600 bash scripts/run_local_pipeline.sh   # scénario d'échec de performance
```

Résultats : `metrics/*.json` (métriques normalisées), `reports/` (rapports bruts, `quality_gate.json`, `incident_report.md` et `incident_report.json`).

### SonarQube Community en local

```bash
docker compose --profile sonar up -d sonarqube        # http://localhost:9000 (admin/admin, à changer)
# Créer un token de type « User Token » (Mon compte > Sécurité), puis :
docker run --rm --network payflex_default -e SONAR_HOST_URL=http://sonarqube:9000 \
  -e SONAR_TOKEN=<token> -v "$PWD:/usr/src" sonarsource/sonar-scanner-cli:11.1 -Dsonar.qualitygate.wait=true
python scripts/collect_metrics.py sonar --host http://localhost:9000 --token <token> --output metrics/sonar.json
python scripts/quality_gate.py evaluate --scope static
```

Pour activer le job `sonarqube` en CI, ajoute les secrets du dépôt `SONAR_HOST_URL` (un serveur joignable depuis GitHub) et `SONAR_TOKEN`. Il faut un *User Token* : un *Global Analysis Token* ne peut pas lire l'API utilisée pour collecter les métriques. Sans ces secrets, le job reste neutre (annotation `notice`) et l'analyse statique repose sur Ruff et Bandit.

## Jobs du pipeline

| # | Job | Contenu | Échoue (critique) si... |
|---|---|---|---|
| 1 | `secrets-scan` | Gitleaks sur le code + **tout l'historique** de la branche, puis sur les fichiers de configuration, et calcul du taux de confidentialité | au moins une fuite (taux < 100 %) |
| 1 | `static-analysis` | Ruff (smells + bugs potentiels) et Bandit (sécurité de `app/`) | bug bloquant : Bandit HIGH, Ruff F821/F823/E9... |
| 1 | `unit-tests` | PyTest + couverture (XML, HTML, JUnit) | test en échec ou couverture < 70 % |
| 1 | `sonarqube` | Optionnel : scanner + collecte via l'API Sonar | issue BLOCKER/CRITICAL de type bug ou vulnérabilité |
| 2 | `build-integration` | Build de l'image, conteneur éphémère (`--wait` sur le healthcheck), Newman, export du FS de l'image, `compose down` en `if: always()` | assertion Newman en échec, conteneur non sain |
| 3 | `artifact-scan` | Gitleaks sur le FS de l'image, sa configuration, les logs de l'API et tous les rapports du pipeline | au moins une fuite dans les artefacts |
| 4 | `performance` | k6 sur l'**image testée en intégration** (`docker save` / `docker load`), 10 VUs pendant 30 s | p95 > 500 ms ou erreurs > 2 % |
| 5 | `quality-gate` | Agrège toutes les métriques et exige leur présence | une erreur critique ou une métrique absente |
| 6 | `deploy` | Déploiement **simulé** (environnement `production`) | - (n'est exécuté que si le gate passe) |
| - | `incident-report` | `if: always()` : classification + `incident_report.md` / `.json` en artefact et dans le résumé du run | - |

### Seuils

| Indicateur | Non critique : avertissement, le pipeline continue | Critique : arrêt immédiat |
|---|---|---|
| Couverture | 70 % ≤ C < 80 % | C < 70 % |
| Bugs / smells | code smells | bug bloquant / crash potentiel |
| Latence p95 (k6) | 200 à 500 ms | > 500 ms |
| Taux d'erreur (k6) | 1 à 2 % | > 2 % |
| Confidentialité | - | taux < 100 % |
| Tests UT / IT | - | au moins un échec |

Taux de confidentialité = `max(0, 1 − N_fuites / max(S_max, 1)) × 100 %` avec `S_max = 0`. La formule du sujet divise par `S_max = 0` ; la convention `max(S_max, 1)` donne 100 % sans fuite et 0 % dès la première fuite (voir [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#3-taux-de-confidentialité)).

Les seuils du gate peuvent être ajustés par variables d'environnement : `QG_COVERAGE_TARGET`, `QG_COVERAGE_MIN`, `QG_P95_WARN_MS`, `QG_P95_CRIT_MS`, `QG_ERROR_WARN_PCT`, `QG_ERROR_CRIT_PCT`.

## Scénarios de démonstration

| Branche | Modification | Comportement attendu |
|---|---|---|
| `main` | - | tout vert, déploiement exécuté |
| `demo/warning-coverage` | fonctionnalité « relevés de compte » livrée avec un seul test partiel | couverture d'environ 76 %, avertissement, le pipeline continue et déploie |
| `demo/perf-fail` | `SIMULATED_LATENCY_MS: "600"` dans le workflow | p95 > 500 ms, job `performance` en échec, gate et déploiement annulés, rapport d'incident |
| `demo/leak` | faux jeton PayFlex + numéro de carte de test committés dans `config/` | Gitleaks détecte les fuites, taux < 100 %, arrêt dès l'étape 1, rapport d'incident |

Les faux secrets n'existent **que** sur `demo/leak`, jamais dans l'historique de `main`. Le scan de `main` ne porte que sur l'historique de la branche (`--log-opts="--full-history HEAD"`).

Captures à prendre pour le rapport : [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md).

## Structure du dépôt

```
app/                    API FastAPI (routes, schémas, stockage mémoire, masquage PAN, sécurité)
tests/                  tests unitaires PyTest (PAN de test générés via Luhn)
postman/                collection Newman (contrats JSON Schema + scénario) et environnement
k6/load.js              tir de charge et seuils critiques
scripts/
  quality_gate.py       Quality Gate : évaluation, classification, rapport d'incident
  confidentiality.py    taux de confidentialité à partir des rapports Gitleaks
  collect_metrics.py    normalisation JUnit / coverage / k6 / Ruff / Bandit / Sonar en JSON
  secrets_scan.sh       Gitleaks : code + historique, configuration, artefacts
  run_local_pipeline.sh exécution locale du pipeline complet
Dockerfile, docker-compose.yml, .gitleaks.toml, sonar-project.properties
.github/workflows/main.yml
docs/                   architecture, métriques mesurées, liste des captures
```
