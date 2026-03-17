# services/widget_api.py
"""
FastAPI сервер для виджета поддержки
"""
import asyncio
import logging
import os
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import aiogram
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Импорты из проекта
from main import bot
from handlers.widget import process_widget_message_to_forum
from services.widget_session import (
    init_widget_db,
    save_widget_message,
    get_session_messages,
    get_session,
    create_session,
    link_session_to_user,
    set_session_site_user_id,
    is_site_user_blocked,
    set_session_state,
    set_session_theme,
    set_session_feedback_type,
    close_session,
    purge_expired_session_content,
)
from storages.db import get_persistent_topic, set_persistent_topic
from services.ai_pipeline import handle_incoming_widget_message
from storages.db import create_user
from services.localization import load_text, load_button
from services.site_user_map import get_telegram_id_by_site_id
from services.theme_map import THEME_MAP
from config import SUPPORT_GROUP_ID, ADMINS, WIDGET_SESSION_TTL_HOURS

# Настроить логгирование в файл
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/app/logs/widget-api.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# === FastAPI приложение ===
app = FastAPI(title="Support Widget API")

# CORS для всех источников (на продакшене ограничить)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

WIDGET_MAX_IMAGE_SIZE = 5 * 1024 * 1024
WIDGET_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def _theme_key_from_label(label: str) -> Optional[str]:
    return THEME_MAP.get(label)


def _get_menu_buttons() -> List[str]:
    return list(THEME_MAP.keys())


def _ttl_hours() -> int:
    return WIDGET_SESSION_TTL_HOURS


def _utc_iso(ts: Optional[object] = None) -> str:
    if ts is None:
        return datetime.utcnow().isoformat() + "Z"
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    value = str(ts)
    # Normalize "YYYY-MM-DD HH:MM:SS" to ISO Z
    if "T" not in value and " " in value:
        value = value.replace(" ", "T", 1)
    if value.endswith("Z"):
        return value
    return value + "Z"


def _attachment_placeholder(filename: str) -> str:
    safe_name = filename or "image"
    return f"🖼 Изображение: {safe_name}"


def _normalize_attachment_name(request: "SendMessageRequest") -> str:
    if not request.attachment_type:
        return "image"
    ext = request.attachment_type.split("/")[-1].lower()
    if ext == "jpeg":
        ext = "jpg"
    return f"attachment.{ext}"


async def _reset_if_session_expired(session: Dict[str, Any], session_id: str) -> bool:
    last_activity = session.get("last_activity")
    state = session.get("state") or "choosing_theme"
    if state != "in_conversation" or not last_activity:
        return False
    try:
        last_dt = datetime.fromisoformat(str(last_activity))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=last_dt.tzinfo)
        if now - last_dt > timedelta(hours=_ttl_hours()):
            await purge_expired_session_content(session_id)
            return True
    except Exception:
        return False
    return False


async def resolve_telegram_user_id(site_user_id: Optional[str]) -> Optional[int]:
    if not site_user_id:
        return None
    try:
        return await get_telegram_id_by_site_id(site_user_id)
    except Exception as e:
        logger.warning(f"⚠️ resolve site_user_id error: {e}")
        return None

# === Монтируем статику (виджет) ===
# Проверяем несколько возможных путей
STATIC_DIR = "/app/widget/dist"
if not os.path.exists(STATIC_DIR):
    STATIC_DIR = "/app/dist"
if not os.path.exists(STATIC_DIR):
    STATIC_DIR = "widget/dist"
    
if os.path.exists(STATIC_DIR):
    app.mount("/widget", StaticFiles(directory=STATIC_DIR, html=True), name="widget")
    logger.info(f"✅ Static files mounted from {STATIC_DIR}")
else:
    logger.warning(f"⚠️ Static directory not found, tried: /app/widget/dist, /app/dist, widget/dist")

# === Модели данных ===

class SendMessageRequest(BaseModel):
    session_id: str
    text: str
    attachment: Optional[str] = None  # base64
    attachment_type: Optional[str] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    site_user_id: Optional[str] = None


class MessageResponse(BaseModel):
    id: int
    text: str
    sender: str
    timestamp: str
    attachment_url: Optional[str] = None
    attachment_type: Optional[str] = None
    attachment_name: Optional[str] = None


class SessionInitRequest(BaseModel):
    user_id: Optional[int] = None
    username: Optional[str] = None
    full_name: Optional[str] = None
    site_user_id: Optional[str] = None


