#!/usr/bin/env python3
"""Interactive local setup; does not publish lots or start the bot."""
import getpass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_env(path):
    values = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                k, v = raw.split("=", 1)
                values[k.strip()] = v.strip()
    return values


def main():
    path = ROOT / ".env"
    env = read_env(path)
    key = getpass.getpass("FunPay golden_key (Enter — оставить прежний): ").strip()
    if key:
        env["FUNPAY_GOLDEN_KEY"] = key
    env.setdefault("FUNPAY_GOLDEN_KEY", "")
    env.setdefault("DRY_RUN", "1")
    env.setdefault("AI_ENABLED", "1")
    env.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")
    env.setdefault("OLLAMA_MODEL", "qwen3:4b")
    env.setdefault("OLLAMA_TIMEOUT_SECONDS", "60")
    env.setdefault("DB_PATH", "data/bot.sqlite3")
    env.setdefault("CATALOG_PATH", "catalog.json")
    env.setdefault("FAQ_PATH", "faq.json")
    env.setdefault("RAISE_CATEGORY_IDS", "")
    env.setdefault("RAISE_INTERVAL_SECONDS", "3600")

    model = input(f"Ollama model [{env['OLLAMA_MODEL']}]: ").strip()
    if model:
        env["OLLAMA_MODEL"] = model
    cats = input(f"ID категорий для автоподнятия через запятую [{env['RAISE_CATEGORY_IDS']}]: ").strip()
    if cats:
        if any(not x.strip().isdigit() for x in cats.split(",") if x.strip()):
            raise SystemExit("ID категорий должны быть числами")
        env["RAISE_CATEGORY_IDS"] = cats

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
    path.chmod(0o600)
    print("Готово. Сначала оставь DRY_RUN=1 и проверь demo-order.")


if __name__ == "__main__":
    main()
