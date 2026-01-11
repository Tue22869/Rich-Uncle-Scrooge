#!/bin/bash
# Полное исправление всех проблем на сервере

set -e

echo "🛠️  ПОЛНОЕ ИСПРАВЛЕНИЕ SMARTFINANCES"
echo "======================================"
echo ""

# 1. ОСТАНОВИТЬ ВСЁ
echo "1️⃣ Останавливаю все процессы..."
systemctl stop smartfinances 2>/dev/null || true
pkill -9 -f "python.*main.py" 2>/dev/null || true
sleep 3

# Проверить что все остановлены
RUNNING=$(ps aux | grep "python.*main.py" | grep -v grep | wc -l)
if [ "$RUNNING" -gt 0 ]; then
    echo "❌ Ошибка: процессы всё ещё запущены!"
    ps aux | grep "python.*main.py" | grep -v grep
    exit 1
fi
echo "✅ Все процессы остановлены"
echo ""

# 2. ИСПРАВИТЬ ПРАВА
echo "2️⃣ Исправляю права на файлы..."
chown -R www-data:www-data /var/www/smartfinances
chmod 775 /var/www/smartfinances
chmod 664 /var/www/smartfinances/smartfinances.db
chmod -R 755 /var/www/smartfinances/venv/bin/
echo "✅ Права исправлены"
echo ""

# 3. ПРОВЕРИТЬ БАЗУ
echo "3️⃣ Проверяю базу данных..."
DB_SIZE=$(stat -c%s /var/www/smartfinances/smartfinances.db)
echo "   Размер БД: $DB_SIZE байт"

if [ "$DB_SIZE" -lt 10000 ]; then
    echo "⚠️  База слишком маленькая, возможно пустая"
fi
echo ""

# 4. ПРОВЕРИТЬ КОНФИГ
echo "4️⃣ Проверяю конфигурацию..."
if [ ! -f /var/www/smartfinances/.env ]; then
    echo "❌ Отсутствует файл .env!"
    exit 1
fi

if [ ! -f /var/www/smartfinances/google_credentials.json ]; then
    echo "⚠️  Отсутствует google_credentials.json"
fi

echo "✅ Конфигурация OK"
echo ""

# 5. ПРОВЕРИТЬ SYSTEMD
echo "5️⃣ Проверяю systemd конфиг..."
if [ ! -f /etc/systemd/system/smartfinances.service ]; then
    echo "⚠️  Копирую service файл..."
    cp /var/www/smartfinances/deploy/smartfinances.service /etc/systemd/system/
    systemctl daemon-reload
fi
echo "✅ Systemd OK"
echo ""

# 6. ЗАПУСТИТЬ БОТА
echo "6️⃣ Запускаю бота..."
systemctl enable smartfinances
systemctl start smartfinances
sleep 5

# 7. ПРОВЕРИТЬ СТАТУС
echo "7️⃣ Проверяю статус..."
echo ""
systemctl status smartfinances --no-pager -l | head -20
echo ""

# 8. ПРОВЕРИТЬ ПРОЦЕССЫ
RUNNING=$(ps aux | grep "python.*main.py" | grep -v grep | wc -l)
echo "Запущено процессов Python: $RUNNING"
if [ "$RUNNING" -eq 1 ]; then
    echo "✅ Запущен ровно 1 процесс (OK)"
elif [ "$RUNNING" -gt 1 ]; then
    echo "❌ Запущено $RUNNING процессов (должен быть 1)!"
    echo "Процессы:"
    ps aux | grep "python.*main.py" | grep -v grep
else
    echo "❌ Бот не запущен!"
fi
echo ""

# 9. ПОКАЗАТЬ ЛОГИ
echo "8️⃣ Последние логи:"
echo ""
tail -30 /var/www/smartfinances/bot.log
echo ""
echo "======================================"
echo "✅ ИСПРАВЛЕНИЕ ЗАВЕРШЕНО"
echo ""
echo "📊 Проверь бота: отправь /start"
echo "📋 Логи: tail -f /var/www/smartfinances/bot.log"

