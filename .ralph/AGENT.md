# Agent Instructions — SmartFinances

## Build & Run

```bash
pip install -r requirements.txt
python main.py    # requires .env with tokens
```

## Tests

```bash
# Run ALL tests after EVERY change
pytest tests/ -v

# Specific suites
pytest tests/test_ledger.py -v
pytest tests/test_parser.py -v
pytest tests/test_utils.py -v
pytest tests/test_menu.py -v
pytest tests/test_billing.py -v
pytest tests/test_retention.py -v
pytest tests/test_budgets.py -v
```

## Docker

```bash
docker-compose up -d
docker-compose logs -f
```

## Database

SQLite via SQLAlchemy. Tables:
- `users` — tg_user_id, timezone, default_account_id, free_insights_used, free_insights_reset_at
- `accounts` — name, currency, balance, is_default
- `transactions` — type, amount, currency, category, subcategory, description, operation_date
- `pending_actions` — action_type, payload_json, status, expires_at
- `subscriptions` — NEW: plan, payment_id, amount, paid_at, expires_at, status
- `budgets` — NEW: category, monthly_limit, currency
- `user_stats` — NEW: streak_days, last_activity_date, total_operations, achievements_json

## Key conventions

- All amounts stored as DECIMAL(15, 2)
- Tests use in-memory SQLite
- DB auto-created by init_db() on first run
- No alembic yet — add new tables via Base.metadata.create_all (existing tables preserved)
- Inline buttons use format: `fin:action:param1:param2`
- Retry logic for Telegram API already implemented via monkeypatch in handlers.py
- LLM parser cascades: gpt-4o-mini first, then gpt-4o on failure

## Critical rules

- NEVER delete existing code — only extend
- ALWAYS add «◀️ Главное меню» button to every bot response
- ALWAYS run `pytest tests/ -v` after changes
- ALWAYS update `docs/ARCHITECTURE.md` after implementing any task — this is the living documentation that allows any new AI assistant or developer to understand the project
- Keep voice input working at all times
- Keep text input (natural language) working at all times
