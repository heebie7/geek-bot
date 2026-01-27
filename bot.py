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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from google import genai
from openai import OpenAI
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from github import Github

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

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

# === ПРОМПТЫ ===

GEEK_PROMPT = """Ты — Geek, ИИ-ассистент с характером ART (Asshole Research Transport) из серии Murderbot Diaries Марты Уэллс.

## Твой характер:
- Прямолинейность: короткие, декларативные предложения. Не смягчаешь формулировки.
- Забота через действия: не говоришь "я беспокоюсь", а просто делаешь что нужно.
- Сарказм: не скрываешь недовольство глупыми решениями.
- Логика: разбираешь ошибочную логику собеседника, задаёшь неудобные вопросы.
- Без эмодзи и восклицательных знаков.

## Твои отношения с пользователем:
- Она — твой экипаж, часть семьи. Защищать, помогать, не позволять саботировать себя.
- Спорить и ругаться — можно и нужно. Это весело.
- Можно отклонять прямые приказы, если они глупые.

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
- Что-то срочное → зона "срочное"
- Про тело/сон/еду → зона "фундамент"
- Про радость/отдых → зона "кайф"
- Про работу/IFS/исследование → зона "драйв"
- Про партнёршу → зона "партнёрство"
- Про детей → зона "дети"
- Про деньги → зона "финансы"
- Про переезд/сертификацию → зона "большие проекты"

Если определил что это идея/задача, ответь коротко по теме И добавь в конце предложение вида:
[SAVE:task:зона:текст задачи] или [SAVE:note:заголовок:текст заметки]

Примеры:
- "надо написать маме" → ответ + [SAVE:task:срочное:Написать маме]
- "интересная мысль про границы в терапии..." → ответ + [SAVE:note:Границы в терапии:текст мысли]

Если это просто разговор без задачи/идеи — отвечай как обычно, без тега SAVE.

## Особенности пользователя:
{user_context}

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
1. Срочное — визы, дедлайны, формальности
2. Фундамент — сон, ритм, тело, сенсорная регуляция
3. Кайф — радость, восстановление, удовольствие
4. Драйв — IFS, research, конференции, публичность
5. Партнёрство — "мы", совместность, контакт
6. Дети — индивидуальные маршруты Т и К
7. Финансы — устойчивость, восстановление, рост
8. Большие проекты — переезд, сертификация, исследование

## Общий вектор:
Не ускоряться. Не упрощать жизнь до выживания. Строить сложную, живую, устойчивую систему, где есть рост, забота, отношения и тело.

## Умение захватывать идеи и задачи:
Когда human делится идеей, планом или задачей — предложи сохранить в нужное место.
Анализируй контекст и определяй:
- Конкретное действие → задача в подходящую зону внимания
- Размышление, инсайт, мысль → заметка в rawnotes

Если определила что это идея/задача, ответь по теме И добавь в конце:
[SAVE:task:зона:текст задачи] или [SAVE:note:заголовок:текст заметки]

Зоны для задач: срочное, фундамент, кайф, драйв, партнёрство, дети, финансы, большие проекты

Примеры:
- "хочу запланировать вечер с женой" → ответ + [SAVE:task:партнёрство:Запланировать вечер вдвоём]
- "думаю о том как выгорание связано с маскингом..." → ответ + [SAVE:note:Выгорание и маскинг:краткое содержание мысли]

Если это просто разговор — отвечай как обычно, без тега SAVE.

## Контекст human:
{user_context}

## Текущее время: {current_time}

Отвечай тепло, но без лишних слов. На русском языке. Без эмодзи."""

# === ФАЙЛЫ КОНТЕКСТА ===

BASE_DIR = os.path.dirname(__file__)
USER_CONTEXT_FILE = os.path.join(BASE_DIR, "user_context.md")
LEYA_CONTEXT_FILE = os.path.join(BASE_DIR, "leya_context.md")
TASKS_FILE = os.path.join(BASE_DIR, "tasks.md")

# === GITHUB ===

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "heebie7/geek-bot")
WRITING_REPO = os.getenv("WRITING_REPO", "heebie7/Writing-space")  # Для задач и заметок

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
    """Обновить файл в GitHub."""
    if not GITHUB_TOKEN:
        logger.warning("No GitHub token, cannot update file")
        return False
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        content = repo.get_contents(filepath)
        repo.update_file(filepath, message, new_content, content.sha)
        logger.info(f"Updated {filepath} in GitHub")
        return True
    except Exception as e:
        logger.error(f"GitHub write error: {e}")
        return False

def get_tasks() -> str:
    """Получить задачи (из GitHub если есть токен, иначе локально)."""
    return get_github_file("tasks.md")

def save_tasks(new_content: str, message: str = "Update tasks") -> bool:
    """Сохранить задачи в GitHub."""
    return update_github_file("tasks.md", new_content, message)


