#!/usr/bin/env python3
"""Interactive local setup. Does not connect to FunPay or start the bot."""
import getpass
import os
from pathlib import Path

import bot


def main():
    print("Настройка FunPay помощника. Ввод не публикует лоты и не запускает продажи.")
    path = bot.ROOT / ".env"
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                existing[key] = value
    key = getpass.getpass("Golden Key (Enter — оставить прежний): ").strip()
    if key:
        if "\n" in key or "\r" in key:
            raise ValueError("Недопустимый ключ")
        existing["FUNPAY_GOLDEN_KEY"] = key
    existing.setdefault("FUNPAY_GOLDEN_KEY", "")
    existing.setdefault("DRY_RUN", "1")
    existing.setdefault("OLLAMA_URL", "http://127.0.0.1:11434")
    existing.setdefault("OLLAMA_MODEL", "qwen3:4b")
    existing.setdefault("DB_PATH", "data/bot.sqlite3")
    existing.setdefault("CATALOG_PATH", "catalog.json")
    existing.setdefault("PRICES_PATH", "prices.json")
    existing.setdefault("AUTO_SCAN_INTERVAL_SECONDS", "1800")
    existing.setdefault("AUTO_PUBLISH_DELAY_SECONDS", "15")
    existing.setdefault("OLLAMA_TIMEOUT_SECONDS", "180")
    model = input(f"Модель Ollama [{existing['OLLAMA_MODEL']}]: ").strip()
    if model:
        if "\n" in model or "=" in model:
            raise ValueError("Недопустимое имя модели")
        existing["OLLAMA_MODEL"] = model
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")
    path.chmod(0o600)
    print("Цены каждого товара задаются отдельно; Enter сохраняет прежнюю цену.")
    current = bot.prices()
    for item in bot.catalog():
        value = input(f"{item['sku']} ({item['kind']}) [{current.get(item['sku'], 'не задана')}]: ").strip()
        if value:
            bot.set_price(item["sku"], value)
    print("Готово. Режим DRY_RUN=1 до проверки заказов и лотов.")


if __name__ == "__main__":
    main()
