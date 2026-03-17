#!/bin/bash
# deploy.sh - Скрипт для развёртывания на сервере

set -e

echo "🚀 Развёртывание Telegram Support Bot + Widget..."

# 1. Копируем файлы
echo "📦 Копирование файлов..."
rsync -avz --exclude 'node_modules' --exclude '.git' --exclude 'widget/dist' \
  . root@94.103.88.196:/root/telegram-support-bot/

# 2. SSH на сервер и развёртывание
echo "🔧 Развёртывание на сервере..."
ssh root@94.103.88.196 << 'ENDSSH'
cd /root/telegram-support-bot

echo "📦 Установка Python зависимостей..."
pip install -r requirements.txt

echo "📦 Установка Node зависимостей виджета..."
cd widget
npm install
npm run build
cd ..

echo "🐳 Пересборка контейнеров..."
docker-compose down
docker-compose up -d --build

echo "⏳ Ожидание запуска..."
sleep 5

echo "📊 Статус контейнеров:"
docker-compose ps

echo ""
echo "📋 Логи (последние 20 строк):"
docker-compose logs --tail=20

echo ""
echo "✅ Развёртывание завершено!"
echo ""
echo "🌐 Виджет: http://94.103.88.196/widget"
echo "📡 API Docs: http://94.103.88.196:8000/docs"
echo ""
echo "📊 Для просмотра логов в реальном времени:"
echo "   ssh root@94.103.88.196 'cd /root/telegram-support-bot && docker-compose logs -f'"

ENDSSH

echo ""
echo "✅ Готово!"
