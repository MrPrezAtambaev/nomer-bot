#!/bin/bash
# Обновить бота на машине Google Cloud: копирует код и перезапускает сервис. Состояние и .env не трогает.
# SSH на машину открыт только через туннель Google IAP, поэтому везде --tunnel-through-iap.
set -euo pipefail
PROJECT=${PROJECT:-meta-strata-380705}; ZONE=${ZONE:-us-central1-a}; VM=${VM:-nomer-bot}
cd "$(dirname "$0")/.."
python3 -m py_compile bot.py card.py forecast.py nice.py render.py series.py srs.py
gcloud compute scp --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap --quiet \
  bot.py card.py forecast.py nice.py render.py series.py srs.py deploy/nomer-bot.service "$VM":/tmp/
gcloud compute ssh "$VM" --project "$PROJECT" --zone "$ZONE" --tunnel-through-iap --quiet --command '
  set -e
  sudo install -o nomerbot -g nomerbot -m 644 /tmp/bot.py /tmp/card.py /tmp/forecast.py /tmp/nice.py /tmp/render.py /tmp/series.py /tmp/srs.py /opt/nomer-bot/
  sudo install -m 644 /tmp/nomer-bot.service /etc/systemd/system/nomer-bot.service
  sudo systemctl daemon-reload
  sudo systemctl restart nomer-bot
  sleep 6
  sudo journalctl -u nomer-bot -n 8 --no-pager -o cat'
