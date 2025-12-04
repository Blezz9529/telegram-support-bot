# Telegram Support Bot with AI

## Overview
This is a Telegram bot that provides automated customer support using Google Gemini AI. The bot:
- Receives support requests from users via Telegram
- Uses AI to analyze messages and images
- Provides automated responses or escalates to human operators
- Creates support tickets in a Telegram forum/group
- Maintains conversation history in SQLite database

## Project Status
✅ Successfully imported and configured for Replit environment
✅ All dependencies installed
✅ Environment variables configured
✅ Bot running and polling for messages

## Architecture

### Tech Stack
- **Language**: Python 3.11
- **Bot Framework**: aiogram 3.x (async Telegram bot library)
- **AI Engine**: Google Gemini 2.0 Flash
- **Database**: SQLite (aiosqlite for async operations)
- **Configuration**: python-dotenv for environment variables

### Project Structure
```
.
├── main.py                 # Bot entry point and initialization
├── config.py              # Configuration and environment variables
├── handlers/              # Message and callback handlers
│   ├── user.py           # User-facing handlers
│   └── admin.py          # Admin commands
├── keyboards/            # Telegram keyboard layouts
│   └── reply.py         # Reply keyboard definitions
├── locales/             # Internationalization and prompts
│   ├── ai_messages.json # AI response templates
│   ├── buttons.json     # Button labels
│   ├── prompts.json     # AI system prompts
│   └── texts.json       # UI text strings
├── services/            # Business logic
│   ├── ai_agent.py     # Google Gemini AI integration
│   ├── forum.py        # Forum/group management
│   └── localization.py # i18n utilities
├── storages/           # Data persistence
│   └── db.py          # Database operations
└── utils/             # Helper functions
    └── helpers.py
```

## Environment Variables

### Required Secrets (in Replit Secrets)
- `BOT_TOKEN` - Telegram Bot API token from @BotFather
- `GEMINI_API_KEY` - Google Gemini API key

### Required Environment Variables (Shared)
- `SUPPORT_GROUP_ID` - Telegram group/forum ID for support tickets
- `ADMINS` - Comma-separated admin user IDs

### Optional Configuration
- `GEMINI_MODEL_NAME` - AI model name (default: gemini-2.0-flash)
- `GEMINI_TEMPERATURE` - AI response randomness (default: 0.1)
- `GEMINI_MAX_OUTPUT_TOKENS` - Max response length (default: 768)
- `GEMINI_RETRY_ATTEMPTS` - Retry attempts on failure (default: 3)
- `GEMINI_RETRY_DELAY_BASE` - Base delay for retries (default: 2.0s)

## Features

### AI-Powered Support
- Analyzes user messages and images
- Provides contextual responses based on conversation history
- Supports image analysis (receipts, screenshots, etc.)
- Automatic escalation to human operators when needed
- Configurable prompts and system instructions

### Database
- SQLite database stored in `data/` directory
- User tracking and conversation history
- Support ticket management
- Block/unblock functionality for admins

### Conversation Flow
1. User sends message to bot
2. Bot analyzes message with AI
3. AI either responds directly or escalates to operator
4. If escalated, creates forum topic in support group
5. Operators can respond through forum
6. Conversation history maintained for context

## Recent Changes
- **2024-12-04**: Initial Replit import and setup
  - Installed Python 3.11 and all dependencies
  - Fixed type annotation errors in `services/ai_agent.py` and `config.py`
  - Configured environment variables and secrets
  - Set up workflow for bot execution
  - Updated .gitignore for Python project

## How to Use

### Running the Bot
The bot runs automatically via the configured workflow. To manually run:
```bash
python main.py
```

### Testing
1. Find your bot on Telegram (search by bot username)
2. Send `/start` to begin conversation
3. Send any message to test AI responses
4. Send images to test image analysis

### Admin Commands
- `/block <user_id>` - Block a user
- `/unblock <user_id>` - Unblock a user
- Admin commands only work for user IDs listed in ADMINS env var

## Database Schema

### Users Table
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

## AI Configuration
AI behavior is configured through `locales/prompts.json`:
- `gemini_system_instruction` - Overall AI personality and rules
- `gemini_image_analysis_prompt` - How to analyze images
- `gemini_main_prompt_template` - Template for user queries

## Notes
- The bot uses long polling (not webhooks) for simplicity
- Database is local SQLite (data/ folder)
- All AI responses are in Russian (can be customized in prompts)
- Image analysis is cached to reduce API calls
- Automatic retry logic for Gemini API rate limits
