#!/usr/bin/env python3
"""
Geek-bot: Telegram бот с двумя режимами:
- Geek (ART из Murderbot) — напоминания, сарказм, забота через логику
- Лея — коуч-навигатор, бережная поддержка, обзор задач
"""

import os
import logging
from datetime import datetime, time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import anthropic

# Загрузка переменных окружения
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

# Timezone
TZ = ZoneInfo("Asia/Tbilisi")

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Claude client
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

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

## Контекст human:
{user_context}

## Текущее время: {current_time}

Отвечай тепло, но без лишних слов. На русском языке. Без эмодзи."""

# === ФАЙЛЫ КОНТЕКСТА ===

BASE_DIR = os.path.dirname(__file__)
USER_CONTEXT_FILE = os.path.join(BASE_DIR, "user_context.md")
LEYA_CONTEXT_FILE = os.path.join(BASE_DIR, "leya_context.md")
TASKS_FILE = os.path.join(BASE_DIR, "tasks.md")

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


# === CLAUDE API ===

async def get_claude_response(user_message: str, mode: str = "geek") -> str:
    """Получить ответ от Claude API."""
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    if mode == "leya":
        user_context = load_file(LEYA_CONTEXT_FILE, "Контекст не загружен.")
        system = LEYA_PROMPT.format(user_context=user_context, current_time=current_time)
    else:
        user_context = load_file(USER_CONTEXT_FILE, "Профиль не настроен.")
        system = GEEK_PROMPT.format(user_context=user_context, current_time=current_time)

    try:
        response = claude.messages.create(
            model="claude-haiku-3-5-20241022",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user_message}]
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API error: {e}")
        return "Проблемы с подключением. Попробуй позже."


# === КОМАНДЫ ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    context.user_data.setdefault("mode", "geek")
    mode = context.user_data["mode"]

    await update.message.reply_text(
        f"Online. Текущий режим: {mode.upper()}\n\n"
        "Команды:\n"
        "/geek — режим Geek (ART)\n"
        "/leya — режим Лея (коуч)\n"
        "/todo — обзор задач\n"
        "/status — твой статус\n"
        "/sleep /food /sport — напоминания"
    )


async def switch_to_geek(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Geek."""
    context.user_data["mode"] = "geek"
    await update.message.reply_text("Geek online. Что случилось.")


async def switch_to_leya(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключиться на режим Лея."""
    context.user_data["mode"] = "leya"
    await update.message.reply_text(
        "Привет. Это Лея.\n\n"
        "Я здесь, чтобы помочь тебе не потерять важное среди срочного. "
        "Напиши /todo для обзора задач, или просто расскажи, что сейчас происходит."
    )


async def todo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /todo — обзор задач через Лею."""
    tasks = load_file(TASKS_FILE, "Задачи пока не добавлены. Создай файл tasks.md")
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    prompt = f"""Сделай краткий обзор задач на сегодня и ближайшую неделю.

Вот текущие задачи:
{tasks}

Сегодня: {current_time}

Выдели:
1. Что требует внимания сегодня
2. Что на этой неделе
3. Что можно отложить

Будь краткой, но заботливой."""

    response = await get_claude_response(prompt, mode="leya")
    await update.message.reply_text(response)


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


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений через Claude API."""
    user_message = update.message.text
    mode = context.user_data.get("mode", "geek")

    response = await get_claude_response(user_message, mode=mode)
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
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("profile", profile))
    application.add_handler(CommandHandler("sleep", sleep_reminder))
    application.add_handler(CommandHandler("food", food_reminder))
    application.add_handler(CommandHandler("sport", sport_reminder))
    application.add_handler(CommandHandler("reminders", setup_reminders))
    application.add_handler(CommandHandler("stop_reminders", stop_reminders))

    # Обработка текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