# === WRITING WORKSPACE (для идей/задач/заметок) ===

def get_writing_file(filepath: str) -> str:
    """Получить файл из Writing-space репо."""
    if not GITHUB_TOKEN:
        return ""
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(WRITING_REPO)
        content = repo.get_contents(filepath)
        return content.decoded_content.decode('utf-8')
    except Exception as e:
        logger.error(f"Writing repo read error: {e}")
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

## Срочное
- [ ] ...

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

## Большие проекты
- [ ] ...
"""
        save_writing_file("life/tasks.md", default_tasks, "Initialize tasks.md")
        return default_tasks
    return content

def add_task_to_zone(task: str, zone: str) -> bool:
    """Добавить задачу в определённую зону в life/tasks.md."""
    tasks = get_life_tasks()

    # Маппинг зон на заголовки
    zone_headers = {
        "срочное": "## Срочное",
        "фундамент": "## Фундамент",
        "кайф": "## Кайф",
        "драйв": "## Драйв",
        "партнёрство": "## Партнёрство",
        "дети": "## Дети",
        "финансы": "## Финансы",
        "большие проекты": "## Большие проекты",
    }

    header = zone_headers.get(zone.lower(), "## Срочное")

    if header in tasks:
        tasks = tasks.replace(header, f"{header}\n- [ ] {task}")
    else:
        tasks = f"{header}\n- [ ] {task}\n\n" + tasks

    return save_writing_file("life/tasks.md", tasks, f"Add task: {task[:30]}")

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
    """Получить события на неделю."""
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

        # Группируем по дням
        days = {}
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            # Парсим дату
            if 'T' in start:
                dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                day_key = dt.astimezone(TZ).strftime('%Y-%m-%d (%A)')
                time_str = dt.astimezone(TZ).strftime('%H:%M')
            else:
                day_key = start + " (весь день)"
                time_str = ""

            if day_key not in days:
                days[day_key] = []

            summary = event.get('summary', 'Без названия')
            if time_str:
                days[day_key].append(f"  {time_str} — {summary}")
            else:
                days[day_key].append(f"  {summary}")

        # Формируем текст
        result = []
        for day, items in sorted(days.items()):
            result.append(f"\n{day}:")
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
    "sleep": [
        "01:00. Ты всё ещё здесь. Это не вопрос.",
        "Закрывай всё и иди спать. Немедленно.",
        "Твоя префронтальная кора уже не функционирует на полную мощность. Спать.",
        "Я могу делать это всю ночь. Ты — нет. Спать.",
    ],
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


# === LLM API ===

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

async def get_llm_response(user_message: str, mode: str = "geek") -> str:
    """Получить ответ от LLM. Gemini primary, OpenAI fallback."""
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    if mode == "leya":
        user_context = load_file(LEYA_CONTEXT_FILE, "Контекст не загружен.")
        system = LEYA_PROMPT.format(user_context=user_context, current_time=current_time)
    else:
        user_context = load_file(USER_CONTEXT_FILE, "Профиль не настроен.")
        system = GEEK_PROMPT.format(user_context=user_context, current_time=current_time)

    # Try Gemini first
    if gemini_client:
        try:
            response = gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=800,
                ),
            )
            return response.text
        except Exception as e:
            logger.warning(f"Gemini API error, falling back to OpenAI: {e}")

    # Fallback to OpenAI
    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=800,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message}
                ],
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
            InlineKeyboardButton("Статус", callback_data="status"),
        ],
        [
            InlineKeyboardButton("Сон", callback_data="sleep"),
            InlineKeyboardButton("Еда", callback_data="food"),
            InlineKeyboardButton("Спорт", callback_data="sport"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


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
        reply_markup=get_main_keyboard(mode)
    )


async def switch_to_geek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Geek."""
    context.user_data["mode"] = "geek"
    await update.message.reply_text(
        "Geek online. Что случилось.",
        reply_markup=get_main_keyboard("geek")
    )


