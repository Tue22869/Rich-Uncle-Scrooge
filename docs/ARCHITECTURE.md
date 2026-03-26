# Архитектура проекта «Дядя Скрудж» (SmartFinances)

> Версия документа: 3.1 | Дата: 2026-03-22

## Текущее состояние проекта

### Реализовано

- **Фаза 1: Кнопочное меню и UX** — Полностью реализовано
  - Persistent ReplyKeyboardMarkup (нижняя клавиатура): Счета, Отчёты, Записать, Настройки, Помощь
  - Главное меню с inline-кнопками (bot/menu.py)
  - Подменю: Финансы, Отчёты, Помощь (📖 Инструкция / ℹ️ О боте), Настройки (Таймзона / Sheets / Подписка), Premium
  - Онбординг нового пользователя с кнопками (bot/account_setup.py)
  - Пошаговое создание счёта кнопками: валюта → название → начальный баланс
  - Быстрое создание счетов кнопками (RUB/USD/EUR)
  - Кнопка «Главное меню» во всех разделах
  - Все старые команды (/start, /help, /accounts, /report) работают

- **Фаза 2: Интеграция ЮKassa** — Полностью реализовано
  - Модель Subscription (trial/monthly/yearly)
  - Активация пробного периода (14 дней, одноразово)
  - Создание платежей YooKassa (services/billing.py)
  - Проверка статуса платежа + подтверждение
  - Paywall — все функции за подпиской
  - Middleware проверки подписки перед каждым действием
  - Экран Premium с информацией и кнопками покупки

- **Фаза 3: Retention-фишки** — Полностью реализовано
  - Еженедельный дайджест (воскресенье 20:00 UTC)
  - Ежемесячный дайджест (1-е число 10:00 UTC)
  - Стрики записей + отслеживание активности
  - Система ачивок (10 достижений)
  - Бюджеты по категориям с уведомлениями 80%/100% + создание через NLP ("бюджет на кофе 3000")
  - Напоминания при неактивности (3+ дней)
  - Совет дня по финансовой грамотности (14 советов)
  - Планировщик на JobQueue (services/scheduler.py)

- **Базовый функционал** (было до текущей работы):
  - Текстовый ввод операций на естественном языке
  - Голосовой ввод (SpeechFlow)
  - LLM-парсинг с каскадом моделей
  - CRUD счетов, транспортов, переводов
  - Отчёты по периодам с разбивкой по категориям
  - AI-аналитика (insight)
  - Google Sheets экспорт/импорт
  - Подтверждение операций кнопками ✅/❌
  - Отмена последней операции (undo)
  - Пакетный ввод операций
  - Редактирование и удаление операций по номеру

### Не реализовано / TODO

- Webhook-режим для YooKassa (сейчас ручная проверка кнопкой)
- Автоматическое напоминание о продлении за 3 дня до истечения
- Alembic миграции (используются ручные ALTER TABLE)

---

## Структура файлов

```
main.py                         — Точка входа, регистрация хендлеров, запуск бота
bot/
  handlers.py                   — Команды: /start, /help, /accounts, /report
                                  Обработка текста и голоса. Единый callback_handler
  intents.py                    — Роутинг по LLM-интентам (batch, report, insight, mutation)
  callbacks.py                  — Логика confirm/cancel/undo/report_analysis
  helpers.py                    — get_db(), валидация, превью, execute_single_operation
  menu.py                       — Главное меню, подменю, навигация кнопками
  account_setup.py              — Онбординг: создание счетов кнопками
  subscription.py               — Premium-меню, активация trial, покупка подписки
  middleware.py                 — Проверка подписки, paywall, usage stats
  sheets.py                     — Команды Google Sheets
  __init__.py
db/
  models.py                     — SQLAlchemy модели: User, Account, Transaction,
                                  PendingAction, Subscription, Budget
  session.py                    — Engine, SessionLocal, init_db(), миграции
  __init__.py
llm/
  parser.py                     — parse_message() → OpenAI → LLMResponse
  prompts.py                    — Системные промпты, категории, форматирование
  __init__.py
schemas/
  llm_schema.py                 — Pydantic-модели для LLM-ответов
  __init__.py
services/
  ledger.py                     — CRUD операции: add_income/expense, transfer, accounts
  reports.py                    — Отчёты за период с разбивкой по категориям
  insights.py                   — AI-аналитика ("почему много на кофе")
  billing.py                    — YooKassa: платежи, trial, подписки
  retention.py                  — Дайджесты, стрики, ачивки, бюджеты
  scheduler.py                  — Планировщик: cron-задачи через JobQueue
  speech.py                     — SpeechFlow API (voice → text)
  google_sheets_client.py       — Низкоуровневый клиент Google Sheets
  sheets_export.py              — Экспорт данных в таблицу
  sheets_import.py              — Импорт данных из таблицы
  sheets_format.py              — Форматирование ячеек
  sheets_sync.py                — Синхронизация
  __init__.py
utils/
  dates.py                      — Работа с датами и таймзонами
  money.py                      — format_amount()
  __init__.py
tests/
  conftest.py                   — Общие фикстуры: in-memory SQLite для изоляции
  test_ledger.py                — Тесты CRUD, счетов, переводов
  test_parser.py                — Тесты парсинга LLM (async, mocked)
  test_utils.py                 — Тесты утилит
  test_billing.py               — Тесты подписок, trial, expiry
  test_retention.py             — Тесты стриков, ачивок, бюджетов, saved_10k
  __init__.py
```

