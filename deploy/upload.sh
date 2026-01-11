#!/bin/bash
# Обновление бота на сервере

SERVER_IP="31.130.131.71"
SERVER_USER="root"
DEPLOY_PATH="/var/www/smartfinances"

echo "🚀 Загружаю код на сервер..."

# Загрузить файлы (исключая .env и google_credentials.json)
rsync -avz --progress \
    --exclude='venv' \
    --exclude='*.db' \
    --exclude='*.log' \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='tests' \
    --exclude='.env' \
    --exclude='google_credentials.json' \
    --exclude='deploy/*.md' \
    ./ ${SERVER_USER}@${SERVER_IP}:${DEPLOY_PATH}/

echo ""
echo "🔄 Перезапускаю бот..."

ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
# Остановить все процессы
systemctl stop smartfinances 2>/dev/null || true
pkill -9 -f "python.*main.py" 2>/dev/null || true
sleep 2

# Исправить права
chown -R www-data:www-data /var/www/smartfinances
chmod 775 /var/www/smartfinances
chmod 664 /var/www/smartfinances/smartfinances.db 2>/dev/null || true

# Запустить
systemctl daemon-reload
systemctl start smartfinances
sleep 3

echo ""
echo "✅ Готово!"
echo "Ботов запущено: $(ps aux | grep 'python.*main.py' | grep -v grep | wc -l)"
systemctl status smartfinances --no-pager | head -6
ENDSSH

echo ""
echo "✅ Бот обновлён и запущен!"
