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
- 💎 Premium-подписка через YooKassa (trial 14 дней)
- ✅ Подтверждение операций через кнопки
- 🎤 Голосовой ввод (SpeechFlow)
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
  speech.py                     — SpeechFlow (voice → text)
  google_sheets_client.py       — Google Sheets клиент
  sheets_export.py / sheets_import.py / sheets_format.py / sheets_sync.py
utils/
  dates.py                      — Даты и таймзоны
  money.py                      — format_amount()
tests/
  conftest.py                   — In-memory SQLite
  test_ledger.py / test_parser.py / test_utils.py
  test_billing.py / test_retention.py
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

## Тесты

```bash
pytest tests/ -v
```

## Docker

```bash
docker-compose up -d
```

## Технологии

- Python 3.10+, python-telegram-bot 20.7
- SQLAlchemy + SQLite
- OpenAI GPT (каскад: gpt-4o-mini → gpt-4o)
- YooKassa (платежи)
- Google Sheets API
- Pydantic, SpeechFlow, APScheduler

## Документация

Подробная архитектурная документация: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## Правовые документы

- Политика конфиденциальности: [`docs/privacy.html`](docs/privacy.html) →
  https://tue22869.github.io/Rich-Uncle-Scrooge/privacy.html
- Условия использования: [`docs/terms.html`](docs/terms.html) →
  https://tue22869.github.io/Rich-Uncle-Scrooge/terms.html

В боте доступны команды `/privacy` и `/terms`.
URL политики конфиденциальности должен быть установлен в `@BotFather` →
Bot Settings → Privacy Policy.

## Резервные копии БД

Скрипт [`scripts/backup_db.sh`](scripts/backup_db.sh) делает атомарный снапшот SQLite
через `sqlite3 .backup`, сжимает gzip-ом, кладёт в `~/backups/scrooge/` и удаляет
файлы старше 14 дней.

### Установка на сервере (одноразово)

```bash
# Установить sqlite3 CLI (нужен для безопасного snapshot работающей БД)
sudo apt-get update && sudo apt-get install -y sqlite3

# Сделать скрипт исполняемым
chmod +x ~/Rich-Uncle-Scrooge/scripts/backup_db.sh

# Прогнать вручную для проверки
~/Rich-Uncle-Scrooge/scripts/backup_db.sh
ls -lh ~/backups/scrooge/

# Поставить в cron: каждые 6 часов (00:00, 06:00, 12:00, 18:00)
crontab -e
# Добавить (подставив свой $HOME):
# 0 */6 * * * $HOME/Rich-Uncle-Scrooge/scripts/backup_db.sh >> $HOME/backups/scrooge/cron.log 2>&1
```

Параметры можно переопределить через env: `DB_PATH`, `BACKUP_DIR`, `RETENTION_DAYS`.

### Восстановление из бэкапа

```bash
docker compose stop smartfinances

gunzip -c ~/backups/scrooge/smartfinances-YYYYMMDD-HHMMSS.db.gz > /tmp/restore.db
mv ~/Rich-Uncle-Scrooge/data/smartfinances.db ~/Rich-Uncle-Scrooge/data/smartfinances.db.broken
cp /tmp/restore.db ~/Rich-Uncle-Scrooge/data/smartfinances.db

docker compose start smartfinances
```
