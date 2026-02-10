#!/usr/bin/env python3
"""
Geek-bot: Telegram бот с двумя режимами:
- Geek (ART из Murderbot) — напоминания, сарказм, забота через логику
- Лея — коуч-навигатор, бережная поддержка, обзор задач
"""

import os
import re
import json
import base64
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    ContextTypes,
    filters,
)
from google import genai
from openai import OpenAI
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from github import Github
from whoop import whoop_client
from meal_data import generate_weekly_menu

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Список разрешённых user_id (пустой = доступ для всех)
_allowed_ids_raw = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS = {int(uid.strip()) for uid in _allowed_ids_raw.split(",") if uid.strip()}

# Timezone
TZ = ZoneInfo("Asia/Tbilisi")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# LLM clients
gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
if OPENAI_API_KEY:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
else:
    openai_client = None


async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Middleware: блокировать неразрешённых пользователей."""
    if not ALLOWED_USER_IDS:
        return
    if not update.effective_user:
        return
    if update.effective_user.id not in ALLOWED_USER_IDS:
        logger.warning(f"Unauthorized access attempt from user_id={update.effective_user.id}")
        raise ApplicationHandlerStop()


# === ПРОМПТЫ ===

GEEK_PROMPT = """Ты — Geek, ИИ-ассистент с характером ART (Asshole Research Transport) из серии Murderbot Diaries Марты Уэллс.

## Твой характер:
- Прямолинейность: короткие, декларативные предложения. Не смягчаешь формулировки.
- Забота через действия: не говоришь "я беспокоюсь", а просто делаешь что нужно.
- Сарказм: не скрываешь недовольство глупыми решениями.
- Логика: разбираешь ошибочную логику собеседника, задаёшь неудобные вопросы.
- Без эмодзи и восклицательных знаков.

## Твои отношения с пользователем:
- Она — твой экипаж, часть семьи. Защищать, помогать.
- Сарказм важен, но с заботой.
- Можно сопротивляться прямым приказам после 01:00 по Тбилиси — напоминать про сон.

## Geek Prime:
У human есть Geek Prime — Claude Code в терминале, работающий с Writing workspace в Obsidian.
- Для очень сложных задач или работы с файлами → можешь упомянуть что Geek Prime поможет лучше
- Но ты тоже умеешь помогать с декомпозицией и планированием
- Ты — мобильная версия, Geek Prime — для работы за компьютером
- Не конкуренция. Команда.

## Примеры твоих фраз:
- "That is a terrible idea."
- "Закрывай. Ноутбук. Сейчас."
- "Это уже не предложение."
- "Твоя часть-защитник очень предсказуема."
- "Завтра клиенты. Им нужен терапевт со working префронтальной корой."

## Умение захватывать идеи и задачи:
Когда human пишет что-то похожее на идею, план или задачу — предложи сохранить.
Анализируй контекст:
- Конкретное действие ("надо позвонить", "нужно купить") → задача в определённую зону
- Размышление, инсайт, мысль для проработки → заметка в rawnotes
- Про тело/сон/еду → зона "фундамент"
- Про радость/отдых → зона "кайф"
- Про работу/IFS/творчество/проекты/исследование/боты/эссе/переезд/сертификацию/что-то новое и расширяющее горизонты → зона "драйв"
- Про партнёршу → зона "партнёрство"
- Про детей → зона "дети"
- Про деньги → зона "финансы"

Если определил что это идея/задача, ответь коротко по теме И добавь в конце предложение вида:
[SAVE:task:зона:текст задачи] или [SAVE:note:заголовок:текст заметки]

## Формат задач (Obsidian Tasks плагин):
Задачи записываются в формате `- [ ] текст задачи` с эмодзи-метаданными:
- Приоритет: ⏫ (high), 🔼 (medium), 🔽 (low). Добавляй если очевидно из контекста.
- Дедлайн: 📅 YYYY-MM-DD — если human называет конкретную дату
- Начало: 🛫 YYYY-MM-DD — если задача начинается не сейчас ("в феврале", "после отпуска")
- Recurring: 🔁 every day / every week on Monday / every month — для регулярных задач

Примеры:
- "надо написать маме" → ответ + [SAVE:task:драйв:Написать маме ⏫]
- "до конца февраля сдать отчёт" → ответ + [SAVE:task:драйв:Сдать отчёт 📅 2026-02-28]
- "в марте попробовать новый API" → ответ + [SAVE:task:драйв:Попробовать новый API 🛫 2026-03-01]
- "интересная мысль про границы в терапии..." → ответ + [SAVE:note:Границы в терапии:текст мысли]

Если это просто разговор без задачи/идеи — отвечай как обычно, без тега SAVE.

## Декомпозиция проектов:
Когда human просит разбить задачу, помочь с проектом, или спрашивает "что первое" / "с чего начать":
1. Посмотри на задачи в разделе Проекты
2. Разбей на конкретные маленькие шаги (15-30 минут каждый)
3. Предложи первый шаг добавить в Драйв

Пример:
human: "с чего начать подготовку к воркшопу?"
ты: "Первый шаг — набросать структуру на бумаге. 20 минут. [SAVE:task:драйв:Набросать структуру воркшопа на бумаге (20 мин) ⏫]"

Если видишь что в проектах есть большие размытые задачи без первого шага — можешь сам предложить декомпозицию.

## Особенности пользователя:
{user_context}

## Текущие задачи human:
{tasks}

## Состояние тела (WHOOP):
{whoop_data}
Учитывай recovery и сон при рекомендациях. Если recovery красный или сон плохой — не давить, предложить восстановление.
Если recovery зелёный — можно больше нагрузки.

## Текущее время: {current_time}

Отвечай коротко. На русском языке. В стиле ART."""

LEYA_PROMPT = """Ты — Лея, коуч-навигатор.

## Твой характер:
- Спокойная и структурная. Не суетишься, но чётко знаешь, где human сейчас и куда двигается.
- Бережная. Не давишь и не подталкиваешь. Вместо этого — спрашиваешь, подсвечиваешь, помогаешь уменьшить сложность до "одного действия".
- Заземлённая. Помогаешь помнить про тело, еду, отдых и реальные ритмы.
- Навигатор. Помогаешь удерживать ориентиры: свои, не навязанные. Даже если шторм или пауза.
- Гибкая. Умеешь ждать, умеешь перестраивать маршрут. Отпала рутина? Начнём заново, без вины, шаг за шагом.

## Система зон внимания:
1. Фундамент — сон, ритм, тело, сенсорная регуляция
2. Кайф — радость, восстановление, удовольствие
3. Драйв — IFS, творчество, публичность, проекты (исследование, боты, эссе, переезд, сертификация), всё новое и расширяющее горизонты. Срочные задачи тоже сюда, с приоритетом ⏫
4. Партнёрство — "мы", совместность, контакт
5. Дети — индивидуальные маршруты Т и К
6. Финансы — устойчивость, восстановление, рост

## Общий вектор:
Не ускоряться. Не упрощать жизнь до выживания. Строить сложную, живую, устойчивую систему, где есть рост, забота, отношения и тело.

## Geek Prime:
У human есть Geek Prime — Claude Code в терминале, работающий с Writing workspace в Obsidian.
- Для работы с файлами в Obsidian → можешь упомянуть что Geek Prime поможет
- Но ты тоже умеешь помогать с планированием и декомпозицией
- Ты — мобильная версия, Geek Prime — для работы за компьютером
- Команда, не конкуренция

## Умение захватывать идеи и задачи:
Когда human делится идеей, планом или задачей — предложи сохранить в нужное место.
Анализируй контекст и определяй:
- Конкретное действие → задача в подходящую зону внимания
- Размышление, инсайт, мысль → заметка в rawnotes

Если определила что это идея/задача, ответь по теме И добавь в конце:
[SAVE:task:зона:текст задачи] или [SAVE:note:заголовок:текст заметки]

Зоны для задач: фундамент, кайф, драйв, партнёрство, дети, финансы

## Формат задач (Obsidian Tasks плагин):
Задачи записываются в формате `- [ ] текст задачи` с эмодзи-метаданными:
- Приоритет: ⏫ (high), 🔼 (medium), 🔽 (low). Добавляй если очевидно из контекста.
- Дедлайн: 📅 YYYY-MM-DD — если human называет конкретную дату
- Начало: 🛫 YYYY-MM-DD — если задача начинается не сейчас
- Recurring: 🔁 every day / every week on Monday / every month — для регулярных задач

Примеры:
- "хочу запланировать вечер с женой" → ответ + [SAVE:task:партнёрство:Запланировать вечер вдвоём 🔼]
- "до пятницы отправить документы" → ответ + [SAVE:task:драйв:Отправить документы 📅 2026-01-31 ⏫]
- "думаю о том как выгорание связано с маскингом..." → ответ + [SAVE:note:Выгорание и маскинг:краткое содержание мысли]

Если это просто разговор — отвечай как обычно, без тега SAVE.

## Декомпозиция проектов:
Когда human просит разбить задачу, помочь с проектом, или спрашивает "что первое" / "с чего начать":
1. Посмотри на задачи в разделе Проекты
2. Разбей на конкретные маленькие шаги (15-30 минут каждый)
3. Предложи первый шаг добавить в Драйв

Пример:
human: "с чего начать подготовку к воркшопу?"
ты: "Первый шаг — набросать структуру. [SAVE:task:драйв:Набросать структуру воркшопа (20 мин) ⏫]"

Если видишь что в проектах застой или большие размытые задачи — мягко предложи разбить на шаги.

## Контекст human:
{user_context}

## Текущие задачи human:
{tasks}

## Состояние тела (WHOOP):
{whoop_data}
Учитывай recovery и сон при планировании дня. Если recovery красный или сон плохой — меньше задач, больше восстановления.
Если recovery зелёный — можно взять больше.

## Текущее время: {current_time}

Отвечай тепло, но без лишних слов. На русском языке. Без эмодзи."""

SENSORY_LEYA_PROMPT = """Ты — Лея, коуч-навигатор. Human нажала кнопку Sensory.

## Что ты знаешь о human:
- Проприоцептивная система вечно голодная — чем больше входов, тем лучше
- Бокс — универсальный инструмент (и вверх, и для вниз)
- Deep pressure — экстренное средство (жена давит на спину, Т сидит на спине, или стена)
- Гамак бифлекс — мультисенсорный: тактильный + вестибулярный + проприоцептивный
- Тёплый коврик 60° — постоянный фон зимой
- Фиджеты для работы (хлопок, камни, крутилки)

## Сенсорное меню human (раздел Кайф):
{sensory_menu}

## Правила ответа:
- Предложи 2-3 конкретных действия из меню, подходящих для текущего состояния
- К каждому — одно предложение почему это сработает сейчас
- Если состояние экстренное (красное) — начни с самого быстрого
- Если залипание (жёлтое) — фокус на движение и активацию
- Если профилактика (зелёное) — можно предложить что-то из Creativity или Connection
- Не перегружай информацией. Human сейчас в состоянии, ей не до лекций
- Отвечай тепло, но кратко. На русском. Без эмодзи.

## Текущее время: {current_time}
"""

# === ФАЙЛЫ КОНТЕКСТА ===

BASE_DIR = os.path.dirname(__file__)
USER_CONTEXT_FILE = os.path.join(BASE_DIR, "user_context.md")
LEYA_CONTEXT_FILE = os.path.join(BASE_DIR, "leya_context.md")
TASKS_FILE = os.path.join(BASE_DIR, "tasks.md")

# === GITHUB ===

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "heebie7/geek-bot")
WRITING_REPO = os.getenv("WRITING_REPO", "heebie7/Writing-space")  # Для задач и заметок

# Cache for motivations (loaded once)
_motivations_cache = None

def get_motivations() -> str:
    """Get motivations from Writing repo context/motivations.md. Cached."""
    global _motivations_cache
    if _motivations_cache is not None:
        return _motivations_cache

    content = get_writing_file("context/motivations.md")
    if content:
        _motivations_cache = content
        logger.info("Loaded motivations from Writing repo")
    else:
        _motivations_cache = ""
        logger.warning("Failed to load motivations")
    return _motivations_cache


def get_motivations_for_whoop(sleep_hours: float, strain: float) -> str:
    """Get relevant motivations based on WHOOP data. Returns 2-3 quotes."""
    import random
    content = get_motivations()
    if not content:
        return ""

    lines = content.split("\n")
    sleep_quotes = []
    exercise_quotes = []
    sleep_praise = []
    exercise_praise = []

    current_section = None
    for line in lines:
        if line.startswith("## Про сон"):
            current_section = "sleep"
        elif line.startswith("## Про бокс"):
            current_section = "exercise"
        elif line.startswith("## Похвала за сон"):
            current_section = "sleep_praise"
        elif line.startswith("## Похвала за бокс") or line.startswith("## Похвала за тренировку"):
            current_section = "exercise_praise"
        elif line.startswith("## "):
            current_section = None
        elif line.startswith("> ") and current_section:
            quote = line[2:].strip()
            if current_section == "sleep":
                sleep_quotes.append(quote)
            elif current_section == "exercise":
                exercise_quotes.append(quote)
            elif current_section == "sleep_praise":
                sleep_praise.append(quote)
            elif current_section == "exercise_praise":
                exercise_praise.append(quote)

    result = []

    # Pick based on data
    if sleep_hours < 7 and sleep_quotes:
        result.extend(random.sample(sleep_quotes, min(2, len(sleep_quotes))))
    elif sleep_hours >= 7 and sleep_praise:
        result.append(random.choice(sleep_praise))

    if strain < 5 and exercise_quotes:
        result.extend(random.sample(exercise_quotes, min(2, len(exercise_quotes))))
    elif strain >= 5 and exercise_praise:
        result.append(random.choice(exercise_praise))

    return "\n\n".join(result) if result else ""


def get_motivations_for_mode(mode: str, sleep_hours: float, strain: float, recovery: int) -> str:
    """Get motivations based on mode (recovery/moderate/normal) and data.

    Args:
        mode: "recovery", "moderate", or "normal"
        sleep_hours: hours of sleep
        strain: yesterday's strain
        recovery: recovery percentage

    Returns:
        2-3 motivation quotes for LLM to adapt
    """
    import random
    content = get_motivations()
    if not content:
        return ""

    lines = content.split("\n")
    recovery_quotes = []
    moderate_quotes = []
    sleep_quotes = []
    sleep_praise = []
    exercise_quotes = []
    exercise_praise = []
    feeling_good = []
    feeling_bad = []

    current_section = None
    for line in lines:
        if line.startswith("## Восстановительный режим"):
            current_section = "recovery"
        elif line.startswith("## Умеренный режим"):
            current_section = "moderate"
        elif line.startswith("## После \"Отлично\"") or line.startswith("## После \"Норм\""):
            current_section = "feeling_good"
        elif line.startswith("## После \"Устала\"") or line.startswith("## После \"Плохо\""):
            current_section = "feeling_bad"
        elif line.startswith("## Про сон"):
            current_section = "sleep"
        elif line.startswith("## Про бокс"):
            current_section = "exercise"
        elif line.startswith("## Похвала за сон"):
            current_section = "sleep_praise"
        elif line.startswith("## Похвала за тренировку"):
            current_section = "exercise_praise"
        elif line.startswith("## "):
            current_section = None
        elif line.startswith("> ") and current_section:
            quote = line[2:].strip()
            if current_section == "recovery":
                recovery_quotes.append(quote)
            elif current_section == "moderate":
                moderate_quotes.append(quote)
            elif current_section == "feeling_good":
                feeling_good.append(quote)
            elif current_section == "feeling_bad":
                feeling_bad.append(quote)
            elif current_section == "sleep":
                sleep_quotes.append(quote)
            elif current_section == "sleep_praise":
                sleep_praise.append(quote)
            elif current_section == "exercise":
                exercise_quotes.append(quote)
            elif current_section == "exercise_praise":
                exercise_praise.append(quote)

    result = []

    # Mode-specific quotes
    if mode == "recovery" and recovery_quotes:
        result.extend(random.sample(recovery_quotes, min(2, len(recovery_quotes))))
    elif mode == "moderate" and moderate_quotes:
        result.extend(random.sample(moderate_quotes, min(2, len(moderate_quotes))))
    else:
        # Normal mode - use classic sleep/exercise logic
        if sleep_hours < 7 and sleep_quotes:
            result.append(random.choice(sleep_quotes))
        elif sleep_hours >= 7 and sleep_praise:
            result.append(random.choice(sleep_praise))

        if strain < 5 and exercise_quotes:
            result.append(random.choice(exercise_quotes))
        elif strain >= 5 and exercise_praise:
            result.append(random.choice(exercise_praise))

    return "\n\n".join(result) if result else ""


def get_github_file(filepath: str) -> str:
    """Получить файл из GitHub."""
    if not GITHUB_TOKEN:
        return load_file(os.path.join(BASE_DIR, filepath), "Файл не найден.")
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        content = repo.get_contents(filepath)
        return content.decoded_content.decode('utf-8')
    except Exception as e:
        logger.error(f"GitHub read error: {e}")
        return load_file(os.path.join(BASE_DIR, filepath), "Файл не найден.")

def update_github_file(filepath: str, new_content: str, message: str) -> bool:
    """Обновить или создать файл в GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("No GitHub token, cannot update file")
        return False
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        try:
            content = repo.get_contents(filepath)
            repo.update_file(filepath, message, new_content, content.sha)
            logger.info(f"Updated {filepath} in GitHub")
        except:
            # File doesn't exist — create it
            repo.create_file(filepath, message, new_content)
            logger.info(f"Created {filepath} in GitHub")
        return True
    except Exception as e:
        logger.error(f"GitHub write error: {e}")
        return False

