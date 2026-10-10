#!/usr/bin/env python3
"""ChekuskaMarket: generic FunPay shop bot.

The bot intentionally keeps shop facts in local config files and uses Qwen only as a
classifier. Qwen never invents prices, stock, delivery times, or order completion.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("chekuska-market")


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def configured_path(key: str, default: str) -> Path:
    value = Path(os.getenv(key, default))
    return value if value.is_absolute() else ROOT / value


DB_PATH = configured_path("DB_PATH", "data/bot.sqlite3")
CATALOG_PATH = configured_path("CATALOG_PATH", "catalog.json")
FAQ_PATH = configured_path("FAQ_PATH", "faq.json")
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"
AI_ENABLED = os.getenv("AI_ENABLED", "1") != "0"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def database() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
      id TEXT PRIMARY KEY,
      buyer TEXT NOT NULL,
      chat_id TEXT NOT NULL,
      sku TEXT NOT NULL,
      kind TEXT NOT NULL,
      status TEXT NOT NULL,
      delivery_payload TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )""")
    # Migration from the old repository schema.
    columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
    if "delivery_payload" not in columns:
        db.execute("ALTER TABLE orders ADD COLUMN delivery_payload TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS sent (
      order_id TEXT NOT NULL,
      purpose TEXT NOT NULL,
      PRIMARY KEY(order_id, purpose)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS inbound (
      chat_id TEXT NOT NULL,
      message_id TEXT NOT NULL,
      PRIMARY KEY(chat_id, message_id)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      action TEXT NOT NULL,
      details TEXT,
      created_at TEXT NOT NULL
    )""")
    db.commit()
    return db


def audit(db: sqlite3.Connection, action: str, details: str = "") -> None:
    with db:
        db.execute(
            "INSERT INTO audit(action, details, created_at) VALUES (?,?,?)",
            (action, details[:2000], utcnow()),
        )


def _safe_local_file(value: str, folder: str) -> Path:
    p = Path(value)
    if p.name != value or p.suffix.lower() != ".txt":
        raise ValueError(f"Файл должен быть .txt в папке {folder}")
    path = ROOT / folder / value
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def catalog() -> list[dict]:
    entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("catalog.json должен содержать список")
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("Каждый товар должен быть объектом")
        for key in ("sku", "match", "kind", "instructions"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"Товару нужен непустой {key}")
        if item["sku"] in seen:
            raise ValueError("Повторяющийся sku")
        if item["kind"] not in {"text", "stock", "service"}:
            raise ValueError("kind: text, stock или service")
        if item["kind"] == "text":
            path = _safe_local_file(item.get("delivery_file", ""), "guides")
            if not path.exists() or not path.read_text(encoding="utf-8").strip():
                raise ValueError(f"Нет текста выдачи для {item['sku']}")
        if item["kind"] == "stock":
            _safe_local_file(item.get("stock_file", ""), "stock")
        seen.add(item["sku"])
    return entries


def faq() -> list[dict]:
    if not FAQ_PATH.exists():
        return []
    entries = json.loads(FAQ_PATH.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("faq.json должен содержать список")
    result = []
    for row in entries:
        if not isinstance(row, dict) or not isinstance(row.get("answer"), str):
            raise ValueError("FAQ требует answer")
        patterns = row.get("patterns", [])
        if not isinstance(patterns, list) or not all(isinstance(x, str) for x in patterns):
            raise ValueError("FAQ patterns должен быть списком строк")
        result.append({"patterns": patterns, "answer": row["answer"].strip()})
    return result


def item_by_sku(sku: str) -> dict | None:
    return next((x for x in catalog() if x["sku"] == sku), None)


def match_item(title: str) -> dict | None:
    found = [
        item for item in catalog()
        if item.get("enabled", True) and item["match"].casefold() in title.casefold()
    ]
    if not found:
        return None
    found.sort(key=lambda x: len(x["match"]), reverse=True)
    if len(found) > 1 and len(found[0]["match"]) == len(found[1]["match"]):
        return None
    return found[0]


SECRET_RE = re.compile(
    r"(?i)(\.ROBLOSECURITY|golden_key|пароль|password|cookie|токен|token|otp|2fa|2sv|код\s*почты|email\s*code)"
)


def redact(text: str) -> str:
    value = str(text or "")[:1200]
    value = re.sub(
        r"(?i)(пароль|password|cookie|токен|token|otp|2fa|2sv|код\s*почты|email\s*code|golden_key)\s*[:=]?\s*\S+",
        "[секрет удалён]",
        value,
    )
    value = re.sub(r"(?i)\.ROBLOSECURITY\S*", "[секрет удалён]", value)
    return value


def has_secret(text: str) -> bool:
    return bool(SECRET_RE.search(str(text or "")))


def model_select_faq(message: str, rows: list[dict]) -> int | None:
    """Use Qwen only to select an existing FAQ row; it cannot author shop facts."""
    if not AI_ENABLED or not rows:
        return None
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OLLAMA_URL должен быть локальным")
    choices = [
        {"id": i, "patterns": row["patterns"], "answer": row["answer"]}
        for i, row in enumerate(rows)
    ]
    payload = {
        "model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        "stream": False,
        "think": False,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты классификатор FAQ магазина. Нельзя придумывать ответ. "
                    "Выбери только ID ответа, который прямо подходит сообщению. "
                    "Если ни один не подходит, верни -1. Ответ только целое число."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"message": redact(message), "faq": choices},
                    ensure_ascii=False,
                ),
            },
        ],
        "options": {"temperature": 0, "num_predict": 12},
    }
    request = urllib.request.Request(
        url + "/api/chat",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(
        request, timeout=int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))
    ) as response:
        raw = str(json.load(response)["message"]["content"]).strip()
    match = re.search(r"-?\d+", raw)
    if not match:
        return None
    idx = int(match.group())
    return idx if 0 <= idx < len(rows) else None


def faq_answer(message: str) -> str | None:
    rows = faq()
    low = message.casefold()
    # Exact deterministic keyword match first.
    scored: list[tuple[int, int]] = []
    for i, row in enumerate(rows):
        score = sum(1 for p in row["patterns"] if p.casefold() in low)
        if score:
            scored.append((score, i))
    if scored:
        scored.sort(reverse=True)
        return rows[scored[0][1]]["answer"]
    try:
        idx = model_select_faq(message, rows)
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError) as exc:
        LOG.warning("Ollama FAQ classifier unavailable: %s", exc)
        idx = None
    return rows[idx]["answer"] if idx is not None else None


def send(account, chat_id: str, content: str) -> bool:
    if DRY_RUN or account is None:
        print(f"[DRY_RUN → chat {chat_id}] {content}")
        return False
    account.send_message(chat_id, content)
    return True


def get_chat_id(account, buyer: str) -> str:
    return str(account.get_chat_by_name(buyer, True).id)


@contextmanager
def locked_text_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        yield handle
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reserve_stock(item: dict) -> str | None:
    path = _safe_local_file(item["stock_file"], "stock")
    with locked_text_file(path) as handle:
        lines = [x.strip() for x in handle.read().splitlines() if x.strip()]
        if not lines:
            return None
        payload = lines.pop(0)
        handle.seek(0)
        handle.truncate(0)
        handle.write("\n".join(lines) + ("\n" if lines else ""))
        return payload


def read_text_delivery(item: dict) -> str:
    path = _safe_local_file(item["delivery_file"], "guides")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Пустой текст выдачи")
    if len(text) > 9000:
        raise ValueError("Слишком длинная выдача; сократите файл до 9000 символов")
    return text


def receive_order(db: sqlite3.Connection, account, order) -> None:
    order_id = str(getattr(order, "id", "") or "")
    title = str(getattr(order, "description", "") or getattr(order, "short_description", "") or "")
    buyer = str(getattr(order, "buyer_username", "") or "")
    if not order_id or not buyer:
        LOG.warning("Заказ без ID/покупателя")
        return
    item = match_item(title)
    if not item:
        LOG.warning("Заказ %s: нет однозначного товара для %r", order_id, title)
        return
    chat_id = get_chat_id(account, buyer) if account is not None else buyer
    now = utcnow()
    with db:
        db.execute(
            """INSERT OR IGNORE INTO orders
            (id,buyer,chat_id,sku,kind,status,delivery_payload,created_at,updated_at)
            VALUES (?,?,?,?,?,'new',NULL,?,?)""",
            (order_id, buyer, chat_id, item["sku"], item["kind"], now, now),
        )
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["status"] in {"delivered", "waiting", "done"}:
        return

    if item["kind"] == "service":
        if send(account, chat_id, item["instructions"]):
            with db:
                db.execute(
                    "UPDATE orders SET status='waiting', updated_at=? WHERE id=?",
                    (utcnow(), order_id),
                )
                db.execute("INSERT OR IGNORE INTO sent VALUES (?, 'welcome')", (order_id,))
            audit(db, "service_opened", order_id)
        return

    if DRY_RUN:
        print(f"[DRY_RUN order {order_id}] would deliver sku={item['sku']}")
        return

    payload = row["delivery_payload"]
    if not payload:
        if item["kind"] == "text":
            payload = read_text_delivery(item)
        else:
            payload = reserve_stock(item)
            if payload is None:
                warning = item.get(
                    "out_of_stock_message",
                    "Товар временно закончился. Продавец проверит заказ вручную и ответит в этом чате.",
                )
                send(account, chat_id, warning)
                with db:
                    db.execute(
                        "UPDATE orders SET status='out_of_stock', updated_at=? WHERE id=?",
                        (utcnow(), order_id),
                    )
                audit(db, "out_of_stock", f"{order_id}:{item['sku']}")
                return
        with db:
            db.execute(
                "UPDATE orders SET delivery_payload=?, updated_at=? WHERE id=?",
                (payload, utcnow(), order_id),
            )

    message = item["instructions"].strip() + "\n\n" + payload
    if send(account, chat_id, message):
        with db:
            db.execute("INSERT OR IGNORE INTO sent VALUES (?, 'delivery')", (order_id,))
            db.execute(
                "UPDATE orders SET status='delivered', delivery_payload=NULL, updated_at=? WHERE id=?",
                (utcnow(), order_id),
            )
        audit(db, "delivered", f"{order_id}:{item['sku']}")


def latest_order_for_chat(db: sqlite3.Connection, chat_id: str):
    return db.execute(
        "SELECT * FROM orders WHERE chat_id=? ORDER BY created_at DESC LIMIT 1", (chat_id,)
    ).fetchone()


def status_text(row) -> str:
    labels = {
        "new": "заказ получен",
        "waiting": "ожидает выполнения продавцом",
        "delivered": "товар/материал отправлен",
        "out_of_stock": "требуется ручная проверка наличия",
        "done": "выполнен",
    }
    return "Статус заказа: " + labels.get(row["status"], row["status"])


def reply_to_message(db: sqlite3.Connection, account, event) -> None:
    msg = event.message
    if str(getattr(msg, "author_id", "")) == str(getattr(account, "id", "")):
        return
    chat_id = str(getattr(msg, "chat_id", "") or "")
    message_id = str(getattr(msg, "id", "") or "")
    text = str(getattr(msg, "text", "") or "").strip()
    if not chat_id or not text:
        return
    if message_id and db.execute(
        "SELECT 1 FROM inbound WHERE chat_id=? AND message_id=?", (chat_id, message_id)
    ).fetchone():
        return

    row = latest_order_for_chat(db, chat_id)
    if has_secret(text):
        answer = (
            "Не присылайте пароли, cookie, golden_key, OTP/2FA-коды и другие данные входа. "
            "Если уже отправили секрет — смените его/завершите активные сеансы."
        )
    elif text.casefold() in {"/status", "статус", "статус заказа"} and row:
        answer = status_text(row)
    else:
        answer = faq_answer(text)
        if answer is None:
            answer = "Не хочу выдумывать ответ. Продавец проверит вопрос и ответит здесь."

    if send(account, chat_id, answer) and message_id:
        with db:
            db.execute("INSERT OR IGNORE INTO inbound VALUES (?,?)", (chat_id, message_id))
        audit(db, "reply", f"chat={chat_id}; order={row['id'] if row else '-'}")


def connect_account():
    key = os.getenv("FUNPAY_GOLDEN_KEY", "").strip()
    if not key:
        raise SystemExit("Укажите FUNPAY_GOLDEN_KEY в .env")
    vendor = ROOT / "vendor"
    if vendor.exists():
        sys.path.insert(0, str(vendor))
    try:
        from FunPayAPI import Account
    except ImportError as exc:
        raise SystemExit("FunPayAPI не установлен. Запустите bash setup.sh") from exc
    account = Account(golden_key=key)
    account.get()
    return account


def run() -> None:
    vendor = ROOT / "vendor"
    if vendor.exists():
        sys.path.insert(0, str(vendor))
    account = connect_account()
    try:
        from FunPayAPI.updater.runner import Runner
        from FunPayAPI.updater.events import NewMessageEvent, NewOrderEvent
        modern = True
    except ImportError:
        from FunPayAPI import Runner, enums
        modern = False
    db = database()
    LOG.info("FunPay: %s; DRY_RUN=%s", getattr(account, "username", "?"), DRY_RUN)
    while True:
        try:
            for event in Runner(account).listen(requests_delay=4):
                try:
                    if modern:
                        if isinstance(event, NewOrderEvent):
                            receive_order(db, account, event.order)
                        elif isinstance(event, NewMessageEvent):
                            reply_to_message(db, account, event)
                    else:
                        if event.type is enums.EventTypes.NEW_ORDER:
                            receive_order(db, account, event.order)
                        elif event.type is enums.EventTypes.NEW_MESSAGE:
                            reply_to_message(db, account, event)
                except Exception:
                    LOG.exception("Ошибка обработки события")
        except KeyboardInterrupt:
            return
        except Exception:
            LOG.exception("Связь с FunPay прервана; повтор через 15 секунд")
            time.sleep(15)
            try:
                account.get()
            except Exception:
                LOG.exception("Не удалось обновить FunPay-сессию")


def complete(order_id: str, message: str | None = None) -> None:
    db = database()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise SystemExit("Заказ не найден")
    if row["status"] == "done":
        print("Уже выполнен")
        return
    account = None if DRY_RUN else connect_account()
    text = message or (
        "Заказ выполнен. Проверьте результат и подтвердите заказ, если всё соответствует договорённости."
    )
    if send(account, row["chat_id"], text):
        with db:
            db.execute("UPDATE orders SET status='done', updated_at=? WHERE id=?", (utcnow(), order_id))
        audit(db, "completed", order_id)
    else:
        print("DRY_RUN: статус не изменён")


def raise_category_ids() -> list[int]:
    result = []
    for chunk in os.getenv("RAISE_CATEGORY_IDS", "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.isdigit():
            raise ValueError("RAISE_CATEGORY_IDS: только числовые ID через запятую")
        result.append(int(chunk))
    return result


def raise_lots_once(account) -> dict[int, str]:
    result: dict[int, str] = {}
    for category_id in raise_category_ids():
        try:
            if DRY_RUN:
                result[category_id] = "dry-run"
                print(f"[DRY_RUN raise] category={category_id}")
                continue
            account.raise_lots(category_id)
            result[category_id] = "ok"
            LOG.info("Лоты категории %s подняты", category_id)
        except Exception as exc:
            result[category_id] = type(exc).__name__
            LOG.warning("Не удалось поднять категорию %s: %s", category_id, exc)
    return result


def list_sections(account) -> None:
    seen = set()
    for section in getattr(account, "subcategories", []):
        category = getattr(section, "category", None)
        cat_id = getattr(category, "id", None)
        cat_name = getattr(category, "name", "?")
        if cat_id is None or cat_id in seen:
            continue
        seen.add(cat_id)
        print(f"{cat_id}\t{cat_name}")


def purge(days: int = 30) -> int:
    db = database()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with db:
        db.execute(
            "DELETE FROM sent WHERE order_id IN (SELECT id FROM orders WHERE status IN ('done','delivered') AND updated_at < ?)",
            (cutoff,),
        )
        cur = db.execute(
            "DELETE FROM orders WHERE status IN ('done','delivered') AND updated_at < ?", (cutoff,)
        )
        db.execute("DELETE FROM inbound WHERE chat_id NOT IN (SELECT chat_id FROM orders)")
    return cur.rowcount


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="ChekuskaMarket FunPay bot")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run")
    sub.add_parser("catalog")
    sub.add_parser("orders")
    sub.add_parser("faq")
    sub.add_parser("sections")
    sub.add_parser("raise-once")
    sub.add_parser("purge")
    done = sub.add_parser("complete")
    done.add_argument("order_id")
    done.add_argument("message", nargs="?")
    demo = sub.add_parser("demo-order")
    demo.add_argument("order_id")
    demo.add_argument("title")
    demo.add_argument("buyer")
    args = parser.parse_args()

    if args.command == "run":
        run()
    elif args.command == "catalog":
        for item in catalog():
            print(item["sku"], item["kind"], "on" if item.get("enabled", True) else "off", repr(item["match"]))
    elif args.command == "orders":
        for row in database().execute("SELECT id,buyer,sku,kind,status FROM orders ORDER BY created_at DESC"):
            print(*row, sep=" | ")
    elif args.command == "faq":
        for i, row in enumerate(faq()):
            print(i, ", ".join(row["patterns"]), "=>", row["answer"])
    elif args.command == "sections":
        list_sections(connect_account())
    elif args.command == "raise-once":
        print(raise_lots_once(connect_account()))
    elif args.command == "complete":
        complete(args.order_id, args.message)
    elif args.command == "purge":
        print("Удалено заказов:", purge())
    elif args.command == "demo-order":
        from types import SimpleNamespace
        receive_order(
            database(),
            None,
            SimpleNamespace(id=args.order_id, description=args.title, buyer_username=args.buyer),
        )


if __name__ == "__main__":
    main()
