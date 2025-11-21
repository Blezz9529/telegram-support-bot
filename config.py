import os
from typing import List
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SUPPORT_GROUP_ID = int(os.getenv("SUPPORT_GROUP_ID"))
ADMINS = [int(x.strip()) for x in os.getenv("ADMINS", "").split(",") if x.strip()]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")