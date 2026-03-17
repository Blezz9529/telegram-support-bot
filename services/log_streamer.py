"""
SSE log streamer that aggregates docker logs from project containers.
"""
import asyncio
import json
import logging
import os
import aiohttp
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, Optional

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, FileResponse
import os.path

logger = logging.getLogger(__name__)

app = FastAPI(title="Log Streamer")

DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")

LOG_BUFFER_SIZE = 10000
_log_buffer: Deque[Dict[str, object]] = deque(maxlen=LOG_BUFFER_SIZE)
_log_id = 0
_buffer_lock = asyncio.Lock()
_buffer_event = asyncio.Condition()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_level(message: str) -> str:
    if "CRITICAL" in message:
        return "CRITICAL"
    if "ERROR" in message or "❌" in message:
        return "ERROR"
    if "WARNING" in message or "⚠️" in message:
        return "WARNING"
    if "✅" in message:
        return "SUCCESS"
    return "INFO"


def _detect_source(container: str, message: str) -> str:
    if container == "widget-api":
        if "widget" in message.lower():
            return "WIDGET"
        return "API"
    if "services.ai_agent" in message or "OpenRouter" in message or "ИИ" in message:
        return "AI"
    if "services.forum" in message or "topic" in message or "Топик" in message:
        return "FORUM"
    return "BOT"


async def _push_log(container: str, raw_line: str) -> None:
    global _log_id
    message = raw_line.strip()
    if not message:
        return
    level = _parse_level(message)
    source = _detect_source(container, message)
    async with _buffer_lock:
        _log_id += 1
        event = {
            "id": _log_id,
            "ts": _iso_now(),
            "source": source,
            "level": level,
            "message": f"[{container}] {message}",
        }
        _log_buffer.append(event)
    async with _buffer_event:
        _buffer_event.notify_all()


async def _get_container_id(session: aiohttp.ClientSession, name: str) -> Optional[str]:
    try:
        async with session.get("http://docker/containers/json?all=1") as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        for item in data:
            names = item.get("Names") or []
            if any(n.lstrip("/") == name for n in names):
                return item.get("Id")
    except Exception as exc:
        logger.warning("container lookup error for %s: %s", name, exc)
    return None


async def _stream_container_logs(container: str) -> None:
    connector = aiohttp.UnixConnector(path=DOCKER_SOCKET)
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            container_id = await _get_container_id(session, container)
            if not container_id:
                logger.warning("container not found: %s", container)
                await asyncio.sleep(2)
                continue
            url = f"http://docker/containers/{container_id}/logs?follow=1&stdout=1&stderr=1&tail=0"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("log stream failed for %s: status %s", container, resp.status)
                        await asyncio.sleep(2)
                        continue
                    async for raw in resp.content:
                        try:
                            text = raw.decode("utf-8", errors="replace")
                        except Exception:
                            text = str(raw)
                        for line in text.splitlines():
                            await _push_log(container, line)
            except Exception as exc:
                logger.warning("log stream error for %s: %s", container, exc)
            await asyncio.sleep(2)


@app.on_event("startup")
async def _startup() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logger.info("starting log streamer")
    asyncio.create_task(_stream_container_logs("telegram-support-bot"))
    asyncio.create_task(_stream_container_logs("widget-api"))


async def _event_generator(last_id: int):
    # Send buffered logs first
    async with _buffer_lock:
        buffered = [e for e in _log_buffer if int(e["id"]) > last_id]
    for event in buffered:
        yield f"id: {event['id']}\n"
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        last_id = int(event["id"])

    # Stream new logs
    while True:
        async with _buffer_event:
            await _buffer_event.wait()
        async with _buffer_lock:
            new_events = [e for e in _log_buffer if int(e["id"]) > last_id]
        for event in new_events:
            yield f"id: {event['id']}\n"
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            last_id = int(event["id"])


@app.get("/api/logs/stream")
async def stream_logs(request: Request):
    last_event_id = request.headers.get("last-event-id")
    try:
        last_id = int(last_event_id) if last_event_id else 0
    except ValueError:
        last_id = 0

    async def generator():
        async for chunk in _event_generator(last_id):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/api/logs/health")
async def health():
    return {"status": "ok"}


@app.get("/logs.html")
async def logs_page():
    """Serve the realtime logs UI from the repo if present.
    Access: http://HOST:8002/logs.html
    """
    candidate = os.path.join(os.getcwd(), "widget", "logs.html")
    if os.path.exists(candidate):
        return FileResponse(candidate, media_type="text/html")
    # Fallback: minimal inline page
    html = """
    <!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>Logs</title></head>
    <body><pre id=\"log\">Connecting...</pre>
    <script>
      var es=new EventSource('/api/logs/stream');
      es.onmessage=function(e){var p=JSON.parse(e.data);var el=document.getElementById('log');el.textContent+='\n'+p.ts+' '+p.level+' '+p.message;};
    </script>
    </body></html>
    """
    return StreamingResponse(iter([html]), media_type="text/html")