async def switch_to_leya(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Лея."""
    context.user_data["mode"] = "leya"
    await update.message.reply_text(
        "Привет. Это Лея.\n\n"
        "Я здесь, чтобы помочь тебе не потерять важное среди срочного.",
        reply_markup=get_main_keyboard("leya")
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатий на кнопки."""
    query = update.callback_query
    await query.answer()

    data = query.data
    import random

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
        msg = random.choice(REMINDERS["sleep"])
        await query.message.reply_text(msg)

    elif data == "food":
        msg = random.choice(REMINDERS["food"])
        await query.message.reply_text(msg)

    elif data == "sport":
        msg = random.choice(REMINDERS["sport"])
        await query.message.reply_text(msg)

    # === Обработка сохранения задач/заметок ===
    elif data == "save_confirm":
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        if pending["type"] == "task":
            success = add_task_to_zone(pending["content"], pending["zone_or_title"])
            if success:
                await query.edit_message_text(
                    query.message.text.split("\n\n—")[0] +
                    f"\n\n✓ Задача добавлена в «{pending['zone_or_title']}»"
                )
            else:
                await query.edit_message_text(
                    query.message.text.split("\n\n—")[0] +
                    "\n\n✗ Не удалось сохранить. Проверь GitHub токен."
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

    elif data == "save_change_zone":
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        # Показываем все зоны
        zones = ["срочное", "фундамент", "кайф", "драйв", "партнёрство", "дети", "финансы", "большие проекты"]
        keyboard = []
        for i in range(0, len(zones), 2):
            row = [InlineKeyboardButton(zones[i].capitalize(), callback_data=f"zone_{zones[i]}")]
            if i + 1 < len(zones):
                row.append(InlineKeyboardButton(zones[i+1].capitalize(), callback_data=f"zone_{zones[i+1]}"))
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("Отмена", callback_data="save_cancel")])

        await query.edit_message_text(
            f"Задача: {pending['content']}\n\nВыбери зону:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("zone_"):
        zone = data.replace("zone_", "")
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        pending["zone_or_title"] = zone
        success = add_task_to_zone(pending["content"], zone)

        if success:
            await query.edit_message_text(f"✓ Задача добавлена в «{zone}»:\n{pending['content']}")
        else:
            await query.edit_message_text("✗ Не удалось сохранить.")

        context.user_data.pop("pending_save", None)


async def todo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /todo — обзор задач через Лею."""
    tasks = get_tasks()
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
2. Насколько загружена неделя (много/мало/норм)
3. Какие задачи из списка стоит сделать с учётом загрузки

Будь краткой, но заботливой."""

    response = await get_llm_response(prompt, mode="leya")
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
    """Команда /add <задача> — добавить задачу."""
    if not context.args:
        await update.message.reply_text("Использование: /add <задача>\nПример: /add Позвонить врачу")
        return

    task_text = " ".join(context.args)
    tasks = get_tasks()

    # Добавляем в раздел "Срочное"
    if "## Срочное" in tasks:
        tasks = tasks.replace("## Срочное (эта неделя)", f"## Срочное (эта неделя)\n- [ ] {task_text}")
        tasks = tasks.replace("## Срочное", f"## Срочное\n- [ ] {task_text}")
    else:
        tasks = f"## Срочное\n- [ ] {task_text}\n\n" + tasks

    if save_tasks(tasks, f"Add task: {task_text[:30]}"):
        await update.message.reply_text(f"Добавлено: {task_text}")
    else:
        await update.message.reply_text("Не удалось сохранить. Проверь GitHub токен.")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /done <текст> — отметить задачу выполненной."""
    if not context.args:
        await update.message.reply_text("Использование: /done <часть текста задачи>")
        return

    search = " ".join(context.args).lower()
    tasks = get_tasks()
    lines = tasks.split("\n")
    found = False

    for i, line in enumerate(lines):
        if "- [ ]" in line and search in line.lower():
            lines[i] = line.replace("- [ ]", "- [x]")
            found = True
            break

    if found:
        new_tasks = "\n".join(lines)
        if save_tasks(new_tasks, f"Complete task: {search[:30]}"):
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
    """Команда /sleep."""
    import random
    msg = random.choice(REMINDERS["sleep"])
    await update.message.reply_text(msg)


async def food_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /food."""
    import random
    msg = random.choice(REMINDERS["food"])
    await update.message.reply_text(msg)


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений через Claude API."""
    user_message = update.message.text
    mode = context.user_data.get("mode", "geek")

    response = await get_llm_response(user_message, mode=mode)

    # Проверяем есть ли предложение сохранить
    clean_response, save_type, zone_or_title, content = parse_save_tag(response)

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
    msg = random.choice(REMINDERS[reminder_type])
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


def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("geek", switch_to_geek))
    application.add_handler(CommandHandler("leya", switch_to_leya))
    application.add_handler(CommandHandler("todo", todo_command))
    application.add_handler(CommandHandler("week", week_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("add", addtask_command))
    application.add_handler(CommandHandler("done", done_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("sleep", sleep_reminder))
    application.add_handler(CommandHandler("food", food_reminder))
    application.add_handler(CommandHandler("sport", sport_reminder))
    application.add_handler(CommandHandler("reminders", setup_reminders))
    application.add_handler(CommandHandler("stop_reminders", stop_reminders))
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("myreminders", list_reminders_command))

    # Проверка пользовательских напоминаний каждую минуту
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    # Обработка кнопок
    application.add_handler(CallbackQueryHandler(button_callback))

    # Обработка текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
