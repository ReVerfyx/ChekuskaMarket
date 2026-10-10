# ChekuskaMarket — FunPay bot

Обычный FunPay-бот без Roblox-ферм, Lua и игровых исполнителей. Работает через `golden_key`, хранит состояние заказов в SQLite и может выдавать текст, единичные товары со склада или принимать ручные услуги.

## Что умеет

- отслеживать новые оплаченные заказы и сообщения;
- автовыдавать текстовый товар из `guides/*.txt`;
- выдавать по одной строке из `stock/*.txt` без повторной выдачи;
- принимать услуги и оставлять их в статусе `waiting` до ручного `complete`;
- отвечать на FAQ;
- использовать локальный Qwen/Ollama **только для выбора готового FAQ-ответа** — модель не сочиняет цены, наличие, сроки и факт выполнения;
- не принимать пароли, cookie, `golden_key`, OTP/2FA-коды и токены;
- поднимать существующие лоты по ID категорий;
- переживать перезапуски без повторной выдачи уже обработанного заказа.

FunPay-интеграция неофициальная. `golden_key` равен доступу к аккаунту: храните его только в `.env` на VPS.

## Ubuntu 24.04

```bash
git clone https://github.com/ReVerfyx/ChekuskaMarket.git /opt/chekuska-market
cd /opt/chekuska-market
sudo bash install-vps.sh
sudo nano /opt/chekuska-market/.env
```

Минимум в `.env`:

```env
FUNPAY_GOLDEN_KEY=ваш_golden_key
DRY_RUN=1
OLLAMA_MODEL=qwen3:4b
```

Если нужен Qwen:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:4b
```

Проверка до реального запуска:

```bash
cd /opt/chekuska-market
sudo -u funpay .venv/bin/python bot.py catalog
sudo -u funpay .venv/bin/python -m unittest -v
```

## Товары

`catalog.json` содержит три **выключенных** примера. Ничего не продаётся автоматически, пока вы не добавите свои названия лотов и не поставите `"enabled": true`.

### Текст/гайд

```json
{
  "sku": "guide_1",
  "match": "ТОЧНОЕ УНИКАЛЬНОЕ НАЗВАНИЕ ЛОТА",
  "kind": "text",
  "enabled": true,
  "delivery_file": "guide_1.txt",
  "instructions": "Спасибо за покупку. Материал ниже."
}
```

Сам текст: `guides/guide_1.txt`.

### Склад

```json
{
  "sku": "code_1",
  "match": "УНИКАЛЬНОЕ НАЗВАНИЕ ТОВАРА",
  "kind": "stock",
  "enabled": true,
  "stock_file": "code_1.txt",
  "instructions": "Спасибо. Ваш товар ниже."
}
```

`stock/code_1.txt` — одна выдаваемая позиция на строку. После заказа первая строка резервируется и удаляется из файла. Если отправка сообщения не удалась, резерв хранится в SQLite и повторно со склада не списывается.

### Услуга

```json
{
  "sku": "service_1",
  "match": "УНИКАЛЬНОЕ НАЗВАНИЕ УСЛУГИ",
  "kind": "service",
  "enabled": true,
  "instructions": "Спасибо за заказ. Напишите необходимые несекретные данные."
}
```

Закрыть услугу после фактического выполнения:

```bash
sudo -u funpay .venv/bin/python bot.py complete ORDER_ID
```

## FAQ + Qwen без дезинформации

Фактические ответы лежат в `faq.json`. Сначала бот ищет совпадение по ключевым фразам. Если точного совпадения нет, Qwen выбирает **номер одного из уже готовых ответов**. Сам текст ответа модель не создаёт. Если ничего не подходит, покупателю приходит: «Не хочу выдумывать ответ. Продавец проверит вопрос и ответит здесь.»

## Автоподнятие

Узнать ID категорий:

```bash
sudo -u funpay .venv/bin/python bot.py sections
```

Добавить в `.env`:

```env
RAISE_CATEGORY_IDS=1,2,3
RAISE_INTERVAL_SECONDS=3600
```

Однократная проверка:

```bash
sudo -u funpay .venv/bin/python autoseller.py once
```

Фоновый сервис:

```bash
sudo systemctl enable --now chekuska-raise
```

## Реальный запуск

Сначала сделайте тесты с `DRY_RUN=1`. Затем:

```bash
sudo nano /opt/chekuska-market/.env
# DRY_RUN=0
sudo systemctl enable --now chekuska-funpay
sudo systemctl status chekuska-funpay --no-pager
journalctl -u chekuska-funpay -f
```

## Команды

```bash
.venv/bin/python bot.py catalog
.venv/bin/python bot.py orders
.venv/bin/python bot.py faq
.venv/bin/python bot.py sections
.venv/bin/python bot.py raise-once
.venv/bin/python bot.py complete ORDER_ID
.venv/bin/python bot.py purge
.venv/bin/python autoseller.py once
.venv/bin/python autoseller.py loop
```

Обновление:

```bash
cd /opt/chekuska-market && sudo bash update.sh
```

## Важный нюанс

Проект использует актуальный публичный форк `MorikTV/FunPayAPI`, который `setup.sh`/`install-vps.sh` кладёт в `vendor/FunPayAPI`. Это не официальный API FunPay, поэтому при изменениях сайта адаптер может потребовать обновления.