---

## Модели данных (БД)

### users

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| tg_user_id | Integer UNIQUE | Telegram user ID |
| timezone | String | Таймзона (default: Europe/London) |
| default_account_id | Integer nullable | FK → accounts.id |
| google_sheets_spreadsheet_id | String nullable | ID таблицы Google Sheets |
| trial_used | Boolean | Был ли использован пробный период |
| trial_activated_at | DateTime nullable | Дата активации trial |
| streak_days | Integer | Текущий стрик (дни подряд) |
| last_activity_date | String nullable | ISO дата последней активности |
| total_operations | Integer | Общее число подтверждённых операций |
| achievements_json | JSON nullable | Список кодов достижений |
| created_at | DateTime | |

### accounts

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| user_id | Integer FK | → users.id |
| name | String | Название (Карта, Наличные) |
| currency | String | ISO 4217 (RUB, USD, EUR, USDT) |
| balance | DECIMAL(15,2) | Текущий баланс |
| is_default | Boolean | Основной счёт |
| created_at | DateTime | |

### transactions

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| user_id | Integer FK | → users.id |
| type | Enum | INCOME, EXPENSE, TRANSFER |
| amount | DECIMAL(15,2) | Сумма (абсолютная) |
| currency | String | Валюта |
| account_id | Integer FK nullable | Для INCOME/EXPENSE |
| from_account_id | Integer FK nullable | Для TRANSFER (источник) |
| to_account_id | Integer FK nullable | Для TRANSFER (получатель) |
| category | String nullable | Категория |
| subcategory | String nullable | Подкатегория |
| description | String nullable | Описание |
| operation_date | DateTime | Дата операции |
| created_at | DateTime | |

### pending_actions

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| user_id | Integer FK | → users.id |
| action_type | Enum ActionType | Тип операции |
| payload_json | JSON | Данные операции |
| preview_message_id | Integer nullable | ID сообщения для редактирования |
| created_at | DateTime | |
| expires_at | DateTime | Время истечения (5-15 мин) |
| status | Enum PendingStatus | PENDING/CONFIRMED/CANCELLED/EXPIRED |

### subscriptions

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| user_id | Integer FK | → users.id |
| plan | Enum | TRIAL, MONTHLY, YEARLY |
| status | Enum | ACTIVE, EXPIRED, CANCELLED |
| payment_id | String nullable | YooKassa payment ID |
| paid_at | DateTime nullable | Дата оплаты |
| expires_at | DateTime | Дата окончания подписки |
| created_at | DateTime | |

### budgets

| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | |
| user_id | Integer FK | → users.id |
| category | String | Категория расходов |
| monthly_limit | DECIMAL(15,2) | Месячный лимит |
| currency | String | Валюта лимита |
| created_at | DateTime | |

---

## Потоки данных (Flows)

### Обработка текстового сообщения

1. `message_handler()` (handlers.py) получает текст
2. Проверка подписки: `check_subscription()` → если нет — paywall
3. Загрузка аккаунтов пользователя
4. `parse_message()` (llm/parser.py) → OpenAI → `LLMResponse`
5. Роутинг по intent:
   - read-only (report, insight, list, show_accounts) → прямой ответ
   - mutation (income, expense, transfer, ...) → `PendingAction` + кнопки ✅/❌
   - set_budget → создание/обновление бюджета (прямое, без PendingAction)
