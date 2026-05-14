# Spec: тесты для UX‑правок счетов, валют, онбординга и /help

## Контекст

Ветка `claude/magical-fermi-1e86df` принесла большой пакет UX‑изменений (план: `/Users/macbook/.claude/plans/abstract-leaping-turing.md`). Код написан, базовые тесты добавлены и зелёные:

```
pytest tests/test_ledger.py tests/test_account_ux.py -v   # 34 passed
pytest --ignore=tests/test_billing.py --ignore=tests/test_parser.py  # 74 passed
```

Цель этой спеки: дописать **полное покрытие новых функций**, мок‑тесты на UI‑flow, и убедиться, что весь tests/ зелёный, включая `test_billing.py` и `test_parser.py` (через venv с telegram + openai).

## Что тестировать

### 1. `services/onboarding.py` — расширенно
- [`tests/test_account_ux.py`](tests/test_account_ux.py) уже содержит happy path. Добавить:
  - `advance_after_first_account` не сбрасывает счёт при `step>=1`.
  - `advance_after_confirmed_op` идемпотентен на `step==3`.
  - Concurrent calls на одного user (две операции почти одновременно) не пропускают шаг.

### 2. `services/ledger.delete_account` — добавочные кейсы
В [`tests/test_ledger.py`](tests/test_ledger.py) есть основа. Добавить:
- Удаление счёта‑участника `transfer` корректно убирает transfer и оставляет балансы других счетов нетронутыми (это и спека плана, и сейчас это работает — нужен явный тест).
- Удаление счёта, который **не** был главным, не трогает `user.default_account_id`.
- Удаление последнего счёта обнуляет и `is_default`, и `default_account_id`.

### 3. `services/ledger.count_account_transactions` — крайние кейсы
- Счёт без операций → 0.
- Счёт с одним income + одним expense → 2.
- Счёт, упомянутый как `from_account_id` и `to_account_id` в разных переводах, считается уникально (каждая запись 1 раз).

### 4. `bot/account_setup` — текстовый поток для произвольного тикера
Создать `tests/test_account_setup.py`. Использовать `unittest.mock.AsyncMock` для Telegram‑объектов.
- `_handle_custom_currency_input` с валидным `vnd` → `context.user_data["custom_account_currency"] == "VND"`, флаг `awaiting_custom_currency` снят, отправлен reply с шагом 2.
- С невалидным `мбр$` или `tooooolongticker` → флаг **сохраняется**, отправлен reply с ошибкой и кнопкой «↩️ Назад к валютам».
- `_handle_custom_currency` для `acct:currency:custom` callback устанавливает `awaiting_custom_currency=True` и шлёт правильный текст.
- Полный flow: currency=custom → ввод тикера → ввод имени → ввод баланса → `create_account` вызван с произвольным тикером.

### 5. `bot/menu._build_accounts_screen` — pure builder
Создать `tests/test_menu_accounts.py`. Этот хелпер чистый: принимает list[Account], возвращает (text, keyboard) — можно тестировать без моков.
- Пустой список → текст про «здесь пусто», keyboard с кнопкой «➕ Создать счёт».
- Один счёт (default) → строка с ⭐, нет кнопки ⭐ в action row, есть ✏️ и 🗑.
- Несколько счетов, один default → у не‑default появляется кнопка ⭐ с callback `acct:setmain:<id>`.
- В keyboard всегда есть кнопки 📄 Google Sheets, 📜 История, 🏠 Главное меню.

### 6. `bot/menu` callbacks — диспетч
В `tests/test_menu_callbacks.py`:
- `account_management_callback` правильно роутит:
  - `acct:setmain:42` → `_handle_setmain_callback(update, 42)`.
  - `acct:rename:42` → `_handle_rename_start(update, context, 42)`.
  - `acct:delete:42` → `_handle_delete_prompt(update, 42)`.
  - `acct:delete_confirm:42` → `_handle_delete_confirm(update, 42)`.
  - `acct:rename_cancel` → `_handle_rename_cancel(update, context)`.
  - Битый payload (`acct:setmain:` без id) — `query.answer` вызван и ничего не падает.
- `handle_rename_account_input` валидирует длину (1..50), дубль‑имя возвращает ошибку, успех вызывает `rename_account` и `_show_accounts_screen`.

### 7. `bot/menu._show_sheets_screen`
Мокать `services.google_sheets_client.is_configured` и `get_service_account_email`. Два кейса:
- `is_configured()=True`, `user.google_sheets_spreadsheet_id` задан → keyboard с 4 кнопками (Экспорт, Импорт, Отключить, Главное меню).
- `is_configured()=True`, привязка отсутствует → текст с инструкцией + только кнопка «Главное меню».
- `is_configured()=False` → текст «не настроено».

### 8. `bot/handlers.HELP_SECTIONS` — содержимое
- Все 6 секций (overview, accounts, record, reports, sheets, troubleshoot) присутствуют и не пустые.
- `accounts` упоминает «главный» и «валюту».
- `record` начинается с действия («Расход:» / «Доход:»).
- `troubleshoot` упоминает удаление и подписку.

### 9. `bot/handlers.callback_handler` routing (mock)
В `tests/test_callback_routing.py`:
- `data="acct:setmain:1"` → вызывается `bot.menu.account_management_callback`.
- `data="acct:delete_confirm:1"` → тоже.
- `data="acct:custom"` → вызывается `account_setup_callback`, не `account_management_callback`.
- `data="acct:more"` → `account_action_callback`.

### 10. E2E pipeline: «Создай счёт в VND название Донг»
В `tests/test_e2e_account_creation.py`:
- Мокать `llm.parser.parse_message` чтобы он вернул `LLMResponse(intent="account_add", data=...)` с `currency="VND"`, `account_new.name="Донг"`.
- Прогнать `process_user_text` → должен быть создан `PendingAction`.
- Прогнать `handle_confirm` с этим `pending.id` → создаётся `Account(currency="VND", name="Донг")`, без `UnboundLocalError`.

### 11. Регрессия
- `pytest tests/test_billing.py -v` — зелёный (запускать через venv).
- `pytest tests/test_parser.py -v` — зелёный.
- `pytest tests/test_menu.py -v` — зелёный (если есть; иначе создать минимальные тесты для основных menu helpers, которые не сломались).

## Технические замечания

- **venv**: `/Users/macbook/Rich-Uncle-Scrooge/venv/bin/python` содержит `telegram==20.7` и `openai==1.12.0`. Все тесты запускать через него: `venv/bin/pytest tests/ -v`.
- **Async‑тесты**: проект использует `pytest-asyncio` в режиме `auto` (см. `pytest.ini`). Async‑тесты не требуют декоратора.
- **Моки Telegram**: используй `unittest.mock.AsyncMock` для `Update`, `CallbackQuery`, `Message`. Образец — посмотри как импортируются `Update` в `bot/menu.py`.
- **Не ломай существующие тесты**. Перед каждым коммитом — `venv/bin/pytest tests/ -v`.
- **Не редактируй продакшен‑код** без сильной необходимости. Если нужно — отдели в отдельный коммит «hotfix» и обоснуй.

## Критерии «готово»

- [ ] `venv/bin/pytest tests/ -v` — **0 failed, 0 errors**, всё зелёное.
- [ ] Покрыты все 11 пунктов выше.
- [ ] Новые тесты не зависят от сети или реального Telegram/OpenAI.
- [ ] `ruff check bot/ services/ db/ tests/` без ошибок (предупреждения OK).
- [ ] Все коммиты в этой ветке.
