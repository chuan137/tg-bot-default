import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
DB_PATH = os.getenv("DB_PATH", "/app/assistant.db")
SUMMARY_INTERVAL_MINUTES = int(os.getenv("SUMMARY_INTERVAL_MINUTES", "60"))

AI_ENABLED = os.getenv("AI_ENABLED", "0") == "1"
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini")
AI_SYSTEM_PROMPT = os.getenv("AI_SYSTEM_PROMPT", "You are a helpful assistant. Answer clearly and concisely.")