6. При confirm → `execute_single_operation()` → обновление баланса + транзакция
7. `update_streak()` → проверка ачивок → уведомление → проверка бюджетных алертов

### Подписка и Paywall

1. Перед каждым действием (кроме /start, sub:*) вызывается `check_subscription()`
2. Если нет активной подписки → показывается paywall с кнопками:
   - «🎁 Пробный период (14 дней)» (если trial_used = False)
   - «📅 Месяц — 190₽» / «📆 Год — 1900₽»
3. При нажатии «sub:buy:monthly» → `create_payment()` → YooKassa → URL оплаты
4. После оплаты пользователь нажимает «✅ Проверить оплату»
5. `check_payment_status()` → если succeeded → `confirm_payment()` → создаётся Subscription
6. `expire_subscriptions()` вызывается каждые 6 часов через JobQueue

### Дайджесты

1. `setup_scheduled_jobs()` регистрирует задачи в JobQueue при старте бота
2. Воскресенье 20:00: `_send_weekly_digests()` → `generate_weekly_digest_llm()` (LLM rewrite с fallback на шаблон)
3. 1-е число 10:00: `_send_monthly_digests()` → `generate_monthly_digest_llm()` (LLM rewrite с fallback на шаблон)
4. Ежедневно 18:00: `_send_inactivity_reminders()` → пользователям без активности 3+ дней

### Стрики и ачивки

1. После confirm операции вызывается `update_streak(db, user)`
2. Проверяется `last_activity_date`:
   - Сегодня → ничего
   - Вчера → streak_days += 1
   - Раньше → streak_days = 1 (сброс)
3. Проверяются условия ачивок (first_op, ops_10, streak_7, saved_10k, multi_account, first_month, ...)
4. Новые ачивки → уведомление пользователю
5. После confirm также проверяются бюджетные алерты (`check_budget_alerts()`)

### Создание бюджета

1. Пользователь пишет: «бюджет на кофе 3000» или «лимит на еду 15000₽»
2. LLM возвращает `intent: "set_budget"`, `data.budget_category`, `data.budget_limit`
3. `_handle_set_budget()` в handlers.py создаёт/обновляет запись в таблице `budgets`
4. При каждом подтверждении расхода `check_budget_alerts()` проверяет лимиты

### Создание своего счёта (пошаговый диалог)

1. Пользователь нажимает «✏️ Создать свой счёт» → `acct:custom`
2. Показываются кнопки валют: RUB/USD/EUR/USDT → `acct:currency:<currency>`
3. Выбранная валюта сохраняется в `context.user_data["custom_account_currency"]`
4. Пользователь вводит название текстом → `handle_custom_account_name()`
5. Счёт создаётся, показывается список счетов

---

## Callback-форматы

### Финансовые операции (`fin:`)
- `fin:confirm:<pending_id>` — подтвердить операцию
- `fin:cancel:<pending_id>` — отменить операцию
- `fin:undo:<pending_id>` — отменить подтверждённую операцию
- `fin:report_analysis:<tg_user_id>:<period>` — GPT-анализ отчёта

### Навигация меню (`menu:`)
- `menu:main` — главное меню
- `menu:finances` — подменю финансов
- `menu:reports` — подменю отчётов
- `menu:premium` — экран подписки
- `menu:help` — помощь
- `menu:settings` — настройки
- `menu:about` — о боте
- `menu:status` — статус подписки

### Команды из меню (`cmd:`)
- `cmd:accounts` — список счетов
- `cmd:history` — подсказка по истории
- `cmd:analytics` — подсказка по аналитике
- `cmd:help` — полная справка
- `cmd:sheets` — Google Sheets
- `cmd:set_default` — выбор главного счёта (picker)
- `cmd:setdef:<account_id>` — назначить конкретный счёт главным

### Настройки (`settings:`)
- `settings:timezone` — выбор таймзоны
- `settings:tz:<timezone>` — установить конкретную таймзону (Europe/Moscow и т.д.)

### Быстрые отчёты (`report:`)
- `report:today` / `report:week` / `report:month` / `report:last_month` / `report:year`

### Создание счетов (`acct:`)
- `acct:create:<name>:<currency>` — быстрое создание
- `acct:custom` — пошаговое создание (шаг 1: выбор валюты)
- `acct:currency:<currency>` — выбор валюты → переход к вводу названия
- `acct:balance:0` — пропустить ввод баланса (создать с 0)
- `acct:more` — добавить ещё счёт
- `acct:back_to_start` — назад к онбордингу

