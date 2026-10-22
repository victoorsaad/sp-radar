#!/usr/bin/env bash
# Daily QuintoAndar crawl (sale + rent). Logs to logs/cron.log
set -uo pipefail
cd "$(dirname "$0")"
for kind in sale rent; do
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] starting pipeline: $kind" >> logs/cron.log
  ./venv/bin/python -m quintoandar.pipeline --kind "$kind" --delay 0.2 >> logs/cron.log 2>&1 \
    || echo "[$(date +%Y-%m-%dT%H:%M:%S)] $kind FAILED" >> logs/cron.log
  echo "[$(date +%Y-%m-%dT%H:%M:%S)] done: $kind" >> logs/cron.log
done
# favoritos: ficha + avaliação do QuintoAndar (novos, preço mudou ou com mais de 7 dias)
echo "[$(date +%Y-%m-%dT%H:%M:%S)] enrich favorites" >> logs/cron.log
./venv/bin/python -m quintoandar.enrich --favorites >> logs/cron.log 2>&1 \
  || echo "[$(date +%Y-%m-%dT%H:%M:%S)] enrich FAILED" >> logs/cron.log
