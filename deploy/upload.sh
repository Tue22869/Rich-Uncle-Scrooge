#!/bin/bash
# Быстрое обновление бота на сервере

SERVER_IP="${1:-31.130.131.71}"
SERVER_USER="root"
DEPLOY_PATH="/var/www/smartfinances"

echo "🚀 Загружаю изменения на сервер..."

# Загрузить файлы
rsync -avz --progress \
    --exclude='venv' \
    --exclude='*.db' \
    --exclude='*.log' \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='tests' \
    --exclude='deploy/*.md' \
    ./ ${SERVER_USER}@${SERVER_IP}:${DEPLOY_PATH}/

echo ""
echo "✅ Файлы загружены!"
echo ""

# Автоматически перезапустить бот
echo "🔄 Перезапускаю бот..."
ssh ${SERVER_USER}@${SERVER_IP} "systemctl restart smartfinances"

echo ""
echo "✅ Бот перезапущен!"
echo ""
echo "📊 Проверить логи:"
echo "   ssh ${SERVER_USER}@${SERVER_IP} 'tail -f ${DEPLOY_PATH}/bot.log'"
echo ""
echo "💬 Проверь бота: /start"