### Persistent-клавиатура (ReplyKeyboardMarkup)
Все кнопки проверяют подписку через `check_subscription()` перед показом контента.
- `💰 Счета` → список счетов + кнопки создания / назначения главного
- `📊 Отчёты` → выбор периода (сегодня, неделя, месяц, прошлый месяц, год)
- `📝 Записать` → подсказка по формату
- `⚙️ Настройки` → таймзона, sheets, подписка
- `❓ Помощь` → инструкция, о боте

### Подписка (`sub:`)
- `sub:activate_trial` — активировать пробный период
- `sub:buy:monthly` — купить месячную подписку
- `sub:buy:yearly` — купить годовую подписку
- `sub:check:<payment_id>` — проверить статус оплаты

---

## Конфигурация (.env)

| Переменная | Назначение | Обязательна |
|-----------|-----------|-------------|
| TELEGRAM_BOT_TOKEN | Токен бота | Да |
| OPENAI_API_KEY | Ключ OpenAI | Да |
| DATABASE_URL | URL базы данных | Нет (default: sqlite) |
| SPEECHFLOW_KEY_ID | SpeechFlow ключ | Для голоса |
| SPEECHFLOW_KEY_SECRET | SpeechFlow секрет | Для голоса |
| GOOGLE_APPLICATION_CREDENTIALS | Путь к JSON сервисного аккаунта | Для Sheets |
| YOOKASSA_SHOP_ID | ID магазина YooKassa | Для оплаты |
| YOOKASSA_SECRET_KEY | Секретный ключ YooKassa | Для оплаты |
| ADMIN_USER_IDS | Telegram ID администраторов | Нет |
| LOG_LEVEL | Уровень логирования | Нет (default: INFO) |

---

## Ограничения и важные нюансы

1. **Callback routing**: Все callback'и проходят через единый `callback_handler()` в handlers.py, который роутит по префиксу (`fin:`, `menu:`, `acct:`, `sub:`, `report:`, `cmd:`)

2. **Подписка обязательна**: Без активной подписки (включая trial) НИКАКОЙ функционал НЕ доступен (кроме /start, /help и покупки). Бесплатного тира нет. Middleware `check_subscription()` вызывается в: `process_user_text()`, `voice_message_handler()`, `accounts_command()`, `report_command()`, `sheets_command()`, `sheets_export_command()`, `sheets_import_command()`, `handle_persistent_menu()`, `menu_callback_handler()` (для protected callbacks), `_handle_report_shortcut()`, `callback_handler()` (для fin:confirm и fin:undo)

3. **Trial — одноразовый**: Флаг `trial_used` в User. После активации повторно недоступен

4. **Retry logic**: Monkeypatch в handlers.py — `Message.reply_text`, `Message.edit_text`, `CallbackQuery.edit_message_text` обёрнуты в retry с экспоненциальным backoff

5. **Concurrent updates OFF**: Бот обрабатывает обновления последовательно (не параллельно)

6. **SQLite миграции**: Нет Alembic. Новые колонки добавляются через `_ensure_sqlite_schema()` в session.py

7. **LLM каскад**: gpt-4o-mini → gpt-4o. Анализ всегда через gpt-4o

8. **JobQueue**: Требует `python-telegram-bot[job-queue]` (включает APScheduler)

9. **Стрики**: Обновляются ТОЛЬКО при confirm операции (не при создании pending)

10. **Бюджеты**: Алерты проверяются при confirm расхода. 80% — предупреждение, 100% — критический

11. **Кнопка «Главное меню»**: Присутствует во ВСЕХ ответах бота — команды, ошибки, confirm/cancel/undo, анализ. Используй `MENU_BUTTON` из handlers.py или `_MENU_KB` из callbacks.py

---

## Changelog

