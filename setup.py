#!/usr/bin/env python3
"""Interactive first-run wizard for ChekuskaMarket."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def ask(prompt: str, default: str) -> str:
    value = input(f"{prompt} [{default}]: ").strip()
    return value or default


def ask_bool(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        value = input(f"{prompt} [{suffix}]: ").strip().casefold()
        if not value:
            return default
        if value in {"y", "yes", "д", "да"}:
            return True
        if value in {"n", "no", "н", "нет"}:
            return False
        print("Ответь да/нет.")


def ask_int(prompt: str, default: int, minimum: int = 0) -> int:
    while True:
        raw = input(f"{prompt} [{default}]: ").strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            print("Нужно целое число.")
            continue
        if value < minimum:
            print(f"Минимум: {minimum}.")
            continue
        return value


def ask_float(prompt: str, default: float, minimum: float = 0.0) -> float:
    while True:
        raw = input(f"{prompt} [{default:g}]: ").strip().replace(",", ".")
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            print("Нужно число.")
            continue
        if value < minimum:
            print(f"Минимум: {minimum:g}.")
            continue
        return value


def safe_value(value: str, name: str) -> str:
    if any(x in value for x in ("\n", "\r", "\x00")):
        raise SystemExit(f"{name}: недопустимое значение")
    return value.strip()


def write_env(path: Path, env: dict[str, str]) -> None:
    preferred = [
        "FUNPAY_GOLDEN_KEY", "DRY_RUN", "AI_ENABLED",
        "OLLAMA_URL", "OLLAMA_MODEL", "OLLAMA_TIMEOUT_SECONDS",
        "DB_PATH", "CATALOG_PATH", "FAQ_PATH",
        "RAISE_CATEGORY_IDS", "RAISE_INTERVAL_SECONDS",
        "AUTOPUBLISH_ENABLED", "AUTOPUBLISH_PRICE",
        "AUTOPUBLISH_SEARCH_RESULTS", "AUTOPUBLISH_MIN_SOURCES",
        "AUTOPUBLISH_QWEN_TIMEOUT_SECONDS", "AUTOPUBLISH_QWEN_NUM_PREDICT",
        "AUTOPUBLISH_QWEN_NUM_CTX", "AUTOPUBLISH_QWEN_MAX_SOURCES",
        "AUTOPUBLISH_QWEN_KEEP_ALIVE", "AUTOPUBLISH_LOG_HEARTBEAT_SECONDS",
        "AUTOPUBLISH_FUNPAY_FORM_TIMEOUT_SECONDS", "AUTOPUBLISH_FUNPAY_SAVE_TIMEOUT_SECONDS",
        "AUTOPUBLISH_GUIDE_MAX_CHARS", "AUTOPUBLISH_STOCK_COPIES",
        "AUTOPUBLISH_DELAY_SECONDS", "AUTOPUBLISH_INTERVAL_SECONDS",
        "AUTOPUBLISH_MAX_NEW_PER_CYCLE",
    ]
    keys = preferred + sorted(k for k in env if k not in preferred)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(f"{key}={env.get(key, '')}" for key in keys) + "\n")
    path.chmod(0o600)


def test_ollama(env: dict[str, str]) -> tuple[bool, str]:
    if env.get("AI_ENABLED") != "1":
        return True, "ИИ отключён"
    url = env.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(url + "/api/tags", timeout=4) as response:
            data = json.load(response)
        names = {str(x.get("name", "")) for x in data.get("models", [])}
        model = env.get("OLLAMA_MODEL", "")
        if any(name == model or name.startswith(model + ":") for name in names):
            return True, f"Ollama работает, {model} уже загружена"
        return True, f"Ollama работает; модель {model} установщик скачает после мастера"
    except Exception as exc:
        return False, f"Ollama пока недоступна ({type(exc).__name__}); установщик запустит её после мастера"


def test_funpay(env: dict[str, str]) -> tuple[bool, str]:
    key = env.get("FUNPAY_GOLDEN_KEY", "").strip()
    if not key:
        return False, "golden_key пустой"
    vendor = ROOT / "vendor"
    if vendor.exists():
        sys.path.insert(0, str(vendor))
    try:
        from FunPayAPI import Account
        account = Account(golden_key=key)
        account.get()
        return True, f"FunPay: вход успешен как {getattr(account, 'username', '?')}"
    except Exception as exc:
        return False, f"FunPay: вход не прошёл ({type(exc).__name__})"


def main() -> None:
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║      🤖 ChekuskaMarket — первый запуск      ║")
    print("╚══════════════════════════════════════════════╝")
    print("Настроим FunPay, Qwen и автопубликацию. Golden Key на экране не показывается.")
    print()

    path = ROOT / ".env"
    env = read_env(path)

    old_key = env.get("FUNPAY_GOLDEN_KEY", "")
    label = "FunPay golden_key (Enter — оставить сохранённый)" if old_key else "FunPay golden_key"
    key = getpass.getpass(label + ": ").strip()
    if key:
        env["FUNPAY_GOLDEN_KEY"] = safe_value(key, "FUNPAY_GOLDEN_KEY")
    elif not old_key:
        raise SystemExit("Без FUNPAY_GOLDEN_KEY бот не сможет подключиться к FunPay.")

    ai = ask_bool("Использовать локальный Qwen", env.get("AI_ENABLED", "1") == "1")
    env["AI_ENABLED"] = "1" if ai else "0"
    env["OLLAMA_URL"] = safe_value(env.get("OLLAMA_URL", "http://127.0.0.1:11434"), "OLLAMA_URL")
    env["OLLAMA_MODEL"] = safe_value(
        ask("Модель Ollama", env.get("OLLAMA_MODEL", "qwen2.5:3b-instruct")),
        "OLLAMA_MODEL",
    )
    env["OLLAMA_TIMEOUT_SECONDS"] = str(
        ask_int("Таймаут Qwen, секунд", int(env.get("OLLAMA_TIMEOUT_SECONDS", "90")), 10)
    )

    auto = ask_bool("ИИ сам создаёт по одному лоту в каждой игре", env.get("AUTOPUBLISH_ENABLED", "1") == "1")
    env["AUTOPUBLISH_ENABLED"] = "1" if auto else "0"
    price = ask_float("Базовая цена авто-лота, ₽", float(env.get("AUTOPUBLISH_PRICE", "1")), 0.01)
    env["AUTOPUBLISH_PRICE"] = f"{price:g}"
    env["AUTOPUBLISH_SEARCH_RESULTS"] = str(
        ask_int("Результатов поиска на запрос", int(env.get("AUTOPUBLISH_SEARCH_RESULTS", "8")), 4)
    )
    env["AUTOPUBLISH_MIN_SOURCES"] = str(
        ask_int("Минимум разных источников для публикации", int(env.get("AUTOPUBLISH_MIN_SOURCES", "2")), 2)
    )
    env["AUTOPUBLISH_QWEN_TIMEOUT_SECONDS"] = str(
        ask_int("Жёсткий таймаут Qwen на одну игру, секунд", int(env.get("AUTOPUBLISH_QWEN_TIMEOUT_SECONDS", "120")), 120)
    )
    env.setdefault("AUTOPUBLISH_QWEN_NUM_PREDICT", "550")
    env.setdefault("AUTOPUBLISH_QWEN_NUM_CTX", "4096")
    env.setdefault("AUTOPUBLISH_QWEN_MAX_SOURCES", "6")
    env.setdefault("AUTOPUBLISH_QWEN_KEEP_ALIVE", "30m")
    env.setdefault("AUTOPUBLISH_LOG_HEARTBEAT_SECONDS", "5")
    env.setdefault("AUTOPUBLISH_FUNPAY_FORM_TIMEOUT_SECONDS", "60")
    env.setdefault("AUTOPUBLISH_FUNPAY_SAVE_TIMEOUT_SECONDS", "60")
    env["AUTOPUBLISH_DELAY_SECONDS"] = str(
        ask_int("Пауза между публикациями, секунд", int(float(env.get("AUTOPUBLISH_DELAY_SECONDS", "15"))), 5)
    )
    env["AUTOPUBLISH_INTERVAL_SECONDS"] = str(
        ask_int("Повторный проход по категориям, секунд", int(env.get("AUTOPUBLISH_INTERVAL_SECONDS", "1800")), 60)
    )
    env["AUTOPUBLISH_MAX_NEW_PER_CYCLE"] = str(
        ask_int("Максимум новых игр за проход (0 = без лимита)", int(env.get("AUTOPUBLISH_MAX_NEW_PER_CYCLE", "0")), 0)
    )
    env.setdefault("AUTOPUBLISH_GUIDE_MAX_CHARS", "3200")
    env["AUTOPUBLISH_STOCK_COPIES"] = str(
        ask_int("Запас одинаковых выдач в одном авто-лоте", int(env.get("AUTOPUBLISH_STOCK_COPIES", "50")), 2)
    )

    env.setdefault("DB_PATH", "data/bot.sqlite3")
    env.setdefault("CATALOG_PATH", "catalog.json")
    env.setdefault("FAQ_PATH", "faq.json")
    env.setdefault("RAISE_CATEGORY_IDS", "")
    env.setdefault("RAISE_INTERVAL_SECONDS", "3600")

    live = ask_bool("Сразу запустить реальные продажи и публикацию", True)
    env["DRY_RUN"] = "0" if live else "1"

    write_env(path, env)

    print()
    print("Проверка настроек:")
    ok_funpay, msg_funpay = test_funpay(env)
    print(("✅ " if ok_funpay else "❌ ") + msg_funpay)
    ok_ollama, msg_ollama = test_ollama(env)
    print(("✅ " if ok_ollama else "ℹ️ ") + msg_ollama)

    if not ok_funpay:
        env["DRY_RUN"] = "1"
        write_env(path, env)
        print()
        print("⚠️  Реальный запуск отключён: DRY_RUN=1. Исправь golden_key и снова запусти setup.py.")
        raise SystemExit(2)

    print()
    if live:
        print("✅ Настройка завершена. Установщик сейчас запустит бота и автопубликатор.")
    else:
        print("✅ Настройка завершена в тестовом режиме DRY_RUN=1.")


if __name__ == "__main__":
    main()
