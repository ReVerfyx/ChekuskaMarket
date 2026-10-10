#!/usr/bin/env bash
# Run after uploading FunPay-Qwen-Assistant.zip into the home directory.
set -euo pipefail
archive="${1:-$HOME/FunPay-Qwen-Assistant.zip}"
project="${2:-$HOME/funpay-assistant}"
if [[ ! -f "$archive" ]]; then
  printf 'Архив не найден: %s\n' "$archive" >&2
  exit 1
fi
if [[ $(id -u) -eq 0 ]]; then
  apt-get update
  apt-get install -y python3-venv python3-pip unzip
else
  sudo apt-get update
  sudo apt-get install -y python3-venv python3-pip unzip
fi
mkdir -p "$project"
if [[ -f "$project/catalog.json" ]]; then
  cp -p "$project/catalog.json" "$project/catalog.json.before-update"
fi
unzip -o "$archive" -d "$project"
if [[ -f "$project/catalog.json.before-update" ]]; then
  python3 - "$project" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
before = json.loads((root / "catalog.json.before-update").read_text(encoding="utf-8"))
current = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
seen = {item["sku"] for item in current}
current.extend(item for item in before if item.get("kind") == "guide" and item.get("sku") not in seen)
(root / "catalog.json").write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(root / "catalog.json.before-update").unlink()
PY
fi
cd "$project"
bash setup.sh
printf '\nУстановлено в %s\n' "$project"
printf 'Для ИИ установите Ollama по официальной инструкции и выполните: ollama pull qwen3:4b\n'
printf 'Проверка: .venv/bin/python bot.py catalog\n'
printf 'Обработка заказов: .venv/bin/python bot.py run\n'
printf 'Поиск и лоты: .venv/bin/python autoseller.py loop\n'
