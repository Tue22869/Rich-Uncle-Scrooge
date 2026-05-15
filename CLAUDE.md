# CLAUDE.md — onboarding for AI assistants

> Прочитай это до того как лезть в код. ~200 строк, 2 минуты.
> Последнее обновление: 2026-05-16.

## Что это

Telegram‑бот «Дядя Скрудж» (@uncle_scrooge_bot) для учёта личных финансов.
Пользователь пишет «такси 500» или говорит голосом — бот распознаёт интент через
LLM, показывает превью, по подтверждению пишет в SQLite.

**Стек:**
- Python 3.10 в Docker контейнере (python:3.10-slim)
- python-telegram-bot 20.7
- SQLAlchemy 2.0 + SQLite (bind‑mount, не in‑container)
- OpenAI API: gpt‑4o‑mini (parse) → gpt‑4o (analysis), Whisper (voice)
- YooKassa SDK + Telegram Stars для платежей
- APScheduler для weekly/monthly digests
- Owner: github.com/Tue22869/Rich-Uncle-Scrooge

## Где живёт прод

| | Значение |
|---|---|
| Хост | `root@31.130.131.71` (Timeweb Cloud, Ubuntu 24.04, Amsterdam) |
| Код | `/var/www/smartfinances` |
| Менеджмент | Docker Compose (`docker compose`, не `docker-compose`) |
| Контейнер | `smartfinances_bot`, образ `smartfinances-smartfinances:latest` |
| БД | `/var/www/smartfinances/data/smartfinances.db` (bind‑mount → `/app/data/` в контейнере) |
| `.env` | `/var/www/smartfinances/.env` (НЕ в git, в `.gitignore`) |
| Бэкапы БД | рядом с БД: `data/smartfinances.db.backup_YYYYMMDD_HHMMSS` (создаются вручную перед каждым деплоем — автоматических нет) |

SSH работает через ключ юзера к личному GitHub аккаунту `IliaIntegral`,
у которого есть read‑доступ к репо `Tue22869/Rich-Uncle-Scrooge`. Remote на сервере
= `git@github.com:Tue22869/Rich-Uncle-Scrooge.git` (через SSH).

## Жёсткие правила

1. **НЕ пушить в `main`.** Все правки через feature‑ветки + merge через PR (или хотя бы локально). `main` — точка отката.
2. **НЕ удалять и не перезаписывать БД** без явного запроса. Перед любым деплоем — `cp data/smartfinances.db data/smartfinances.db.backup_$(date +%Y%m%d_%H%M%S)`.
3. **НЕ коммитить `.env`** — там прод‑токены (TELEGRAM_BOT_TOKEN боевого бота, OPENAI_API_KEY с балансом, и т.п.).
4. **Перед чем‑либо на сервере** — спроси юзера, не делай молча. Особенно: stop контейнера, миграции, изменения в env.
5. **Перед merge feature‑ветки** — обязательно прогнать `pytest tests/ -v` локально через venv, чтобы не задеплоить красное.
6. **Ветка `claude/magical-fermi-1e86df` — текущая прод‑ветка.** Сейчас на ней HEAD = `6ab0bed`. Новые правки — либо в неё, либо в новую feature‑ветку.

## Стандартные команды

### Локально (на Mac юзера)

```bash
# venv с зависимостями
/Users/macbook/Rich-Uncle-Scrooge/venv/bin/python
/Users/macbook/Rich-Uncle-Scrooge/venv/bin/pytest tests/ -v

# worktree куда я обычно мерджу/работаю
cd /Users/macbook/Rich-Uncle-Scrooge/.claude/worktrees/magical-fermi-1e86df

# главный репо (другая ветка может быть checked out)
cd /Users/macbook/Rich-Uncle-Scrooge
```

### На сервере (🛰️)

```bash
cd /var/www/smartfinances

# логи
docker compose logs --tail=100
docker compose logs -f                        # live
docker compose logs --tail=200 | grep -E "ERROR|Traceback"

# рестарт без пересборки
docker compose restart smartfinances

# пересборка + рестарт (нужно после git pull)
docker compose down && docker compose build --no-cache && docker compose up -d

# деплой (полный pipeline)
cp data/smartfinances.db data/smartfinances.db.backup_$(date +%Y%m%d_%H%M%S) && \
git rev-parse HEAD > /tmp/prev_commit.txt && \
git fetch origin && git pull --ff-only origin claude/magical-fermi-1e86df && \
docker compose down && docker compose build --no-cache && docker compose up -d && \
sleep 5 && docker compose logs --tail=40

# откат: git checkout $(cat /tmp/prev_commit.txt) → rebuild → restore БД из backup
```

### SSH keepalive (без него идёт rapid disconnect)

```bash
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=6 root@31.130.131.71
```

