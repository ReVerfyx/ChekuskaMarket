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

# Migrate old local settings so a normal update actually switches the running bot.
if [[ -f .env ]]; then
  sed -i -E 's/^OLLAMA_MODEL=(qwen3:4b|qwen2\.5:3b-instruct)$/OLLAMA_MODEL=qwen2.5:1.5b-instruct/' .env
  sed -i -E 's/^AUTOPUBLISH_QWEN_NUM_PREDICT=(550|1600)$/AUTOPUBLISH_QWEN_NUM_PREDICT=480/' .env
  sed -i -E 's/^AUTOPUBLISH_QWEN_NUM_CTX=4096$/AUTOPUBLISH_QWEN_NUM_CTX=3072/' .env
  sed -i -E 's/^AUTOPUBLISH_SEARCH_BACKENDS=.*/AUTOPUBLISH_SEARCH_BACKENDS=yahoo,startpage,wikipedia,grokipedia,mojeek/' .env
fi
.venv/bin/pip install -r requirements.txt
if [[ -d vendor/FunPayAPI/.git ]]; then
  git -C vendor/FunPayAPI pull --ff-only || true
fi
.venv/bin/python -m py_compile bot.py autoseller.py setup.py
.venv/bin/python -m unittest -v

if command -v ollama >/dev/null 2>&1 && [[ -f .env ]]; then
  MODEL="$(grep -E '^OLLAMA_MODEL=' .env | tail -1 | cut -d= -f2- || true)"
  if [[ -n "$MODEL" ]]; then
    echo "[+] Загружаю модель Ollama: $MODEL"
    ollama pull "$MODEL"
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl try-restart chekuska-funpay.service || true
  systemctl try-restart chekuska-raise.service || true
fi

echo "Обновление завершено"