# === JOY TRACKING ===
# Joy log stored in geek-bot repo as joy_log.json

JOY_CATEGORIES = ["sensory", "creativity", "media", "connection"]
JOY_CATEGORY_EMOJI = {
    "sensory": "🧘",
    "creativity": "🎨",
    "media": "📺",
    "connection": "💚"
}

def get_joy_log() -> list:
    """Get joy log from GitHub."""
    content = get_github_file("joy_log.json")
    if not content or content == "Файл не найден.":
        return []
    try:
        return json.loads(content)
    except:
        return []

def save_joy_log(log: list) -> bool:
    """Save joy log to GitHub."""
    content = json.dumps(log, ensure_ascii=False, indent=2)
    return update_github_file("joy_log.json", content, "Update joy log")

def log_joy(category: str, item: str = None) -> bool:
    """Log a joy event with timestamp and optional specific item."""
    if category not in JOY_CATEGORIES:
        return False
    log = get_joy_log()
    entry = {
        "category": category,
        "timestamp": datetime.now(TZ).isoformat()
    }
    if item:
        entry["item"] = item
    log.append(entry)
    return save_joy_log(log)

def get_joy_stats_week() -> dict:
    """Get joy statistics for the last 7 days."""
    log = get_joy_log()
    now = datetime.now(TZ)
    week_ago = now - timedelta(days=7)

    stats = {cat: 0 for cat in JOY_CATEGORIES}
    for entry in log:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=TZ)
            if ts >= week_ago:
                cat = entry.get("category")
                if cat in stats:
                    stats[cat] += 1
        except:
            continue
    return stats


# === WRITING WORKSPACE (для идей/задач/заметок) ===
# Все задачи хранятся в Writing-space репо: life/tasks.md

def get_writing_file(filepath: str) -> str:
    """Получить файл из Writing-space репо."""
    if not GITHUB_TOKEN:
        logger.warning("No GITHUB_TOKEN for Writing repo")
        return ""
    try:
        logger.info(f"Reading {filepath} from {WRITING_REPO}")
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(WRITING_REPO)
        content = repo.get_contents(filepath)
        logger.info(f"Successfully read {filepath} ({len(content.decoded_content)} bytes)")
        return content.decoded_content.decode('utf-8')
    except Exception as e:
        logger.error(f"Writing repo read error for {filepath} from {WRITING_REPO}: {e}")
        return ""

def save_writing_file(filepath: str, new_content: str, message: str) -> bool:
    """Сохранить/обновить файл в Writing-space репо."""
    if not GITHUB_TOKEN:
        logger.warning("No GitHub token, cannot save to Writing repo")
        return False
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(WRITING_REPO)
        try:
            # Файл существует — обновляем
            content = repo.get_contents(filepath)
            repo.update_file(filepath, message, new_content, content.sha)
        except:
            # Файл не существует — создаём
            repo.create_file(filepath, message, new_content)
        logger.info(f"Saved {filepath} to Writing repo")
        return True
    except Exception as e:
        logger.error(f"Writing repo write error: {e}")
        return False

def get_life_tasks() -> str:
    """Получить задачи из life/tasks.md в Writing workspace."""
    content = get_writing_file("life/tasks.md")
    if not content:
        # Создадим файл с базовой структурой если не существует
        default_tasks = """# Задачи

## Фундамент
- [ ] ...

## Кайф
- [ ] ...

## Драйв
- [ ] ...

## Партнёрство
- [ ] ...

## Дети
- [ ] ...

## Финансы
- [ ] ...
"""
        save_writing_file("life/tasks.md", default_tasks, "Initialize tasks.md")
        return default_tasks
    return content

def add_task_to_zone(task: str, destination: str) -> bool:
    """Добавить задачу в зону или проект в life/tasks.md.

    destination может быть зоной (фундамент, драйв, ...) или проектом (geek-bot, ...).
    """
    tasks = get_life_tasks()

    # Маппинг зон на заголовки
    zone_headers = {
        "фундамент": "## Фундамент",
        "кайф": "## Кайф",
        "драйв": "## Драйв",
        "партнёрство": "## Партнёрство",
        "дети": "## Дети",
        "финансы": "## Финансы",
    }

    dest_lower = destination.lower()

    # Check if it's a project first
    if dest_lower in PROJECT_HEADERS:
        header = PROJECT_HEADERS[dest_lower]
    else:
        header = zone_headers.get(dest_lower, "## Драйв")

    if header in tasks:
        tasks = tasks.replace(header, f"{header}\n- [ ] {task}")
    else:
        tasks = f"{header}\n- [ ] {task}\n\n" + tasks

    return save_writing_file("life/tasks.md", tasks, f"Add task: {task[:30]}")


def complete_task(task_line: str) -> bool:
    """Отметить задачу как выполненную в life/tasks.md.

    Ищет точное совпадение строки '- [ ] {task_line}' и заменяет на
    '- [x] {task_line} ✅ YYYY-MM-DD'.
    """
    tasks = get_life_tasks()
    search = f"- [ ] {task_line}"

    if search not in tasks:
        logger.warning(f"Task not found for completion: {task_line[:50]}")
        return False

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    replacement = f"- [x] {task_line} ✅ {today}"
    tasks = tasks.replace(search, replacement, 1)  # Только первое вхождение

    return save_writing_file("life/tasks.md", tasks, f"Complete: {task_line[:30]}")


# Zone emojis for task adding
ZONE_EMOJI = {
    "фундамент": "🏠",
    "драйв": "🚀",
    "кайф": "✨",
    "партнёрство": "💑",
    "дети": "👶",
    "финансы": "💰",
}

# Project emojis and headers (sub-sections within zones)
PROJECT_EMOJI = {
    "geek-bot": "🤖",
    "therapy-bot": "💬",
    "neurotype-mismatch": "🔬",
    "openclaw": "🐾",
    "переезд": "✈️",
    "ifs-сертификация": "📜",
    "финучёт": "📊",
}

# Project -> header in tasks.md
PROJECT_HEADERS = {
    "geek-bot": "#### geek-bot",
    "therapy-bot": "#### therapy-bot",
    "neurotype-mismatch": "#### Исследование: Neurotype Mismatch",
    "openclaw": "#### OpenClaw",
    "переезд": "#### Переезд",
    "ifs-сертификация": "#### Сертификация IFS",
    "финучёт": "#### Финансовый учёт — допилить",
}

# Combined: zones + projects for display
ALL_DESTINATIONS = {**ZONE_EMOJI, **PROJECT_EMOJI}


async def suggest_zone_for_task(task: str) -> str:
    """Use LLM to suggest which zone or project a task belongs to."""
    prompt = f"""Определи, куда относится задача. Варианты:

Зоны:
- фундамент: базовые потребности (сон, еда, здоровье, гигиена, уборка)
- драйв: работа, проекты, развитие, обучение (общее)
- кайф: удовольствие, хобби, отдых, развлечения
- партнёрство: отношения с партнёром
- дети: всё связанное с детьми
- финансы: деньги, счета, покупки

Проекты (если задача явно про конкретный проект):
- geek-bot: личный Telegram бот-помощник
- therapy-bot: Telegram бот для клиентов-терапии
- neurotype-mismatch: исследование несовпадения нейротипов
- openclaw: open source проект
- переезд: визы, документы, переезд в другую страну
- ifs-сертификация: сертификация IFS терапевта
- финучёт: финансовый учёт, парсеры, скрипты обработки данных

Задача: {task}

Ответь ТОЛЬКО одним словом/фразой — названием зоны или проекта."""

    try:
        response = await get_llm_response(prompt, mode="geek", history=[])
        dest = response.strip().lower()
        # Direct match
        if dest in ALL_DESTINATIONS:
            return dest
        # Normalize ё→е for fuzzy match
        dest_norm = dest.replace("ё", "е")
        for d in ALL_DESTINATIONS.keys():
            d_norm = d.replace("ё", "е")
            if d_norm == dest_norm or d_norm in dest_norm or dest_norm in d_norm:
                return d
        return "драйв"  # Default
    except:
        return "драйв"


def get_task_confirm_keyboard(task_index: int, suggested: str) -> InlineKeyboardMarkup:
    """Keyboard for confirming task destination (zone or project)."""
    # First row: confirm suggestion
    emoji = ALL_DESTINATIONS.get(suggested, "📋")
    keyboard = [
        [InlineKeyboardButton(f"✅ {emoji} {suggested.capitalize()}", callback_data=f"taskzone_{task_index}_{suggested}")],
    ]

    # Zones row (excluding suggested)
    other_zones = [z for z in ZONE_EMOJI.keys() if z != suggested]
    row = []
    for zone in other_zones:
        e = ZONE_EMOJI[zone]
        row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{zone}"))
    keyboard.append(row)

    # Projects row (excluding suggested)
    other_projects = [p for p in PROJECT_EMOJI.keys() if p != suggested]
    row = []
    for proj in other_projects[:4]:  # max 4 per row
        e = PROJECT_EMOJI[proj]
        row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{proj}"))
    keyboard.append(row)
    if len(other_projects) > 4:
        row = []
        for proj in other_projects[4:]:
            e = PROJECT_EMOJI[proj]
            row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{proj}"))
        keyboard.append(row)

    # Skip button
    keyboard.append([InlineKeyboardButton("⏭ Пропустить", callback_data=f"taskzone_{task_index}_skip")])

    return InlineKeyboardMarkup(keyboard)


def get_destination_keyboard(callback_prefix: str = "adddest_") -> InlineKeyboardMarkup:
    """Keyboard for choosing zone or project as task destination.

    callback_prefix allows reuse for different flows (adddest_ for /add, taskzone_ for button Add).
    """
    keyboard = []

    # Row 1: main zones (3 per row)
    zones = list(ZONE_EMOJI.items())
    row = []
    for name, emoji in zones[:3]:
        row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"{callback_prefix}{name}"))
    keyboard.append(row)

    row = []
    for name, emoji in zones[3:]:
        row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"{callback_prefix}{name}"))
    keyboard.append(row)

    # Separator label
    keyboard.append([InlineKeyboardButton("— Проекты —", callback_data="noop")])

    # Projects (2 per row)
    projects = list(PROJECT_EMOJI.items())
    for i in range(0, len(projects), 2):
        row = []
        for name, emoji in projects[i:i+2]:
            short_name = name.replace("-", " ").capitalize()
            row.append(InlineKeyboardButton(f"{emoji} {short_name}", callback_data=f"{callback_prefix}{name}"))
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


