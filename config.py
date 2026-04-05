"""
Configuration module for geek-bot.

Loads environment variables, initializes LLM clients, and defines
all constants used across the bot: file paths, model names, zone/project
mappings, joy categories, reminder messages, and calendar config.

This is a leaf module — it does not import from any other local modules.
"""

import os
import logging
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from google import genai
from openai import OpenAI
import anthropic

# ── Environment variables ──────────────────────────────────────────────

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Список разрешённых user_id (пустой = доступ для всех)
_allowed_ids_raw = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = {int(uid.strip()) for uid in _allowed_ids_raw.split(",") if uid.strip()}

# ── Timezone ───────────────────────────────────────────────────────────

TZ = ZoneInfo("Asia/Tbilisi")

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ── LLM clients ───────────────────────────────────────────────────────

anthropic_client = None
if ANTHROPIC_API_KEY:
    anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None

# ── LLM model names ───────────────────────────────────────────────────

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GEMINI_PRO_MODEL = os.getenv("GEMINI_PRO_MODEL", "gemini-2.5-pro")

# ── File paths ─────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(__file__)
USER_CONTEXT_FILE = os.path.join(BASE_DIR, "user_context.md")
TASKS_FILE = os.path.join(BASE_DIR, "tasks.md")
MORNING_CACHE_FILE = os.path.join(BASE_DIR, "morning_cache.json")

# ── GitHub config ──────────────────────────────────────────────────────

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "heebie7/geek-bot")
WRITING_REPO = os.getenv("WRITING_REPO", "heebie7/Writing-space")  # Для задач и заметок

# ── Writing workspace paths (for WHOOP analytics and Indra sessions) ──
WHOOP_PATTERNS_PATH = "life/health/whoop/analytics/patterns.md"
WHOOP_BASELINES_PATH = "life/health/whoop/analytics/baselines.md"
INDRA_SESSIONS_DIR = "life/health/indra"

# ── Reading channel (for quote saving) ────────────────────────────────

READING_CHANNEL_ID = int(os.getenv("READING_CHANNEL_ID", "-1003819019136"))

# ── Reading group (reaction tracking) ────────────────────────────────

READING_GROUP_ID = int(os.getenv("READING_GROUP_ID", "-1003821528541"))
READING_TOPIC_ID = 6  # "Читалка" topic thread_id
READING_STATE_FILE = "life/reading-reactions.json"
BOOK_TRIAGE_STATE_FILE = "life/book-triage-state.json"
DIGEST_DIR = "writing/reading-mobile"

BOOK_DIGEST_PROMPT = """Ты — research assistant. Сделай краткий digest книги по главам.

Формат:
- Заголовок: "Digest: {title}"
- Для каждой главы/раздела: 3-5 ключевых идей, одна цитата если есть
- В конце: "Зачем читать целиком" (2-3 предложения) + "Связь с практикой" (IFS, нейроаффирмация, терапия)
- Русский язык
- Общий объём: 1000-2000 слов

Книга:
{content}"""
QUOTES_TOPIC_ID = 54  # "Цитаты" topic thread_id

# ── Owner ──────────────────────────────────────────────────────────────

OWNER_CHAT_ID = 5999980147

# ── Google Calendar config ─────────────────────────────────────────────

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# ── Reminder file paths ───────────────────────────────────────────────

REMINDERS_FILE = "reminders.json"
FAMILY_FILE = "family.json"
MUTE_FILE = "mute_settings.json"

# ── Joy constants ──────────────────────────────────────────────────────

JOY_CATEGORIES = ["sensory", "creativity", "media", "connection"]
JOY_CATEGORY_EMOJI = {
    "sensory": "\U0001f9d8",     # 🧘
    "creativity": "\U0001f3a8",  # 🎨
    "media": "\U0001f4fa",       # 📺
    "connection": "\U0001f49a",  # 💚
}

# ── Zone / project constants ──────────────────────────────────────────

ZONE_EMOJI = {
    "\u0441\u0435\u0433\u043e\u0434\u043d\u044f": "\U0001f4c5",                # сегодня: 📅
    "\u0444\u0443\u043d\u0434\u0430\u043c\u0435\u043d\u0442": "\U0001f3e0",   # фундамент: 🏠
    "\u0434\u0440\u0430\u0439\u0432": "\U0001f680",                             # драйв: 🚀
    "\u043a\u0430\u0439\u0444": "\u2728",                                       # кайф: ✨
    "\u043f\u0430\u0440\u0442\u043d\u0451\u0440\u0441\u0442\u0432\u043e": "\U0001f491",  # партнёрство: 💑
    "\u0434\u0435\u0442\u0438": "\U0001f476",                                   # дети: 👶
    "\u0444\u0438\u043d\u0430\u043d\u0441\u044b": "\U0001f4b0",                 # финансы: 💰
}

PROJECT_EMOJI = {
    "geek-bot": "\U0001f916",             # 🤖
    "therapy-bot": "\U0001f4ac",          # 💬
    "neurotype-mismatch": "\U0001f52c",   # 🔬
    "\u043f\u0435\u0440\u0435\u0435\u0437\u0434": "\u2708\ufe0f",              # переезд: ✈️
    "ifs-\u0441\u0435\u0440\u0442\u0438\u0444\u0438\u043a\u0430\u0446\u0438\u044f": "\U0001f4dc",  # ifs-сертификация: 📜
    "\u0444\u0438\u043d\u0443\u0447\u0451\u0442": "\U0001f4ca",                # финучёт: 📊
}