Уже прописано в `~/.ssh/config` юзера (host `31.130.131.71`).

## Архитектура (1 параграф)

`main.py` бутит python-telegram-bot Application + регистрирует все handlers и
APScheduler job_queue. Входящий текст идёт через `bot/handlers.message_handler`
→ `bot/handlers.process_user_text` → `llm/parser.parse_message` (cascade
gpt-4o-mini → gpt-4o) → `bot/intents.handle_*_intent` → создаёт `PendingAction`
→ юзер жмёт ✅ → `bot/callbacks.handle_confirm` → `bot/helpers.execute_single_operation`
→ `services/ledger.create_account|add_income|add_expense|transfer` → SQLite.
Голос: `bot/handlers.voice_message_handler` → `services/speech.transcribe_telegram_voice`
(OpenAI Whisper) → дальше как обычный текст. Аналитика: `services/analytics.log_event`
пишет в таблицу `usage_events` при каждом MESSAGE_IN/VOICE_IN/COMMAND/WHISPER/etc.
Платежи: `services/billing.py` (Stars + YooKassa с идемпотентностью). Подписки и
премиум — `bot/subscription.py`. Админ‑метрики — `bot/admin.py:/admin_stats`.

## Env vars (что есть в .env)

| Var | Зачем | Сейчас |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | прод‑бот @uncle_scrooge_bot | ✅ задан |
| `OPENAI_API_KEY` | LLM + Whisper | ✅ задан |
| `ADMIN_USER_IDS` | comma‑separated TG ID для `/admin_stats` | ✅ задан (`707102323,671094213`) |
| `SENTRY_DSN` | observability | ❌ выключен; код готов, добавишь DSN — заработает |
| `ENV` | dev/staging/prod (тэг для Sentry) | по умолчанию `dev` |
| `YOOKASSA_SHOP_ID` / `YOOKASSA_SECRET_KEY` | оплата картой | ❌ не заданы → кнопка YooKassa в UI показывает заглушку |
| `WEBHOOK_HOST` / `WEBHOOK_PORT` | для YooKassa webhook | не используются пока |
| `GOOGLE_APPLICATION_CREDENTIALS` | service account для Google Sheets | внутри контейнера зашит путь `/app/google_credentials.json`, на хосте файл рядом с docker-compose.yml |
| `SPEECHFLOW_KEY_ID` / `SPEECHFLOW_KEY_SECRET` | legacy от SpeechFlow API | бот их игнорирует (перешли на Whisper) |

`.env` пробрасывается в контейнер через `env_file: - .env` в docker-compose.yml.
`DATABASE_URL` и `GOOGLE_APPLICATION_CREDENTIALS` дополнительно прописаны в
`environment:` блоке и перекрывают любые значения из .env.

## База данных

- **SQLite**, файл `/var/www/smartfinances/data/smartfinances.db`.
- Миграции: `db/session._ensure_sqlite_schema()` запускается при `init_db()` на старте
  бота. Идемпотентно добавляет недостающие колонки через `ALTER TABLE`. Сейчас она
  знает про `onboarding_step`, `provider`, `streak_days`, и пр.
- Alembic тоже в репо (`alembic.ini`, `alembic/versions/...`), но в runtime НЕ
  используется — opt‑in для проектов которые предпочитают alembic. На SQLite в проде
  работает auto‑schema.
- Новые таблицы (`usage_events`) создаются через `Base.metadata.create_all()` при
  старте.

Текущие таблицы: `users`, `accounts`, `transactions`, `pending_actions`,
`subscriptions`, `budgets`, `usage_events`.

Поля `users` сверх дефолтных: `timezone`, `default_account_id`,
`google_sheets_spreadsheet_id`, `trial_used`, `trial_activated_at`, `streak_days`,
`last_activity_date`, `total_operations`, `achievements_json`, `onboarding_step`.

## Что сейчас в проде (фичи)

- **Единый экран «💰 Счета»** с inline‑кнопками `⭐ ✏️ 🗑` на каждом счёте (`acct:setmain:<id>`, `acct:rename:<id>`, `acct:delete:<id>`).
- **Каскадное удаление счёта** с переносом главного на оставшийся.
- **Кастомные валюты** — любой тикер 1–10 символов A‑Z0‑9 (VND/DONG/THB/etc).
- **Онбординг‑тур** из 3 шагов (`User.onboarding_step` 0→1→2→3).
- **Sectioned /help** через `cmd:help:<section>` — 6 разделов.
- **Google Sheets** в главном меню + полноценный экран `cmd:sheets` с Export/Import/Disconnect.
- **Whisper для голоса** (вместо SpeechFlow).
- **Analytics event log** (`usage_events`) — MESSAGE_IN, VOICE_IN, REPORT_VIEW, COMMAND, WHISPER, SUBSCRIPTION_PAID etc + cost accounting в micros USD.
- **Telegram Stars платежи** — работают.
- **YooKassa** — показывает кнопку, при клике → заглушка «ещё не подключена» (включится автоматически когда юзер добавит `YOOKASSA_SHOP_ID` + `_SECRET_KEY` в .env).
- **`/admin_stats`** — DAU/WAU/MAU, конверсия trial→paid, revenue, топ‑5 по OpenAI расходам, Whisper нагрузка. Доступно ADMIN_USER_IDS.
- **Retry logic** для Telegram API в `bot/handlers.py` (monkeypatch с exp backoff 4 попытки).
- **Insufficient balance check отключён** — баланс может уйти в минус (юзеры используют бот для трекинга трат, а не для отслеживания нетто).

