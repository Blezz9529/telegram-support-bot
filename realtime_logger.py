#!/usr/bin/env python3
"""
Real-time логгер для Telegram Support Bot
Собирает логи из всех источников в реальном времени
"""
import asyncio
import subprocess
import sqlite3
import time
from datetime import datetime

class RealtimeLogger:
    def __init__(self):
        self.log_file = "/root/telegram-support-bot/logs/realtime.log"
        self.last_message_count = 0
        
    async def start(self):
        """Запуск real-time логирования"""
        print("🚀 Starting real-time logger...")
        
        while True:
            try:
                # Собираем логи из всех источников
                logs = []
                current_time = datetime.now().strftime('%H:%M:%S')
                
                # 1. Логи из widget-api (stdout)
                widget_logs = await self.get_container_logs("widget-api", lines=20)
                for log in widget_logs:
                    if any(keyword in log for keyword in [
                        "🆕", "🤖", "❌", "✅", "⚠️", "📤", "🔌", 
                        "process_widget_message", "AI ответ", "topic_id",
                        "OpenRouter", "process_ticket", "send_to_widget"
                    ]):
                        logs.append(f"WIDGET: {log}")
                
                # 2. Логи из telegram-support-bot (stdout)
                bot_logs = await self.get_container_logs("telegram-support-bot", lines=20)
                for log in bot_logs:
                    if any(keyword in log for keyword in [
                        "forum", "topic", "Топик", "process_widget_message_to_forum",
                        "send_to_topic", "✅", "❌", "⚠️", "get_or_create_topic"
                    ]):
                        logs.append(f"BOT: {log}")
                
                # 3. Новые сообщения из БД
                new_messages = await self.get_new_messages()
                for msg in new_messages:
                    logs.append(f"MESSAGE: {msg}")
                
                # 4. Записываем в файл
                if logs:
                    with open(self.log_file, 'a', encoding='utf-8') as f:
                        for log in logs:
                            f.write(f"{current_time} {log}\n")
                
                await asyncio.sleep(2)  # Проверяем каждые 2 секунды
                
            except Exception as e:
                print(f"❌ Logger error: {e}")
                await asyncio.sleep(5)
    
    async def get_container_logs(self, container_name, lines=10):
        """Получить логи контейнера"""
        try:
            result = subprocess.run(
                ['docker', 'logs', container_name, '--tail', str(lines)],
                capture_output=True, text=True, timeout=5
            )
            return result.stdout.strip().split('\n') if result.stdout else []
        except:
            return []
    
    async def get_new_messages(self):
        """Получить новые сообщения из БД"""
        try:
            conn = sqlite3.connect('/root/telegram-support-bot/data/support.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT session_id, sender, text, timestamp 
                FROM widget_messages 
                WHERE timestamp > datetime('now', '-10 seconds')
                ORDER BY timestamp DESC
            ''')
            
            messages = []
            for row in cursor.fetchall():
                messages.append(f"{row[0][:8]}... | {row[1]} | {row[2][:40]}...")
            
            conn.close()
            return messages
        except:
            return []

if __name__ == "__main__":
    logger = RealtimeLogger()
    asyncio.run(logger.start())
