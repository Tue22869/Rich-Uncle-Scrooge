# SmartFinances — Telegram-бот «Дядя Скрудж»

Бот для учёта личных финансов с ИИ (GPT) для понимания естественного языка на русском.

## Возможности

- 💰 Доходы и расходы естественным языком
- 🔄 Переводы между счетами (включая кросс-валютные)
- 📊 Отчёты с разбивкой по категориям и бюджетами
- 🔍 AI-аналитика: «почему так много на кофе»
- 📋 Бюджеты по категориям с уведомлениями 80%/100%
- 🔥 Стрики и ачивки (10 достижений)
- 📊 Еженедельные и ежемесячные дайджесты (LLM-generated)
- 💎 Premium-подписка через Telegram Stars и YooKassa (trial 14 дней)
- ✅ Подтверждение операций через кнопки
- 🎤 Голосовой ввод (OpenAI Whisper)
- 📄 Google Sheets экспорт/импорт
- 📦 Пакетный ввод нескольких операций

## Быстрый старт

```bash
pip install -r requirements.txt
cp env.example .env
# Заполни OPENAI_API_KEY и TELEGRAM_BOT_TOKEN в .env

python main.py
```

## Структура проекта

```
main.py                         — Точка входа, регистрация хендлеров
bot/
  handlers.py                   — /start, /help, text, voice, callback router
  menu.py                       — Persistent keyboard + inline menus
  account_setup.py              — Онбординг: пошаговое создание счетов
  subscription.py               — Premium: trial, покупка, статус
  middleware.py                 — Paywall, проверка подписки
  intents.py                    — Роутинг по LLM-интентам
  callbacks.py                  — confirm/cancel/undo
  helpers.py                    — get_db(), execute_single_operation
  sheets.py                     — Google Sheets команды
db/
  models.py                     — User, Account, Transaction, Subscription, Budget
  session.py                    — Engine, init_db, миграции
llm/
  parser.py                     — parse_message() → OpenAI → LLMResponse
  prompts.py                    — Системные промпты
schemas/
  llm_schema.py                 — Pydantic-модели
services/
  ledger.py                     — CRUD: доходы, расходы, переводы, счета
  reports.py                    — Отчёты с бюджетами
  insights.py                   — AI-аналитика
  billing.py                    — YooKassa: платежи, trial, подписки
  retention.py                  — Дайджесты, стрики, ачивки, бюджеты
  scheduler.py                  — JobQueue: cron-задачи
  speech.py                     — OpenAI Whisper (voice → text)
  webhook_server.py             — YooKassa webhook (отдельный процесс)
  google_sheets_client.py       — Google Sheets клиент
  sheets_export.py / sheets_import.py / sheets_format.py / sheets_sync.py
utils/
  dates.py                      — Даты и таймзоны
  money.py                      — format_amount()
tests/
  conftest.py                   — In-memory SQLite
  test_ledger.py / test_parser.py / test_utils.py
  test_billing.py / test_billing_stars.py / test_retention.py
  test_speech.py / test_middleware.py / test_webhook.py
alembic/                        — миграции схемы (env.py, versions/)
```

## Примеры

```
кофе 320                     # расход (категория автоматически)
+50000 зп                    # доход
переведи 5к с карты на нал   # перевод
отчет за ноябрь              # отчёт
почему так много на еду      # аналитика
бюджет на кофе 3000          # установить бюджет
история операций             # список транзакций
мои счета                    # баланс счетов
```

## Google Sheets

- `/sheets <ссылка>` — подключить таблицу
- `/sheets_export` — выгрузить данные
- `/sheets_import` — загрузить данные
- `/sheets reset` — отключить

## Тесты и линтер

```bash
pytest tests/ -v
ruff check .
```

CI запускает обе команды на Python 3.10–3.12 (см. `.github/workflows/ci.yml`).

## Миграции БД (Alembic)

Локально (свежая БД): `python main.py` сам прогонит `init_db()`/lightweight миграции — Alembic не нужен.

Для прода используйте Alembic:

```bash
# первое подключение к существующей prod-БД (отметить, что схема уже соответствует head):
alembic stamp head

# при обновлении кода (если в models.py появились новые поля):
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```

## YooKassa webhook (опционально)

YooKassa подтверждает платежи через HTTP-нотификации. Запустите отдельный процесс
рядом с ботом:

```bash
python -m services.webhook_server
```

Прокиньте его наружу через HTTPS-прокси (`https://your.domain/yookassa/webhook`)
и пропишите этот URL в YooKassa → Интеграции → HTTP-уведомления.
Без webhook оплата YooKassa всё ещё работает в режиме «нажми Проверить оплату».

Telegram Stars **не требуют** webhook — `successful_payment` приходит обычным update'ом.

## Docker

```bash
docker-compose up -d
```

## Технологии

- Python 3.10+, python-telegram-bot 20.7
- SQLAlchemy + SQLite (миграции через Alembic)
- OpenAI GPT (каскад: gpt-4o-mini → gpt-4o) + Whisper для голоса
- Telegram Stars + YooKassa (платежи)
- Google Sheets API
- Sentry (мониторинг ошибок)
- Pydantic, APScheduler, aiohttp

## Документация

Подробная архитектурная документация: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