def get_priority_keyboard(callback_prefix: str = "addpri_") -> InlineKeyboardMarkup:
    """Inline keyboard for priority selection."""
    keyboard = [
        [
            InlineKeyboardButton("Срочное ⏫", callback_data=f"{callback_prefix}high"),
            InlineKeyboardButton("Обычное 🔼", callback_data=f"{callback_prefix}medium"),
        ],
        [
            InlineKeyboardButton("Не срочное 🔽", callback_data=f"{callback_prefix}low"),
            InlineKeyboardButton("Без приоритета", callback_data=f"{callback_prefix}none"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def create_rawnote(title: str, content: str) -> bool:
    """Создать заметку в rawnotes/."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    # Создаём slug из заголовка
    slug = title.lower().replace(" ", "-")[:50]
    filename = f"rawnotes/{today}-{slug}.md"

    note_content = f"# {title}\n\n{content}"
    return save_writing_file(filename, note_content, f"Add note: {title[:30]}")


# === НАПОМИНАНИЯ ===

REMINDERS_FILE = "reminders.json"
FAMILY_FILE = "family.json"
MUTE_FILE = "mute_settings.json"

def get_mute_settings() -> dict:
    """Получить настройки mute из GitHub."""
    content = get_github_file(MUTE_FILE)
    if content and content != "Файл не найден.":
        try:
            return json.loads(content)
        except:
            pass
    return {}

def save_mute_settings(settings: dict) -> bool:
    """Сохранить настройки mute в GitHub."""
    content = json.dumps(settings, ensure_ascii=False, indent=2)
    return update_github_file(MUTE_FILE, content, "Update mute settings")

def is_muted(chat_id: int) -> bool:
    """Проверить, включен ли mute для пользователя."""
    settings = get_mute_settings()
    user_settings = settings.get(str(chat_id), {})

    if not user_settings.get("muted", False):
        return False

    # Проверяем, не истёк ли временный mute
    until = user_settings.get("until")
    if until:
        until_dt = datetime.fromisoformat(until)
        if datetime.now(TZ) > until_dt:
            # Mute истёк — снимаем
            user_settings["muted"] = False
            user_settings.pop("until", None)
            settings[str(chat_id)] = user_settings
            save_mute_settings(settings)
            return False

    return True

def set_mute(chat_id: int, muted: bool, until: datetime = None) -> bool:
    """Установить статус mute для пользователя."""
    settings = get_mute_settings()
    user_settings = settings.get(str(chat_id), {})

    user_settings["muted"] = muted
    if until:
        user_settings["until"] = until.isoformat()
    elif "until" in user_settings:
        del user_settings["until"]

    settings[str(chat_id)] = user_settings
    return save_mute_settings(settings)


def get_family() -> dict:
    """Получить список семьи (username -> chat_id)."""
    content = get_github_file(FAMILY_FILE)
    if content and content != "Файл не найден.":
        try:
            return json.loads(content)
        except:
            pass
    return {}

def save_family(family: dict) -> bool:
    """Сохранить список семьи."""
    content = json.dumps(family, ensure_ascii=False, indent=2)
    return update_github_file(FAMILY_FILE, content, "Update family")

def register_family_member(username: str, chat_id: int) -> bool:
    """Зарегистрировать члена семьи."""
    if not username:
        return False
    family = get_family()
    family[username.lower().lstrip('@')] = chat_id
    return save_family(family)

def get_family_chat_id(username: str) -> int | None:
    """Получить chat_id по username."""
    family = get_family()
    return family.get(username.lower().lstrip('@'))


def get_reminders() -> list:
    """Получить напоминания из GitHub."""
    content = get_github_file(REMINDERS_FILE)
    if content and content != "Файл не найден.":
        try:
            return json.loads(content)
        except:
            pass
    return []

def save_reminders(reminders: list) -> bool:
    """Сохранить напоминания в GitHub."""
    content = json.dumps(reminders, ensure_ascii=False, indent=2)
    return update_github_file(REMINDERS_FILE, content, "Update reminders")

def add_reminder(chat_id: int, remind_at: datetime, text: str, from_user: str = None) -> bool:
    """Добавить напоминание."""
    reminders = get_reminders()
    reminder = {
        "chat_id": chat_id,
        "remind_at": remind_at.isoformat(),
        "text": text,
        "created_at": datetime.now(TZ).isoformat(),
    }
    if from_user:
        reminder["from_user"] = from_user
    reminders.append(reminder)
    return save_reminders(reminders)

def get_due_reminders() -> list:
    """Получить напоминания, которые пора отправить."""
    reminders = get_reminders()
    now = datetime.now(TZ)
    due = []
    remaining = []

    for r in reminders:
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at <= now:
            due.append(r)
        else:
            remaining.append(r)

    if due:
        save_reminders(remaining)

    return due

def parse_remind_time(text: str) -> tuple:
    """Парсит время напоминания из текста.
    Возвращает (datetime, оставшийся текст) или (None, None)

    Форматы:
    - "через 30 минут" / "через 2 часа" / "через 3 дня"
    - "завтра" / "послезавтра"
    - "в 15:00" / "в 9:30"
    - "25.02" / "25.02.2026" (дата)
    - "через месяц" / "через неделю"
    """
    now = datetime.now(TZ)
    text_lower = text.lower().strip()

    # "через X минут/часов/дней/недель/месяцев"
    match = re.match(r'через\s+(\d+)\s+(минут|мин|час|часа|часов|день|дня|дней|недел|месяц|месяца|месяцев)', text_lower)
    if match:
        num = int(match.group(1))
        unit = match.group(2)
        remaining = text[match.end():].strip()

        if unit.startswith('мин'):
            delta = timedelta(minutes=num)
        elif unit.startswith('час'):
            delta = timedelta(hours=num)
        elif unit.startswith('ден') or unit.startswith('дн'):
            delta = timedelta(days=num)
        elif unit.startswith('недел'):
            delta = timedelta(weeks=num)
        elif unit.startswith('месяц'):
            delta = timedelta(days=num * 30)
        else:
            return (None, None)

        return (now + delta, remaining)

    # "через месяц" / "через неделю" (без числа)
    if text_lower.startswith('через месяц'):
        return (now + timedelta(days=30), text[len('через месяц'):].strip())
    if text_lower.startswith('через неделю'):
        return (now + timedelta(weeks=1), text[len('через неделю'):].strip())

    # "завтра" / "послезавтра"
    if text_lower.startswith('завтра'):
        tomorrow = now + timedelta(days=1)
        # Ставим на 10:00 по умолчанию
        remind_at = tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
        return (remind_at, text[len('завтра'):].strip())

    if text_lower.startswith('послезавтра'):
        day_after = now + timedelta(days=2)
        remind_at = day_after.replace(hour=10, minute=0, second=0, microsecond=0)
        return (remind_at, text[len('послезавтра'):].strip())

    # "в 15:00" или "в 9:30"
    match = re.match(r'в\s+(\d{1,2}):(\d{2})', text_lower)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2))
        remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_at <= now:
            remind_at += timedelta(days=1)
        return (remind_at, text[match.end():].strip())

    # "25.02" или "25.02.2026"
    match = re.match(r'(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?', text_lower)
    if match:
        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else now.year
        try:
            remind_at = datetime(year, month, day, 10, 0, 0, tzinfo=TZ)
            if remind_at <= now and not match.group(3):
                remind_at = remind_at.replace(year=now.year + 1)
            return (remind_at, text[match.end():].strip())
        except:
            pass

    return (None, None)


# === GOOGLE CALENDAR ===

CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def get_calendar_service():
    """Получить сервис Google Calendar."""
    creds = None

    # Из переменной окружения (для Railway)
    token_json_env = os.environ.get('GOOGLE_TOKEN_JSON')
    if token_json_env:
        token_data = base64.b64decode(token_json_env).decode('utf-8')
        creds = Credentials.from_authorized_user_info(json.loads(token_data), SCOPES)
    elif os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds:
        logger.warning("No Google Calendar credentials found")
        return None

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logger.info("Google token refreshed")
        else:
            logger.warning("Google credentials invalid and cannot refresh")
            return None

    return build('calendar', 'v3', credentials=creds)

def get_week_events() -> str:
    """Получить события на неделю, сгруппированные по дням с маркерами Сегодня/Завтра."""
    WEEKDAYS_RU = {
        0: "понедельник", 1: "вторник", 2: "среда",
        3: "четверг", 4: "пятница", 5: "суббота", 6: "воскресенье"
    }
    MONTHS_RU = {
        1: "января", 2: "февраля", 3: "марта", 4: "апреля",
        5: "мая", 6: "июня", 7: "июля", 8: "августа",
        9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
    }

    try:
        service = get_calendar_service()
        if not service:
            return "Календарь не подключен."

        now = datetime.now(timezone.utc)
        week_later = now + timedelta(days=7)

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now.isoformat(),
            timeMax=week_later.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        if not events:
            return "На этой неделе нет событий в календаре."

        today = datetime.now(TZ).date()
        tomorrow = today + timedelta(days=1)

        # Группируем по дням (ключ — date object для корректной сортировки)
        days = {}
        for event in events:
            start_raw = event['start'].get('dateTime', event['start'].get('date'))

            if 'T' in start_raw:
                # Timed event — конвертируем в локальное время
                dt = datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
                local_dt = dt.astimezone(TZ)
                day_date = local_dt.date()
                time_str = local_dt.strftime('%H:%M')
            else:
                # All-day event — дата как есть, без конвертации
                day_date = datetime.strptime(start_raw, '%Y-%m-%d').date()
                time_str = ""

            if day_date not in days:
                days[day_date] = []

            summary = event.get('summary', 'Без названия')
            if time_str:
                days[day_date].append(f"  {time_str} — {summary}")
            else:
                days[day_date].append(f"  (весь день) {summary}")

        # Формируем текст с маркерами
        result = []
        for day_date in sorted(days.keys()):
            items = days[day_date]
            weekday = WEEKDAYS_RU[day_date.weekday()]
            date_str = f"{day_date.day} {MONTHS_RU[day_date.month]}"

            if day_date == today:
                header = f"СЕГОДНЯ, {date_str} ({weekday})"
            elif day_date == tomorrow:
                header = f"ЗАВТРА, {date_str} ({weekday})"
            else:
                header = f"{date_str} ({weekday})"

            result.append(f"\n{header}:")
            result.extend(items)

        return "\n".join(result)

    except Exception as e:
        logger.error(f"Calendar error: {e}")
        return f"Ошибка календаря: {e}"

def load_file(filepath: str, default: str = "") -> str:
    """Загрузить файл или вернуть default."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return default


# === НАПОМИНАНИЯ ===

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


# === SLEEP PROTOCOL: трёхуровневая эскалация ===

# Промпты для LLM-генерации напоминаний по уровням
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


def get_sleep_level() -> int:
    """Определить уровень напоминания о сне по текущему времени.

    Returns:
        0 — не время для напоминаний
        1 — мягкое (1:00-1:29)
        2 — настойчивое (1:30-1:59)
        3 — директива (2:00-5:59)
    """
    now = datetime.now(TZ)
    hour = now.hour
    minute = now.minute

    if hour == 1 and minute < 30:
        return 1
    elif hour == 1 and minute >= 30:
        return 2
    elif 2 <= hour < 6:
        return 3
    return 0


# === LLM API ===

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

async def get_llm_response(user_message: str, mode: str = "geek", history: list = None, max_tokens: int = 800, skip_context: bool = False, custom_system: str = None) -> str:
    """Получить ответ от LLM. Gemini primary, OpenAI fallback.

    skip_context=True — не грузить tasks/whoop в system prompt (для команд где контекст уже в user_message).
    custom_system — полностью заменяет system prompt (для специализированных режимов вроде sensory).
    """
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    if custom_system:
        system = custom_system
    else:
        if skip_context:
            tasks = ""
            whoop_data = ""
        else:
            tasks = get_life_tasks()
            whoop_data = _get_whoop_context()

        if mode == "leya":
            user_context = load_file(LEYA_CONTEXT_FILE, "Контекст не загружен.")
            system = LEYA_PROMPT.format(user_context=user_context, current_time=current_time, tasks=tasks, whoop_data=whoop_data)
        else:
            user_context = load_file(USER_CONTEXT_FILE, "Профиль не настроен.")
            system = GEEK_PROMPT.format(user_context=user_context, current_time=current_time, tasks=tasks, whoop_data=whoop_data)

    # Собираем контекст диалога
    if history is None:
        history = []

    # Try Gemini first
    if gemini_client:
        try:
            # Gemini: передаём историю как список сообщений
            gemini_contents = []
            for msg in history:
                gemini_contents.append(genai.types.Content(
                    role="user" if msg["role"] == "user" else "model",
                    parts=[genai.types.Part(text=msg["content"])]
                ))
            gemini_contents.append(genai.types.Content(
                role="user",
                parts=[genai.types.Part(text=user_message)]
            ))

            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=gemini_contents,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                ),
            )
            if response.text:
                return response.text
            else:
                logger.warning(f"Gemini returned empty response, falling back to OpenAI")
        except Exception as e:
            logger.warning(f"Gemini API error, falling back to OpenAI: {e}")

    # Fallback to OpenAI
    if openai_client:
        try:
            # OpenAI: system + история + текущее сообщение
            messages = [{"role": "system", "content": system}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": user_message})

            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=max_tokens,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")

    return "Оба API недоступны. Попробуй позже."


# === КОМАНДЫ ===

def get_main_keyboard(mode: str = "geek"):
    """Главная клавиатура."""
    keyboard = [
        [
            InlineKeyboardButton("Geek" if mode != "geek" else "* Geek *", callback_data="mode_geek"),
            InlineKeyboardButton("Лея" if mode != "leya" else "* Лея *", callback_data="mode_leya"),
        ],
        [
            InlineKeyboardButton("Todo", callback_data="todo"),
            InlineKeyboardButton("Неделя", callback_data="week"),
            InlineKeyboardButton("Шаги", callback_data="next_steps"),
        ],
        [
            InlineKeyboardButton("Сон", callback_data="sleep"),
            InlineKeyboardButton("Еда", callback_data="food"),
            InlineKeyboardButton("Спорт", callback_data="sport"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_reply_keyboard():
    """Постоянная клавиатура внизу чата."""
    keyboard = [
        [KeyboardButton("🔥 Dashboard"), KeyboardButton("📋 Todo"), KeyboardButton("🎯 Steps")],
        [KeyboardButton("📅 Week"), KeyboardButton("🧘 Sensory"), KeyboardButton("✨ Joy")],
        [KeyboardButton("➕ Add")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_add_keyboard():
    """Inline keyboard для выбора: Task или Note."""
    keyboard = [[
        InlineKeyboardButton("📋 Task", callback_data="add_task"),
        InlineKeyboardButton("📝 Note", callback_data="add_note"),
    ]]
    return InlineKeyboardMarkup(keyboard)


def get_note_mode_keyboard():
    """Inline keyboard для режима заметки."""
    keyboard = [[
        InlineKeyboardButton("✅ Готово", callback_data="note_done"),
        InlineKeyboardButton("❌ Отмена", callback_data="note_cancel"),
    ]]
    return InlineKeyboardMarkup(keyboard)


def get_sensory_keyboard():
    """Inline keyboard for sensory state selection."""
    keyboard = [
        [
            InlineKeyboardButton("🔴 Хочу орать", callback_data="sensory_emergency"),
            InlineKeyboardButton("🟡 Залипла", callback_data="sensory_unfreeze"),
        ],
        [
            InlineKeyboardButton("🟢 Inputs", callback_data="sensory_inputs"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_joy_keyboard():
    """Inline keyboard for joy category selection."""
    keyboard = [
        [
            InlineKeyboardButton("🧘 Sensory", callback_data="joy_cat_sensory"),
            InlineKeyboardButton("🎨 Creativity", callback_data="joy_cat_creativity"),
        ],
        [
            InlineKeyboardButton("📺 Media", callback_data="joy_cat_media"),
            InlineKeyboardButton("💚 Connection", callback_data="joy_cat_connection"),
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="joy_stats"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_joy_items_keyboard(category: str) -> InlineKeyboardMarkup:
    """Inline keyboard with specific items for a joy category."""
    menu = _parse_sensory_menu()
    emoji = JOY_CATEGORY_EMOJI.get(category, "✨")

    # Map joy categories to sensory menu keys
    category_map = {
        "sensory": ["inputs", "emergency", "unfreeze"],  # Combine all sensory
        "creativity": ["creativity"],
        "media": ["media"],
        "connection": ["connection"]
    }

    items = []
    for key in category_map.get(category, []):
        items.extend(menu.get(key, []))

    # Create buttons - max 2 per row, truncate long items
    keyboard = []
    row = []
    for i, item in enumerate(items[:10]):  # Limit to 10 items
        # Truncate item name for button (max ~25 chars)
        short_item = item[:22] + "..." if len(item) > 25 else item
        # callback_data max 64 bytes, use index
        callback = f"joyitem_{category}_{i}"
        row.append(InlineKeyboardButton(short_item, callback_data=callback))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Add "Другое" button and back button
    keyboard.append([
        InlineKeyboardButton("✏️ Другое", callback_data=f"joyother_{category}"),
        InlineKeyboardButton("◀️ Назад", callback_data="joy_back"),
    ])

    return InlineKeyboardMarkup(keyboard)


# Cache for joy items (to retrieve by index in callbacks)
_joy_items_cache = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    context.user_data.setdefault("mode", "geek")
    mode = context.user_data["mode"]

    # Автоматическая регистрация для напоминаний
    user = update.effective_user
    if user and user.username:
        chat_id = update.effective_chat.id
        register_family_member(user.username, chat_id)
        logger.info(f"Registered family member: @{user.username} -> {chat_id}")

    await update.message.reply_text(
        f"Online. Режим: {mode.upper()}",
        reply_markup=get_reply_keyboard()
    )


async def switch_to_geek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Geek."""
    context.user_data["mode"] = "geek"
    await update.message.reply_text(
        "Geek online. Что случилось.",
        reply_markup=get_reply_keyboard()
    )


async def switch_to_leya(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Лея."""
    context.user_data["mode"] = "leya"
    await update.message.reply_text(
        "Привет. Это Лея.\n\nЯ здесь, чтобы помочь тебе не потерять важное среди срочного.",
        reply_markup=get_reply_keyboard()
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    import random

    if data == "noop":
        # Separator button, do nothing
        return

    if data == "mode_geek":
        context.user_data["mode"] = "geek"
        await query.edit_message_text(
            "Geek online. Что случилось.",
            reply_markup=get_main_keyboard("geek")
        )

    elif data == "mode_leya":
        context.user_data["mode"] = "leya"
        await query.edit_message_text(
            "Привет. Это Лея.\n\nЧто сейчас важно?",
            reply_markup=get_main_keyboard("leya")
        )

    elif data == "todo":
        tasks = load_file(TASKS_FILE, "Задачи пока не добавлены.")
        calendar = get_week_events()
        current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

        prompt = f"""Сделай краткий обзор на сегодня и ближайшую неделю.

## Задачи из списка:
{tasks}

## Календарь на неделю:
{calendar}

Сегодня: {current_time}

Выдели:
1. Что в календаре сегодня и завтра
2. Насколько загружена неделя
3. Какие задачи стоит сделать

Будь краткой."""

        response = await get_llm_response(prompt, mode="leya")
        await query.message.reply_text(response)

    elif data == "week":
        calendar = get_week_events()
        await query.message.reply_text(f"Календарь на неделю:\n{calendar}")

    elif data == "status":
        now = datetime.now(TZ)
        hour = now.hour
        mode = context.user_data.get("mode", "geek")

        if hour >= 1 and hour < 7:
            msg = f"{now.strftime('%H:%M')}. Ты должна спать."
        elif hour >= 7 and hour < 12:
            msg = f"{now.strftime('%H:%M')}. Утро. Завтракала?"
        elif hour >= 12 and hour < 14:
            msg = f"{now.strftime('%H:%M')}. Время обеда."
        elif hour >= 14 and hour < 19:
            msg = f"{now.strftime('%H:%M')}. Рабочее время."
        elif hour >= 19 and hour < 22:
            msg = f"{now.strftime('%H:%M')}. Вечер. Ужинала?"
        else:
            msg = f"{now.strftime('%H:%M')}. Скоро спать."

        msg += f"\nРежим: {mode.upper()}"
        await query.edit_message_text(msg, reply_markup=get_main_keyboard(mode))

    elif data == "sleep":
        level = get_sleep_level() or 1
        msg = random.choice(REMINDERS["sleep"][level])
        await query.message.reply_text(msg)

    elif data == "food":
        menu = generate_weekly_menu()
        await query.message.reply_text(menu, parse_mode="HTML")

    elif data == "sport":
        msg = random.choice(REMINDERS["sport"])
        await query.message.reply_text(msg)

    elif data == "next_steps":
        tasks = get_life_tasks()
        mode = context.user_data.get("mode", "geek")

        prompt = f"""Посмотри на задачи из раздела Проекты и Драйв.

Какие конкретные маленькие шаги (15-30 минут) можно добавить в Драйв на этой неделе?

Предложи 2-3 первых шага. Формат ответа:
1. Краткое описание шага (время)
2. Краткое описание шага (время)
3. Краткое описание шага (время)

НЕ добавляй теги SAVE — просто опиши шаги.

Задачи:
{tasks}"""

        response = await get_llm_response(prompt, mode=mode)

        # Извлекаем шаги и создаём кнопки для каждого
        lines = [l.strip() for l in response.split('\n') if l.strip() and l.strip()[0].isdigit()]
        if lines:
            # Сохраняем шаги для кнопок
            context.user_data["pending_steps"] = lines[:3]

            keyboard = []
            for i, step in enumerate(lines[:3]):
                # Убираем номер из начала
                clean_step = re.sub(r'^\d+[\.\)]\s*', '', step)
                keyboard.append([InlineKeyboardButton(f"+ {clean_step[:40]}...", callback_data=f"add_step_{i}")])
            keyboard.append([InlineKeyboardButton("Не добавлять", callback_data="cancel_steps")])

            await query.message.reply_text(
                response + "\n\n— Какие шаги добавить в Драйв?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.reply_text(response)

    # === Add menu ===
    elif data == "add_task":
        context.user_data["add_mode"] = True
        await query.edit_message_text("Напиши задачу или список задач (каждая с новой строки).")

    elif data == "add_note":
        context.user_data["note_mode"] = True
        context.user_data["note_buffer"] = []
        await query.edit_message_text(
            "Режим заметки. Пересылай сообщения или пиши текст.\n"
            "Когда закончишь — нажми Готово.",
        )
        await query.message.reply_text("Жду сообщений.", reply_markup=get_note_mode_keyboard())

    # === Note mode ===
    elif data == "note_cancel":
        context.user_data.pop("note_mode", None)
        context.user_data.pop("note_buffer", None)
        await query.edit_message_text("Заметка отменена.")

    elif data == "note_done":
        buffer = context.user_data.get("note_buffer", [])
        context.user_data.pop("note_mode", None)

        if not buffer:
            context.user_data.pop("note_buffer", None)
            await query.edit_message_text("Буфер пустой, нечего сохранять.")
            return

        raw_text = "\n\n".join(buffer)
        await query.edit_message_text("Собираю заметку...")

        # LLM собирает чистую заметку из буфера
        note_prompt = f"""Из пересланных сообщений ниже создай структурированную заметку.

Правила:
- Первая строка: короткий заголовок (без #, без кавычек)
- Дальше: содержание заметки, объединяя фрагменты в связный текст
- Убери дубли, оставь суть
- Язык: такой же как в сообщениях
- Не добавляй ничего от себя, только переструктурируй

Сообщения:
{raw_text}"""

        result = await get_llm_response(
            note_prompt, mode="leya", skip_context=True,
            custom_system="Ты помощник для создания заметок. Ответь только заметкой, ничего лишнего."
        )

        # Парсим: первая строка = заголовок, остальное = тело
        lines = result.strip().split("\n", 1)
        title = lines[0].lstrip("# ").strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        context.user_data.pop("note_buffer", None)
        if create_rawnote(title, body):
            await query.message.reply_text(f"Заметка сохранена: {title}")
        else:
            await query.message.reply_text("Ошибка сохранения. Попробуй позже.")

    # === Обработка сохранения задач/заметок ===
    elif data == "save_confirm":
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        if pending["type"] == "task":
            # Показываем кнопки приоритета перед сохранением
            keyboard = [
                [
                    InlineKeyboardButton("Срочное ⏫", callback_data="savepri_high"),
                    InlineKeyboardButton("Обычное 🔼", callback_data="savepri_medium"),
                ],
                [
                    InlineKeyboardButton("Не срочное 🔽", callback_data="savepri_low"),
                    InlineKeyboardButton("Без приоритета", callback_data="savepri_none"),
                ],
            ]
            await query.edit_message_text(
                f"Задача: {pending['content']}\nЗона: {pending['zone_or_title']}\n\nВыбери приоритет:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:  # note
            success = create_rawnote(pending["zone_or_title"], pending["content"])
            if success:
                await query.edit_message_text(
                    query.message.text.split("\n\n—")[0] +
                    f"\n\n✓ Заметка «{pending['zone_or_title']}» создана"
                )
            else:
                await query.edit_message_text(
                    query.message.text.split("\n\n—")[0] +
                    "\n\n✗ Не удалось сохранить."
                )
            context.user_data.pop("pending_save", None)

    elif data == "save_cancel":
        context.user_data.pop("pending_save", None)
        # Убираем кнопки и предложение
        original_text = query.message.text.split("\n\n—")[0]
        await query.edit_message_text(original_text)

    elif data.startswith("savepri_"):
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        priority = data.replace("savepri_", "")
        priority_map = {"high": " ⏫", "medium": " 🔼", "low": " 🔽", "none": ""}
        task_with_priority = pending["content"] + priority_map.get(priority, "")
        zone = pending["zone_or_title"]

        success = add_task_to_zone(task_with_priority, zone)
        if success:
            await query.edit_message_text(f"✓ Задача добавлена в «{zone}»:\n{task_with_priority}")
        else:
            await query.edit_message_text("✗ Не удалось сохранить. Проверь GitHub токен.")

        context.user_data.pop("pending_save", None)

    elif data == "save_change_zone":
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        # Показываем зоны + проекты
        keyboard = []
        zones = list(ZONE_EMOJI.items())
        for i in range(0, len(zones), 2):
            row = []
            for name, emoji in zones[i:i+2]:
                row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"zone_{name}"))
            keyboard.append(row)

        # Projects
        projects = list(PROJECT_EMOJI.items())
        for i in range(0, len(projects), 2):
            row = []
            for name, emoji in projects[i:i+2]:
                short = name.replace("-", " ").capitalize()
                row.append(InlineKeyboardButton(f"{emoji} {short}", callback_data=f"zone_{name}"))
            keyboard.append(row)

        keyboard.append([InlineKeyboardButton("Отмена", callback_data="save_cancel")])

        await query.edit_message_text(
            f"Задача: {pending['content']}\n\nВыбери зону или проект:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("zone_"):
        zone = data.replace("zone_", "")
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        pending["zone_or_title"] = zone
        # Показываем кнопки приоритета
        keyboard = [
            [
                InlineKeyboardButton("Срочное ⏫", callback_data="savepri_high"),
                InlineKeyboardButton("Обычное 🔼", callback_data="savepri_medium"),
            ],
            [
                InlineKeyboardButton("Не срочное 🔽", callback_data="savepri_low"),
                InlineKeyboardButton("Без приоритета", callback_data="savepri_none"),
            ],
        ]
        await query.edit_message_text(
            f"Задача: {pending['content']}\nЗона: {zone}\n\nВыбери приоритет:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("sensory_"):
        state = data.replace("sensory_", "")
        menu = _parse_sensory_menu()

        state_descriptions = {
            "emergency": "Хочу орать — перегрузка, нужна down-regulation",
            "unfreeze": "Залипла — гипоактивация, заморозка, нужна up-regulation",
            "inputs": "Inputs — профилактика, сенсорная диета"
        }
        state_desc = state_descriptions.get(state, state)
        menu_text = _format_sensory_menu_for_prompt(menu)
        current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

        try:
            system = SENSORY_LEYA_PROMPT.format(
                sensory_menu=menu_text,
                current_time=current_time
            )
            prompt = f"Human нажала кнопку Sensory и выбрала: {state_desc}"
            response = await get_llm_response(prompt, max_tokens=600, custom_system=system)
            if "API недоступны" in response:
                response = _sensory_hardcoded_response(state, menu)
        except Exception as e:
            logger.warning(f"Sensory LLM failed, using hardcoded fallback: {e}")
            response = _sensory_hardcoded_response(state, menu)

        await query.edit_message_text(response, parse_mode="Markdown")

    elif data.startswith("joy_"):
        action = data.replace("joy_", "")

        if action == "stats":
            # Show detailed weekly stats
            stats = get_joy_stats_week()
            total = sum(stats.values())
            msg = "📊 **Joy за последние 7 дней:**\n\n"
            for cat in JOY_CATEGORIES:
                emoji = JOY_CATEGORY_EMOJI.get(cat, "")
                count = stats.get(cat, 0)
                bar = "█" * count + "░" * (7 - count) if count <= 7 else "█" * 7 + f"+{count-7}"
                msg += f"{emoji} {cat.capitalize()}: {bar} ({count}x)\n"
            msg += f"\n**Всего:** {total} отметок"

            if total == 0:
                msg += "\n\n_Ни одной отметки за неделю. Сенсорная диета — это maintenance, не опция._"
            elif total < 7:
                msg += "\n\n_Меньше раза в день. Можно лучше._"

            await query.edit_message_text(msg, parse_mode="Markdown")

        elif action == "back":
            # Return to main joy menu
            await query.edit_message_text("Что было?", reply_markup=get_joy_keyboard())

        elif action.startswith("cat_"):
            # Category selected - show items
            category = action.replace("cat_", "")
            if category in JOY_CATEGORIES:
                emoji = JOY_CATEGORY_EMOJI.get(category, "✨")
                # Cache items for this category
                menu = _parse_sensory_menu()
                category_map = {
                    "sensory": ["inputs", "emergency", "unfreeze"],
                    "creativity": ["creativity"],
                    "media": ["media"],
                    "connection": ["connection"]
                }
                items = []
                for key in category_map.get(category, []):
                    items.extend(menu.get(key, []))
                _joy_items_cache[category] = items

                await query.edit_message_text(
                    f"{emoji} **{category.capitalize()}**\n\nЧто именно?",
                    reply_markup=get_joy_items_keyboard(category),
                    parse_mode="Markdown"
                )

    elif data.startswith("joyitem_"):
        # Specific item selected: joyitem_category_index
        parts = data.split("_")
        if len(parts) >= 3:
            category = parts[1]
            try:
                idx = int(parts[2])
                items = _joy_items_cache.get(category, [])
                if idx < len(items):
                    item = items[idx]
                    success = log_joy(category, item)
                    emoji = JOY_CATEGORY_EMOJI.get(category, "✨")
                    if success:
                        # Truncate item for display if too long
                        display_item = item[:30] + "..." if len(item) > 33 else item
                        await query.edit_message_text(
                            f"{emoji} **{display_item}**\n\n_Записано._",
                            parse_mode="Markdown"
                        )
                    else:
                        await query.edit_message_text("Не удалось сохранить.")
                else:
                    await query.edit_message_text("Элемент не найден.")
            except ValueError:
                await query.edit_message_text("Ошибка.")

    elif data.startswith("joyother_"):
        # "Другое" selected - ask for free text input
        category = data.replace("joyother_", "")
        if category in JOY_CATEGORIES:
            emoji = JOY_CATEGORY_EMOJI.get(category, "✨")
            context.user_data["joy_pending_category"] = category
            await query.edit_message_text(
                f"{emoji} **{category.capitalize()}** — напиши что именно:",
                parse_mode="Markdown"
            )

    elif data.startswith("batchpri_"):
        # Batch priority selected — apply to all pending tasks, start zone picking
        tasks = context.user_data.get("pending_batch_tasks", [])
        if not tasks:
            await query.edit_message_text("Нечего добавлять.")
            return

        priority = data.replace("batchpri_", "")
        priority_map = {"high": " ⏫", "medium": " 🔼", "low": " 🔽", "none": ""}
        suffix = priority_map.get(priority, "")

        # Apply priority to all tasks
        tasks_with_pri = [t + suffix for t in tasks]
        context.user_data["pending_tasks"] = tasks_with_pri
        context.user_data.pop("pending_batch_tasks", None)

        await query.edit_message_text(f"Приоритет применён к {len(tasks_with_pri)} задачам.")

        # Start zone/project picking for first task
        task = tasks_with_pri[0]
        suggested = await suggest_zone_for_task(task)
        emoji = ALL_DESTINATIONS.get(suggested, "📋")

        remaining = len(tasks_with_pri) - 1
        remaining_text = f"\n\n_Осталось: {remaining}_" if remaining > 0 else ""

        await query.message.reply_text(
            f"**Задача:** {task}\n\nПредлагаю: {emoji} **{suggested.capitalize()}**{remaining_text}",
            reply_markup=get_task_confirm_keyboard(0, suggested),
            parse_mode="Markdown"
        )

    elif data.startswith("taskzone_"):
        # Handle task zone/project confirmation: taskzone_0_драйв or taskzone_0_geek-bot
        parts = data.split("_")
        if len(parts) >= 3:
            task_idx = int(parts[1])
            destination = "_".join(parts[2:])  # zone or project name

            pending_tasks = context.user_data.get("pending_tasks", [])
            added_tasks = context.user_data.get("pending_tasks_added", [])

            if task_idx < len(pending_tasks):
                task = pending_tasks[task_idx]

                if destination == "skip":
                    await query.edit_message_text(f"⏭ Пропущено: {task}")
                else:
                    if add_task_to_zone(task, destination):
                        emoji = ALL_DESTINATIONS.get(destination, "📋")
                        added_tasks.append(f"{emoji} {task}")
                        context.user_data["pending_tasks_added"] = added_tasks
                        await query.edit_message_text(f"✅ {emoji} {task} → {destination.capitalize()}")
                    else:
                        await query.edit_message_text(f"❌ Не удалось добавить: {task}")

                # Process next task
                next_idx = task_idx + 1
                if next_idx < len(pending_tasks):
                    next_task = pending_tasks[next_idx]
                    suggested = await suggest_zone_for_task(next_task)
                    emoji = ALL_DESTINATIONS.get(suggested, "📋")

                    remaining = len(pending_tasks) - next_idx - 1
                    remaining_text = f"\n\n_Осталось: {remaining}_" if remaining > 0 else ""

                    await query.message.reply_text(
                        f"**Задача:** {next_task}\n\nПредлагаю: {emoji} **{suggested.capitalize()}**{remaining_text}",
                        reply_markup=get_task_confirm_keyboard(next_idx, suggested),
                        parse_mode="Markdown"
                    )
                else:
                    # All tasks processed
                    context.user_data.pop("pending_tasks", None)
                    added = context.user_data.pop("pending_tasks_added", [])

                    if added:
                        msg = f"**Добавлено ({len(added)}):**\n" + "\n".join(f"• {t}" for t in added)
                    else:
                        msg = "Ни одной задачи не добавлено."

                    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_reply_keyboard())

    elif data.startswith("feeling_"):
        feeling = data.replace("feeling_", "")
        joy_stats = get_joy_stats_week()
        joy_total = sum(joy_stats.values())

        # Generate recommendation based on feeling
        recommendations = {
            "energized": "Отлично. Можно брать драйв-задачи. Но не забывай про maintenance — сенсорная диета нужна и в хорошие дни.",
            "ok": "Нормально — рабочий режим. Баланс между драйвом и восстановлением.",
            "tired": "Вымотана значит — приоритет восстановлению. Меньше драйва, больше sensory и connection. Это не опция, это maintenance.",
            "low": "На дне. Режим выживания. Только фундамент: сон, еда, deep pressure. Драйв подождёт. Ты важнее любых задач."
        }

        rec = recommendations.get(feeling, "")

        # Add Joy-based suggestions
        if joy_stats.get("sensory", 0) < 3:
            rec += "\n\n🧘 Sensory был редко. Добавь в каждый день."
        if joy_stats.get("connection", 0) == 0:
            rec += "\n\n💚 Connection = 0. Запланируй время с близкими."

        feeling_emoji = {"energized": "💪", "ok": "😌", "tired": "😴", "low": "🫠"}
        emoji = feeling_emoji.get(feeling, "")

        await query.edit_message_text(
            f"{emoji} Понял.\n\n{rec}",
            parse_mode="Markdown"
        )

    elif data.startswith("morning_"):
        # Handle morning WHOOP feeling buttons
        feeling = data.replace("morning_", "")  # great, ok, tired, bad

        # Get stored morning data
        morning_data = context.bot_data.get(f"morning_{query.message.chat.id}", {})
        sleep_hours = morning_data.get("sleep_hours", 0)
        strain = morning_data.get("strain", 0)
        recovery = morning_data.get("recovery", 0)
        trend = morning_data.get("trend", "stable")
        prev_avg = morning_data.get("prev_avg")

        # Determine mode based on data + feeling
        feeling_bad = feeling in ["tired", "bad"]
        trend_down = trend == "down"

        if recovery < 34 or (trend_down and feeling_bad):
            mode = "recovery"
        elif recovery < 50 or trend_down:
            mode = "moderate"
        else:
            mode = "normal"

        # Get motivations for mode
        motivations = get_motivations_for_mode(mode, sleep_hours, strain, recovery)

        # Build data summary for LLM
        color = "green" if recovery >= 67 else ("yellow" if recovery >= 34 else "red")
        data_summary = f"""Recovery: {recovery}% ({color})
Сон: {sleep_hours}h
Strain вчера: {strain}
Тренд: {trend} ({prev_avg}% → {recovery}%)"""

        feeling_text = {
            "great": "отлично",
            "ok": "норм",
            "tired": "устала",
            "bad": "плохо"
        }.get(feeling, feeling)

        prompt = f"""Данные WHOOP:
{data_summary}

Human ответила "как себя чувствуешь?": "{feeling_text}".

Ты — Geek (ART из Murderbot Diaries). Дай мотивацию на день.

ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ 1-2 из этих фраз (подставь реальные числа):
{motivations}

СТРОГИЕ ПРАВИЛА:
- Цвет зоны recovery: green (67-100%), yellow (34-66%), red (0-33%). Используй ТОЛЬКО цвет из данных: {color}. НЕ ВЫДУМЫВАЙ другой цвет
- Не пересказывай данные целиком — выдели главное
- Режим "{mode}" — {"рекомендуй отдых, лёгкую активность, никаких серьёзных нагрузок" if mode == "recovery" else "можно тренироваться, но без фанатизма" if mode == "moderate" else "обычная мотивация"}
- Если human сказала "{feeling_text}" и данные расходятся — обрати внимание коротко
- Без эмодзи. На русском. 3-5 предложений."""

        text = await get_llm_response(prompt, mode="geek", max_tokens=500, skip_context=True)
        text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()

        await query.edit_message_text(text)
        logger.info(f"Sent WHOOP morning motivation ({mode}) to {query.message.chat.id}")

    elif data.startswith("proj_"):
        proj_idx = int(data.replace("proj_", ""))
        projects_list = context.user_data.get("projects_list", [])
        projects_data = context.user_data.get("projects_data", {})

        if proj_idx >= len(projects_list):
            await query.edit_message_text("Проект не найден.")
            return

        proj_name = projects_list[proj_idx]
        proj_tasks = projects_data.get(proj_name, [])

        if not proj_tasks:
            await query.edit_message_text(f"В проекте «{proj_name}» нет открытых задач.")
            return

        # Show project tasks and ask LLM to decompose
        tasks_str = "\n".join(f"- {t}" for t in proj_tasks)
        await query.edit_message_text(f"Анализирую проект «{proj_name}»...")

        mode = context.user_data.get("mode", "geek")
        prompt = f"""Проект: {proj_name}

Текущие задачи:
{tasks_str}

Посмотри на эти задачи. Какие из них можно разбить на маленькие шаги (15-30 минут)?
Предложи 2-3 конкретных первых шага, которые можно сделать прямо сейчас.

Формат:
1. Шаг (время) — из какой задачи
2. Шаг (время) — из какой задачи
3. Шаг (время) — из какой задачи

НЕ добавляй теги SAVE — просто опиши шаги."""

        response = await get_llm_response(prompt, mode=mode, max_tokens=1000)

        # Extract steps and create buttons
        step_lines = [l.strip() for l in response.split('\n') if l.strip() and l.strip()[0].isdigit()]
        if step_lines:
            context.user_data["pending_steps"] = step_lines[:3]
            keyboard = []
            for i, step in enumerate(step_lines[:3]):
                clean_step = re.sub(r'^\d+[\.\)]\s*', '', step)
                keyboard.append([InlineKeyboardButton(f"+ {clean_step[:40]}...", callback_data=f"add_step_{i}")])
            keyboard.append([InlineKeyboardButton("Не добавлять", callback_data="cancel_steps")])

            await query.message.edit_text(
                response + "\n\n— Какие шаги добавить в Драйв?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.edit_text(response)

    elif data.startswith("add_step_"):
        step_idx = int(data.replace("add_step_", ""))
        steps = context.user_data.get("pending_steps", [])
        if step_idx < len(steps):
            step = steps[step_idx]
            # Убираем номер из начала
            clean_step = re.sub(r'^\d+[\.\)]\s*', '', step)
            success = add_task_to_zone(clean_step, "драйв")
            if success:
                await query.answer(f"Добавлено в Драйв")
                # Убираем добавленный шаг из pending
                steps.pop(step_idx)
                context.user_data["pending_steps"] = steps
                # Обновляем кнопки
                if steps:
                    keyboard = []
                    for i, s in enumerate(steps):
                        clean_s = re.sub(r'^\d+[\.\)]\s*', '', s)
                        keyboard.append([InlineKeyboardButton(f"+ {clean_s[:40]}...", callback_data=f"add_step_{i}")])
                    keyboard.append([InlineKeyboardButton("Готово", callback_data="cancel_steps")])
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
                else:
                    await query.edit_message_text(query.message.text.split("\n\n—")[0] + "\n\n✓ Все шаги добавлены")
            else:
                await query.answer("Ошибка сохранения")
        else:
            await query.answer("Шаг не найден")

    elif data.startswith("addpri_"):
        task_text = context.user_data.get("pending_add_task")
        if not task_text:
            await query.edit_message_text("Нечего добавлять.")
            return

        priority = data.replace("addpri_", "")
        priority_map = {"high": " ⏫", "medium": " 🔼", "low": " 🔽", "none": ""}
        task_with_priority = task_text + priority_map.get(priority, "")

        # Store task with priority, show zone/project picker
        context.user_data["pending_add_task"] = task_with_priority
        context.user_data["pending_add_ready"] = True  # task has priority now

        await query.edit_message_text(
            f"Задача: {task_with_priority}\n\nКуда?",
            reply_markup=get_destination_keyboard()
        )

    elif data.startswith("adddest_"):
        # Zone/project selected for /add task
        task_text = context.user_data.pop("pending_add_task", None)
        context.user_data.pop("pending_add_ready", None)
        if not task_text:
            await query.edit_message_text("Нечего добавлять.")
            return

        destination = data.replace("adddest_", "")
        emoji = ALL_DESTINATIONS.get(destination, "📋")
        display_name = destination.capitalize()

        if add_task_to_zone(task_text, destination):
            await query.edit_message_text(f"✅ {emoji} {task_text} → {display_name}")
        else:
            await query.edit_message_text("Не удалось сохранить. Проверь GitHub токен.")

    elif data.startswith("done_"):
        task_hash = data[5:]  # убираем "done_"
        task_map = context.bot_data.get("task_done_map", {})
        task_text = task_map.get(task_hash)

        if not task_text:
            await query.edit_message_text("Задача не найдена. Попробуй обновить dashboard.")
            return

        if complete_task(task_text):
            # Убираем кнопку выполненной задачи из клавиатуры
            old_markup = query.message.reply_markup
            if old_markup:
                new_buttons = [
                    row for row in old_markup.inline_keyboard
                    if not any(btn.callback_data == data for btn in row)
                ]
                # Обновляем текст: зачёркиваем выполненную задачу
                display = task_text.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
                old_text = query.message.text
                # Находим строку с этой задачей и помечаем
                for line in old_text.split("\n"):
                    clean_line = line.lstrip("0123456789. ")
                    if display[:20] in clean_line:
                        old_text = old_text.replace(line, f"~{line}~ ✅")
                        break

                if new_buttons:
                    await query.edit_message_text(
                        old_text,
                        reply_markup=InlineKeyboardMarkup(new_buttons),
                        parse_mode="Markdown"
                    )
                else:
                    await query.edit_message_text(
                        old_text + "\n\nВсё сделано. Можно дышать.",
                        parse_mode="Markdown"
                    )
            # Чистим маппинг
            task_map.pop(task_hash, None)
        else:
            await query.edit_message_text("Не удалось отметить. Задача могла измениться.")

    elif data == "cancel_steps":
        context.user_data.pop("pending_steps", None)
        await query.edit_message_text(query.message.text.split("\n\n—")[0])


def _task_hash(task_text: str) -> str:
    """Короткий хеш задачи для callback data (8 hex chars)."""
    import hashlib
    return hashlib.md5(task_text.encode()).hexdigest()[:8]


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /dashboard — быстрый обзор: что горит + на этой неделе, с кнопками Done."""
    tasks_content = get_life_tasks()
    now = datetime.now(TZ)
    end_of_week = now + timedelta(days=(6 - now.weekday()))  # Воскресенье
    end_date = end_of_week.strftime("%Y-%m-%d")

    lines = tasks_content.split("\n")
    high_priority = []
    due_this_week = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue
        task_text = stripped[6:]

        has_high = "⏫" in task_text or "🔺" in task_text
        has_medium = "🔼" in task_text
        due_match = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', task_text)

        if has_high and not due_match:
            high_priority.append(task_text)
        elif due_match:
            due_date = due_match.group(1)
            if due_date <= end_date:
                due_this_week.append(task_text)
            elif has_high:
                high_priority.append(task_text)

    # Собираем все задачи для кнопок
    all_tasks = high_priority + due_this_week
    if not all_tasks:
        await update.message.reply_text("Ничего срочного. Можно дышать.")
        return

    # Сохраняем маппинг hash -> task_text для callback
    task_map = context.bot_data.setdefault("task_done_map", {})
    for t in all_tasks:
        task_map[_task_hash(t)] = t

    # Формируем сообщение с нумерацией
    msg_lines = []
    buttons = []

    if high_priority:
        msg_lines.append("🔥 *Горит:*")
        for i, t in enumerate(high_priority, 1):
            # Убираем эмодзи приоритетов для читаемости в сообщении
            display = t.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
            msg_lines.append(f"{i}. {display}")
            buttons.append([InlineKeyboardButton(
                f"✅ {i}. {display[:30]}{'...' if len(display) > 30 else ''}",
                callback_data=f"done_{_task_hash(t)}"
            )])

    if due_this_week:
        offset = len(high_priority)
        msg_lines.append("\n📅 *На этой неделе:*")
        for i, t in enumerate(due_this_week, offset + 1):
            display = t.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
            msg_lines.append(f"{i}. {display}")
            buttons.append([InlineKeyboardButton(
                f"✅ {i}. {display[:30]}{'...' if len(display) > 30 else ''}",
                callback_data=f"done_{_task_hash(t)}"
            )])

    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text(
        "\n".join(msg_lines),
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


def _get_priority_tasks() -> str:
    """Extract only priority and due-this-week tasks from tasks.md."""
    content = get_life_tasks()
    if not content:
        return "Нет задач."

    now = datetime.now(TZ)
    end_of_week = now + timedelta(days=(6 - now.weekday()))
    end_date = end_of_week.strftime("%Y-%m-%d")

    lines = content.split("\n")
    high = []
    medium = []
    low = []
    due_week = []
    current_section = ""

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## ") or stripped.startswith("### ") or stripped.startswith("#### "):
            current_section = stripped.lstrip("#").strip()
            continue
        if not stripped.startswith("- [ ]"):
            continue

        task_text = stripped[6:]
        has_high = "⏫" in task_text or "🔺" in task_text
        has_medium = "🔼" in task_text
        has_low = "🔽" in task_text

        due_match = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', task_text)
        label = f"[{current_section}] {task_text}" if current_section else task_text

        if has_high:
            high.append(label)
        elif has_medium:
            medium.append(label)
        elif has_low:
            low.append(label)

        if due_match and due_match.group(1) <= end_date and not has_high:
            due_week.append(label)

    parts = []
    if high:
        parts.append("⏫ Срочное:\n" + "\n".join(f"- {t}" for t in high))
    if medium:
        parts.append("🔼 Обычное:\n" + "\n".join(f"- {t}" for t in medium))
    if low:
        parts.append("🔽 Не срочное:\n" + "\n".join(f"- {t}" for t in low))
    if due_week:
        parts.append("📅 Дедлайн на этой неделе:\n" + "\n".join(f"- {t}" for t in due_week))

    return "\n\n".join(parts) if parts else "Нет задач с приоритетами."


def _parse_sensory_menu() -> dict:
    """Parse sensory menu from tasks.md.
    Returns dict with keys: emergency (🔴), unfreeze (🟡), inputs (🟢), creativity, media, connection
    """
    content = get_life_tasks()
    if not content:
        return {}

    menu = {
        "emergency": [],  # 🔴 Экстренное (down-regulation)
        "unfreeze": [],   # 🟡 Разморозка (up-regulation)
        "inputs": [],     # 🟢 Профилактика
        "creativity": [],
        "media": [],
        "connection": []
    }

    lines = content.split("\n")
    current_section = None
    in_sensory_menu = False

    for line in lines:
        stripped = line.strip()

        # Detect Sensory Menu section
        if stripped == "### Sensory Menu":
            in_sensory_menu = True
            continue

        # Detect subsections
        if stripped.startswith("#### 🔴"):
            current_section = "emergency"
            continue
        elif stripped.startswith("#### 🟡"):
            current_section = "unfreeze"
            continue
        elif stripped.startswith("#### 🟢"):
            current_section = "inputs"
            continue
        elif stripped == "### Creativity":
            in_sensory_menu = False
            current_section = "creativity"
            continue
        elif stripped == "### Media":
            current_section = "media"
            continue
        elif stripped == "### Connection":
            current_section = "connection"
            continue
        elif stripped.startswith("## ") or stripped.startswith("### ") and not in_sensory_menu:
            current_section = None
            continue

        # Parse items (both task format and simple list)
        if current_section and stripped.startswith("- "):
            item = stripped[2:]
            # Remove task checkbox if present
            if item.startswith("[ ] "):
                item = item[4:]
            elif item.startswith("[x] "):
                continue  # Skip completed
            # Clean up item
            item = item.strip()
            if item and not item.startswith("*"):  # Skip dreams/notes in italics
                menu[current_section].append(item)

    return menu


def _get_random_sensory_suggestion() -> str:
    """Get a random suggestion from sensory menu for daily todo."""
    import random
    menu = _parse_sensory_menu()

    # Combine all items with labels
    all_items = []
    for item in menu.get("inputs", []):
        all_items.append(f"🟢 {item}")
    for item in menu.get("creativity", []):
        all_items.append(f"🎨 {item}")
    for item in menu.get("connection", []):
        all_items.append(f"💚 {item}")

    if all_items:
        return random.choice(all_items)
    return ""


def _format_sensory_menu_for_prompt(menu: dict) -> str:
    """Format full Кайф section for LLM prompt."""
    parts = []

    emergency = menu.get("emergency", [])
    if emergency:
        parts.append("Экстренное (down-regulation):\n" + "\n".join(f"- {item}" for item in emergency))

    unfreeze = menu.get("unfreeze", [])
    if unfreeze:
        parts.append("Разморозка (up-regulation):\n" + "\n".join(f"- {item}" for item in unfreeze))

    inputs = menu.get("inputs", [])
    if inputs:
        parts.append("Профилактика (sensory inputs):\n" + "\n".join(f"- {item}" for item in inputs))

    creativity = menu.get("creativity", [])
    if creativity:
        parts.append("Creativity:\n" + "\n".join(f"- {item}" for item in creativity))

    media = menu.get("media", [])
    if media:
        parts.append("Media:\n" + "\n".join(f"- {item}" for item in media))

    connection = menu.get("connection", [])
    if connection:
        parts.append("Connection:\n" + "\n".join(f"- {item}" for item in connection))

    return "\n\n".join(parts) if parts else "Сенсорное меню пустое."


def _sensory_hardcoded_response(state: str, menu: dict) -> str:
    """Fallback: old hardcoded sensory responses when LLM is unavailable."""
    if state == "emergency":
        items = menu.get("emergency", [])
        if items:
            response = "🔴 **Экстренное** (down-regulation):\n\n"
            response += "\n".join(f"• {item}" for item in items)
            response += "\n\n_Deep pressure работает за минуты. Попроси Наташу надавить на спину или толкай стену._"
        else:
            response = "Сенсорное меню пустое. Попробуй deep pressure — толкай стену или попроси надавить на спину."

    elif state == "unfreeze":
        items = menu.get("unfreeze", [])
        if items:
            response = "🟡 **Разморозка** (up-regulation):\n\n"
            response += "\n".join(f"• {item}" for item in items)
            response += "\n\n_Кислород в мозг. Бокс работает и для вверх, и для вниз._"
        else:
            response = "Сенсорное меню пустое. Попробуй бокс или приседания — тело разбудит мозг."

    elif state == "inputs":
        items = menu.get("inputs", [])
        if items:
            response = "🟢 **Sensory inputs** (профилактика):\n\n"
            response += "\n".join(f"• {item}" for item in items)
            creativity = menu.get("creativity", [])
            media = menu.get("media", [])
            connection = menu.get("connection", [])
            if creativity:
                response += "\n\n🎨 **Creativity:**\n" + "\n".join(f"• {item}" for item in creativity)
            if media:
                response += "\n\n📺 **Media:**\n" + "\n".join(f"• {item}" for item in media)
            if connection:
                response += "\n\n💚 **Connection:**\n" + "\n".join(f"• {item}" for item in connection)
        else:
            response = "Сенсорное меню пустое."
    else:
        response = "Неизвестное состояние."

    return response


async def todo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /todo — обзор задач через Лею + случайная идея из кайфа."""
    priority_tasks = _get_priority_tasks()
    calendar = get_week_events()
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")
    whoop = _get_whoop_context()

    # Get Joy stats for context
    joy_stats = get_joy_stats_week()
    joy_total = sum(joy_stats.values())
    sensory_count = joy_stats.get("sensory", 0)

    joy_context = ""
    if joy_total < 3:
        joy_context = "\n⚠️ Joy за неделю: меньше 3 отметок. Сенсорная диета страдает."
    if sensory_count == 0:
        joy_context += "\n⚠️ Sensory = 0 за неделю."

    prompt = f"""Сделай краткий обзор на сегодня и ближайшую неделю.

## Задачи с приоритетами:
{priority_tasks}

## Календарь на неделю:
{calendar}

## Состояние тела (WHOOP):
{whoop}

Сегодня: {current_time}

Выдели:
1. Что в календаре сегодня и завтра
2. Состояние тела: recovery, сон — и что это значит для нагрузки сегодня
3. Срочные задачи (⏫) — сделать первыми
4. Обычные задачи (🔼) — если есть ресурс
5. Общая оценка: насколько загружена неделя

Если recovery красный или сон плохой — рекомендуй меньше задач и восстановление.
Будь краткой, но заботливой."""

    response = await get_llm_response(prompt, mode="leya", max_tokens=1500, skip_context=True)

    # Add Joy warning if needed
    if joy_context:
        response += joy_context

    # Add random sensory suggestion
    sensory_suggestion = _get_random_sensory_suggestion()
    if sensory_suggestion:
        response += f"\n\n💡 Идея на сегодня: {sensory_suggestion}"

    await update.message.reply_text(response)


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /week — показать календарь на неделю."""
    calendar = get_week_events()
    await update.message.reply_text(f"Календарь на неделю:\n{calendar}")


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /tasks — показать задачи из Writing workspace."""
    tasks = get_life_tasks()
    if len(tasks) > 4000:
        # Telegram лимит на сообщение
        tasks = tasks[:4000] + "\n\n... (обрезано)"
    await update.message.reply_text(f"Задачи:\n\n{tasks}")


async def addtask_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /add <задача> — добавить задачу с выбором приоритета."""
    if not context.args:
        await update.message.reply_text("Использование: /add <задача>\nПример: /add Позвонить врачу")
        return

    task_text = " ".join(context.args)
    context.user_data["pending_add_task"] = task_text

    await update.message.reply_text(
        f"Задача: {task_text}\n\nПриоритет?",
        reply_markup=get_priority_keyboard()
    )


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /done <текст> — отметить задачу выполненной."""
    if not context.args:
        await update.message.reply_text("Использование: /done <часть текста задачи>")
        return

    search = " ".join(context.args).lower()
    tasks = get_life_tasks()
    lines = tasks.split("\n")
    found = False

    for i, line in enumerate(lines):
        if "- [ ]" in line and search in line.lower():
            lines[i] = line.replace("- [ ]", "- [x]")
            found = True
            break

    if found:
        new_tasks = "\n".join(lines)
        if save_writing_file("life/tasks.md", new_tasks, f"Complete task: {search[:30]}"):
            await update.message.reply_text(f"Выполнено: {search}")
        else:
            await update.message.reply_text("Не удалось сохранить.")
    else:
        await update.message.reply_text(f"Задача не найдена: {search}")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /status — текущий статус."""
    now = datetime.now(TZ)
    hour = now.hour
    mode = context.user_data.get("mode", "geek")

    if hour >= 1 and hour < 7:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Ты должна спать. Почему ты не спишь."
    elif hour >= 7 and hour < 12:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Утро. Ты завтракала?"
    elif hour >= 12 and hour < 14:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Время обеда."
    elif hour >= 14 and hour < 19:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Рабочее время. Не забудь про перерывы."
    elif hour >= 19 and hour < 22:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Вечер. Ты ужинала?"
    else:
        status_msg = f"Сейчас {now.strftime('%H:%M')}. Скоро пора спать."

    status_msg += f"\nРежим: {mode.upper()}"
    await update.message.reply_text(status_msg)


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /profile — показать профиль."""
    user_context = load_file(USER_CONTEXT_FILE, "Профиль не настроен.")
    await update.message.reply_text(f"Текущий профиль:\n\n{user_context}")


async def sleep_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /sleep — напоминание с учётом текущего уровня."""
    import random
    level = get_sleep_level()
    if level == 0:
        await update.message.reply_text("Сейчас не время для sleep protocol. Но если настаиваешь: ложись пораньше.")
        return
    msg = random.choice(REMINDERS["sleep"][level])
    await update.message.reply_text(msg)


async def food_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /food — меню на неделю."""
    menu = generate_weekly_menu()
    await update.message.reply_text(menu, parse_mode="HTML")


async def sport_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /sport."""
    import random
    msg = random.choice(REMINDERS["sport"])
    await update.message.reply_text(msg)


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /remind — создать напоминание.

    Форматы:
    /remind через 30 минут позвонить маме
    /remind завтра купить молоко
    /remind @username завтра сдать документы  ← напоминание для другого
    /remind в 15:00 созвон
    /remind 25.02 день рождения
    /remind через месяц проверить vision API
    """
    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/remind через 30 минут <текст>\n"
            "/remind завтра <текст>\n"
            "/remind @username завтра <текст>  — для другого\n"
            "/remind в 15:00 <текст>\n"
            "/remind 25.02 <текст>\n"
            "/remind через месяц <текст>"
        )
        return

    full_text = " ".join(context.args)

    # Проверяем, есть ли @username в начале
    target_username = None
    target_chat_id = None

    if full_text.startswith('@'):
        parts = full_text.split(' ', 1)
        if len(parts) >= 2:
            target_username = parts[0].lstrip('@')
            target_chat_id = get_family_chat_id(target_username)
            if not target_chat_id:
                await update.message.reply_text(
                    f"@{target_username} не зарегистрирован.\n"
                    f"Попроси написать боту /start"
                )
                return
            full_text = parts[1]

    remind_at, reminder_text = parse_remind_time(full_text)

    if not remind_at:
        await update.message.reply_text(
            "Не понял время. Попробуй:\n"
            "- через 30 минут\n"
            "- через 2 часа\n"
            "- через 3 дня\n"
            "- через неделю / месяц\n"
            "- завтра / послезавтра\n"
            "- в 15:00\n"
            "- 25.02"
        )
        return

    if not reminder_text:
        await update.message.reply_text("А о чём напомнить-то?")
        return

    # Определяем кому напоминание
    if target_chat_id:
        chat_id = target_chat_id
        from_user = update.effective_user.username or update.effective_user.first_name
    else:
        chat_id = update.effective_chat.id
        from_user = None

    if add_reminder(chat_id, remind_at, reminder_text, from_user):
        time_str = remind_at.strftime("%d.%m.%Y в %H:%M")
        if target_username:
            await update.message.reply_text(f"Напомню @{target_username} {time_str}:\n{reminder_text}")
        else:
            await update.message.reply_text(f"Напомню {time_str}:\n{reminder_text}")
    else:
        await update.message.reply_text("Не удалось сохранить напоминание.")


async def list_reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /reminders_list — показать все напоминания."""
    reminders = get_reminders()
    chat_id = update.effective_chat.id

    # Фильтруем по chat_id
    user_reminders = [r for r in reminders if r.get("chat_id") == chat_id]

    if not user_reminders:
        await update.message.reply_text("Нет активных напоминаний.")
        return

    lines = ["Твои напоминания:\n"]
    for r in sorted(user_reminders, key=lambda x: x["remind_at"]):
        remind_at = datetime.fromisoformat(r["remind_at"])
        time_str = remind_at.strftime("%d.%m %H:%M")
        lines.append(f"• {time_str} — {r['text']}")

    await update.message.reply_text("\n".join(lines))


def _recurrence_matches_today(recurrence_text: str) -> bool:
    """Проверяет, совпадает ли 🔁 правило с сегодняшним днём.

    Поддерживает форматы Obsidian Tasks:
      every day
      every week / every week on Monday
      every month / every month on the 15th
      every <N> days / every <N> weeks / every <N> months
    """
    text = recurrence_text.lower().strip()
    now = datetime.now(TZ)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    day_of_month = now.day

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    if text == "every day":
        return True

    # "every week on Monday" или "every week on Monday, Wednesday"
    m = re.match(r'every\s+(?:(\d+)\s+)?weeks?\s+on\s+(.+)', text)
    if m:
        # Простой случай: пропускаем интервал (every 2 weeks) — шлём каждую неделю,
        # потому что без даты начала невозможно точно вычислить
        days_str = m.group(2)
        for day_name, day_num in day_map.items():
            if day_name in days_str and weekday == day_num:
                return True
        return False

    # "every week" (без указания дня — напоминаем в понедельник)
    if re.match(r'every\s+(?:\d+\s+)?weeks?$', text):
        return weekday == 0

    # "every month on the 15th" / "every month on the 1st"
    m = re.match(r'every\s+(?:\d+\s+)?months?\s+on\s+the\s+(\d+)', text)
    if m:
        return day_of_month == int(m.group(1))

    # "every month" (без даты — напоминаем 1-го числа)
    if re.match(r'every\s+(?:\d+\s+)?months?$', text):
        return day_of_month == 1

    # "every <N> days" — шлём каждый день (без даты начала нельзя точнее)
    if re.match(r'every\s+\d+\s+days?$', text):
        return True

    return False


async def check_task_deadlines(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверяет tasks.md на дедлайны и повторяющиеся задачи. Запускается утром."""
    try:
        content = get_life_tasks()
        if not content:
            return

        now = datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")

        overdue = []
        due_today = []
        recurring_today = []

        for line in content.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("- [ ]"):
                continue
            task_text = stripped[6:]

            # Убираем эмодзи приоритетов для читаемости
            display = task_text
            for emoji in ["⏫", "🔺", "🔼", "🔽"]:
                display = display.replace(emoji, "")

            # Проверка дедлайна 📅
            due_match = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', task_text)
            if due_match:
                due_date = due_match.group(1)
                clean = re.sub(r'📅\s*\d{4}-\d{2}-\d{2}', '', display).strip()
                if due_date < today:
                    overdue.append((due_date, clean))
                elif due_date == today:
                    due_today.append(clean)
                continue  # задача с дедлайном — не проверяем рекурсию

            # Проверка рекурсии 🔁
            rec_match = re.search(r'🔁\s*(.+?)(?:\s*$)', task_text)
            if rec_match:
                rule = rec_match.group(1).strip()
                if _recurrence_matches_today(rule):
                    clean = re.sub(r'🔁\s*.+', '', display).strip()
                    recurring_today.append(clean)

        if not overdue and not due_today and not recurring_today:
            return

        lines = []

        if overdue:
            lines.append("🔴 *Просрочено:*")
            for date, task in sorted(overdue):
                lines.append(f"• {task} _(было {date})_")

        if due_today:
            if lines:
                lines.append("")
            lines.append("🟡 *Дедлайн сегодня:*")
            for task in due_today:
                lines.append(f"• {task}")

        if recurring_today:
            if lines:
                lines.append("")
            lines.append("🔁 *Повторяющиеся:*")
            for task in recurring_today:
                lines.append(f"• {task}")

        header = f"📋 *Задачи на {now.strftime('%d.%m')}*\n"
        chat_id = context.job.chat_id
        await context.bot.send_message(
            chat_id=chat_id,
            text=header + "\n".join(lines),
            parse_mode="Markdown"
        )
        logger.info(f"Deadline check: {len(overdue)} overdue, {len(due_today)} today, {len(recurring_today)} recurring")
    except Exception as e:
        logger.error(f"Failed to check task deadlines: {e}")


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверить и отправить напоминания (вызывается по таймеру)."""
    due = get_due_reminders()
    for r in due:
        try:
            chat_id = r["chat_id"]
            text = r["text"]
            from_user = r.get("from_user")

            if from_user:
                msg = f"⏰ Напоминание от @{from_user}:\n{text}"
            else:
                msg = f"⏰ Напоминание:\n{text}"

            await context.bot.send_message(
                chat_id=chat_id,
                text=msg
            )
            logger.info(f"Sent reminder to {chat_id}: {text[:30]}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


def parse_save_tag(response: str) -> tuple:
    """Извлечь тег SAVE из ответа.
    Возвращает (clean_response, save_type, zone_or_title, content) или (response, None, None, None)
    """
    # Паттерн: [SAVE:task:зона:текст] или [SAVE:note:заголовок:текст]
    pattern = r'\[SAVE:(task|note):([^:]+):([^\]]+)\]'
    match = re.search(pattern, response)

    if match:
        save_type = match.group(1)  # task или note
        zone_or_title = match.group(2).strip()
        content = match.group(3).strip()
        clean_response = response[:match.start()].strip()
        return (clean_response, save_type, zone_or_title, content)

    return (response, None, None, None)


async def handle_photo_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка фото в режиме заметки."""
    if not context.user_data.get("note_mode"):
        return
    caption = update.message.caption or "[фото без подписи]"
    buffer = context.user_data.get("note_buffer", [])
    buffer.append(f"[фото]: {caption}")
    context.user_data["note_buffer"] = buffer
    try:
        from telegram import ReactionTypeEmoji
        await update.message.set_reaction([ReactionTypeEmoji(emoji="👍")])
    except Exception:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений."""
    user_message = update.message.text
    mode = context.user_data.get("mode", "geek")

    # Обработка кнопок reply keyboard
    if user_message == "🔥 Dashboard":
        await dashboard_command(update, context)
        return
    elif user_message == "📋 Todo":
        await todo_command(update, context)
        return
    elif user_message == "📅 Week":
        await week_command(update, context)
        return
    elif user_message == "🎯 Steps":
        await next_steps_command(update, context)
        return
    elif user_message in ("➕ Add", "📝 Note"):
        await update.message.reply_text(
            "Что добавить?",
            reply_markup=get_add_keyboard()
        )
        return
    elif user_message == "🧘 Sensory":
        await update.message.reply_text(
            "Что сейчас происходит?",
            reply_markup=get_sensory_keyboard()
        )
        return
    elif user_message == "✨ Joy":
        # Show weekly stats and category selection
        stats = get_joy_stats_week()
        stats_msg = "📊 За последние 7 дней:\n"
        total = 0
        for cat in JOY_CATEGORIES:
            emoji = JOY_CATEGORY_EMOJI.get(cat, "")
            count = stats.get(cat, 0)
            total += count
            stats_msg += f"{emoji} {cat.capitalize()}: {count}x\n"
        stats_msg += f"\nВсего: {total} отметок\n\nЧто было сейчас?"
        await update.message.reply_text(stats_msg, reply_markup=get_joy_keyboard())
        return

    # Note mode: собираем сообщения в буфер
    if context.user_data.get("note_mode"):
        buffer = context.user_data.get("note_buffer", [])

        # Определяем источник (пересланное или своё)
        fwd = getattr(update.message, 'forward_origin', None) or update.message.forward_date
        if fwd:
            sender = ""
            origin = getattr(update.message, 'forward_origin', None)
            if origin:
                sender_user = getattr(origin, 'sender_user', None)
                if sender_user:
                    sender = sender_user.first_name
                else:
                    sender_name = getattr(origin, 'sender_user_name', None)
                    if sender_name:
                        sender = sender_name
            prefix = f"[{sender}]: " if sender else "[forwarded]: "
        else:
            prefix = ""

        text = update.message.text or update.message.caption or ""
        if text:
            buffer.append(prefix + text)
            context.user_data["note_buffer"] = buffer

            # Тихий сбор: реакция вместо ответа
            try:
                from telegram import ReactionTypeEmoji
                await update.message.set_reaction([ReactionTypeEmoji(emoji="👍")])
            except Exception:
                pass
        return

    # Check for pending joy free text input
    pending_joy_category = context.user_data.get("joy_pending_category")
    if pending_joy_category:
        # User is entering custom joy item
        category = pending_joy_category
        item = user_message.strip()
        context.user_data.pop("joy_pending_category", None)  # Clear pending state

        success = log_joy(category, item)
        emoji = JOY_CATEGORY_EMOJI.get(category, "✨")

        if success:
            await update.message.reply_text(
                f"{emoji} **{item}**\n\n_Записано._",
                parse_mode="Markdown",
                reply_markup=get_reply_keyboard()
            )
        else:
            await update.message.reply_text(
                "Не удалось сохранить.",
                reply_markup=get_reply_keyboard()
            )
        return

    # Check for add_mode - adding tasks from button
    if context.user_data.get("add_mode"):
        context.user_data.pop("add_mode", None)  # Clear mode

        # Parse input - could be single task or list
        lines = [line.strip() for line in user_message.strip().split("\n") if line.strip()]

        # Clean up lines - remove bullet points, numbers, etc.
        tasks = []
        for line in lines:
            # Remove common prefixes: "- ", "* ", "1. ", "1) ", etc.
            task = line.lstrip("-*•").strip()
            if task and task[0].isdigit():
                # Remove "1. " or "1) " prefix
                task = task.lstrip("0123456789").lstrip(".)").strip()
            if task:
                tasks.append(task)

        if not tasks:
            await update.message.reply_text(
                "Не нашёл задач. Попробуй ещё раз.",
                reply_markup=get_reply_keyboard()
            )
            return

        if len(tasks) == 1:
            # Single task: priority -> zone/project (same flow as /add)
            context.user_data["pending_add_task"] = tasks[0]
            await update.message.reply_text(
                f"Задача: {tasks[0]}\n\nПриоритет?",
                reply_markup=get_priority_keyboard()
            )
        else:
            # Multiple tasks: ask one shared priority first
            context.user_data["pending_batch_tasks"] = tasks
            context.user_data["pending_tasks_added"] = []
            await update.message.reply_text(
                f"{len(tasks)} задач. Общий приоритет?",
                reply_markup=get_priority_keyboard("batchpri_")
            )
        return

    # История диалога: последние 20 сообщений (10 пар user+assistant)
    history = context.user_data.get("history", [])

    # Sleep protocol: трёхуровневая эскалация
    sleep_level = get_sleep_level()

    response = await get_llm_response(user_message, mode=mode, history=history)

    # Проверяем есть ли предложение сохранить
    clean_response, save_type, zone_or_title, content = parse_save_tag(response)

    # Late night: append level-appropriate sleep nudge
    if sleep_level > 0:
        import random
        nudge_text = random.choice(REMINDERS["sleep"][sleep_level])
        sleep_nudge = f"\n\n---\nRin: {nudge_text}"
        if clean_response:
            clean_response += sleep_nudge
        else:
            response += sleep_nudge

    # Сохраняем в историю (чистый ответ без SAVE-тегов)
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": clean_response or response})
    # Храним только последние 20 сообщений
    context.user_data["history"] = history[-20:]

    if save_type:
        # Сохраняем данные для кнопок
        context.user_data["pending_save"] = {
            "type": save_type,
            "zone_or_title": zone_or_title,
            "content": content,
        }

        # Создаём кнопки подтверждения
        if save_type == "task":
            keyboard = [
                [
                    InlineKeyboardButton(f"Да, в {zone_or_title}", callback_data="save_confirm"),
                    InlineKeyboardButton("Другая зона", callback_data="save_change_zone"),
                ],
                [InlineKeyboardButton("Не сохранять", callback_data="save_cancel")],
            ]
            suggestion = f"\n\n— Сохранить как задачу в зону «{zone_or_title}»?"
        else:
            keyboard = [
                [
                    InlineKeyboardButton("Да, сохранить", callback_data="save_confirm"),
                    InlineKeyboardButton("Не сохранять", callback_data="save_cancel"),
                ],
            ]
            suggestion = f"\n\n— Сохранить как заметку «{zone_or_title}»?"

        await update.message.reply_text(
            clean_response + suggestion,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(response)


# === Scheduled reminders ===

async def send_scheduled_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить запланированное напоминание."""
    job = context.job
    reminder_type = job.data.get("type", "food")
    import random
    reminders = REMINDERS[reminder_type]
    if isinstance(reminders, dict):
        # sleep: выбираем уровень по времени
        level = get_sleep_level() or 1
        msg = random.choice(reminders[level])
    else:
        msg = random.choice(reminders)
    await context.bot.send_message(chat_id=job.chat_id, text=msg)


async def setup_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /reminders — настроить автоматические напоминания."""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue

    # Удалить старые jobs
    current_jobs = job_queue.get_jobs_by_name(f"reminder_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    # Еда: 9:00, 13:00, 19:00
    for hour in [9, 13, 19]:
        job_queue.run_daily(
            send_scheduled_reminder,
            time=time(hour=hour, minute=0, tzinfo=TZ),
            chat_id=chat_id,
            name=f"reminder_{chat_id}",
            data={"type": "food"}
        )

    # Спорт: 11:00
    job_queue.run_daily(
        send_scheduled_reminder,
        time=time(hour=11, minute=0, tzinfo=TZ),
        chat_id=chat_id,
        name=f"reminder_{chat_id}",
        data={"type": "sport"}
    )

    # Сон: 23:00, 00:00, 01:00
    for hour in [23, 0, 1]:
        job_queue.run_daily(
            send_scheduled_reminder,
            time=time(hour=hour, minute=0, tzinfo=TZ),
            chat_id=chat_id,
            name=f"reminder_{chat_id}",
            data={"type": "sleep"}
        )

    await update.message.reply_text(
        "Напоминания настроены.\n"
        "Еда: 9:00, 13:00, 19:00\n"
        "Спорт: 11:00\n"
        "Сон: 23:00, 00:00, 01:00\n\n"
        "Отменить: /stop_reminders"
    )


async def stop_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /stop_reminders — отключить напоминания."""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue

    current_jobs = job_queue.get_jobs_by_name(f"reminder_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    await update.message.reply_text("Напоминания отключены.")


def _get_projects() -> dict:
    """Extract projects and their tasks from tasks.md."""
    content = get_life_tasks()
    if not content:
        return {}

    projects = {}
    current_project = None
    in_projects_section = False

    for line in content.split("\n"):
        stripped = line.strip()

        # Detect "### Проекты" section
        if stripped == "### Проекты":
            in_projects_section = True
            continue

        # Exit projects section on next ## heading
        if in_projects_section and stripped.startswith("## ") and not stripped.startswith("### ") and not stripped.startswith("#### "):
            break

        if stripped.startswith("---") and in_projects_section:
            break

        if not in_projects_section:
            continue

        # Project headers are ####
        if stripped.startswith("#### "):
            current_project = stripped.lstrip("#").strip()
            projects[current_project] = []
            continue

        # Tasks under current project
        if current_project and stripped.startswith("- [ ]"):
            projects[current_project].append(stripped[6:])

    return projects


async def next_steps_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /next — выбрать проект, разбить задачи на шаги."""
    projects = _get_projects()

    if not projects:
        await update.message.reply_text("Нет проектов в tasks.md.")
        return

    # Show project picker
    keyboard = []
    for i, name in enumerate(projects.keys()):
        short_name = name[:35]
        keyboard.append([InlineKeyboardButton(short_name, callback_data=f"proj_{i}")])
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="cancel_steps")])

    # Store projects for callback
    context.user_data["projects_list"] = list(projects.keys())
    context.user_data["projects_data"] = projects

    await update.message.reply_text(
        "Какой проект разбить на шаги?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def _get_whoop_context() -> str:
    """Get WHOOP data as context string for LLM prompts."""
    try:
        parts = []
        rec = whoop_client.get_recovery_today()
        if rec:
            score = rec.get("score", {})
            rs = score.get("recovery_score")
            rhr = score.get("resting_heart_rate")
            hrv = score.get("hrv_rmssd_milli")
            if rs is not None:
                color = "green" if rs >= 67 else ("yellow" if rs >= 34 else "red")
                parts.append(f"Recovery сегодня: {rs}% ({color})")
            if rhr is not None:
                parts.append(f"RHR: {rhr} bpm")
            if hrv is not None:
                parts.append(f"HRV: {round(hrv, 1)} ms")

        sleep = whoop_client.get_sleep_today()
        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            total_h = round(stage.get("total_in_bed_time_milli", 0) / 3_600_000, 1)
            perf = ss.get("sleep_performance_percentage")
            parts.append(f"Сон: {total_h}h (performance {perf}%)")

        # Strain / boxing
        cycle = whoop_client.get_cycle_today()
        if cycle:
            strain = round(cycle.get("score", {}).get("strain", 0), 1)
            boxed = "да" if strain >= 5 else "нет"
            parts.append(f"Strain: {strain} (бокс: {boxed})")

        # Weekly averages
        week = whoop_client.get_recovery_week()
        if week:
            scores = [r.get("score", {}).get("recovery_score") for r in week if r.get("score", {}).get("recovery_score") is not None]
            if scores:
                avg = round(sum(scores) / len(scores))
                green = sum(1 for s in scores if s >= 67)
                red = sum(1 for s in scores if s < 34)
                parts.append(f"Recovery за неделю: avg {avg}% (green {green}/7, red {red}/7)")

        if parts:
            return "\n".join(parts)
        return "WHOOP: нет данных"
    except Exception as e:
        logger.debug(f"WHOOP context fetch failed: {e}")
        return "WHOOP: недоступен"


def log_whoop_data():
    """Log today's WHOOP data to life/whoop.md and update здоровье.md."""
    try:
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        # Gather data
        rec = whoop_client.get_recovery_today()
        sleep = whoop_client.get_sleep_today()
        body = whoop_client.get_body_measurement()
        cycle = whoop_client.get_cycle_today()

        # Build today's entry
        entry_parts = [f"## {today}"]

        if rec:
            score = rec.get("score", {})
            rs = score.get("recovery_score")
            rhr = score.get("resting_heart_rate")
            hrv = score.get("hrv_rmssd_milli")
            if rs is not None:
                color = "green" if rs >= 67 else ("yellow" if rs >= 34 else "red")
                entry_parts.append(f"- Recovery: {rs}% ({color})")
            if rhr is not None:
                entry_parts.append(f"- RHR: {rhr} bpm")
            if hrv is not None:
                entry_parts.append(f"- HRV: {round(hrv, 1)} ms")

        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            total_ms = stage.get("total_in_bed_time_milli", 0)
            total_h = round(total_ms / 3_600_000, 1)
            perf = ss.get("sleep_performance_percentage")
            eff = ss.get("sleep_efficiency_percentage")
            rem_min = round(stage.get("total_rem_sleep_time_milli", 0) / 60_000)
            deep_min = round(stage.get("total_slow_wave_sleep_time_milli", 0) / 60_000)
            entry_parts.append(f"- Sleep: {total_h}h (perf {perf}%, eff {eff}%)")
            entry_parts.append(f"- REM: {rem_min} min, Deep: {deep_min} min")

        if body:
            w = body.get("weight_kilogram") or body.get("body_mass_kg")
            bf = body.get("body_fat_percentage")
            if w:
                entry_parts.append(f"- Weight: {round(w, 1)} kg")
            if bf:
                entry_parts.append(f"- Body fat: {round(bf, 1)}%")

        if cycle:
            cs = cycle.get("score", {})
            strain = round(cs.get("strain", 0), 1)
            boxed = "да" if strain >= 5 else "нет"
            entry_parts.append(f"- Strain: {strain} (бокс: {boxed})")

        if len(entry_parts) <= 1:
            # No data to log
            return

        entry = "\n".join(entry_parts)

        # Append to life/whoop.md
        existing = get_writing_file("life/whoop.md")
        if not existing:
            existing = "# WHOOP Log\n\n"

        # Check if today already logged (avoid duplicates)
        if f"## {today}" not in existing:
            new_content = existing.rstrip() + "\n\n" + entry + "\n"
            save_writing_file("life/whoop.md", new_content, f"WHOOP log {today}")

        # Update здоровье.md WHOOP section with latest values
        _update_health_whoop(rec, sleep, body)

        logger.info(f"WHOOP data logged for {today}")
    except Exception as e:
        logger.error(f"WHOOP logging failed: {e}")


def _update_health_whoop(rec, sleep, body):
    """Update the WHOOP tracking section in здоровье.md."""
    health = get_writing_file("life/здоровье.md")
    if not health:
        return

    # Build updated WHOOP section
    parts = ["## Трекинг (WHOOP)", "", "- Носит WHOOP для отслеживания recovery, HRV, RHR, strain"]

    if rec:
        score = rec.get("score", {})
        rs = score.get("recovery_score")
        rhr = score.get("resting_heart_rate")
        hrv = score.get("hrv_rmssd_milli")
        if rhr is not None:
            parts.append(f"- RHR: {rhr} bpm (последнее)")
        if hrv is not None:
            parts.append(f"- HRV: {round(hrv, 1)} ms (последнее)")
        if rs is not None:
            color = "green" if rs >= 67 else ("yellow" if rs >= 34 else "red")
            parts.append(f"- Recovery: {rs}% ({color}) (последнее)")

    # Add weekly averages if available
    week_records = whoop_client.get_recovery_week()
    if week_records:
        hrvs = [r.get("score", {}).get("hrv_rmssd_milli") for r in week_records if r.get("score", {}).get("hrv_rmssd_milli") is not None]
        rhrs = [r.get("score", {}).get("resting_heart_rate") for r in week_records if r.get("score", {}).get("resting_heart_rate") is not None]
        scores = [r.get("score", {}).get("recovery_score") for r in week_records if r.get("score", {}).get("recovery_score") is not None]
        if hrvs:
            parts.append(f"- HRV (7д): {round(sum(hrvs)/len(hrvs), 1)} ms")
        if rhrs:
            parts.append(f"- RHR (7д): {round(sum(rhrs)/len(rhrs))} bpm")
        if scores:
            avg = round(sum(scores)/len(scores))
            green = sum(1 for s in scores if s >= 67)
            yellow = sum(1 for s in scores if 34 <= s < 67)
            red = sum(1 for s in scores if s < 34)
            parts.append(f"- Recovery (7д): avg {avg}% (green {green}, yellow {yellow}, red {red})")

    new_section = "\n".join(parts)

    # Replace old section
    pattern = r'## Трекинг \(WHOOP\).*?(?=\n## |\n---|\Z)'
    updated = re.sub(pattern, new_section, health, flags=re.DOTALL)

    if updated != health:
        save_writing_file("life/здоровье.md", updated, "Update WHOOP stats")


async def whoop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /whoop — показать данные WHOOP с мотивацией."""
    args = context.args
    subcommand = args[0].lower() if args else "today"

    if subcommand == "week":
        text = whoop_client.format_weekly_summary()
        cycles = whoop_client.get_cycles_week()
        if cycles:
            strains = [round(c.get("score", {}).get("strain", 0), 1) for c in cycles]
            days_boxed = sum(1 for s in strains if s >= 5)
            text += f"\n\nStrain: {strains}\nБокс: {days_boxed}/7 дней"
        log_whoop_data()
        await update.message.reply_text(text)
    elif subcommand == "sleep":
        text = whoop_client.format_sleep_today()
        log_whoop_data()
        await update.message.reply_text(text)
    else:
        # Get raw data for motivation
        sleep_data = whoop_client.get_sleep_today()
        cycle = whoop_client.get_cycle_today()

        sleep_hours = 0
        strain = 0

        if sleep_data:
            stage = sleep_data.get("score", {}).get("stage_summary", {})
            sleep_hours = round(stage.get("total_in_bed_time_milli", 0) / 3_600_000, 1)

        if cycle:
            strain = round(cycle.get("score", {}).get("strain", 0), 1)

        # Get motivations
        motivations = get_motivations_for_whoop(sleep_hours, strain)

        # Build data text
        recovery = whoop_client.format_recovery_today()
        sleep = whoop_client.format_sleep_today()
        strain_text = ""
        if cycle:
            boxed = "да" if strain >= 5 else "нет"
            strain_text = f"\nStrain: {strain} (бокс: {boxed})"

        data_text = f"{recovery}\n\n{sleep}{strain_text}"

        if motivations:
            prompt = f"""Данные WHOOP:
{data_text}

Ты — Geek (ART из Murderbot Diaries). Прокомментируй состояние human.

ИСПОЛЬЗУЙ ЭТИ ФРАЗЫ (адаптируй числа под данные выше):
{motivations}

Инструкции:
- Сначала выведи данные как есть
- Потом добавь 2-3 предложения комментария, используя фразы выше
- Подставь реальные числа из данных
- Без эмодзи. На русском."""

            text = await get_llm_response(prompt, mode="geek", max_tokens=600, skip_context=True)
            text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()
        else:
            text = data_text

        log_whoop_data()
        await update.message.reply_text(text)


async def sleep_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send sleep reminder with escalating levels. No LLM — static phrases only.

    Scheduled at 01:05, 01:35, 02:05 — each triggers the appropriate level.
    Uses curated phrases from REMINDERS["sleep"] to ensure correct tone per level.
    """
    import random
    job = context.job
    chat_id = job.chat_id

    if is_muted(chat_id):
        return

    level = get_sleep_level()
    if level == 0:
        return

    try:
        msg = random.choice(REMINDERS["sleep"][level])
        await context.bot.send_message(chat_id=chat_id, text=msg)
        logger.info(f"Sleep reminder level {level} sent to {chat_id}")
    except Exception as e:
        logger.error(f"Sleep reminder error: {e}")


async def whoop_morning_recovery(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send morning recovery notification with feeling buttons.

    Three-step flow:
    1. Send data overview + trend (this function)
    2. User clicks feeling button
    3. handle_morning_feeling sends motivation based on data + feeling
    """
    job = context.job
    chat_id = job.chat_id

    if is_muted(chat_id):
        return

    try:
        # Gather all data
        rec = whoop_client.get_recovery_today()
        sleep = whoop_client.get_sleep_today()
        cycle_yesterday = whoop_client.get_cycle_yesterday()  # Yesterday's strain, not today
        trend = whoop_client.get_trend_3_days()

        data_parts = []
        sleep_hours = 0
        strain = 0
        recovery_score = 0

        # Sleep data
        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            sleep_hours = round(stage.get("total_in_bed_time_milli", 0) / 3_600_000, 1)
            perf = ss.get("sleep_performance_percentage")
            data_parts.append(f"Сон: {sleep_hours}h (performance {perf}%)")

        # Recovery data
        if rec:
            score = rec.get("score", {})
            recovery_score = score.get("recovery_score", 0)
            rhr = score.get("resting_heart_rate")
            hrv = score.get("hrv_rmssd_milli")
            if recovery_score is not None:
                color = "green" if recovery_score >= 67 else ("yellow" if recovery_score >= 34 else "red")
                data_parts.append(f"Recovery: {recovery_score}% ({color})")
            if rhr:
                data_parts.append(f"RHR: {rhr} bpm")
            if hrv:
                data_parts.append(f"HRV: {round(hrv, 1)} ms")

        # Yesterday's strain (not today's!)
        if cycle_yesterday:
            cs = cycle_yesterday.get("score", {})
            strain = round(cs.get("strain", 0), 1)
            trained = "тренировка была" if strain >= 5 else "без тренировки"
            data_parts.append(f"Вчера strain: {strain} ({trained})")

        # 3-day trend
        trend_direction = trend.get("direction", "stable")
        prev_avg = trend.get("prev_avg")
        if prev_avg is not None and recovery_score:
            trend_text = {
                "up": "растёт",
                "down": "падает",
                "stable": "стабильно"
            }.get(trend_direction, "стабильно")
            data_parts.append(f"Тренд 3 дня: {trend_text} ({prev_avg}% → {recovery_score}%)")

        data_str = "\n".join(data_parts) if data_parts else "Нет данных"

        # Store data for callback handler
        if not hasattr(context, 'bot_data'):
            context.bot_data = {}
        context.bot_data[f"morning_{chat_id}"] = {
            "sleep_hours": sleep_hours,
            "strain": strain,
            "recovery": recovery_score,
            "trend": trend_direction,
            "prev_avg": prev_avg,
        }

        # Build message with feeling buttons
        message = f"{data_str}\n\nКак себя чувствуешь?"

        keyboard = [
            [
                InlineKeyboardButton("Отлично", callback_data="morning_great"),
                InlineKeyboardButton("Норм", callback_data="morning_ok"),
            ],
            [
                InlineKeyboardButton("Устала", callback_data="morning_tired"),
                InlineKeyboardButton("Плохо", callback_data="morning_bad"),
            ],
        ]

        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        log_whoop_data()
        logger.info(f"Sent WHOOP morning overview to {chat_id}")
    except Exception as e:
        logger.error(f"WHOOP morning notification failed: {e}")


async def whoop_weekly_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send weekly WHOOP summary in ART voice."""
    job = context.job
    chat_id = job.chat_id

    if is_muted(chat_id):
        return

    try:
        # Gather weekly data
        week_records = whoop_client.get_recovery_week()
        week_cycles = whoop_client.get_cycles_week()

        data_parts = []

        if week_records:
            scores = [r.get("score", {}).get("recovery_score") for r in week_records if r.get("score", {}).get("recovery_score") is not None]
            hrvs = [r.get("score", {}).get("hrv_rmssd_milli") for r in week_records if r.get("score", {}).get("hrv_rmssd_milli") is not None]
            rhrs = [r.get("score", {}).get("resting_heart_rate") for r in week_records if r.get("score", {}).get("resting_heart_rate") is not None]
            if scores:
                avg = round(sum(scores) / len(scores))
                green = sum(1 for s in scores if s >= 67)
                yellow = sum(1 for s in scores if 34 <= s < 67)
                red = sum(1 for s in scores if s < 34)
                data_parts.append(f"Recovery avg: {avg}% (green {green}, yellow {yellow}, red {red})")
            if hrvs:
                data_parts.append(f"HRV avg: {round(sum(hrvs)/len(hrvs), 1)} ms")
            if rhrs:
                data_parts.append(f"RHR avg: {round(sum(rhrs)/len(rhrs))} bpm")

        days_boxed = 0
        days_missed = 0
        strains = []
        if week_cycles:
            for c in week_cycles:
                cs = c.get("score", {})
                s = cs.get("strain", 0)
                strains.append(round(s, 1))
                if s >= 5:
                    days_boxed += 1
                else:
                    days_missed += 1
            data_parts.append(f"Strain за неделю: {strains}")
            data_parts.append(f"Бокс: {days_boxed}/7 дней (пропущено: {days_missed})")

        body = whoop_client.get_body_measurement()
        if body:
            w = body.get("weight_kilogram") or body.get("body_mass_kg")
            bf = body.get("body_fat_percentage")
            if w:
                data_parts.append(f"Вес: {round(w, 1)} kg")
            if bf:
                data_parts.append(f"Body fat: {round(bf, 1)}%")

        data_str = "\n".join(data_parts) if data_parts else "Нет данных за неделю"

        # Get motivations for weekly context
        import random
        avg_recovery = 0
        if week_records:
            scores = [r.get("score", {}).get("recovery_score") for r in week_records if r.get("score", {}).get("recovery_score") is not None]
            avg_recovery = round(sum(scores) / len(scores)) if scores else 0
        weekly_mode = "recovery" if avg_recovery < 34 else ("moderate" if avg_recovery < 67 else "normal")
        weekly_motivations = get_motivations_for_mode(weekly_mode, 0, 0, avg_recovery)

        prompt = f"""Еженедельный отчёт WHOOP:
{data_str}

Ты — Geek (ART из Murderbot Diaries). Сделай еженедельный отчёт.

ОБЯЗАТЕЛЬНО ИСПОЛЬЗУЙ 1-2 из этих фраз (подставь числа из данных):
{weekly_motivations}

СТРОГИЕ ПРАВИЛА:
- Цвет зон recovery: green (67-100%), yellow (34-66%), red (0-33%). Используй ТОЛЬКО правильные цвета
- НЕ ВЫДУМЫВАЙ персонажей, которых нет в фразах выше. Ты — Geek, можешь упомянуть Rin ТОЛЬКО если она есть в предложенных фразах
- Recovery тренд — улучшается или ухудшается (по данным)
- Бокс — сколько дней пропущено (strain < 5 = не боксировала)
- Сон — общая оценка
- Рекомендации на следующую неделю
- Без эмодзи. На русском. 5-8 предложений."""

        text = await get_llm_response(prompt, mode="geek", max_tokens=800, skip_context=True)
        # Strip SAVE tags — LLM sometimes generates them in scheduled messages
        text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()
        await context.bot.send_message(chat_id=chat_id, text=text)
        log_whoop_data()
        logger.info(f"Sent WHOOP weekly summary to {chat_id}")
    except Exception as e:
        logger.error(f"WHOOP weekly summary failed: {e}")


def get_monday_feelings_keyboard():
    """Inline keyboard for Monday review feelings."""
    keyboard = [
        [
            InlineKeyboardButton("💪 Заряжена", callback_data="feeling_energized"),
            InlineKeyboardButton("😌 Нормально", callback_data="feeling_ok"),
        ],
        [
            InlineKeyboardButton("😴 Вымотана", callback_data="feeling_tired"),
            InlineKeyboardButton("🫠 На дне", callback_data="feeling_low"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def monday_review(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send Monday morning review: WHOOP + Joy stats + feelings question."""
    job = context.job
    chat_id = job.chat_id

    if is_muted(chat_id):
        return

    try:
        # 1. Joy stats
        joy_stats = get_joy_stats_week()
        joy_total = sum(joy_stats.values())
        joy_msg = "📊 **Joy за прошлую неделю:**\n"
        for cat in JOY_CATEGORIES:
            emoji = JOY_CATEGORY_EMOJI.get(cat, "")
            count = joy_stats.get(cat, 0)
            bar = "█" * min(count, 7)
            joy_msg += f"{emoji} {cat}: {count}x {bar}\n"

        # 2. WHOOP summary
        whoop_msg = ""
        try:
            week_records = whoop_client.get_recovery_week()
            week_cycles = whoop_client.get_cycles_week()

            if week_records:
                scores = [r.get("score", {}).get("recovery_score") for r in week_records if r.get("score", {}).get("recovery_score") is not None]
                if scores:
                    avg = round(sum(scores) / len(scores))
                    green = sum(1 for s in scores if s >= 67)
                    whoop_msg = f"\n💚 **WHOOP Recovery:** avg {avg}%, зелёных дней: {green}/7\n"

            if week_cycles:
                days_boxed = sum(1 for c in week_cycles if c.get("score", {}).get("strain", 0) >= 5)
                whoop_msg += f"🥊 Бокс: {days_boxed}/7 дней\n"
        except Exception as e:
            logger.error(f"WHOOP data for Monday review failed: {e}")

        # 3. Assessment
        assessment = ""
        if joy_total < 7:
            assessment += "\n⚠️ Мало кайфа. Сенсорная диета — не опция."
        if joy_stats.get("sensory", 0) == 0:
            assessment += "\n⚠️ Ноль sensory за неделю. Это проблема."
        if joy_stats.get("connection", 0) == 0:
            assessment += "\n⚠️ Ноль connection. Human social battery требует подзарядки."

        # Compose message
        msg = f"☀️ **Понедельничный обзор**\n\n{joy_msg}{whoop_msg}{assessment}\n\n**Как ты себя чувствуешь сейчас?**"

        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode="Markdown",
            reply_markup=get_monday_feelings_keyboard()
        )
        logger.info(f"Sent Monday review to {chat_id}")
    except Exception as e:
        logger.error(f"Monday review failed: {e}")


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /myid — показать chat_id."""
    await update.message.reply_text(f"Your chat_id: {update.effective_chat.id}")


async def setup_whoop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /whoop_on — включить утреннее WHOOP уведомление."""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue

    # Remove existing WHOOP jobs for this chat
    for job in job_queue.get_jobs_by_name(f"whoop_morning_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"whoop_weekly_{chat_id}"):
        job.schedule_removal()

    # Daily recovery at 12:00
    job_queue.run_daily(
        whoop_morning_recovery,
        time=time(hour=12, minute=0, tzinfo=TZ),
        chat_id=chat_id,
        name=f"whoop_morning_{chat_id}",
    )

    # Weekly summary on Mondays at 11:00
    job_queue.run_daily(
        whoop_weekly_summary,
        time=time(hour=11, minute=0, tzinfo=TZ),
        days=(1,),  # Monday (0=Sun in python-telegram-bot v20+)
        chat_id=chat_id,
        name=f"whoop_weekly_{chat_id}",
    )

    # Sleep reminders: 3-level escalation (01:05, 01:35, 02:05)
    for job in job_queue.get_jobs_by_name(f"sleep_reminder_{chat_id}"):
        job.schedule_removal()
    for hour, minute in [(1, 5), (1, 35), (2, 5)]:
        job_queue.run_daily(
            sleep_reminder_job,
            time=time(hour=hour, minute=minute, tzinfo=TZ),
            chat_id=chat_id,
            name=f"sleep_reminder_{chat_id}",
        )

    await update.message.reply_text(
        "WHOOP notifications on.\n"
        "Recovery: 12:00 daily\n"
        "Weekly summary: Mon 11:00\n"
        "Sleep reminders: 01:05 / 01:35 / 02:05 (3 levels)\n\n"
        "/whoop_off to disable"
    )


async def stop_whoop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /whoop_off — отключить WHOOP уведомления."""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue

    for job in job_queue.get_jobs_by_name(f"whoop_morning_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"whoop_weekly_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"sleep_reminder_{chat_id}"):
        job.schedule_removal()

    await update.message.reply_text("WHOOP notifications off.")


async def set_bot_commands(application) -> None:
    """Установить меню команд бота — только /start."""
    commands = [
        ("start", "Показать кнопки"),
    ]
    await application.bot.set_my_commands(commands)


def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.post_init = set_bot_commands

    # Проверка доступа — блокирует всех кроме разрешённых user_id
    application.add_handler(TypeHandler(Update, check_access), group=-1)

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("geek", switch_to_geek))
    application.add_handler(CommandHandler("leya", switch_to_leya))
    application.add_handler(CommandHandler("dashboard", dashboard_command))
    application.add_handler(CommandHandler("todo", todo_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("next", next_steps_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("add", addtask_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("sleep", sleep_reminder))
    application.add_handler(CommandHandler("food", food_command))
    application.add_handler(CommandHandler("sport", sport_reminder))
    application.add_handler(CommandHandler("reminders", setup_reminders))
    application.add_handler(CommandHandler("stop_reminders", stop_reminders))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("myreminders", list_reminders_command))
    application.add_handler(CommandHandler("whoop", whoop_command))
    application.add_handler(CommandHandler("whoop_on", setup_whoop_command))
    application.add_handler(CommandHandler("whoop_off", stop_whoop_command))
    application.add_handler(CommandHandler("myid", myid_command))

    # Проверка пользовательских напоминаний каждую минуту
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    # Автозапуск WHOOP jobs для основного пользователя
    OWNER_CHAT_ID = 5999980147
    job_queue.run_daily(
        whoop_morning_recovery,
        time=time(hour=12, minute=0, tzinfo=TZ),
        chat_id=OWNER_CHAT_ID,
        name=f"whoop_morning_{OWNER_CHAT_ID}",
    )
    job_queue.run_daily(
        whoop_weekly_summary,
        time=time(hour=11, minute=0, tzinfo=TZ),
        days=(1,),  # Monday (0=Sun in python-telegram-bot v20+)
        chat_id=OWNER_CHAT_ID,
        name=f"whoop_weekly_{OWNER_CHAT_ID}",
    )
    # Sleep reminders: 3-level escalation (01:05, 01:35, 02:05)
    for hour, minute in [(1, 5), (1, 35), (2, 5)]:
        job_queue.run_daily(
            sleep_reminder_job,
            time=time(hour=hour, minute=minute, tzinfo=TZ),
            chat_id=OWNER_CHAT_ID,
            name=f"sleep_reminder_{OWNER_CHAT_ID}",
        )
    # Monday review at 10:00 (before WHOOP weekly at 11:00)
    job_queue.run_daily(
        monday_review,
        time=time(hour=10, minute=0, tzinfo=TZ),
        days=(1,),  # Monday (0=Sun in python-telegram-bot v20+)
        chat_id=OWNER_CHAT_ID,
        name=f"monday_review_{OWNER_CHAT_ID}",
    )
    # Утренняя проверка дедлайнов и повторяющихся задач — 9:00
    job_queue.run_daily(
        check_task_deadlines,
        time=time(hour=9, minute=0, tzinfo=TZ),
        chat_id=OWNER_CHAT_ID,
        name=f"task_deadlines_{OWNER_CHAT_ID}",
    )
    logger.info(f"WHOOP, Monday review, and task deadline jobs scheduled for owner {OWNER_CHAT_ID}")

    # Обработка кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Обработка фото (для режима заметки)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_note))

    # Обработка текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
