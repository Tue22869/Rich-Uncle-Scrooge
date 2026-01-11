#!/bin/bash
# Скрипт для исправления прав на сервере (запускать в веб-консоли)

echo "🛠️  Исправление прав и перезапуск бота..."

# Остановить бот
systemctl stop smartfinances
pkill -9 -f "python.*main.py"
sleep 2

# Исправить права
chown -R www-data:www-data /var/www/smartfinances
chmod 664 /var/www/smartfinances/smartfinances.db
chmod 775 /var/www/smartfinances
chmod 755 /var/www/smartfinances/venv/bin/python3

# Убедиться что директория БД доступна для записи
chmod 775 /var/www/smartfinances

# Запустить бот
systemctl start smartfinances
sleep 3

# Проверить
echo ""
echo "📊 Статус:"
systemctl status smartfinances --no-pager -l

echo ""
echo "📋 Последние логи:"
tail -20 /var/www/smartfinances/bot.log

echo ""
echo "✅ Готово!"