class SessionInitResponse(BaseModel):
    session_id: str
    messages: List[MessageResponse]


class WidgetPushRequest(BaseModel):
    session_id: str
    message: Dict[str, Any]


class SessionCloseRequest(BaseModel):
    session_id: str


class MenuSelectRequest(BaseModel):
    session_id: str
    menu_label: str
    site_user_id: Optional[str] = None


class FeedbackSelectRequest(BaseModel):
    session_id: str
    label: str


# === Инициализация ===

@app.on_event("startup")
async def startup_event():
    await init_widget_db()
    from services.conversation_store import init_conversation_store
    await init_conversation_store()
    logger.info("✅ Widget API запущен")


# === REST API Endpoints ===

@app.get("/api/logs")
async def get_logs(lines: int = 50):
    """Получить real-time логи из всех источников"""
    try:
        # Читаем real-time логи
        log_file = "/app/logs/realtime.log"
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            
            # Возвращаем последние строки
            recent_lines = [line.strip() for line in all_lines[-lines:] if line.strip()]
            
            # Если логов мало, добавляем статус
            if len(recent_lines) < 5:
                recent_lines.append("📊 Активные сессии:")
                
                # Добавляем последние сообщения
                import sqlite3
                conn = sqlite3.connect("/app/data/support.db")
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT session_id, sender, substr(text, 1, 40), timestamp 
                    FROM widget_messages 
                    ORDER BY timestamp DESC 
                    LIMIT 3
                ''')
                for row in cursor.fetchall():
                    recent_lines.append(f"  {row[0][:8]}... | {row[1]} | {row[2]}...")
                conn.close()
            
            return recent_lines
        else:
            return ["Real-time логи не запущены"]
    except Exception as e:
        return [f"Ошибка чтения логов: {str(e)}"]


@app.get("/api/widget/ui-config")
async def get_ui_config():
    return {
        "menu_buttons": _get_menu_buttons(),
        "texts": {
            "select_theme": await load_text("select_theme"),
            "describe_problem": await load_text("describe_problem"),
            "feedback_type_question": await load_text("feedback_type_question"),
            "feedback_positive": await load_text("feedback_positive"),
            "feedback_negative": await load_text("feedback_negative"),
            "feedback_type_invalid": await load_text("feedback_type_invalid"),
            "invalid_theme": await load_text("invalid_theme"),
            "blocked_user_response": await load_text("blocked_user_response"),
            "active_dialog_warning": await load_text("active_dialog_warning"),
            "old_topic_warning": await load_text("old_topic_warning"),
            "feedback_details_request": await load_text("feedback_details_request")
        },
        "close_button_label": await load_button("menu", "close_ticket"),
        "new_dialog_label": await load_button("menu", "new_dialog"),
        "ttl_hours": _ttl_hours()
    }


@app.get("/api/widget/session/state/{session_id}")
async def get_session_state(session_id: str):
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    state = session.get("state") or "choosing_theme"
    expired = await _reset_if_session_expired(session, session_id)
    if expired:
        state = "choosing_theme"
        session = await get_session(session_id) or session
    return {
        "state": state,
        "theme": session.get("theme"),
        "feedback_type": session.get("feedback_type"),
        "is_blocked": bool(session.get("is_blocked")),
        "last_activity": session.get("last_activity"),
        "ttl_hours": _ttl_hours()
    }


@app.post("/api/widget/menu/select")
async def menu_select(request: MenuSelectRequest):
    session = await get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Session blocked")

    if request.site_user_id and not session.get("site_user_id"):
        await set_session_site_user_id(request.session_id, request.site_user_id)

    theme_key = _theme_key_from_label(request.menu_label)
    if not theme_key:
        return {"ok": False, "message": await load_text("invalid_theme")}

    await set_session_theme(request.session_id, theme_key)
    if theme_key == "feedback":
        await set_session_state(request.session_id, "choosing_feedback_type")
        return {"ok": True, "message": await load_text("feedback_type_question")}

    await set_session_state(request.session_id, "in_conversation")
    return {"ok": True, "message": await load_text("describe_problem")}


@app.post("/api/widget/feedback/select")
async def feedback_select(request: FeedbackSelectRequest):
    session = await get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Session blocked")

    positive = await load_text("feedback_positive")
    negative = await load_text("feedback_negative")

    if request.label == positive:
        await set_session_feedback_type(request.session_id, "positive")
    elif request.label == negative:
        await set_session_feedback_type(request.session_id, "negative")
    else:
        return {"ok": False, "message": await load_text("feedback_type_invalid")}

    await set_session_state(request.session_id, "in_conversation")
    return {"ok": True, "message": await load_text("feedback_details_request")}


@app.post("/api/widget/session/close")
async def close_widget_session(request: SessionCloseRequest):
    session = await get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await close_session(request.session_id)
    return {"ok": True}


@app.post("/api/widget/session/init")
async def init_session(request: SessionInitRequest) -> SessionInitResponse:
    """Инициализация новой сессии виджета"""
    if request.site_user_id and await is_site_user_blocked(request.site_user_id):
        raise HTTPException(status_code=403, detail="Session blocked")

    session_id = await create_session(
        user_id=request.user_id,
        site_user_id=request.site_user_id,
        username=request.username or "",
        full_name=request.full_name or ""
    )
    await set_session_state(session_id, "choosing_theme")
    
    # Возвращаем приветственное сообщение
    welcome_msg = MessageResponse(
        id=0,
        text="Привет! 👋 Чем могу помочь? Задайте ваш вопрос, и я отвечу как можно скорее.",
        sender="operator",
        timestamp=_utc_iso()
    )
    
    return SessionInitResponse(
        session_id=session_id,
        messages=[welcome_msg]
    )


@app.get("/api/widget/messages/{session_id}")
async def get_messages(session_id: str, limit: int = 50) -> List[MessageResponse]:
    """Получить историю сообщений сессии"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Session blocked")

    expired = await _reset_if_session_expired(session, session_id)
    if expired:
        return []
    
    messages = await get_session_messages(session_id, limit)
    
    return [
        MessageResponse(
            id=msg["id"],
            text=msg["text"],
            sender=msg["sender"],
            timestamp=_utc_iso(msg["timestamp"]),
            attachment_url=msg.get("attachment_url"),
            attachment_type=msg.get("attachment_type"),
            attachment_name=msg.get("attachment_name")
        )
        for msg in messages
    ]


@app.post("/api/widget/message")
async def send_message(request: SendMessageRequest, background_tasks: BackgroundTasks) -> MessageResponse:
    """Отправить сообщение в поддержку"""
    # Проверяем сессию
    session = await get_session(request.session_id)
    if not session:
        if request.site_user_id and await is_site_user_blocked(request.site_user_id):
            raise HTTPException(status_code=403, detail="Session blocked")

        # Создаём новую если не найдена
        session_id = await create_session(
            user_id=None,
            site_user_id=request.site_user_id,
            username=request.username or "",
            full_name=request.full_name or ""
        )
        session = {"session_id": session_id, "is_blocked": False}
    else:
        session_id = session["session_id"]

    if session and await _reset_if_session_expired(session, session_id):
        session = await get_session(session_id) or {"session_id": session_id, "is_blocked": False}

    if request.site_user_id and not session.get("site_user_id"):
        await set_session_site_user_id(session_id, request.site_user_id)

    if request.site_user_id and not session.get("user_id"):
        resolved_user_id = await resolve_telegram_user_id(request.site_user_id)
        if resolved_user_id:
            await create_user(resolved_user_id, request.username or "", request.full_name or "")
            await link_session_to_user(session_id, resolved_user_id)
            session["user_id"] = resolved_user_id
            logger.info(f"🔗 site_user_id={request.site_user_id} -> telegram_user_id={resolved_user_id}")
    
    # Если пользователь пишет текст без выбора темы — автотема "other"
    session_state = session.get("state") or "choosing_theme"
    if session_state != "in_conversation":
        await set_session_theme(session_id, "other")
        await set_session_state(session_id, "in_conversation")

    if session.get("is_blocked"):
        raise HTTPException(status_code=403, detail="Session blocked")

    image_bytes = None
    attachment_name = None
    message_text = request.text
    if request.attachment:
        if request.attachment_type not in WIDGET_ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=400, detail="Only image files are supported")
        try:
            attachment_data = request.attachment.split(",")[-1]
            image_bytes = base64.b64decode(attachment_data)
            if len(image_bytes) > WIDGET_MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail="Image must be 5 MB or smaller")
            attachment_name = _normalize_attachment_name(request)
            logger.info(f"📎 Получен attachment размером {len(image_bytes)} байт")
        except Exception as e:
            if isinstance(e, HTTPException):
                raise e
            logger.error(f"❌ Ошибка декодирования attachment: {e}")
            raise HTTPException(status_code=400, detail="Invalid image payload")

        if not message_text.strip():
            message_text = _attachment_placeholder(attachment_name)

    # Сохраняем сообщение пользователя без payload файла
    message_id = await save_widget_message(
        session_id=session_id,
        text=message_text,
        sender="user",
        attachment_url=None,
        attachment_type=request.attachment_type,
        attachment_name=attachment_name
    )

    # Обеспечить постоянный форум-топик: если есть tg — ключ tg:<id>, иначе site:<id>
    try:
        client_key = None
        if session.get("user_id"):
            client_key = f"tg:{int(session['user_id'])}"
        elif request.site_user_id:
            client_key = f"site:{request.site_user_id}"
        if client_key:
            # Триггерим создание/фиксацию в обработчике форума (через pipeline), но если уже есть — не трогаем
            existing = await get_persistent_topic(client_key)
            if existing is None:
                # Ничего не делаем здесь, создание произойдёт при первой отправке в форум
                pass
    except Exception:
        pass

    # Отправляем в AI для обработки
    background_tasks.add_task(
        handle_incoming_widget_message,
        bot=bot,
        session_id=session_id,
        user_message=message_text,
        image_bytes=image_bytes,
        filename=attachment_name or "file",
        attachment_type=request.attachment_type,
    )

    logger.info(f"✅ Сообщение из виджета сохранено и отправлено в обработку: {session_id[:8]}...")

    return MessageResponse(
        id=message_id,
        text=message_text,
        sender="user",
        timestamp=_utc_iso(),
        attachment_url=None,
        attachment_type=request.attachment_type,
        attachment_name=attachment_name
    )


@app.get("/api/widget/status/{session_id}")
async def get_operator_status(session_id: str) -> dict:
    """Получить статус оператора (online/typing)"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    # Пока всегда возвращаем online
    # В будущем можно проверять, печатает ли оператор
    return {
        "status": "online",
        "operator_name": "Алекс",
        "typing": False
    }


# === WebSocket для real-time обновлений ===

# Хранилище активных WebSocket подключений
active_connections: dict[str, WebSocket] = {}


@app.websocket("/api/widget/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """WebSocket для real-time сообщений"""
    await websocket.accept()
    active_connections[session_id] = websocket
    
    logger.info(f"🔌 WebSocket подключён: {session_id[:8]}...")
    
    try:
        while True:
            # Получаем данные от клиента (если нужно)
            data = await websocket.receive_text()
            
            # Можно обрабатывать команды от клиента
            try:
                message_data = json.loads(data)
                if message_data.get("type") == "typing":
                    # Пользователь печатает - можно показать индикатор оператору
                    pass
            except json.JSONDecodeError:
                pass
    
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket отключён: {session_id[:8]}...")
    finally:
        active_connections.pop(session_id, None)


@app.post("/api/widget/push")
async def push_to_widget(request: WidgetPushRequest):
    if isinstance(request.message, dict):
        data = request.message.get("data")
        if isinstance(data, dict):
            ts = data.get("timestamp")
            if ts is None or ts == "" or ts == "null" or ts == "None":
                data["timestamp"] = _utc_iso()
    await send_to_widget(request.session_id, request.message)
    return {"ok": True}


async def send_to_widget(session_id: str, message: dict):
    """Отправляет сообщение в WebSocket виджета"""
    if session_id in active_connections:
        try:
            await active_connections[session_id].send_json(message)
            logger.info(f"📤 Отправлено в виджет: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки в виджет: {e}")
            active_connections.pop(session_id, None)


# === Обработка сообщений ===

async def process_widget_message(
    bot: Bot,  # 🔑 Добавляем bot как параметр
    session_id: str,
    user_message: str,
    image_bytes: Optional[bytes] = None,
    filename: str = "",
    attachment_type: Optional[str] = None
):
    """Совместимость: старый entrypoint перенаправлен на новый AI pipeline."""
    logger.info("↪️ process_widget_message() redirected to shared ai_pipeline for session=%s", session_id[:8])
    await handle_incoming_widget_message(
        bot=bot,
        session_id=session_id,
        user_message=user_message,
        image_bytes=image_bytes,
        filename=filename,
        attachment_type=attachment_type,
    )
