import asyncio
import logging
import os
import json
import base64
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sqlite3
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импорты из проекта
from handlers.widget import process_widget_message_to_forum
from storages.db import get_user

logger = logging.getLogger(__name__)

# === FastAPI приложение ===
app = FastAPI(title="Support Widget API")

# === Конфигурация ===
STATIC_DIR = os.path.join(os.path.dirname(__file__), "widget", "dist")

if os.path.exists(STATIC_DIR):
    app.mount("/widget", StaticFiles(directory=STATIC_DIR, html=True), name="widget")
    logger.info(f"✅ Static files mounted from {STATIC_DIR}")
else:
    logger.warning(f"⚠️ Static directory not found, tried: /app/widget/dist, /app/dist, widget/dist")

# === Модели данных ===
class SessionInitRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None

class SessionInitResponse(BaseModel):
    session_id: str
    messages: List[Dict[str, Any]]

class ChatMessage(BaseModel):
    text: str
    attachment: Optional[str] = None

class Operator(BaseModel):
    name: str = "Алекс"
    status: str = "online"
    typing: bool = False

# === WebSocket подключения ===
active_connections: Dict[str, WebSocket] = {}

# === Инициализация БД ===
async def init_widget_db():
    """Инициализация таблиц для виджета"""
    db_path = os.path.join(os.path.dirname(__file__), "data", "support.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Таблица сессий виджета
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS widget_sessions (
            session_id TEXT PRIMARY KEY,
            user_id INTEGER,
            username TEXT,
            full_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            topic_id INTEGER,
            forum_message_id INTEGER
        )
    """)
    
    # Таблица сообщений виджета
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS widget_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            text TEXT,
            sender TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            attachment_url TEXT,
            FOREIGN KEY (session_id) REFERENCES widget_sessions (session_id)
        )
    """)
    
    conn.commit()
    conn.close()
    logger.info("✅ Widget DB initialized")

# === API Endpoints ===

@app.get("/api/logs")
async def get_logs(lines: int = 50):
    """Получить последние логи (для виджета мониторинга)"""
    try:
        log_file = "/app/logs/widget-api.log"
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
                return [line.strip() for line in all_lines[-lines:] if line.strip()]
        else:
            return ["Лог файл не найден"]
    except Exception as e:
        return [f"Ошибка чтения логов: {str(e)}"]

@app.post("/api/widget/session/init")
async def init_session(request: SessionInitRequest) -> SessionInitResponse:
    """Инициализация новой сессии виджета"""
    import uuid
    session_id = str(uuid.uuid4())
    
    # Приветственное сообщение
    welcome_message = {
        "id": 0,
        "text": "Привет! 👋 Чем могу помочь? Задайте ваш вопрос, и я отвечу как можно скорее.",
        "sender": "operator",
        "timestamp": datetime.now().isoformat()
    }
    
    return SessionInitResponse(
        session_id=session_id,
        messages=[welcome_message]
    )

@app.get("/api/widget/status/{session_id}")
async def get_operator_status(session_id: str) -> Operator:
    """Получить статус оператора"""
    return Operator()

@app.get("/api/widget/messages/{session_id}")
async def get_messages(session_id: str) -> List[Dict[str, Any]]:
    """Получить сообщения сессии"""
    # Возвращаем приветственное сообщение
    return [{
        "id": 0,
        "text": "Привет! 👋 Чем могу помочь? Задайте ваш вопрос, и я отвечу как можно скорее.",
        "sender": "operator",
        "timestamp": datetime.now().isoformat()
    }]

@app.post("/api/widget/send")
async def send_message(message: ChatMessage, background_tasks: BackgroundTasks):
    """Отправить сообщение из виджета"""
    # Здесь будет логика отправки в Telegram
    return {"status": "sent"}

@app.websocket("/api/widget/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket для реального времени"""
    await websocket.accept()
    active_connections[session_id] = websocket
    logger.info(f"🔌 WebSocket подключён: {session_id[:8]}...")
    
    try:
        while True:
            data = await websocket.receive_text()
            # Обработка входящих сообщений
            pass
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket отключён: {session_id[:8]}...")
    finally:
        active_connections.pop(session_id, None)

async def send_to_widget(session_id: str, message: Dict[str, Any]):
    """Отправить сообщение в виджет"""
    if session_id in active_connections:
        try:
            await active_connections[session_id].send_json(message)
            logger.info(f"📤 Отправлено в виджет: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в виджет: {e}")
            active_connections.pop(session_id, None)

@app.on_event("startup")
async def startup_event():
    await init_widget_db()
    logger.info("✅ Widget API запущен")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
