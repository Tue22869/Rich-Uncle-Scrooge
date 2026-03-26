# Архитектура — SmartFinances (Rich-Uncle-Scrooge)

## Целевая архитектура после всех фаз

```
main.py                         — точка входа, регистрация хендлеров + запуск scheduler
bot/
  handlers.py                   — входные обработчики (text, voice, callbacks) [НЕ МЕНЯТЬ существующий код]
  menu.py                       — NEW: построение клавиатур, навигация, обработка нажатий кнопок
  menu_handlers.py              — NEW: ConversationHandler для онбординга и пошаговых диалогов
  intents.py                    — роутинг по интентам [расширить, не удалять]
  callbacks.py                  — обработка inline-кнопок [расширить]
  helpers.py                    — утилиты [расширить]
  sheets.py                     — Google Sheets команды [не трогать]
db/
  models.py                     — РАСШИРИТЬ: + Subscription, Budget, UserStats
  session.py                    — engine, SessionLocal, init_db [не трогать]
llm/
  parser.py                     — parse_message() [не трогать]
  prompts.py                    — системные промпты [расширить: + set_budget intent]
schemas/
  llm_schema.py                 — Pydantic-модели [расширить если нужно]
services/
  ledger.py                     — финансовые операции [не трогать]
  reports.py                    — отчёты [не трогать]
  insights.py                   — аналитика [добавить проверку лимитов]
  billing.py                    — NEW: ЮKassa интеграция, управление подписками
  scheduler.py                  — NEW: планировщик задач (дайджесты, напоминания, проверки)
  retention.py                  — NEW: генерация дайджестов, стрики, ачивки, напоминания
  budgets.py                    — NEW: CRUD бюджетов, проверка превышения
  google_sheets_client.py       — [не трогать]
  sheets_export.py / import.py  — [не трогать]
  sheets_format.py / sync.py    — [не трогать]
  speech.py                     — [не трогать]
utils/
  dates.py                      — [не трогать]
  money.py                      — [не трогать]
tests/
  test_ledger.py                — [не трогать]
  test_parser.py                — [не трогать]
  test_utils.py                 — [не трогать]
  test_menu.py                  — NEW
  test_billing.py               — NEW
  test_retention.py             — NEW
  test_budgets.py               — NEW
  test_scheduler.py             — NEW
```

## Новые модели данных

### Subscription
```python
class SubscriptionPlan(PyEnum):
    TRIAL = "trial"        # 14 дней бесплатно (один раз на пользователя)
    MONTHLY = "monthly"    # 190 ₽
    YEARLY = "yearly"      # 1900 ₽

class SubscriptionStatus(PyEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    plan = Column(SQLEnum(SubscriptionPlan), nullable=False)
    payment_id = Column(String, nullable=True)           # ЮKassa payment ID (null для trial)
    amount = Column(DECIMAL(10, 2), nullable=False)      # 0 для trial
    paid_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    status = Column(SQLEnum(SubscriptionStatus), default=SubscriptionStatus.ACTIVE)
    created_at = Column(DateTime, default=datetime.utcnow)
```

### Budget
```python
class Budget(Base):
    __tablename__ = "budgets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    category = Column(String, nullable=False)             # Точное название категории из промпта
    monthly_limit = Column(DECIMAL(15, 2), nullable=False)
    currency = Column(String, default="RUB", nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

### UserStats
```python
class UserStats(Base):
    __tablename__ = "user_stats"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    streak_days = Column(Integer, default=0)
    last_activity_date = Column(Date, nullable=True)
    total_operations = Column(Integer, default=0)
    achievements_json = Column(JSON, default=list)        # ["first_operation", "streak_7", ...]
    reminders_enabled = Column(Boolean, default=True)
    last_reminder_at = Column(DateTime, nullable=True)
