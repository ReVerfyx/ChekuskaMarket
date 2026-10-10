#!/usr/bin/env bash
set -Eeuo pipefail
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

if [[ ! -d .git ]]; then
  echo "Не git-клон: $PROJECT_DIR" >&2
  exit 1
fi

git fetch --prune origin
git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)"
.venv/bin/pip install -r requirements.txt
if [[ -d vendor/FunPayAPI/.git ]]; then
  git -C vendor/FunPayAPI pull --ff-only || true
fi
.venv/bin/python -m py_compile bot.py autoseller.py setup.py
.venv/bin/python -m unittest -v

if command -v systemctl >/dev/null 2>&1; then
  systemctl try-restart chekuska-funpay.service || true
  systemctl try-restart chekuska-raise.service || true
fi

echo "Обновление завершено"
