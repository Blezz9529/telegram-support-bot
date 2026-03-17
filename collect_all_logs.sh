#!/bin/bash
# Скрипт для сбора всех логов системы
# Используем отслеживание позиции в файлах для показа только новых строк

WIDGET_LOG_POS="/tmp/widget-log-pos"
BOT_LOG_POS="/tmp/bot-log-pos"

# Инициализируем позиции если нет
[ ! -f "$WIDGET_LOG_POS" ] && echo "0" > "$WIDGET_LOG_POS"
[ ! -f "$BOT_LOG_POS" ] && echo "0" > "$BOT_LOG_POS"

while true; do
    {
        # WIDGET API LOGS - только новые строки
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') WIDGET API LOGS ==="
        WIDGET_POS=$(cat "$WIDGET_LOG_POS")
        docker logs widget-api --since 10s 2>/dev/null | grep -v "GET /api/logs" | grep -v "HTTP/1.1\" 200 OK" | grep -v "INFO: 172.18.0." | grep -v "^$"
        echo ""
        
        # TELEGRAM BOT LOGS - только новые строки
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') TELEGRAM BOT LOGS ==="
        docker logs telegram-support-bot --since 10s 2>/dev/null | grep -v "^$"
        echo ""
        
        # SYSTEM EVENTS - только за последние 10 секунд
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') SYSTEM EVENTS ==="
        docker events --since 10s --format "table {{.Type}}\t{{.Action}}\t{{.Actor.Attributes.name}}" 2>/dev/null | tail -10
        echo ""
        
        # CONTAINER STATUS - всегда показываем
        echo "=== $(date '+%Y-%m-%d %H:%M:%S') CONTAINER STATUS ==="
        docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null
        echo ""
        
        echo "=== END OF LOG CYCLE ==="
        echo ""
    } >> /root/telegram-support-bot/logs/widget-api.log  # 🔑 Добавляем, а не перезаписываем!
    
    sleep 10  # Каждые 10 секунд
done