```

### Расширение User
```python
# Добавить в существующую модель User:
trial_used = Column(Boolean, default=False)              # True после активации trial (повторно нельзя)
trial_activated_at = Column(DateTime, nullable=True)     # Когда был активирован trial
```

## Flow: кнопочная навигация

```
/start или любое сообщение
    │
    ├── Новый пользователь (нет счетов)
    │   └── Онбординг: Приветствие → Валюта → Название → Баланс
    │       └── Предложить trial: «🎁 Активировать 14 дней Pro бесплатно»
    │           └── Главное меню
    │
    └── Существующий пользователь
        │
        ├── check_subscription(user_id) == False
        │   └── PAYWALL: «Для использования нужна подписка»
        │       ├── 🎁 Попробовать 14 дней бесплатно (если trial_used == False)
        │       ├── Месяц — 190₽
        │       └── Год — 1900₽
        │
        └── check_subscription(user_id) == True → Главное меню (ReplyKeyboard)
            │
            ├── 💰 Счета ──→ Список + [➕ Создать] [⭐ Главный] [◀️ Меню]
            │                    └── ➕ Создать → Валюта → Название → Баланс → Готово
            │
            ├── 📊 Отчёты ──→ [Неделя] [Месяц] [Пр.месяц] [Год] [◀️ Меню]
            │                    └── Отчёт + [🤖 Анализ GPT] [◀️ Меню]
            │
            ├── 📝 Записать ──→ [💸 Расход] [💰 Доход] [🔄 Перевод] [◀️ Меню]
            │                    └── Пошаговый диалог → Подтверждение ✅/❌
            │
            ├── ⚙️ Настройки ──→ [🕐 TZ] [📋 Sheets] [💎 Подписка] [📊 Бюджеты] [◀️ Меню]
            │                       └── 💎 → [Месяц 190₽] [Год 1900₽] [◀️ Меню]
            │
            └── ❓ Помощь ──→ [📖 Инструкция] [ℹ️ О боте] [◀️ Меню]

Параллельно: текстовый ввод «кофе 320» и голосовые сообщения → LLM-парсер → как раньше
```

## Flow: платёж ЮKassa

```
Пользователь нажимает «Месяц — 190₽»
    │
    ├── billing.create_payment(user_id, "monthly")
    │   └── yookassa.Payment.create(amount=190, currency="RUB", ...)
    │       └── Возвращает confirmation_url
    │
    ├── Бот отправляет: «Оплатите подписку» + ссылка на оплату
    │
    ├── Пользователь оплачивает на странице ЮKassa
    │
    ├── ЮKassa отправляет webhook / бот проверяет polling
    │   └── billing.check_payment_status(payment_id)
    │       └── Если succeeded → billing.activate_subscription(user_id, plan, payment_id)
    │
    └── Бот отправляет: «✅ Pro подписка активирована до ДД.ММ.ГГГГ»
```

## Flow: еженедельный дайджест

```
scheduler (каждое воскресенье 20:00)
    │
    └── Для каждого активного пользователя:
        │
        ├── retention.generate_weekly_digest(user_id)
        │   ├── Собрать расходы за эту неделю
        │   ├── Собрать расходы за прошлую неделю
        │   ├── Сравнить по категориям
        │   ├── Найти где сэкономил / перерасходовал
        │   └── Сгенерировать текст через LLM (позитивный, мотивирующий)
        │
        └── Отправить: дайджест + [📊 Подробный отчёт] [◀️ Меню]
```

## Ачивки (achievements)

| ID | Название | Условие |
|----|----------|---------|
| first_op | Первая операция | Записал 1-ю операцию |
| streak_7 | Неделя подряд 🔥 | 7 дней подряд записывал |
| streak_30 | Месяц без пропусков 🏆 | 30 дней подряд |
| ops_100 | Сотня операций | 100 операций всего |
| ops_500 | Полтысячи | 500 операций |
| saved_1k | Экономист | Сэкономил 1000₽ за месяц (vs предыдущий) |
| saved_10k | Финансовый гуру | Сэкономил 10000₽ за месяц |
| budget_master | Мастер бюджета | Не превысил ни один бюджет за месяц |
| multi_account | Мультисчёт | Создал 3+ счетов |
| voice_user | Голосовой помощник | Записал 10+ операций голосом |

## Переменные окружения (env.example дополнения)

```env
# --- YooKassa (payment processing) ---
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
# Return URL after payment (optional, Telegram bot doesn't really need it)
YOOKASSA_RETURN_URL=https://t.me/YOUR_BOT_USERNAME

# --- Subscription ---
SUBSCRIPTION_MONTHLY_PRICE=190
SUBSCRIPTION_YEARLY_PRICE=1900
FREE_INSIGHTS_LIMIT=3
```

## Зависимости (requirements.txt дополнения)

```
yookassa>=3.0.0
APScheduler>=3.10.0    # если JobQueue недостаточно
```
