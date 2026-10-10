#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="${PROJECT_DIR:-/opt/funpay-assistant}"
cd "$PROJECT_DIR"
if [[ ! -d .git ]]; then
  echo "Каталог не является git-клоном: $PROJECT_DIR" >&2
  exit 1
fi
git fetch --prune origin
git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)"
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m py_compile *.py
if command -v systemctl >/dev/null 2>&1; then
  for service in funpay-assistant funpay-autoseller funpay-leveling-guard funpay-gift-notifier; do
    systemctl try-restart "$service.service" || true
  done
fi
echo "Обновление завершено"
