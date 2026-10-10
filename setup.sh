#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip wheel
.venv/bin/pip install -r requirements.txt

mkdir -p vendor data guides stock
if [[ ! -d vendor/FunPayAPI/.git ]]; then
  rm -rf vendor/FunPayAPI
  git clone --depth 1 https://github.com/MorikTV/FunPayAPI.git vendor/FunPayAPI
else
  git -C vendor/FunPayAPI pull --ff-only
fi

.venv/bin/python -m unittest -v
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
fi
printf '\nГотово. Настройка: .venv/bin/python setup.py\nПроверка: .venv/bin/python bot.py catalog\n'
