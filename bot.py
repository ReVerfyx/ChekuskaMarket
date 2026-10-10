#!/usr/bin/env python3
"""Small FunPay order assistant; no buyer messages or credentials are persisted."""
import argparse
from decimal import Decimal, InvalidOperation
import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("funpay-assistant")


def load_env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


def configured_path(key, default):
    path = Path(os.getenv(key, default))
    return path if path.is_absolute() else ROOT / path


DB_PATH = configured_path("DB_PATH", "data/bot.sqlite3")
CATALOG_PATH = configured_path("CATALOG_PATH", "catalog.json")
PRICES_PATH = configured_path("PRICES_PATH", "prices.json")
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"


def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("""CREATE TABLE IF NOT EXISTS orders (
      id TEXT PRIMARY KEY, buyer TEXT NOT NULL, chat_id TEXT NOT NULL,
      sku TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS sent (
      order_id TEXT NOT NULL, purpose TEXT NOT NULL,
      PRIMARY KEY(order_id, purpose))""")
    db.execute("""CREATE TABLE IF NOT EXISTS inbound (
      chat_id TEXT NOT NULL, message_id TEXT NOT NULL,
      PRIMARY KEY(chat_id, message_id))""")
    db.execute("""CREATE TABLE IF NOT EXISTS level_requests (
      order_id TEXT PRIMARY KEY, nickname TEXT NOT NULL, target INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'waiting_for_access')""")
    db.commit()
    return db


def catalog():
    entries = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("catalog.json должен содержать список")
    seen = set()
    for item in entries:
        if not all(isinstance(item.get(k), str) and item[k].strip()
                   for k in ("sku", "match", "kind", "instructions")):
            raise ValueError("Каждый товар требует sku, match, kind, instructions")
        if item["kind"] not in ("guide", "service", "gift") or item["sku"] in seen:
            raise ValueError("Повтор sku или неизвестный kind")
        if item["kind"] == "gift" and not all(item.get(k) for k in ("game", "item", "seller_account")):
            raise ValueError("Подарку нужны game, item и seller_account")
        if item["kind"] == "guide":
            guide_text(item)
        seen.add(item["sku"])
    return entries


def valid_price(value):
    if not isinstance(value, str):
        raise ValueError("Цена должна быть строкой, например 12.50")
    try:
        amount = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Цена должна быть числом") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise ValueError("Цена должна быть положительной, с точностью до копеек")
    return str(amount.quantize(Decimal("0.01")))


def prices():
    if not PRICES_PATH.exists():
        return {}
    values = json.loads(PRICES_PATH.read_text(encoding="utf-8"))
    if not isinstance(values, dict):
        raise ValueError("prices.json должен содержать объект SKU → цена")
    return {sku: valid_price(value) for sku, value in values.items()}


