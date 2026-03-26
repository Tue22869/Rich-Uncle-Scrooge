# Спецификация: Кнопочное меню и UX

## Главное правило

**Из любого места бот должен позволять вернуться в главное меню**. Каждый ответ бота содержит либо reply-клавиатуру главного меню, либо inline-кнопку «◀️ Главное меню».

## Типы клавиатур

### ReplyKeyboardMarkup (persistent, внизу экрана)
Используется для главного меню. Всегда видна пользователю.

```python
main_menu = ReplyKeyboardMarkup(
    [
        ["💰 Счета", "📊 Отчёты"],
        ["📝 Записать", "⚙️ Настройки"],
        ["❓ Помощь"]
    ],
    resize_keyboard=True,
    one_time_keyboard=False
)
```

### InlineKeyboardMarkup (под сообщением)
Используется для подменю, действий, подтверждений.

## Обработка нажатий reply-кнопок

В handlers.py добавить MessageHandler с фильтром:

```python
# Обработка кнопок главного меню
menu_filter = filters.Regex(r'^(💰 Счета|📊 Отчёты|📝 Записать|⚙️ Настройки|❓ Помощь)$')
application.add_handler(MessageHandler(menu_filter, menu_button_handler), group=0)
```

**Важно**: этот хендлер должен иметь приоритет (group=0) над message_handler (текстовый ввод), чтобы нажатия кнопок не уходили в LLM-парсер.

## Онбординг

Использовать `ConversationHandler` из python-telegram-bot:

```python
onboarding_handler = ConversationHandler(
    entry_points=[CommandHandler("start", start_command)],
    states={
        WELCOME: [CallbackQueryHandler(onboard_start, pattern="^onboard_start$")],
        CURRENCY: [CallbackQueryHandler(onboard_currency, pattern="^currency_")],
        NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_name),
               CallbackQueryHandler(onboard_preset_name, pattern="^name_")],
        BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_balance),
                  CallbackQueryHandler(onboard_skip_balance, pattern="^skip_balance$")],
    },
    fallbacks=[CommandHandler("cancel", cancel_onboarding)],
)
```

## Пошаговое создание счёта (из меню)

Аналогичный ConversationHandler:
1. Выбор валюты (кнопки: RUB, USD, EUR, USDT, Другая)
2. Ввод названия (текстом или кнопки: Карта, Наличка, Крипто, Другое)
3. Начальный баланс (текстом или кнопка «Пропустить — 0»)
4. Подтверждение

## Пошаговая запись операции (из меню)

1. Тип: 💸 Расход / 💰 Доход / 🔄 Перевод
2. Сумма: ввод текстом
3. Категория: кнопки из списка категорий (для расхода — 19 категорий, показать первые 8 + «Ещё»)
4. Счёт: кнопки из списка счетов пользователя
5. Описание: текстом или «Пропустить»
6. Подтверждение ✅/❌

## Формат callback_data

Существующий формат: `fin:action:param1:param2`

Новые:
- `menu:accounts` — открыть подменю счетов
- `menu:reports` — открыть подменю отчётов
- `menu:settings` — открыть настройки
- `menu:help` — открыть помощь
- `menu:main` — вернуться в главное меню
- `menu:create_account` — начать создание счёта
- `menu:set_default:ACCOUNT_ID` — назначить главный счёт
- `menu:report:week` / `month` / `prev_month` / `year` — отчёт за период
- `menu:settings:timezone` — таймзона
- `menu:settings:sheets` — Google Sheets
- `menu:settings:subscription` — подписка
- `menu:settings:budgets` — бюджеты
- `sub:buy:monthly` / `sub:buy:yearly` — покупка подписки