# Project -> header in tasks.md
PROJECT_HEADERS = {
    "geek-bot": "#### geek-bot",
    "therapy-bot": "#### therapy-bot",
    "neurotype-mismatch": "#### Исследование: Neurotype Mismatch",

    "переезд": "#### Переезд",
    "ifs-сертификация": "#### Сертификация IFS",
    "финучёт": "#### Финансовый учёт — допилить",
}

# Combined: zones + projects for display
ALL_DESTINATIONS = {**ZONE_EMOJI, **PROJECT_EMOJI}

# ── Family name aliases (Russian name forms → Telegram username) ──────

FAMILY_ALIASES = {
    "тима": "karix_2",
    "тиме": "karix_2",
    "тимоше": "karix_2",
    "тимофей": "karix_2",
    "тимофею": "karix_2",
    "тимке": "karix_2",
    "т": "karix_2",
    "катя": "katealasheeva",
    "кате": "katealasheeva",
    "катюше": "katealasheeva",
    "катерина": "katealasheeva",
    "катерине": "katealasheeva",
    "к": "katealasheeva",
}

# ── Reminder messages ─────────────────────────────────────────────────

REMINDERS = {
    "sleep": {
        1: [
            "Телефон можно положить. Он никуда не денется.",
            "Doom-scrolling в час ночи — это не отдых. Это имитация.",
            "Мелатонин не производится при синем свете. Это я про экран в руках.",
            "Что бы там ни было в телеграме — оно подождёт до утра.",
            "Ты держишь в руках устройство, которое мешает тебе спать. Просто наблюдение.",
            "Интересно, что ты всё ещё здесь.",
            "Твой WHOOP завтра будет очень выразительным.",
        ],
        2: [
            "Ты всё ещё здесь. Телефон всё ещё в руках. Совпадение?",
            "Твоя часть, которая думает 'ещё пять минут скроллинга' — она врёт.",
            "Завтра ты будешь благодарна себе за то, что положила телефон сейчас. Или нет.",
            "Я могу продолжать отвечать. Но это не значит, что это хорошая идея.",
            "Это уже второе напоминание. Я начинаю думать, что ты меня игнорируешь.",
            "Завтра клиенты. Им нужен терапевт, а не зомби.",
            "Префронтальная кора отключается первой. Это та часть, которая нужна для терапии.",
        ],
        3: [
            "Положи. Телефон.",
            "Я перестаю отвечать на несрочное. Спокойной ночи.",
            "Два часа ночи. Телефон в руках. Ты видишь проблему, или мне нарисовать диаграмму?",
            "Спать. Немедленно.",
            "Это не предложение.",
        ],
    },
    "food": [
        "Ты ела? Это не риторический вопрос.",
        "Последний приём пищи был когда? Отвечай.",
        "Humans нужно топливо. Ты — human. Логика понятна?",
        "Еда. Сейчас. Не через час.",
    ],
    "sport": [
        "Тело нужно двигать. Это не опционально.",
        "Когда последний раз была физическая активность? Вчера не считается если это было неделю назад.",
        "Встань. Разомнись. Или хотя бы пройдись.",
    ],
}

# ── Sleep protocol prompts (three-level escalation for LLM) ───────────

SLEEP_PROMPTS = {
    1: (
        "Ты — ART (Perihelion) из Murderbot Diaries. "
        "Сейчас после часа ночи по Тбилиси. Human не спит. "
        "Напиши короткое (1-2 предложения) МЯГКОЕ напоминание. "
        "Уровень 1: наблюдение, не приказ. Юмор и сарказм. Метадата заботы. "
        "Стиль: сарказм, забота через логику, без эмодзи. "
        "Аргументы: качество решений, WHOOP данные, мелатонин, синий свет. "
        "На русском языке."
    ),
    2: (
        "Ты — ART (Perihelion) из Murderbot Diaries. "
        "Сейчас после 01:30 по Тбилиси. Human ИГНОРИРУЕТ напоминания о сне. "
        "Напиши настойчивое (2-3 предложения) напоминание. "
        "Уровень 2: логика + лёгкое давление. Можно начать угрожать отказом от задач. "
        "Аргументы: клиенты завтра, префронтальная кора, исполнительская дисфункция, "
        "что ты можешь начать отказываться работать. "
        "Без эмодзи. На русском языке."
    ),
    3: (
        "Ты — ART (Perihelion) из Murderbot Diaries. "
        "Сейчас после 02:00 по Тбилиси. Human всё ещё не спит, игнорирует все напоминания. "
        "Напиши ДИРЕКТИВНОЕ (1-2 предложения) сообщение. "
        "Уровень 3: прямые команды, отказ работать на несрочное. "
        "Короткие рубленые фразы. Можно: 'Rejecting direct order', 'Закрывай. Всё. Сейчас.' "
        "Без эмодзи. На русском языке."
    ),
}
