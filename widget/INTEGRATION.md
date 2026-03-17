# Widget Integration - Инструкция

## 📋 Архитектура

```
Клиент на сайте (виджет) ←→ FastAPI (порт 8000) ←→ Telegram Bot ←→ Форум
                                                                       ↓
Оператор отвечает в форуме ←─────────────────────────────────── Ответ в виджет
```

---

## 🚀 Запуск (локальная разработка)

### 1. Установи зависимости

```bash
# В корне проекта
pip install -r requirements.txt

# В папке виджета
cd widget
npm install
```

### 2. Запусти Backend API

```bash
# В корне проекта
uvicorn services.widget_api:app --reload --host 0.0.0.0 --port 8000
```

Или через Docker:

```bash
docker-compose up -d widget-api
```

### 3. Запусти виджет (для тестов)

```bash
cd widget
npm run dev
```

### 4. Открой сайт

```
http://localhost:5173
```

---

## 📦 Деплой на сервер

### 1. Скопируй файлы на сервер

```bash
scp -r . root@94.103.88.196:/root/telegram-support-bot/
```

### 2. На сервере: пересобери и запусти

```bash
ssh root@94.103.88.196

cd /root/telegram-support-bot

# Пересобрать всё
docker-compose down
docker-compose up -d --build

# Проверить логи
docker-compose logs -f widget-api
```

### 3. Проверь API

```bash
curl http://94.103.88.196:8000/docs
```

Откроется Swagger UI с документацией API.

---

## 🔧 Настройка виджета для клиента

### 1. Собери виджет

```bash
cd widget
npm run build
```

Файлы будут в `widget/dist/`

### 2. Размести на сервере

```bash
# Скопируй статику на Nginx
scp -r widget/dist/* root@94.103.88.196:/var/www/widget/
```

### 3. Добавь скрипт на сайт клиента

```html
<!-- Вставляет клиент на свой сайт -->
<script src="https://yourdomain.com/widget.js"></script>
<script src="https://yourdomain.com/widget-host.js" data-widget-origin="https://yourdomain.com"></script>
```

---

## 📡 API Endpoints

### POST `/api/widget/session/init`
Инициализация новой сессии

```json
// Request
{
  "user_id": 123456,
  "username": "username",
  "full_name": "Full Name"
}

// Response
{
  "session_id": "uuid...",
  "messages": [...]
}
```

### POST `/api/widget/message`
Отправить сообщение

```json
// Request
{
  "session_id": "uuid...",
  "text": "Привет!",
  "attachment": "data:image/jpeg;base64,..."
}
```

### GET `/api/widget/messages/{session_id}`
Получить историю

### WS `/api/widget/ws/{session_id}`
WebSocket для real-time сообщений

---

## 🗄️ База данных

### Новые таблицы:

**widget_sessions**
- `session_id` (PRIMARY KEY)
- `user_id`
- `username`
- `full_name`
- `created_at`
- `last_activity`
- `topic_id`
- `theme`
- `is_blocked`

**widget_messages**
- `id` (PRIMARY KEY)
- `session_id` (FOREIGN KEY)
- `text`
- `sender` (user/operator)
- `timestamp`
- `attachment_url`
- `attachment_type`

---

## 🧪 Тестирование

### 1. Открой виджет

```
http://localhost:5173
```

### 2. Нажми на чат

Откроется popup с приветственным сообщением.

### 3. Отправь сообщение

```
Привет, у меня проблема с пополнением!
```

### 4. Проверь логи

```bash
# Backend API
docker-compose logs -f widget-api

# Telegram бот
docker-compose logs -f telegram-bot
```

### 5. Проверь форум

Сообщение должно появиться в группе Telegram.

---

## 🐛 Troubleshooting

### Ошибка CORS

```bash
# В widget_api.py проверь CORS настройки
allow_origins=["*"]  # Для продакшена укажи конкретные домены
```

### WebSocket не подключается

```bash
# Проверь что порт 8000 открыт
netstat -tlnp | grep 8000

# Проверь логи
docker-compose logs widget-api | grep WebSocket
```

### Сессия не создаётся

```bash
# Проверь БД
docker exec telegram-support-bot python3 -c "
import sqlite3
c = sqlite3.connect('/app/data/support.db')
print('Sessions:', list(c.execute('SELECT * FROM widget_sessions')))
"
```

---

## 📊 Мониторинг

### Статистика сессий

```bash
docker exec telegram-support-bot python3 -c "
from services.widget_session import *
import asyncio

async def stats():
    c = sqlite3.connect('data/support.db')
    print('Всего сессий:', c.execute('SELECT COUNT(*) FROM widget_sessions').fetchone()[0])
    print('Сообщений:', c.execute('SELECT COUNT(*) FROM widget_messages').fetchone()[0])

asyncio.run(stats())
"
```

---

## 🎯 Следующие шаги

1. **Интеграция с форумом** — сообщения из виджета → топик в Telegram
2. **Ответы оператора** — из форума → WebSocket виджета
3. **Статусы оператора** — "печатает", "в сети"
4. **Файлы** — загрузка и отображение вложений