| Дата | Что сделано | Файлы |
|------|------------|-------|
| 2026-03-21 | Фаза 1: Кнопочное меню, онбординг | bot/menu.py, bot/account_setup.py, bot/handlers.py, main.py |
| 2026-03-21 | Фаза 2: YooKassa, подписки, trial, paywall | db/models.py, services/billing.py, bot/subscription.py, bot/middleware.py |
| 2026-03-21 | Фаза 3: Стрики, ачивки, дайджесты, бюджеты | services/retention.py, services/scheduler.py, db/models.py |
| 2026-03-21 | Фаза 4: Тесты, документация | tests/test_billing.py, tests/test_retention.py, docs/ARCHITECTURE.md |
| 2026-03-21 | Единый callback router в handlers.py | bot/handlers.py |
| 2026-03-21 | requirements.txt: yookassa, job-queue | requirements.txt |
| 2026-03-21 | Bugfix: async parser tests, missing _handle_custom_currency | tests/test_parser.py, bot/account_setup.py |
| 2026-03-21 | Subscription check в report shortcuts (была дыра в paywall) | bot/menu.py |
| 2026-03-21 | NLP-бюджеты: intent set_budget, _handle_set_budget | schemas/llm_schema.py, llm/prompts.py, bot/handlers.py |
| 2026-03-21 | saved_10k ачивка + budget alerts при confirm | services/retention.py, bot/handlers.py |
| 2026-03-21 | Пошаговое создание счёта (валюта кнопками → название текстом) | bot/account_setup.py, bot/handlers.py |
| 2026-03-21 | Тест-изоляция: conftest.py + in-memory SQLite | tests/conftest.py |
| 2026-03-21 | Persistent ReplyKeyboardMarkup (нижняя клавиатура) | bot/menu.py, bot/handlers.py |
| 2026-03-21 | Настройки: таймзона (picker 10 зон), подписка, sheets | bot/menu.py |
| 2026-03-21 | /help: добавлены секции бюджеты, стрики, ачивки, подписка | bot/handlers.py |
| 2026-03-21 | Создание счёта: шаг ввода начального баланса | bot/account_setup.py |
| 2026-03-21 | env.example: добавлены YOOKASSA\_SHOP\_ID, YOOKASSA\_SECRET\_KEY | env.example |
| 2026-03-21 | Edge case тесты: billing (5), retention (5) | tests/test\_billing.py, tests/test\_retention.py |
| 2026-03-21 | PERSISTENT\_MENU после создания счёта | bot/account\_setup.py |
| 2026-03-21 | Кнопка «Назначить главный» в подменю Счета | bot/menu.py |
| 2026-03-21 | report:last\_month — кнопка «Прошлый месяц» | bot/menu.py, utils/dates.py |
| 2026-03-21 | Бюджеты в отчётах (прогресс % по категориям) | services/reports.py |
| 2026-03-21 | LLM-генерация дайджестов (с fallback на шаблон) | services/retention.py, services/scheduler.py |
| 2026-03-21 | README.md обновлён (все фичи, структура) | README.md |
| 2026-03-21 | Paywall в persistent menu (была дыра — кнопки работали без подписки) | bot/menu.py |
| 2026-03-21 | Тесты: last\_month, week, year периоды + budget\_progress в отчётах | tests/test\_utils.py, tests/test\_retention.py |
| 2026-03-21 | Inline help: добавлены бюджеты и стрики | bot/menu.py |
| 2026-03-21 | Paywall: /accounts и /report теперь проверяют подписку | bot/handlers.py |
| 2026-03-21 | Paywall: inline menu callbacks проверяют подписку (exempt: main, premium, help, about) | bot/menu.py |
| 2026-03-21 | Paywall: /sheets, /sheets\_export, /sheets\_import проверяют подписку | bot/sheets.py |
| 2026-03-21 | Paywall: fin:confirm и fin:undo проверяют подписку (защита от expired sub) | bot/handlers.py |
| 2026-03-21 | Удалён dead code: check\_report\_limit(), \_record\_report() | bot/middleware.py, bot/menu.py |
| 2026-03-21 | Paywall: fin:report\_analysis теперь проверяет подписку | bot/handlers.py |
| 2026-03-21 | Bugfix: user\_data очищается при навигации назад в создании счёта | bot/account\_setup.py |
| 2026-03-21 | UX: persistent keyboard не спамит — отправляется на основном сообщении | bot/menu.py, bot/account\_setup.py |
| 2026-03-21 | UX: кнопка «Главное меню» добавлена во ВСЕ ответы бота | bot/handlers.py, bot/callbacks.py |
| 2026-03-22 | Убран бесплатный тир (free tier) — все функции только по подписке | bot/middleware.py, bot/menu.py |
| 2026-03-22 | Исправлены имена моделей LLM в документации (gpt-4o-mini/gpt-4o) | docs/ARCHITECTURE.md |
| 2026-03-22 | Версия бота обновлена до 3.0 | bot/menu.py |
