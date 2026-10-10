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

## Автопубликация: один лот в каждой игре

`autoseller.py` теперь сам проходит по всем игровым категориям, доступным аккаунту FunPay. Для каждой игры он:

1. находит в интернете свежие результаты поиска по гайдам/механикам;
2. отдаёт их локальному Qwen;
3. принимает от Qwen только советы, для которых модель указала точную цитату из найденного snippet;
4. требует минимум два разных источника;
5. оформляет название и описание со смайликами;
6. создаёт лот с авто-выдачей полного мини-гайда;
7. записывает ID игры в `data/autopublish.json` и второй авто-лот в этой же игре не создаёт.

Одинаковый формат может повторяться в разных играх. Заголовок авто-лота заканчивается на `🤖`; это дополнительный маркер для восстановления защиты от дублей, если локальный state потеряется.

Бот предпочитает разделы `Гайды`, затем `Прочее/Other`, затем любой обычный раздел игры. Валютные/chips-разделы не используются как обычные лоты.

Настройки:

```env
AUTOPUBLISH_ENABLED=1
AUTOPUBLISH_PRICE=1
AUTOPUBLISH_SEARCH_RESULTS=8
AUTOPUBLISH_MIN_SOURCES=2
AUTOPUBLISH_GUIDE_MAX_CHARS=3200
AUTOPUBLISH_DELAY_SECONDS=15
AUTOPUBLISH_INTERVAL_SECONDS=1800
AUTOPUBLISH_MAX_NEW_PER_CYCLE=0
```

`AUTOPUBLISH_MAX_NEW_PER_CYCLE=0` означает: не ограничивать число новых игр за проход. Между попытками остаётся задержка, чтобы не отправлять пачку запросов одновременно.

Сначала посмотреть выбранные разделы:

```bash
sudo -u funpay .venv/bin/python autoseller.py sections
```

Прогон без публикации при `DRY_RUN=1`:

```bash
sudo -u funpay .venv/bin/python autoseller.py once
sudo -u funpay .venv/bin/python autoseller.py status
```

После переключения `DRY_RUN=0`:

```bash
sudo systemctl enable --now chekuska-raise
journalctl -u chekuska-raise -f
```

Сервис одновременно создаёт недостающие авто-лоты и поднимает существующие категории, указанные в `RAISE_CATEGORY_IDS`.

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
