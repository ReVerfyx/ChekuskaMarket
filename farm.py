#!/usr/bin/env python3
"""Queue/runner for seller-owned game accounts; worker selected by seller, never by LLM."""
import argparse
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "farm.sqlite3"
PROFILES = ROOT / "farm_profiles.json"


def db_open():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS accounts (
      alias TEXT PRIMARY KEY, game TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready')""")
    db.execute("""CREATE TABLE IF NOT EXISTS jobs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, alias TEXT NOT NULL, game TEXT NOT NULL,
      target INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
      progress INTEGER NOT NULL DEFAULT 0, last_error TEXT,
      attempts INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      FOREIGN KEY(alias) REFERENCES accounts(alias))""")
    db.execute("""CREATE TABLE IF NOT EXISTS gifts (
      order_id TEXT PRIMARY KEY, alias TEXT NOT NULL, game TEXT NOT NULL,
      item TEXT NOT NULL, recipient TEXT NOT NULL, method TEXT NOT NULL,
      server_url TEXT, status TEXT NOT NULL DEFAULT 'queued',
      confirmation TEXT, last_error TEXT, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL)""")
    db.commit()
    return db


def profile(game):
    profiles = json.loads(PROFILES.read_text(encoding="utf-8"))
    p = profiles.get(game)
    if not isinstance(p, dict) or not isinstance(p.get("command"), list) or not p["command"]:
        raise ValueError("Нет настроенного исполнителя для игры: " + game)
    path = Path(p["command"][0])
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ValueError("Укажите абсолютный путь к исполняемому файлу в farm_profiles.json")
    if any(not isinstance(arg, str) for arg in p["command"]):
        raise ValueError("Аргументы исполнителя должны быть строками")
    return p


def enqueue(db, alias, game, target):
    if target < 1 or target > 10_000_000:
        raise ValueError("Цель должна быть от 1 до 10000000")
    account = db.execute("SELECT * FROM accounts WHERE alias=? AND game=?", (alias, game)).fetchone()
    if not account:
        raise ValueError("Сначала добавьте свой аккаунт с нужной игрой")
    if db.execute("SELECT 1 FROM jobs WHERE alias=? AND status IN ('queued','running')", (alias,)).fetchone():
        raise ValueError("У этого аккаунта уже есть активная задача")
    now = datetime.now(timezone.utc).isoformat()
    with db:
        cursor = db.execute("INSERT INTO jobs(alias, game, target, created_at, updated_at) VALUES (?,?,?,?,?)",
                            (alias, game, target, now, now))
    return cursor.lastrowid


def process_one(db):
    # Atomic claim prevents two worker processes from taking the same job.
    with db:
        job = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not job:
            return False
        changed = db.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=? AND status='queued'",
                             (datetime.now(timezone.utc).isoformat(), job["id"]))
        if changed.rowcount != 1:
            return False
    try:
        p = profile(job["game"])
        payload = {"job_id": job["id"], "account_alias": job["alias"], "game": job["game"],
                   "target": job["target"], "private_server": p.get("private_server")}
        # No shell, no login credentials, no LLM-generated command.
        result = subprocess.run(p["command"], input=json.dumps(payload), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=ROOT, timeout=int(p.get("timeout_seconds", 3600)), check=True,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        data = json.loads(result.stdout)
        progress = data["observed_progress"]
        if isinstance(progress, bool) or not isinstance(progress, int) or progress < 0:
            raise ValueError("Исполнитель вернул неверное значение observed_progress")
        if data.get("account_alias") != job["alias"] or data.get("game") != job["game"]:
            raise ValueError("Исполнитель не подтвердил аккаунт и игру")
        if data.get("session_closed") is not True:
            raise ValueError("Исполнитель не подтвердил выход из сеанса")
        status = "done" if progress >= job["target"] else (
            "failed" if progress <= job["progress"] or job["attempts"] >= 9 else "queued")
        with db:
            db.execute("UPDATE jobs SET status=?, progress=?, updated_at=?, last_error=NULL WHERE id=?",
                       (status, progress, datetime.now(timezone.utc).isoformat(), job["id"]))
        print(f"Задача {job['id']}: {status}, прогресс {progress}/{job['target']}")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        with db:
            db.execute("UPDATE jobs SET status='failed', updated_at=?, last_error=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), type(exc).__name__, job["id"]))
        print(f"Задача {job['id']}: failed ({type(exc).__name__})")
    return True


def enqueue_gift(db, *, order_id, alias, game, item, recipient, method, server_url=None):
    if method not in ("private", "friend"):
        raise ValueError("Способ: private или friend")
    if not isinstance(recipient, str) or not recipient.isascii() or not recipient.replace("_", "").isalnum() or len(recipient) > 20:
        raise ValueError("Неверный ник Roblox")
    if method == "private":
        from urllib.parse import urlsplit
        parts = urlsplit(server_url or "")
        if parts.scheme != "https" or parts.hostname not in ("roblox.com", "www.roblox.com"):
            raise ValueError("Нужна ссылка на приватный сервер roblox.com")
    elif server_url:
        raise ValueError("Для заявки в друзья ссылка не нужна")
    if not db.execute("SELECT 1 FROM accounts WHERE alias=? AND game=?", (alias, game)).fetchone():
        raise ValueError("Сначала добавьте аккаунт исполнителя")
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("""INSERT OR IGNORE INTO gifts
          (order_id, alias, game, item, recipient, method, server_url, created_at, updated_at)
          VALUES (?,?,?,?,?,?,?,?,?)""",
          (order_id, alias, game, item, recipient, method, server_url, now, now))


def process_gift(db):
    with db:
        gift = db.execute("SELECT * FROM gifts WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not gift:
            return False
        changed = db.execute("UPDATE gifts SET status='running', updated_at=? WHERE order_id=? AND status='queued'",
                             (datetime.now(timezone.utc).isoformat(), gift["order_id"]))
        if changed.rowcount != 1:
            return False
    try:
        p = profile(gift["game"])
        payload = {"operation": "gift", "order_id": gift["order_id"], "account_alias": gift["alias"],
                   "game": gift["game"], "item": gift["item"], "recipient": gift["recipient"],
                   "method": gift["method"], "server_url": gift["server_url"]}
        result = subprocess.run(p["command"], input=json.dumps(payload), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=ROOT, timeout=int(p.get("timeout_seconds", 3600)), check=True,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        data = json.loads(result.stdout)
        if data.get("confirmed") is not True or data.get("recipient") != gift["recipient"] or data.get("item") != gift["item"]:
            raise ValueError("Исполнитель не подтвердил передачу нужного предмета получателю")
        confirmation = str(data.get("receipt", ""))[:180]
        if not confirmation:
            raise ValueError("Исполнитель не вернул квитанцию передачи")
        with db:
            db.execute("UPDATE gifts SET status='done', confirmation=?, updated_at=? WHERE order_id=?",
                       (confirmation, datetime.now(timezone.utc).isoformat(), gift["order_id"]))
        print(f"Подарок по заказу {gift['order_id']}: подтверждён")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        with db:
            db.execute("UPDATE gifts SET status='failed', last_error=?, updated_at=? WHERE order_id=?",
                       (type(exc).__name__, datetime.now(timezone.utc).isoformat(), gift["order_id"]))
        print(f"Подарок по заказу {gift['order_id']}: ошибка {type(exc).__name__}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Очередь прокачки собственных аккаунтов")
    subs = parser.add_subparsers(dest="command", required=True)
    add = subs.add_parser("add-account")
    add.add_argument("alias")
    add.add_argument("game")
    task = subs.add_parser("enqueue")
    task.add_argument("alias")
    task.add_argument("game")
    task.add_argument("target", type=int)
    subs.add_parser("jobs")
    subs.add_parser("gifts")
    subs.add_parser("once")
    subs.add_parser("loop")
    args = parser.parse_args()
    db = db_open()
    if args.command == "add-account":
        with db:
            db.execute("INSERT INTO accounts(alias, game) VALUES (?,?)", (args.alias, args.game))
        print("Добавлен свой аккаунт:", args.alias)
    elif args.command == "enqueue":
        print("Задача:", enqueue(db, args.alias, args.game, args.target))
    elif args.command == "jobs":
        for row in db.execute("SELECT id, alias, game, target, progress, status FROM jobs ORDER BY id DESC"):
            print(*row, sep=" | ")
    elif args.command == "gifts":
        for row in db.execute("SELECT order_id, game, item, recipient, method, status FROM gifts ORDER BY created_at DESC"):
            print(*row, sep=" | ")
    elif args.command == "once":
        process_gift(db) or process_one(db)
    elif args.command == "loop":
        while True:
            if not (process_gift(db) or process_one(db)):
                time.sleep(10)


if __name__ == "__main__":
    main()