## Тесты

```bash
/Users/macbook/Rich-Uncle-Scrooge/venv/bin/pytest tests/ -v
```

Сейчас 237 passed. Покрытие хорошее (Ralph написал ~75 тестов на UI flows,
analytics, billing, admin_stats, e2e). Без полного прогона `pytest` — не пушить.

В CI: `.github/workflows/ci.yml` есть в репо, но прогоняется ли через GitHub
Actions — надо проверить (есть ли badge в README).

## Метрики и логи

- **`/admin_stats` в Telegram** — агрегаты (DAU/WAU/MAU, revenue, costs).
- **`usage_events` таблица** — сырая лента событий по юзерам, можно вытаскивать SQL’ом.
- **Docker logs** — `docker compose logs --tail=100`, max 30 MB (rolling).
- **Sentry** — выключен. Когда включат — exceptions будут там.

## TODO / известный долг

В порядке важности:

1. **Privacy Policy + ToS** — обязательно для платных Telegram‑ботов. Нет ничего.
2. **Cron‑бэкапы БД** — сейчас бэкапы только ручные при деплое. Нужен cron каждые 6ч в `/var/backups/`, хранить 14 дней.
3. **Sentry** — добавить `SENTRY_DSN` в .env когда зарегается на sentry.io.
4. **YooKassa подключить** — когда самозанятость подтвердят. Заглушка готова, нужно `YOOKASSA_SHOP_ID` + `_SECRET_KEY` + поднять `services/webhook_server.py` отдельным процессом + nginx с HTTPS перед ним + IP whitelist YooKassa.
5. **`datetime.utcnow()` deprecation** — везде. К Python 3.13 будет error. Рефактор на `datetime.now(datetime.UTC)`.
6. **152‑ФЗ / PDN‑локация** — сервер в Амстердаме. Для российских юзеров — нарушение хранения ПДн. Если планируется масштабирование в РФ — перенос на RU‑дата‑центр.
7. **CI на GitHub Actions** — `.github/workflows/ci.yml` есть, надо проверить что прогоняется.
8. **`docker image prune -af`** — старые образы накапливаются, диск 51%.
9. **Referral программа** — главный канал роста для TG‑ботов.
10. **SMS‑интеграция / автоимпорт из банков** — большой value‑add, но дорого в реализации.

## Что НЕ работает или сделано как костыль

- **YooKassa** — заглушка, при клике текст «ещё не подключена».
- **YooKassa webhook server** — `services/webhook_server.py` есть в коде, но в проде не запущен (отдельный процесс не поднимали). Нужен только когда подключим оплату картой.
- **Sentry** — выключен.
- **Privacy Policy** — отсутствует.
- **Cron‑бэкапы** — нет, бэкап только при деплое.
- **`datetime.utcnow()`** — куча DeprecationWarning в логах, не критично, но накопится.
- **52‑ФЗ кассовые чеки для Stars** — самозанятый должен формировать чеки. Stars не формирует автоматически, в коде заглушка отсутствует.

## История миграций (для контекста)

- `main` (24e6369) — версия от марта, простой read‑only список счетов, без Stars, без analytics, без Whisper.
- `feature/stability-and-payments` (8ac4a80) — отдельная ветка разработчика, которая добавила Whisper, Stars, analytics, /admin_stats, alembic. Не была задеплоена.
- `claude/magical-fermi-1e86df` (HEAD = 6ab0bed) — текущая прод‑ветка. **Merge `feature/stability-and-payments` + наш UX‑патч + балансовая правка.** На сервере с 2026-05-15.

Тег `pre-deploy-20260515_191448` на сервере указывает на коммит до первого деплоя
(24e6369) — крайняя точка отката.

## Если что‑то неочевидно

- AGENT.md в `.ralph/` — устаревший, не читай.
- `docs/ARCHITECTURE.md` — частично устарел (от 2026-03-22), но содержит полезное описание UI‑потоков.
- Когда узнаёшь новое крупное о боте — обнови этот файл.
