# Спецификация: Интеграция ЮKassa

## SDK

Используем официальный Python SDK: `yookassa` (pip install yookassa)

```python
from yookassa import Configuration, Payment

Configuration.account_id = os.getenv("YOOKASSA_SHOP_ID")
Configuration.secret_key = os.getenv("YOOKASSA_SECRET_KEY")
```

## Создание платежа

```python
import uuid
from yookassa import Payment

payment = Payment.create({
    "amount": {
        "value": "190.00",
        "currency": "RUB"
    },
    "confirmation": {
        "type": "redirect",
        "return_url": "https://t.me/YOUR_BOT"
    },
    "capture": True,
    "description": "Подписка SmartFinances — 1 месяц",
    "metadata": {
        "user_id": str(user_id),
        "plan": "monthly"
    }
}, uuid.uuid4())

# payment.confirmation.confirmation_url — ссылка для оплаты
# payment.id — ID платежа для отслеживания
```

## Проверка статуса

Два подхода:
1. **Webhook** (рекомендуется для прода) — ЮKassa отправляет POST на наш URL. Требует публичный endpoint (aiohttp или Flask). Подходит если есть веб-сервер.
2. **Polling** (проще для Telegram-бота) — периодически проверяем статус через API.

Для нашего бота используем **polling** через JobQueue:
- После создания платежа — запускаем job который каждые 10 сек проверяет статус
- Максимум 30 минут (таймаут)
- При succeeded → activate_subscription

```python
payment_info = Payment.find_one(payment_id)
if payment_info.status == "succeeded":
    # Активируем подписку
elif payment_info.status == "canceled":
    # Уведомляем: «Платёж отменён»
```

## Тарифы

| План | Цена | Длительность |
|------|------|-------------|
| monthly | 190 ₽ | 30 дней |
| yearly | 1900 ₽ | 365 дней |

## Модель доступа

**ВЕСЬ функционал бота — только по подписке (Pro)**. Без подписки бот не работает.

### Trial (14 дней бесплатно)
- Каждый пользователь может активировать ОДИН раз
- Полный доступ ко всем функциям на 14 дней
- После истечения — paywall
- Хранится в User.trial_used = True (повторно нельзя)
- Subscription создаётся с plan="trial", amount=0, payment_id=null

### Тарифы

| План | Цена | Длительность |
|------|------|-------------|
| trial | 0 ₽ | 14 дней (один раз) |
| monthly | 190 ₽ | 30 дней |
| yearly | 1900 ₽ | 365 дней |

### Что включает Pro (весь функционал)

- Запись операций (текст, голос, кнопки)
- Управление счетами
- Отчёты за любой период
- LLM-аналитика — безлимит
- Еженедельные/ежемесячные дайджесты
- Бюджеты по категориям с уведомлениями
- Google Sheets экспорт/импорт
- Стрики, ачивки, напоминания

### Без подписки

Бот показывает ТОЛЬКО paywall:
- Описание возможностей
- Кнопка «🎁 Попробовать 14 дней бесплатно» (если trial не использован)
- Кнопки «Месяц — 190₽» и «Год — 1900₽»
- Никакой другой функционал НЕ доступен