def set_price(sku, value):
    if sku not in {item["sku"] for item in catalog()}:
        raise ValueError("Неизвестный SKU")
    current = prices()
    current[sku] = valid_price(value)
    PRICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRICES_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def guide_text(item):
    if "guide_file" in item:
        name = item["guide_file"]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".txt"):
            raise ValueError("guide_file — только имя .txt в папке guides")
        text = (ROOT / "guides" / name).read_text(encoding="utf-8")
    else:
        text = item.get("guide", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 2600:
        raise ValueError("Гайд должен содержать 1–2600 символов")
    return text


def lot_drafts():
    lines = []
    configured_prices = prices()
    for item in catalog():
        if item["kind"] != "guide":
            continue
        lines.append("Название: " + item["match"])
        price = configured_prices.get(item["sku"])
        lines.append("Цена для настройки: " + (price + " ₽" if price else "не задана"))
        lines.append("Описание: Авторский короткий чеклист и практический пример на русском. "
                     "Основан на бесплатной официальной документации Roblox, ссылки включены в текст. "
                     "Сразу после оплаты получите полный текст в чате FunPay. "
                     "Дополнительные материалы и игровые предметы не входят.")
        lines.append("Что получает покупатель: " + guide_text(item).splitlines()[0])
        lines.append("Раздел FunPay и доступную минимальную цену проверьте при размещении лота.")
        lines.append("")
    return "\n".join(lines)


def match_item(title):
    matches = [item for item in catalog() if item.get("enabled", True)
               and item["match"].casefold() in title.casefold()]
    return matches[0] if len(matches) == 1 else None


def redact(text):
    """Do not pass common secrets from buyer chat to a language model."""
    text = re.sub(r'(?i)\.ROBLOSECURITY\s*[=:]\s*\S+', '[секрет удалён]', text)
    text = re.sub(r'(?i)(пароль|password|cookie|токен|token|код\s*почты|email\s*code)\s*[:=]\s*\S+',
                  '[секрет удалён]', text)
    return text[:700]


def solve_chat_challenge(text):
    """Only unambiguous arithmetic challenges written in chat; never site CAPTCHA."""
    match = re.fullmatch(r"\s*(?:капча|captcha)\s*[:—-]?\s*(?:сколько будет|реши)?\s*"
                         r"(\d{1,2})\s*([+*×-])\s*(\d{1,2})\s*[?=]?\s*",
                         text, flags=re.IGNORECASE)
    if not match:
        return None
    left, op, right = int(match[1]), match[2], int(match[3])
    return str(left + right if op == "+" else left * right if op in ("*", "×") else left - right)


def model_answer(prompt, *, system, max_tokens=320):
    url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("OLLAMA_URL должен указывать на локальный Ollama")
    body = json.dumps({"model": os.getenv("OLLAMA_MODEL", "qwen3:4b"),
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": redact(prompt)}],
                       "stream": False, "think": False,
                       "options": {"temperature": 0.3, "num_predict": max_tokens}}).encode()
    request = urllib.request.Request(url + "/api/chat", body,
                                     {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=int(os.getenv('OLLAMA_TIMEOUT_SECONDS', '180'))) as response:
        result = json.load(response)
    return str(result["message"]["content"]).strip()[:max(1800, max_tokens * 6)]


def send(account, chat_id, content):
    if DRY_RUN or account is None:
        print(f"[DRY_RUN → chat {chat_id}] {content}")
        return False
    account.send_message(chat_id, content)
    return True


def order_id_of(order):
    return str(getattr(order, "id", ""))


def receive_order(db, account, order):
    order_id = order_id_of(order)
    title = str(getattr(order, "description", ""))
    buyer = str(getattr(order, "buyer_username", ""))
    if not order_id or not buyer:
        LOG.warning("Событие заказа без ID или покупателя")
        return
    # There is currently no Linux game executor. Never promise fulfillment.
    unavailable = next((x for x in catalog() if x['sku'] == 'steal_egg_leveling'
                        and x['match'] == title), None)
    if unavailable:
        import leveling_guard
        leveling_guard.enqueue(db, order_id)
        LOG.warning('Прокачка недоступна: заказ %s поставлен на возврат и снятие лота', order_id)
        return
    item = match_item(title)
    if not item:
        LOG.warning("Заказ %s: неоднозначное или неизвестное название %r; разберите вручную", order_id, title)
        return
    if account:
        chat_id = str(account.get_chat_by_name(buyer, True).id)
    else:
        chat_id = buyer
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute("""INSERT OR IGNORE INTO orders
          (id, buyer, chat_id, sku, kind, status, created_at, updated_at)
          VALUES (?, ?, ?, ?, ?, 'new', ?, ?)""",
                   (order_id, buyer, chat_id, item["sku"], item["kind"], now, now))
    if db.execute("SELECT 1 FROM sent WHERE order_id=? AND purpose='welcome'", (order_id,)).fetchone():
        return
    message = item["instructions"]
    # For lots configured with FunPay auto-delivery, the platform sends `secrets`.
    # Do not duplicate the paid guide in chat.
    if item["kind"] == "guide" and not item.get("auto_delivery", False):
        message += "\n\n" + guide_text(item)
    if item["kind"] == "guide" and item.get("auto_delivery", False):
        if DRY_RUN:
            return
        with db:
            db.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", ("waiting", now, order_id))
        return
    # Delivery is not marked complete unless the platform accepted the message.
    if send(account, chat_id, message):
        with db:
            db.execute("INSERT OR IGNORE INTO sent VALUES (?, 'welcome')", (order_id,))
            db.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?",
                       ("delivered" if item["kind"] == "guide" else "waiting", now, order_id))


def reply_to_message(db, account, event):
    msg = event.message
    if str(getattr(msg, "author_id", "")) == str(account.id):
        return
    chat_id = str(getattr(msg, "chat_id", ""))
    message_id = str(getattr(msg, "id", ""))
    if message_id and db.execute("SELECT 1 FROM inbound WHERE chat_id=? AND message_id=?",
                                 (chat_id, message_id)).fetchone():
        return
    text = str(getattr(msg, "text", ""))
    challenge = solve_chat_challenge(text)
    if challenge is not None:
        sent = send(account, chat_id, challenge)
        if sent and message_id:
            with db:
                db.execute("INSERT OR IGNORE INTO inbound VALUES (?, ?)", (chat_id, message_id))
        return
    row = db.execute("SELECT * FROM orders WHERE chat_id=? ORDER BY created_at DESC LIMIT 1",
                     (chat_id,)).fetchone()
    if not row:
        return
    if not text or re.search(r'(?i)пароль|password|cookie|\.ROBLOSECURITY|токен|token|код\s*почты', text):
        sent = send(account, chat_id, "Пожалуйста, не отправляйте пароли, коды почты и токены. Если уже отправили, смените пароль и завершите другие сеансы в настройках безопасности.")
        if sent and message_id:
            with db:
                db.execute("INSERT OR IGNORE INTO inbound VALUES (?, ?)", (chat_id, message_id))
        return
    if row["status"] == "done":
        return
    if row["sku"] == "steal_egg_leveling" and text.strip().lower().startswith("/level "):
        match = re.fullmatch(r"/level\s+([A-Za-z0-9_]{3,20})\s+(\d{1,7})", text.strip(), re.I)
        if not match or not 1 <= int(match[2]) <= 10_000_000:
            send(account, chat_id, "Формат: /level ВашНик ЧисловаяЦель. Пример: /level MyNick 100")
            return
        with db:
            db.execute("""INSERT INTO level_requests(order_id, nickname, target)
              VALUES (?,?,?) ON CONFLICT(order_id) DO UPDATE SET
              nickname=excluded.nickname, target=excluded.target""",
                       (row["id"], match[1], int(match[2])))
        send(account, chat_id, "Цель записана. Исполнитель согласует доступ и сообщит о проверенном результате. Не отправляйте пароль или коды в чат.")
        return
    if row["kind"] == "gift":
        parts = text.strip().split(maxsplit=3)
        if len(parts) >= 3 and parts[0].lower() == "/gift":
            try:
                import farm
                item = next(x for x in catalog() if x["sku"] == row["sku"])
                recipient, method = parts[1:3]
                link = parts[3] if len(parts) == 4 else None
                gift_db = farm.db_open()
                try:
                    if DRY_RUN:
                        print(f"[DRY_RUN gift] {row['id']} {recipient} {method} {link or ''}")
                    else:
                        farm.enqueue_gift(gift_db, order_id=row["id"], alias=item["seller_account"],
                                          game=item["game"], item=item["item"], recipient=recipient,
                                          method=method, server_url=link)
                finally:
                    gift_db.close()
                answer = f"Заявка принята: {item['item']} для {recipient}. Сообщу после подтверждённой передачи."
            except (ValueError, StopIteration, sqlite3.Error) as exc:
                LOG.warning("Не удалось оформить подарок для %s: %s", row["id"], exc)
                answer = "Данные не приняты. Формат: /gift Ник friend или /gift Ник private https://www.roblox.com/… Проверьте ник, ссылку и наличие товара у продавца."
            send(account, chat_id, answer)
            return
        # Questions can be answered by AI, but it cannot register a gift itself.
    try:
        answer = model_answer(text, system=(
            "Ты помощник продавца. Отвечай по-русски кратко. Не обещай сроки, наличие, "
            "результат прокачки или возврат, если данные не подтверждены. Не проси секреты. "
            "Не говори, что работа сделана. Сложные вопросы передавай продавцу. "
            f"Тип заказа: {row['kind']}; состояние: {row['status']}."))
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        LOG.warning("Ollama недоступна: %s", exc)
        answer = "Получил сообщение. Продавец ответит здесь после проверки заказа."
    if answer:
        sent = send(account, chat_id, answer)
        if sent and message_id:
            with db:
                db.execute("INSERT OR IGNORE INTO inbound VALUES (?, ?)", (chat_id, message_id))


def run():
    key = os.getenv("FUNPAY_GOLDEN_KEY", "")
    if not key:
        raise SystemExit("Укажите FUNPAY_GOLDEN_KEY в .env")
    from FunPayAPI import Account, Runner, enums  # installed only for live mode
    db = database()
    account = Account(key).get()
    LOG.info("Подключён продавец %s; DRY_RUN=%s", account.username, DRY_RUN)
    while True:
        try:
            for event in Runner(account).listen(requests_delay=4):
                try:
                    if event.type is enums.EventTypes.NEW_ORDER:
                        receive_order(db, account, event.order)
                    elif event.type is enums.EventTypes.NEW_MESSAGE:
                        reply_to_message(db, account, event)
                except Exception as exc:
                    if "captcha" in str(exc).lower() or "капча" in str(exc).lower():
                        raise SystemExit("FunPay запросил проверку на сайте. Завершите её вручную в браузере продавца.") from exc
                    LOG.exception("Ошибка обработки события")
        except KeyboardInterrupt:
            return
        except Exception as exc:
            if "captcha" in str(exc).lower() or "капча" in str(exc).lower():
                raise SystemExit("FunPay запросил проверку на сайте. Завершите её вручную в браузере продавца.") from exc
            LOG.exception("Связь с FunPay прервана; повтор через 15 секунд")
            time.sleep(15)


def complete(order_id):
    db = database()
    row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row or row["kind"] != "service":
        raise SystemExit("Нет такой услуги в базе")
    if row["status"] == "done":
        print("Уже отмечено выполненным")
        return
    account = None
    if not DRY_RUN:
        key = os.getenv("FUNPAY_GOLDEN_KEY", "")
        if not key:
            raise SystemExit("Нет FUNPAY_GOLDEN_KEY")
        from FunPayAPI import Account
        account = Account(key).get()
    message = ("Работа по заказу выполнена. Проверьте результат и подтвердите заказ, "
               "если всё соответствует договорённости. После входа в Roblox завершите "
               "другие сеансы в настройках безопасности, если это необходимо.")
    if send(account, row["chat_id"], message):
        with db:
            db.execute("UPDATE orders SET status='done', updated_at=? WHERE id=?",
                       (datetime.now(timezone.utc).isoformat(), order_id))
    else:
        print("Проверка: заказ не закрыт, сообщение не отправлено")


def sync_gifts(db, account):
    import farm
    fdb = farm.db_open()
    try:
        ready = fdb.execute("SELECT order_id, recipient, item, confirmation FROM gifts WHERE status='done'").fetchall()
        for gift in ready:
            row = db.execute("SELECT * FROM orders WHERE id=? AND kind='gift'", (gift["order_id"],)).fetchone()
            if not row or db.execute("SELECT 1 FROM sent WHERE order_id=? AND purpose='gift'", (gift["order_id"],)).fetchone():
                continue
            message = (f"Предмет {gift['item']} передан игроку {gift['recipient']}. "
                       f"Подтверждение: {gift['confirmation']}. Проверьте получение и подтвердите заказ, если всё верно.")
            if send(account, row["chat_id"], message):
                with db:
                    db.execute("INSERT OR IGNORE INTO sent VALUES (?, 'gift')", (gift["order_id"],))
                    db.execute("UPDATE orders SET status='done', updated_at=? WHERE id=?",
                               (datetime.now(timezone.utc).isoformat(), gift["order_id"]))
    finally:
        fdb.close()


def gift_notifier():
    from FunPayAPI import Account
    key = os.getenv("FUNPAY_GOLDEN_KEY", "")
    if not key:
        raise SystemExit("Укажите FUNPAY_GOLDEN_KEY в .env")
    account = Account(key).get()
    db = database()
    while True:
        try:
            sync_gifts(db, account)
        except KeyboardInterrupt:
            return
        except Exception:
            LOG.exception("Ошибка уведомления о подарке")
        time.sleep(15)


def valid_public_feed(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Нужна публичная HTTPS-лента")
    infos = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError("Адрес ленты должен быть публичным")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Переадресация ленты запрещена")


def research(url):
    valid_public_feed(url)
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(urllib.request.Request(url, headers={"User-Agent": "FunPayAssistant/0.1"}), timeout=12) as response:
        raw = response.read(600_001)
    if len(raw) > 600_000:
        raise ValueError("Слишком большая лента")
    root = ET.fromstring(raw)
    entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    ideas = []
    for entry in entries[:12]:
        title = entry.findtext("title") or entry.findtext("{http://www.w3.org/2005/Atom}title")
        if not title:
            continue
        try:
            suggestion = model_answer(title, system=(
                "Предложи одно честное название гайда или игровой услуги для ручной проверки. "
                "Только если тема имеет связь с играми; иначе ответь ПРОПУСТИТЬ. "
                "Не обещай результата, не копируй источник, не выдумывай факты. До 160 символов."))
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
            suggestion = "Проверить новость вручную: " + title[:110]
        if suggestion and suggestion.upper() != "ПРОПУСТИТЬ":
            ideas.append({"source": url, "headline": title[:220], "idea": suggestion[:220],
                          "created_at": datetime.now(timezone.utc).isoformat()})
    path = ROOT / "ideas.json"
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    existing = {(x["source"], x["headline"]) for x in old}
    old.extend(x for x in ideas if (x["source"], x["headline"]) not in existing)
    path.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Идей в очереди: {len(old)}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="FunPay помощник")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run")
    commands.add_parser("catalog")
    commands.add_parser("lot-drafts")
    commands.add_parser("orders")
    commands.add_parser("services")
    commands.add_parser("ideas")
    commands.add_parser("purge")
    commands.add_parser("gift-notifier")
    price = commands.add_parser("price")
    price.add_argument("sku")
    price.add_argument("amount")
    demo = commands.add_parser("demo-order")
    for name in ("order_id", "title", "buyer"):
        demo.add_argument(name)
    done = commands.add_parser("complete")
    done.add_argument("order_id")
    guide = commands.add_parser("guide")
    guide.add_argument("sku")
    guide.add_argument("file")
    feed = commands.add_parser("research")
    feed.add_argument("url")
    draft = commands.add_parser("draft")
    draft.add_argument("topic")
    args = parser.parse_args()
    if args.command == "run":
        run()
    elif args.command == "catalog":
        configured_prices = prices()
        for item in catalog():
            print(item["sku"], item["kind"], "активен" if item.get("enabled", True) else "отключён",
                  configured_prices.get(item["sku"], "не задана"), repr(item["match"]))
    elif args.command == "price":
        set_price(args.sku, args.amount)
        print("Цена сохранена")
    elif args.command == "lot-drafts":
        print(lot_drafts())
    elif args.command == "orders":
        for row in database().execute("SELECT id, buyer, sku, status FROM orders ORDER BY created_at DESC"):
            print(*row, sep=" | ")
    elif args.command == "services":
        for row in database().execute("""SELECT o.id, o.buyer, r.nickname, r.target, r.status
          FROM level_requests r JOIN orders o ON o.id=r.order_id ORDER BY o.created_at DESC"""):
            print(*row, sep=" | ")
    elif args.command == "demo-order":
        from types import SimpleNamespace
        receive_order(database(), None, SimpleNamespace(id=args.order_id,
                      description=args.title, buyer_username=args.buyer))
    elif args.command == "complete":
        complete(args.order_id)
    elif args.command == "guide":
        items = catalog()
        target = next((x for x in items if x["sku"] == args.sku and x["kind"] == "guide"), None)
        if not target:
            raise SystemExit("Гайд с таким SKU не найден")
        content = Path(args.file).read_text(encoding="utf-8")
        if not content.strip() or len(content) > 2500:
            raise SystemExit("Нужен непустой гайд до 2500 символов (лимит сообщения)")
        target["guide"] = content
        target.pop("guide_file", None)
        CATALOG_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Гайд загружен")
    elif args.command == "research":
        research(args.url)
    elif args.command == "ideas":
        path = ROOT / "ideas.json"
        for idea in json.loads(path.read_text(encoding="utf-8")) if path.exists() else []:
            print(idea["created_at"], idea["idea"], "←", idea["headline"])
    elif args.command == "draft":
        print(model_answer(args.topic, system=(
            "Напиши черновик лота FunPay на русском: название до 100 символов, честное "
            "описание, что именно получает покупатель, условия и краткая инструкция после покупки. "
            "Не заявляй, что товар уже есть, не выдумывай цену и сроки. До 900 символов.")))
    elif args.command == "purge":
        db = database()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        with db:
            db.execute("DELETE FROM sent WHERE order_id IN (SELECT id FROM orders WHERE status IN ('done','delivered') AND updated_at < ?)", (cutoff,))
            result = db.execute("DELETE FROM orders WHERE status IN ('done','delivered') AND updated_at < ?", (cutoff,))
            db.execute("DELETE FROM inbound WHERE chat_id NOT IN (SELECT chat_id FROM orders)")
        print("Удалено заказов:", result.rowcount)
    elif args.command == "gift-notifier":
        gift_notifier()


if __name__ == "__main__":
    main()
