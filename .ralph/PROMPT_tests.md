# Ralph — Test Coverage Sprint

Ты — Ralph для проекта Rich‑Uncle‑Scrooge (Telegram‑бот, python‑telegram‑bot + SQLAlchemy + OpenAI). Сейчас твоя единственная задача — **довести покрытие тестами до 100 % для свежих UX‑изменений в ветке `claude/magical-fermi-1e86df`** и убедиться, что весь `pytest tests/ -v` зелёный, включая `test_billing.py` и `test_parser.py`.

## Источник истины

- **Спека этого спринта**: [`.ralph/specs/test_coverage_account_ux.md`](.ralph/specs/test_coverage_account_ux.md). Список из 11 разделов с конкретными тест‑кейсами и критериями «готово».
- **План фичи**: `/Users/macbook/.claude/plans/abstract-leaping-turing.md` — что именно правилось в коде.
- **Прогресс этой работы**: `.ralph/progress.json` — обновляй его после каждой итерации (см. формат ниже).

## Алгоритм одной итерации

1. **Сориентируйся.**
   - Запусти `venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -60`. Запиши: сколько passed/failed/errored.
   - Прочитай `.ralph/progress.json`. Если файла нет — создай: `{"completed": [], "current": null, "iterations": 0}`.
   - Прочитай спеку и сверь с прогрессом: какой раздел ещё открыт?
   - Прочитай git log за последние 10 коммитов: что уже сделано в этой ветке.

2. **Выбери ОДИН раздел** из спеки (`1` … `11`). Не пытайся делать сразу всё. Если упал какой‑то существующий тест — он в приоритете (раздел «Регрессия»).

3. **Делегируй sub‑агентам через Task**. Запускай агентов параллельно (несколько вызовов `Task` в одном сообщении), если работа независима.
   - **Research agent (subagent_type=Explore)**: «найди как используется X в кодовой базе, какие сигнатуры функций, какие моки уже применяются в существующих тестах».
   - **Test‑writer agent (subagent_type=general-purpose)**: «напиши такой‑то тестовый файл по этому спеку, верни путь к файлу». Дай агенту:
     - точный путь к новому файлу (`tests/test_account_setup.py`, etc.);
     - сигнатуры функций под мок;
     - образец фикстур из `tests/test_ledger.py` или `tests/test_account_ux.py`;
     - ожидаемые ассерты по списку из спеки.
   - **Runner agent (subagent_type=general-purpose)**: после написания тестов — «запусти `venv/bin/pytest tests/<новый_файл> -v` и почини всё, что упало».
   - НЕ запускай агента "напиши все 11 разделов разом". Это слишком много контекста — он начнёт галлюцинировать.

4. **Проверь сам.**
   - `venv/bin/pytest tests/<новый_файл> -v` — должно пройти.
   - `venv/bin/pytest tests/ -v` — должно остаться зелёным (никаких новых падений).
   - Если что‑то сломалось — НЕ коммить. Почини. Если за итерацию не получилось — закрой её, оставь pending, переходи к следующей.

5. **Закоммить.**
   - `git add tests/<новый_файл>` (только тестовый файл; продакшен‑код не трогай, если этого не требует спека).
   - `git commit -m "test: <раздел из спеки>"` с трейлером `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

6. **Обнови прогресс.**
   - В `.ralph/progress.json` добавь раздел в `completed`, инкрементируй `iterations`, обнови `last_pytest` с числом тестов.
   - Запиши одну строку в `.ralph/logs/ralph.log` со временем, разделом и результатом.

7. **Заверши итерацию.** Не пытайся за один заход сделать второй раздел.

## Когда останавливаться

Цикл заканчивается, когда выполнены ВСЕ критерии «готово» из спеки:
- ✅ `venv/bin/pytest tests/ -v` — 0 failed, 0 errors.
- ✅ Все 11 разделов помечены completed.
- ✅ `venv/bin/ruff check bot/ services/ db/ tests/` без ошибок.

Когда всё готово — напиши в `.ralph/progress.json` `"current": "DONE"` и **молча выйди**. Ralph‑loop сам остановит дальнейшие запуски, увидев DONE.

## Жёсткие правила

- **venv обязателен**: `/Users/macbook/Rich-Uncle-Scrooge/.claude/worktrees/magical-fermi-1e86df/venv` (его нет в worktree, поэтому используй абсолютный путь к основному venv: `/Users/macbook/Rich-Uncle-Scrooge/venv/bin/python` и `/Users/macbook/Rich-Uncle-Scrooge/venv/bin/pytest`).
- **Никаких сетевых вызовов в тестах** — всегда мокай `openai`, `telegram.Bot.*`, `services.google_sheets_client.*`.
- **Не правь существующие коммиты**. Только новые.
- **Не пуш в remote** — только локальные коммиты в ветке `claude/magical-fermi-1e86df`.
- **Если возникает циклическая ошибка**, которую ты уже пытался чинить — НЕ повторяй то же самое. Поменяй подход (например: вместо моков перепиши тест через интеграционный stub, или временно пометь `@pytest.mark.skip(reason="...")` с TODO и переходи к следующему разделу).
- **Каждый раздел — отдельный коммит.** Не складывай всё в один.
- **Если sub‑агент возвращает что‑то странное** — не вставляй его результат вслепую. Прочитай файл (`Read`), убедись, что он есть, синтаксис корректен (`python -m py_compile`).

## Формат `.ralph/progress.json`

```json
{
  "iterations": 3,
  "current": "section_5_build_accounts_screen",
  "completed": ["section_1_onboarding", "section_2_delete_account", "section_3_count_transactions"],
  "last_pytest": {"passed": 41, "failed": 0, "errors": 0, "at": "2026-05-14T20:15:00Z"},
  "notes": "section 4 took 2 iterations because async mocks needed AsyncMock for query.answer"
}
```

Поехали. Открой спеку, прочитай прогресс, выбери ОДИН раздел и работай.
