# Telegram Support Bot — Project Context

## Project Overview

A Telegram customer support bot powered by **Google Gemini AI** that:
- Receives support requests from users via Telegram
- Analyzes messages and images using AI
- Provides automated responses or escalates to human operators
- Creates support tickets in a Telegram forum/group
- Maintains conversation history in SQLite

**Tech Stack:**
- Python 3.11+
- aiogram 3.x (async Telegram bot framework)
- Google Gemini 2.0 Flash (AI engine)
- SQLite + aiosqlite (async database)
- python-dotenv (configuration)

## Project Structure

```
telegram-support-bot/
├── main.py              # Entry point, bot initialization
├── config.py            # Environment variables, AI settings
├── requirements.txt     # Python dependencies
│
├── handlers/            # aiogram message handlers
│   ├── user.py         # User-facing handlers (FSM states)
│   └── admin.py        # Admin commands (block users, replies)
│
├── keyboards/           # Telegram keyboard layouts
│   └── reply.py        # Reply keyboards (main menu, etc.)
│
├── locales/             # i18n and AI prompts
│   ├── prompts.json    # AI system prompts, behavior rules
│   ├── buttons.json    # Button labels
│   ├── texts.json      # UI text strings
│   └── ai_messages.json # AI response templates
│
├── services/            # Business logic
│   ├── ai_agent.py     # Gemini AI integration, image analysis
│   ├── forum.py        # Forum topic management
│   └── localization.py # JSON localization loader
│
├── storages/            # Data persistence
│   └── db.py           # SQLite operations (users table)
│
└── utils/               # Helper utilities
    └── helpers.py      # (empty stub)
```

## Building and Running

### Prerequisites
1. Python 3.11+
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Environment Variables
Create a `.env` file or set these variables:

| Variable | Required | Description |
|----------|----------|-------------|
| `BOT_TOKEN` | ✅ | Telegram Bot token from @BotFather |
| `GEMINI_API_KEY` | ✅ | Google Gemini API key |
| `SUPPORT_GROUP_ID` | ✅ | Telegram group/forum ID for tickets |
| `ADMINS` | ✅ | Comma-separated admin user IDs |
| `GEMINI_MODEL_NAME` | ❌ | Default: `gemini-2.0-flash` |
| `GEMINI_TEMPERATURE` | ❌ | Default: `0.1` |
| `GEMINI_MAX_OUTPUT_TOKENS` | ❌ | Default: `768` |
| `GEMINI_RETRY_ATTEMPTS` | ❌ | Default: `3` |
| `GEMINI_RETRY_DELAY_BASE` | ❌ | Default: `2.0` |

### Running the Bot
```bash
python main.py
```

The bot uses **long polling** (not webhooks).

## Key Features

### Conversation Flow
1. User sends `/start` → shown main menu with 6 topics
2. User selects topic → bot asks for description
3. User sends message (text/image) → AI analyzes
4. AI either:
   - Responds directly (auto-reply)
   - Escalates to operator (creates forum topic)
5. Admins reply in forum → message forwarded to user

### AI Behavior (configured in `locales/prompts.json`)
- **Persona**: Friendly support agent ("Женя", ~20 years old)
- **Tone**: Direct, concise, no filler
- **Max sentences**: 3 per response
- **Language**: Russian
- **Escalation trigger**: `[OPERATOR]` keyword in response

### Supported Topics
| Topic | Operator Required |
|-------|-------------------|
| Оставить отзыв | Always |
| Проблема с пополнением | After info collected |
| Как играть | If user confused |
| Хочу заработать | Never |
| Предлагаю сотрудничество | Always |
| Другой вопрос | After info collected |

### Image Analysis
- Supports photos and documents
- Images cached by (user_id, timestamp) to reduce API calls
- MIME type detection via `imghdr`
- Descriptions inserted into conversation history

### Admin Features
- Reply to user messages in forum topics
- Block users via inline button (🚫 Заблокировать)
- Blocked users cannot interact with bot

## Database Schema

**Table: `users`**
```sql
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    topic_id INTEGER,
    theme TEXT,
    is_blocked BOOLEAN DEFAULT 0,
    last_message_id INTEGER DEFAULT 0,
    first_message_in_ticket BOOLEAN DEFAULT 1
)
```

Database location: `data/support.db`

## Development Conventions

### Code Style
- Async/await throughout (aiogram, aiosqlite)
- Type hints for function parameters
- Logging via `logging.getLogger(__name__)`
- Russian comments and messages

### Error Handling
- Retry logic for Gemini API (exponential backoff)
- Flood control handling for Telegram API
- Graceful fallback if AI unavailable

### Localization
- All UI text in `locales/*.json`
- Use `load_text()`, `load_button()` from `services/localization.py`
- Supports variable substitution via `.format()`

## Testing

### Manual Testing
1. Find bot on Telegram by username
2. Send `/start` → verify menu appears
3. Select topic → verify prompt for description
4. Send message → verify AI response
5. Send image → verify image analysis
6. As admin: reply in forum → verify user receives message

### Logs
Check console output for:
- `🚀 Инициализация бота...` — startup
- `📡 Бот запущен` — ready for messages
- `🆕 Запрос ИИ` — AI request
- `✅ Топик` — new forum topic created

## Common Issues

| Issue | Solution |
|-------|----------|
| `GEMINI_API_KEY не задан` | Set environment variable |
| `429 ResourceExhausted` | Automatic retry, wait |
| `topic not found` | Bot creates new topic automatically |
| `user blocked` | Check `is_blocked` in database |

## Files to Know

| File | Purpose |
|------|---------|
| `config.py` | All environment variables, AI settings |
| `handlers/user.py` | Main conversation FSM logic |
| `handlers/admin.py` | Admin reply/block handlers |
| `services/ai_agent.py` | Gemini integration, prompt loading |
| `services/forum.py` | Topic creation, message sending |
| `storages/db.py` | User CRUD operations |
| `locales/prompts.json` | AI behavior configuration |
