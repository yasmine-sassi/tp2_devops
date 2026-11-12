# Captures d'écran à prendre pour le rapport de synthèse

Enregistre les fichiers dans `docs/screenshots/` avec **exactement** ces noms. Chaque capture correspond à un run réel, listé dans [METRICS_FOR_REPORT.md](METRICS_FOR_REPORT.md).

| # | Fichier | Page à ouvrir | Ce qui doit être visible |
|---|---|---|---|
| 1 | `pipeline-succes.png` | [run main 43212339911](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212339911) | Le graphe des jobs entièrement vert (10 jobs, du job 1 au déploiement simulé), le statut « Success », et sous le graphe les annotations `notice` « OK - … » (couverture 100.00 %, p95 1.6 ms, taux de confidentialité 100 %) |
| 2 | `pipeline-warning.png` | [run demo/warning-coverage 43212346706](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212346706) | Le graphe entièrement vert **avec** l'annotation jaune `warning` « Attention, couverture à 76.0 % (objectif 80 %) » et le job « 6 · Déploiement (simulé) » réussi : le pipeline a continué |
| 3 | `pipeline-echec-perf.png` | [run demo/perf-fail 43212355133](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212355133) | Le job « 4 · Tests de performance (k6) » en rouge, les jobs « 5 · Quality Gate global » et « 6 · Déploiement » grisés (*skipped*), et l'annotation `error` « CRITIQUE - Performance : latence p95 … 604 ms, au-delà de 500 ms » |
| 4 | `pipeline-echec-fuite.png` | [run demo/leak 43212363578](https://github.com/yasmine-sassi/tp2_devops/actions/runs/43212363578) | Le job « 1 · Confidentialité (code, historique, config) » en rouge, tous les jobs suivants *skipped*, et les annotations `error` « Taux de confidentialité 0 % < 100 % (2 fuite(s) …) » et « Fuite de donnée confidentielle » |
| 5 | `rapport-incident.png` | Run demo/perf-fail ou demo/leak, puis clic sur le job « Rapport d'incident / de pipeline » et défilement jusqu'au **résumé** du job (ou ouverture de l'artefact `incident-report` > `incident_report.md`) | Le titre « 🔴 Rapport d'incident : déploiement BLOQUÉ », le tableau de contexte (branche, commit, URL du run) et la section « Cause exacte du blocage » : job fautif, indicateur, valeur mesurée, seuil violé, classification, action recommandée |

## Conseils

* Prends les captures en étant connecté à GitHub : sinon, les artefacts et les logs détaillés ne sont pas accessibles.
* Pour la capture 5, le rapport s'affiche directement dans l'onglet **Summary** du run, en bas de page, dans la section du job « Rapport d'incident / de pipeline ».
* Pour les captures 3 et 4, un zoom navigateur à 80 % permet de faire tenir le graphe complet et les annotations sur un seul écran.

## Capture facultative (bonus observabilité)

| Fichier | Comment l'obtenir |
|---|---|
| `grafana-pipeline.png` (facultatif) | `docker compose --profile monitoring up -d`, puis exécuter `PUSHGATEWAY_URL=http://localhost:9091 bash scripts/run_local_pipeline.sh` sur chaque branche (avec `SIMULATED_LATENCY_MS=600` pour `demo/perf-fail`). Ouvrir http://localhost:3000, tableau de bord « PayFlex - indicateurs du pipeline CI/CD », et capturer la rangée « Comparaison des branches » |
