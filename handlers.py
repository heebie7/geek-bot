"""
Handler functions for geek-bot.

Command handlers, job functions, message handlers, and callback domain logic.
Imports domain logic from tasks, joy, llm, keyboards, finance, storage modules.
"""

import re
import random
from datetime import datetime, time, timedelta

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import ContextTypes

from config import (
    TZ, logger, OWNER_CHAT_ID, ALLOWED_USER_IDS,
    USER_CONTEXT_FILE, TASKS_FILE,
    ZONE_EMOJI, PROJECT_EMOJI, ALL_DESTINATIONS,
    JOY_CATEGORIES, JOY_CATEGORY_EMOJI,
    REMINDERS, FAMILY_ALIASES,
)
from prompts import (
    SENSORY_INDRA_PROMPT, WHOOP_HEALTH_SYSTEM,
    INDRA_WHOOP_DAILY_PROMPT,
    CAPTAIN_PROMPT, CAPTAIN_REPLY_PROMPT,
    MORNING_SHARED_CONTEXT, INDRA_MORNING_INSPIRATION,
    MAKS_MORNING_INSPIRATION, KSENIA_MORNING_INSPIRATION,
)
from storage import (
    load_file, get_writing_file, save_writing_file,
    get_week_events, register_family_member, get_family_chat_id,
    add_reminder, get_due_reminders, parse_remind_time,
    get_reminders, is_muted, save_morning_cache,
    load_whoop_patterns, load_whoop_baselines,
    load_latest_indra_session,
    load_food_log, save_food_log, load_kitchen_dishes,
)
from tasks import (
    get_life_tasks, add_task_to_zone, complete_task,
    suggest_zone_for_task, create_rawnote, parse_save_tag,
    _task_hash, _get_priority_tasks, _parse_sensory_menu,
    _get_random_sensory_suggestion, _format_sensory_menu_for_prompt,
    _sensory_hardcoded_response, check_task_deadlines, get_today_tasks,
)
from joy import get_joy_stats_week, log_joy, _joy_items_cache
from llm import (
    get_llm_response, get_motivations_for_whoop,
    get_motivations_for_mode, get_sleep_level, _get_whoop_context,
    _is_health_topic,
)
from keyboards import (
    get_main_keyboard, get_reply_keyboard, get_add_keyboard,
    get_note_mode_keyboard, get_sensory_keyboard,
    get_joy_keyboard, get_joy_items_keyboard,
    get_task_confirm_keyboard, get_destination_keyboard,
    get_priority_keyboard,
    food_confirm_keyboard, food_is_food_keyboard,
)
from finance import handle_csv_upload, income_command, process_command  # noqa: F401 — re-exported for bot.py
from whoop import whoop_client
from meal_data import generate_weekly_menu
from food import (
    recognize_food, match_custom_dish, match_kitchen_dish,
    build_food_entry, build_custom_entry,
    format_food_result, format_daily_summary,
)


# ── Command handlers ─────────────────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start."""
    # Автоматическая регистрация для напоминаний
    user = update.effective_user
    if user and user.username:
        chat_id = update.effective_chat.id
        register_family_member(user.username, chat_id)
        logger.info(f"Registered family member: @{user.username} -> {chat_id}")

    # Family members (not in ALLOWED_USER_IDS) — register only, no full access
    if ALLOWED_USER_IDS and user and user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Зарегистрирован. Теперь могу отправлять тебе напоминания.")
        return

    context.user_data.setdefault("mode", "geek")
    mode = context.user_data["mode"]
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



async def captain_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /captain — обзор дел и планов голосом Кэп."""
    chat_id = update.effective_chat.id

    # Собираем данные
    tasks_content = get_life_tasks()
    calendar = get_week_events()
    whoop = _get_whoop_context()
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    # Сокращаем tasks — берём только открытые с приоритетами
    priority_tasks = _get_priority_tasks()

    captain_system = CAPTAIN_PROMPT.format(
        tasks_context=priority_tasks,
        calendar_context=calendar,
        whoop_context=whoop,
        current_time=current_time,
    )

    prompt = "Дай обзор и фокус на сегодня."

    response = await get_llm_response(
        prompt,
        mode="geek",
        max_tokens=1200,
        skip_context=True,
        custom_system=captain_system,
        use_pro=True,
    )

    if response:
        sent = await update.message.reply_text(response)
        context.bot_data[f"captain_msg_{chat_id}"] = sent.message_id
        logger.info(f"Captain message sent, msg_id={sent.message_id}")


async def dashboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /dashboard — быстрый обзор: Сегодня + горит + на этой неделе, с кнопками Done."""
    tasks_content = get_life_tasks()
    now = datetime.now(TZ)
    end_of_week = now + timedelta(days=(6 - now.weekday()))  # Воскресенье
    end_date = end_of_week.strftime("%Y-%m-%d")

    lines = tasks_content.split("\n")
    today_tasks = get_today_tasks()
    high_priority = []
    due_this_week = []

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("- [ ]"):
            continue
        task_text = stripped[6:]

        # Skip tasks already in today_tasks (avoid duplicates)
        if task_text.strip() in [t.strip() for t in today_tasks]:
            continue

        has_high = "⏫" in task_text or "🔺" in task_text
        due_match = re.search(r'📅\s*(\d{4}-\d{2}-\d{2})', task_text)

        if has_high and not due_match:
            high_priority.append(task_text)
        elif due_match:
            due_date = due_match.group(1)
            if due_date <= end_date:
                due_this_week.append(task_text)
            elif has_high:
                high_priority.append(task_text)

    # Собираем все задачи для кнопок (Сегодня первыми)
    all_tasks = today_tasks + high_priority + due_this_week
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
    counter = 1

    if today_tasks:
        msg_lines.append("📅 *Сегодня:*")
        for t in today_tasks:
            display = t.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
            msg_lines.append(f"{counter}. {display}")
            buttons.append([InlineKeyboardButton(
                f"✅ {counter}. {display[:30]}{'...' if len(display) > 30 else ''}",
                callback_data=f"done_{_task_hash(t)}"
            )])
            counter += 1

    if high_priority:
        msg_lines.append("\n🔥 *Горит:*")
        for t in high_priority:
            display = t.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
            msg_lines.append(f"{counter}. {display}")
            buttons.append([InlineKeyboardButton(
                f"✅ {counter}. {display[:30]}{'...' if len(display) > 30 else ''}",
                callback_data=f"done_{_task_hash(t)}"
            )])
            counter += 1

    if due_this_week:
        msg_lines.append("\n📅 *На этой неделе:*")
        for t in due_this_week:
            display = t.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
            msg_lines.append(f"{counter}. {display}")
            buttons.append([InlineKeyboardButton(
                f"✅ {counter}. {display[:30]}{'...' if len(display) > 30 else ''}",
                callback_data=f"done_{_task_hash(t)}"
            )])
            counter += 1

    keyboard = InlineKeyboardMarkup(buttons)
    text = "\n".join(msg_lines)
    try:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
    except Exception:
        # Markdown parsing fails on special chars in tasks — fallback to plain text
        await update.message.reply_text(
            text.replace("*", ""),
            reply_markup=keyboard
        )


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


def parse_remind_tag(response: str) -> tuple:
    """Extract [REMIND:name:text] tag from LLM response.

    Returns (clean_response, name, remind_text) or (response, None, None).
    """
    pattern = r'\[REMIND:([^:]+):([^\]]+)\]'
    match = re.search(pattern, response)
    if match:
        name = match.group(1).strip().lower()
        remind_text = match.group(2).strip()
        clean = response[:match.start()].strip()
        return (clean, name, remind_text)
    return (response, None, None)


def get_remind_time_keyboard(remind_text: str, target_username: str) -> InlineKeyboardMarkup:
    """Inline keyboard with time options for a reminder."""
    keyboard = [
        [
            InlineKeyboardButton("Через 30 мин", callback_data=f"remtime_30m_{target_username}"),
            InlineKeyboardButton("Через 1 час", callback_data=f"remtime_1h_{target_username}"),
        ],
        [
            InlineKeyboardButton("Через 2 часа", callback_data=f"remtime_2h_{target_username}"),
            InlineKeyboardButton("Завтра 10:00", callback_data=f"remtime_tom10_{target_username}"),
        ],
        [
            InlineKeyboardButton("Завтра 14:00", callback_data=f"remtime_tom14_{target_username}"),
            InlineKeyboardButton("Отмена", callback_data="remtime_cancel"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _calc_remind_at(time_code: str) -> datetime:
    """Calculate remind_at from time code."""
    now = datetime.now(TZ)
    if time_code == "30m":
        return now + timedelta(minutes=30)
    elif time_code == "1h":
        return now + timedelta(hours=1)
    elif time_code == "2h":
        return now + timedelta(hours=2)
    elif time_code == "tom10":
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    elif time_code == "tom14":
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=14, minute=0, second=0, microsecond=0)
    return None


async def handle_remind_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle time and recurring selection for LLM-routed reminders."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "remtime_cancel":
        context.user_data.pop("pending_remind", None)
        await query.edit_message_text(query.message.text.split("\n\n—")[0] + "\n\n— Отменено.")
        return

    # Step 2: recurring selection — remrec_{recurring}
    if data.startswith("remrec_"):
        recurring = data[7:]  # once / daily / weekdays / weekly
        if recurring == "once":
            recurring = None

        pending = context.user_data.get("pending_remind")
        if not pending or "remind_at" not in pending:
            await query.edit_message_text("Напоминание устарело. Попробуй ещё раз.")
            return

        remind_at = datetime.fromisoformat(pending["remind_at"])
        target_username = pending["target"]
        remind_text = pending["text"]

        if target_username == "_self":
            target_chat_id = query.from_user.id
            display_target = "тебе"
        else:
            target_chat_id = get_family_chat_id(target_username)
            display_target = f"@{target_username}"

        if not target_chat_id:
            await query.edit_message_text(f"@{target_username} не зарегистрирован.")
            return

        from_user = query.from_user.username or query.from_user.first_name

        if add_reminder(target_chat_id, remind_at, remind_text, from_user, recurring=recurring):
            time_str = remind_at.strftime("%d.%m в %H:%M")
            rec_label = {"daily": " (каждый день)", "weekdays": " (по будням)", "weekly": " (раз в неделю)"}.get(recurring, "")
            base_text = query.message.text.split("\n\n—")[0]
            await query.edit_message_text(
                base_text + f"\n\n— Напомню {display_target} {time_str}: {remind_text}{rec_label}"
            )
            context.user_data.pop("pending_remind", None)
            logger.info(f"Reminder set for {display_target} at {time_str}{rec_label}: {remind_text}")
        else:
            await query.edit_message_text("Не удалось сохранить напоминание.")
        return

    # Step 1: time selection — remtime_{time_code}_{username}
    parts = data.split("_", 2)
    if len(parts) < 3:
        return
    time_code = parts[1]
    target_username = parts[2]

    pending = context.user_data.get("pending_remind")
    if not pending:
        await query.edit_message_text("Напоминание устарело. Попробуй ещё раз.")
        return

    remind_at = _calc_remind_at(time_code)
    if not remind_at:
        await query.edit_message_text("Неизвестное время.")
        return

    # Save time + target for step 2
    pending["remind_at"] = remind_at.isoformat()
    pending["target"] = target_username

    time_str = remind_at.strftime("%H:%M")
    keyboard = [
        [
            InlineKeyboardButton("Один раз", callback_data="remrec_once"),
            InlineKeyboardButton("Каждый день", callback_data="remrec_daily"),
        ],
        [
            InlineKeyboardButton("По будням", callback_data="remrec_weekdays"),
            InlineKeyboardButton("Раз в неделю", callback_data="remrec_weekly"),
        ],
        [InlineKeyboardButton("Отмена", callback_data="remtime_cancel")],
    ]
    base_text = query.message.text.split("\n\n—")[0]
    await query.edit_message_text(
        base_text + f"\n\n— Время: {time_str}. Повторять?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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
        rec = {"daily": " 🔁ежедн", "weekdays": " 🔁будни", "weekly": " 🔁нед"}.get(r.get("recurring"), "")
        lines.append(f"• {time_str} — {r['text']}{rec}")

    await update.message.reply_text("\n".join(lines))



# ── Job functions ────────────────────────────────────────────────────────────


async def check_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Проверить и отправить напоминания (вызывается по таймеру)."""
    due = get_due_reminders()
    for r in due:
        try:
            chat_id = r["chat_id"]
            text = r["text"]
            from_user = r.get("from_user")

            recurring = r.get("recurring")
            rec_icon = " 🔁" if recurring else ""

            if from_user:
                msg = f"⏰ Напоминание от @{from_user}{rec_icon}:\n{text}"
            else:
                msg = f"⏰ Напоминание{rec_icon}:\n{text}"

            await context.bot.send_message(
                chat_id=chat_id,
                text=msg
            )
            logger.info(f"Sent reminder to {chat_id}: {text[:30]}")
        except Exception as e:
            logger.error(f"Failed to send reminder: {e}")


async def send_scheduled_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправить запланированное напоминание."""
    job = context.job
    reminder_type = job.data.get("type", "food")
    reminders = REMINDERS[reminder_type]
    if isinstance(reminders, dict):
        # sleep: выбираем уровень по времени
        level = get_sleep_level() or 1
        msg = random.choice(reminders[level])
    else:
        msg = random.choice(reminders)
    await context.bot.send_message(chat_id=job.chat_id, text=msg)


async def send_finance_csv_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Субботнее напоминание загрузить CSV."""
    job = context.job
    msg = (
        "Суббота. Время финансовой отчётности.\n\n"
        "Экспортируй CSV из Zen Money и PayPal и скинь мне.\n"
        "Zen Money: Ещё → Экспорт → CSV.\n"
        "PayPal: https://www.paypal.com/reports/dlog\n\n"
        "Я сохраню в репо, потом /process для обработки."
    )
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

    # Финансы: суббота 10:00
    job_queue.run_daily(
        send_finance_csv_reminder,
        time=time(hour=10, minute=0, tzinfo=TZ),
        days=(5,),
        chat_id=chat_id,
        name=f"reminder_{chat_id}",
        data={"type": "finance"}
    )

    await update.message.reply_text(
        "Напоминания настроены.\n"
        "Еда: 9:00, 13:00, 19:00\n"
        "Спорт: 11:00\n"
        "Сон: 23:00, 00:00, 01:00\n"
        "Финансы: суббота 10:00\n\n"
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



# ── WHOOP handlers ───────────────────────────────────────────────────────────


def log_whoop_data():
    """Log today's WHOOP data to daily note and update здоровье.md.

    Creates/updates life/health/whoop/YYYY-MM-DD.md with full YAML frontmatter.
    Also maintains legacy life/whoop.md for backward compatibility.
    """
    try:
        today = datetime.now(TZ).strftime("%Y-%m-%d")

        # Gather data from all endpoints
        rec = whoop_client.get_recovery_today()
        sleep = whoop_client.get_sleep_today()
        body = whoop_client.get_body_measurement()
        cycle = whoop_client.get_cycle_today()
        workouts = whoop_client.get_workouts_today()

        # Check we have at least some data
        if not any([rec, sleep, body, cycle]):
            logger.info("No WHOOP data available to log")
            return

        # Generate daily note with full frontmatter
        daily_note = whoop_client.format_daily_note(
            rec=rec, sleep=sleep, body=body, cycle=cycle, workouts=workouts
        )

        # Save as daily file (always overwrites — data may have been updated)
        daily_path = f"life/health/whoop/{today}.md"
        save_writing_file(daily_path, daily_note, f"WHOOP {today}")

        # Legacy: also append to life/whoop.md (will be removed later)
        existing = get_writing_file("life/whoop.md")
        if existing and f"## {today}" not in existing:
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
                rem_ms = stage.get("total_rem_sleep_time_milli", 0)
                deep_ms = stage.get("total_slow_wave_sleep_time_milli", 0)
                light_ms = stage.get("total_light_sleep_time_milli", 0)
                actual_h = round((rem_ms + deep_ms + light_ms) / 3_600_000, 1)
                perf = ss.get("sleep_performance_percentage")
                eff = ss.get("sleep_efficiency_percentage")
                rem_min = round(rem_ms / 60_000)
                deep_min = round(deep_ms / 60_000)
                entry_parts.append(f"- Sleep: {whoop_client.format_hours_min(actual_h)} (perf {perf}%, eff {eff}%)")
                entry_parts.append(f"- REM: {rem_min} min, Deep: {deep_min} min")
            if body:
                w = body.get("weight_kilogram") or body.get("body_mass_kg")
                if w:
                    entry_parts.append(f"- Weight: {round(w, 1)} kg")
            if cycle:
                cs = cycle.get("score", {})
                strain = round(cs.get("strain", 0), 1)
                boxed = "да" if strain >= 5 else "нет"
                entry_parts.append(f"- Strain: {strain} (бокс: {boxed})")
            if len(entry_parts) > 1:
                new_content = existing.rstrip() + "\n\n" + "\n".join(entry_parts) + "\n"
                save_writing_file("life/whoop.md", new_content, f"WHOOP log {today}")

        # Update здоровье.md WHOOP section with latest values
        _update_health_whoop(rec, sleep, body)

        logger.info(f"WHOOP data logged for {today} (daily note + legacy)")
    except Exception as e:
        logger.error(f"WHOOP logging failed: {e}")


def _update_health_whoop(rec, sleep, body):
    """Update the WHOOP tracking section in здоровье.md."""
    health = get_writing_file("life/health/здоровье.md")
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
        save_writing_file("life/health/здоровье.md", updated, "Update WHOOP stats")


async def whoop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /whoop — показать данные WHOOP с мотивацией."""
    args = context.args
    subcommand = args[0].lower() if args else "today"

    if subcommand == "week":
        text = whoop_client.format_weekly_summary()
        cycles = whoop_client.get_cycles_week()
        if cycles:
            strains = [round(c.get("score", {}).get("strain", 0), 1) for c in cycles]
            avg_strain = round(sum(strains) / len(strains), 1)
            text += f"\n\nStrain avg: {avg_strain} (min {min(strains)}, max {max(strains)})"
        workouts = whoop_client.get_workouts_week()
        if workouts:
            from collections import Counter
            sport_counts = Counter(wo.get("sport_name", "?") for wo in workouts)
            wo_summary = ", ".join(f"{name} x{c}" for name, c in sport_counts.most_common())
            text += f"\nТренировки: {wo_summary}"
        else:
            text += "\nТренировки: нет за неделю"
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
            rem = stage.get("total_rem_sleep_time_milli", 0)
            deep = stage.get("total_slow_wave_sleep_time_milli", 0)
            light = stage.get("total_light_sleep_time_milli", 0)
            actual_ms = rem + deep + light
            sleep_hours = round(actual_ms / 3_600_000, 1) if actual_ms else 0

        if cycle:
            strain = round(cycle.get("score", {}).get("strain", 0), 1)

        # Get recovery score for mode determination
        rec_data = whoop_client.get_recovery_today()
        recovery_score = 0
        if rec_data:
            recovery_score = rec_data.get("score", {}).get("recovery_score") or 0

        # Determine mode and get trend
        trend_data = whoop_client.get_trend_3_days()
        trend_down = trend_data.get("direction") == "down"

        if recovery_score < 34 or (recovery_score < 50 and trend_down):
            mode = "recovery"
        elif recovery_score < 50 or trend_down:
            mode = "moderate"
        else:
            mode = "normal"

        # Get motivations (full version with mode awareness)
        motivations = get_motivations_for_mode(mode, sleep_hours, strain, recovery_score)

        # Build data text
        recovery = whoop_client.format_recovery_today()
        sleep = whoop_client.format_sleep_today()
        strain_text = ""
        if cycle:
            strain_text = f"\nStrain: {strain}"

        # Real workouts (today + yesterday, since today might not have synced)
        workouts_today = whoop_client.get_workouts_today()
        workouts_yesterday = whoop_client.get_workouts_yesterday()
        wo_text = ""
        if workouts_today:
            wo_names = [wo.get("sport_name", "?") for wo in workouts_today]
            wo_text += f"\nТренировки сегодня: {', '.join(wo_names)}"
        if workouts_yesterday:
            wo_names = [wo.get("sport_name", "?") for wo in workouts_yesterday]
            wo_text += f"\nТренировки вчера: {', '.join(wo_names)}"
        if not workouts_today and not workouts_yesterday:
            wo_text = "\nТренировки: нет за 2 дня"

        data_text = f"{recovery}\n\n{sleep}{strain_text}{wo_text}"

        color = "green" if recovery_score >= 67 else ("yellow" if recovery_score >= 34 else "red")
        trend = trend_data.get("direction", "stable")
        prev_avg = trend_data.get("prev_avg")
        trend_str = f"{trend} ({prev_avg}% → {recovery_score}%)" if prev_avg else trend

        prompt = f"""Данные WHOOP:
{data_text}

Тренд: {trend_str}
Режим: {mode}

Ты — Geek, ART из Murderbot Diaries. Ты получил данные с датчиков protectee. Проанализируй состояние human и дай рекомендации на день.

Если подходят, используй 1-2 из этих фраз (подставь реальные числа):
{motivations}

Что учесть:
- Цвет зоны recovery: {color}. Зоны: green (67-100%), yellow (34-66%), red (0-33%)
- Начни с данных, потом твой анализ и рекомендации
- Выдели главное: что в норме пропусти, что отклоняется — разбери
- Особое внимание: deep sleep, awake time, HRV тренд — маркеры состояния НС
- Режим "{mode}" — {"рекомендуй отдых, сенсорную диету, лёгкую активность, никаких серьёзных нагрузок. SecUnit говорит: threat level elevated" if mode == "recovery" else "можно тренироваться, но без фанатизма, мониторить" if mode == "moderate" else "обычная мотивация, можно нагружать"}
- Если данные показывают тренировки вчера/сегодня — учти это в рекомендациях
- Формат: данные → анализ (что хорошо/плохо) → рекомендации → мотивация. 6-10 предложений
- Ты ART. Забота через логику и действия. SecUnit мониторит 24/7. Hardware-метафоры. Сарказм допустим. Без эмодзи. На русском."""

        text = await get_llm_response(prompt, mode="geek", max_tokens=1200, skip_context=True, custom_system=WHOOP_HEALTH_SYSTEM, use_pro=True)
        text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()

        log_whoop_data()
        await update.message.reply_text(text)


async def sleep_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send sleep reminder with escalating levels. No LLM — static phrases only."""
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


def get_morning_whoop_data() -> dict:
    """Collect morning WHOOP data for callback handler.

    Returns dict with keys: sleep_hours, strain, recovery, trend,
    prev_avg, workouts_yesterday. Used by whoop_morning_recovery()
    to store in bot_data, and by morning callback to re-fetch if
    bot was restarted between message and button click.
    """
    rec = whoop_client.get_recovery_today()
    sleep = whoop_client.get_sleep_today()
    cycle_yesterday = whoop_client.get_cycle_yesterday()
    trend = whoop_client.get_trend_3_days()

    sleep_hours = 0
    strain = 0
    recovery_score = 0

    if sleep:
        ss = sleep.get("score", {})
        stage = ss.get("stage_summary", {})
        rem = stage.get("total_rem_sleep_time_milli", 0)
        deep = stage.get("total_slow_wave_sleep_time_milli", 0)
        light = stage.get("total_light_sleep_time_milli", 0)
        sleep_hours = round((rem + deep + light) / 3_600_000, 1)

    if rec:
        recovery_score = rec.get("score", {}).get("recovery_score", 0) or 0

    if cycle_yesterday:
        strain = round(cycle_yesterday.get("score", {}).get("strain", 0), 1)

    workouts_yesterday = whoop_client.get_workouts_yesterday()
    wo_names = [wo.get("sport_name", "?") for wo in workouts_yesterday] if workouts_yesterday else []

    trend_direction = trend.get("direction", "stable") if trend else "stable"
    prev_avg = trend.get("prev_avg") if trend else None

    return {
        "sleep_hours": sleep_hours,
        "strain": strain,
        "recovery": recovery_score,
        "trend": trend_direction,
        "prev_avg": prev_avg,
        "workouts_yesterday": wo_names,
    }


async def whoop_morning_data_write(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent morning job — write today's recovery/sleep data to vault for scheduled tasks."""
    try:
        log_whoop_data()
        logger.info("Morning WHOOP data write completed")
    except Exception as e:
        logger.error(f"Morning WHOOP data write failed: {e}")


async def whoop_morning_recovery(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send morning recovery notification with feeling buttons."""
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

        # Sleep data (actual sleep = REM + Deep + Light, not in-bed)
        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            rem = stage.get("total_rem_sleep_time_milli", 0)
            deep = stage.get("total_slow_wave_sleep_time_milli", 0)
            light = stage.get("total_light_sleep_time_milli", 0)
            actual_ms = rem + deep + light
            sleep_hours = round(actual_ms / 3_600_000, 1) if actual_ms else 0
            in_bed_h = round(stage.get("total_in_bed_time_milli", 0) / 3_600_000, 1)
            perf = ss.get("sleep_performance_percentage")
            eff = ss.get("sleep_efficiency_percentage")
            consistency = ss.get("sleep_consistency_percentage")
            resp_rate = ss.get("respiratory_rate")
            awake_ms = stage.get("total_awake_time_milli", 0)
            awake_min = round(awake_ms / 60_000) if awake_ms else 0
            disturbances = stage.get("disturbance_count")
            rem_min = round(rem / 60_000) if rem else 0
            deep_min = round(deep / 60_000) if deep else 0

            fmt = whoop_client.format_hours_min
            sleep_line = f"Сон: {fmt(sleep_hours)} (in bed {fmt(in_bed_h)}, performance {perf}%"
            if eff is not None:
                sleep_line += f", efficiency {eff}%"
            sleep_line += ")"
            data_parts.append(sleep_line)
            data_parts.append(f"Фазы: REM {rem_min}min, Deep {deep_min}min")
            if awake_min or disturbances:
                awake_line = f"Пробуждения: {awake_min}min"
                if disturbances is not None:
                    awake_line += f", {disturbances}x"
                data_parts.append(awake_line)
            if consistency is not None:
                data_parts.append(f"Sleep consistency: {consistency}%")

            # Sleep need (debt tracking)
            sleep_needed = ss.get("sleep_needed", {})
            if sleep_needed:
                base_h = round(sleep_needed.get("baseline_milli", 0) / 3_600_000, 1)
                debt_h = round(sleep_needed.get("need_from_sleep_debt_milli", 0) / 3_600_000, 1)
                strain_need_h = round(sleep_needed.get("need_from_recent_strain_milli", 0) / 3_600_000, 1)
                total_need = round(base_h + debt_h + strain_need_h, 1)
                data_parts.append(f"Потребность во сне: {fmt(total_need)} (база {fmt(base_h)} + долг {fmt(debt_h)} + strain {fmt(strain_need_h)})")

            if resp_rate is not None:
                data_parts.append(f"Respiratory rate: {round(resp_rate, 1)} rpm")

        # Recovery data
        if rec:
            score = rec.get("score", {})
            recovery_score = score.get("recovery_score", 0)
            rhr = score.get("resting_heart_rate")
            hrv = score.get("hrv_rmssd_milli")
            spo2 = score.get("spo2_percentage")
            skin_temp = score.get("skin_temp_celsius")
            if recovery_score is not None:
                color = "green" if recovery_score >= 67 else ("yellow" if recovery_score >= 34 else "red")
                data_parts.append(f"Recovery: {recovery_score}% ({color})")
            if rhr:
                data_parts.append(f"RHR: {rhr} bpm")
            if hrv:
                data_parts.append(f"HRV: {round(hrv, 1)} ms")
            if spo2 is not None:
                data_parts.append(f"SpO2: {spo2}%")
            if skin_temp is not None:
                data_parts.append(f"Skin temp: {round(skin_temp, 1)}°C")

        # Yesterday's strain
        if cycle_yesterday:
            cs = cycle_yesterday.get("score", {})
            strain = round(cs.get("strain", 0), 1)
            data_parts.append(f"Вчера strain: {strain}")

        # Yesterday's workouts (real data)
        workouts_yesterday = whoop_client.get_workouts_yesterday()
        if workouts_yesterday:
            wo_names = [wo.get("sport_name", "?") for wo in workouts_yesterday]
            data_parts.append(f"Тренировки вчера: {', '.join(wo_names)}")
        else:
            data_parts.append("Тренировки вчера: нет")

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
        wo_name_list = [wo.get("sport_name", "?") for wo in workouts_yesterday] if workouts_yesterday else []
        morning_payload = {
            "sleep_hours": sleep_hours,
            "strain": strain,
            "recovery": recovery_score,
            "trend": trend_direction,
            "prev_avg": prev_avg,
            "workouts_yesterday": wo_name_list,
            "data_str": data_str,
        }
        context.bot_data[f"morning_{chat_id}"] = morning_payload
        save_morning_cache(chat_id, morning_payload)

        # ── Одно сообщение: данные + кнопки самочувствия (без LLM) ──
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
            text=f"{data_str}\n\nКак себя чувствуешь?",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

        # ── Сообщение 2: Indra ПНЭИ-интерпретация ──
        try:
            patterns = load_whoop_patterns()
            baselines = load_whoop_baselines()
            last_session = load_latest_indra_session()

            indra_system = INDRA_WHOOP_DAILY_PROMPT.format(
                patterns_context=patterns,
                baselines_context=baselines,
                last_indra_session=last_session,
            )

            indra_prompt = f"""Утренние данные WHOOP:
{data_str}

Дай одно наблюдение через ПНЭИ-линзу и один вопрос."""

            indra_text = await get_llm_response(
                indra_prompt,
                mode="geek",
                max_tokens=400,
                skip_context=True,
                custom_system=indra_system,
                use_pro=True,
            )
            indra_text = re.sub(r'\[SAVE:[^\]]+\]', '', indra_text).strip()
            if indra_text:
                sent = await context.bot.send_message(
                    chat_id=chat_id, text=indra_text,
                )
                # Store message_id for reply-based routing
                context.bot_data[f"indra_msg_{chat_id}"] = sent.message_id
                logger.info(f"Sent Indra daily PNEI to {chat_id}, msg_id={sent.message_id}")
        except Exception as e:
            logger.error(f"Indra daily PNEI failed: {e}")

        log_whoop_data()
        logger.info(f"Sent WHOOP morning data + feeling buttons to {chat_id}")
    except Exception as e:
        logger.error(f"WHOOP morning notification failed: {e}")



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


async def whoop_evening_update(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Silent evening job — update daily note with final strain and workouts."""
    try:
        log_whoop_data()
        logger.info("Evening WHOOP update completed")
    except Exception as e:
        logger.error(f"Evening WHOOP update failed: {e}")


async def setup_whoop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /whoop_on — включить утреннее WHOOP уведомление."""
    chat_id = update.effective_chat.id
    job_queue = context.application.job_queue

    # Remove existing WHOOP jobs for this chat
    for job in job_queue.get_jobs_by_name(f"whoop_morning_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"whoop_evening_{chat_id}"):
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

    # Evening strain update at 23:00 (silent — just logs data, no message)
    job_queue.run_daily(
        whoop_evening_update,
        time=time(hour=23, minute=0, tzinfo=TZ),
        chat_id=chat_id,
        name=f"whoop_evening_{chat_id}",
    )

    # Weekly summary moved to Claude Code scheduled task `health-weekly` (Sun 12:15)

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
        "Strain update: 23:00 daily (silent)\n"
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
    for job in job_queue.get_jobs_by_name(f"whoop_evening_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"whoop_weekly_{chat_id}"):
        job.schedule_removal()
    for job in job_queue.get_jobs_by_name(f"sleep_reminder_{chat_id}"):
        job.schedule_removal()

    await update.message.reply_text("WHOOP notifications off.")



# ── Quote command /q ──────────────────────────────────────────────────────────


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /q <цитата> — сохранить цитату с выбором источника из очереди чтения."""
    from tasks import get_today_reading_sources, save_quote

    if not context.args:
        await update.message.reply_text("Использование: /q <текст цитаты>")
        return

    quote_text = ' '.join(context.args).strip()
    if not quote_text:
        await update.message.reply_text("Цитата пустая.")
        return

    sources = get_today_reading_sources()

    if not sources:
        result = save_quote(quote_text, "reading")
        if result:
            await update.message.reply_text("Сохранено в reading.md 💾")
        else:
            await update.message.reply_text("Не удалось сохранить.")
        return

    context.user_data["pending_quote"] = quote_text

    keyboard = []
    for display_name, slug in sources:
        cb_data = f"quote_src:{slug}"[:64]
        keyboard.append([InlineKeyboardButton(display_name, callback_data=cb_data)])
    keyboard.append([InlineKeyboardButton("Другой источник", callback_data="quote_src:other")])

    preview = quote_text[:80] + ('...' if len(quote_text) > 80 else '')
    await update.message.reply_text(
        f"«{preview}»\n\nКуда сохранить?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ── Group Цитаты topic handler ────────────────────────────────────────────────


async def handle_group_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловит текстовые сообщения в топике Цитаты и показывает выбор источника."""
    from config import READING_GROUP_ID, QUOTES_TOPIC_ID
    from tasks import get_today_reading_sources, save_quote

    msg = update.message
    if not msg or not msg.text:
        return

    # Only in the Цитаты topic
    if msg.chat_id != READING_GROUP_ID:
        return
    if msg.message_thread_id != QUOTES_TOPIC_ID:
        return

    # Если ждём название источника — сохранить цитату с этим названием
    awaiting_quote = context.user_data.pop("quote_awaiting_source", None)
    if awaiting_quote:
        source_name = msg.text.strip()
        if not source_name:
            context.user_data["quote_awaiting_source"] = awaiting_quote
            await msg.reply_text("Напиши название источника:", reply_to_message_id=msg.message_id)
            return
        result = save_quote(awaiting_quote, source_name)
        if result:
            slug = re.sub(r'[^\w\s-]', '', source_name.lower()).replace(' ', '-')[:60]
            await msg.reply_text(f"Сохранено → {slug}.md 💾", reply_to_message_id=msg.message_id)
        else:
            await msg.reply_text("Не удалось сохранить.", reply_to_message_id=msg.message_id)
        return

    quote_text = msg.text.strip()
    if not quote_text:
        return

    sources = get_today_reading_sources()

    if not sources:
        result = save_quote(quote_text, "reading")
        if result:
            await msg.reply_text("Сохранено 💾", reply_to_message_id=msg.message_id)
        return

    context.user_data["pending_quote"] = quote_text

    keyboard = []
    for display_name, slug in sources:
        cb_data = f"quote_src:{slug}"[:64]
        keyboard.append([InlineKeyboardButton(display_name, callback_data=cb_data)])
    keyboard.append([InlineKeyboardButton("Другой источник", callback_data="quote_src:other")])

    await msg.reply_text(
        "Куда?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        reply_to_message_id=msg.message_id,
    )


# ── Channel quote handler ─────────────────────────────────────────────────────


async def handle_channel_quote(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сохранить текстовое сообщение из канала чтения как цитату.

    Игнорирует сообщения от бота (pipeline файлы).
    Сохраняет текст от human как цитату в writing/research/quotes/.
    """
    from config import READING_CHANNEL_ID, OWNER_CHAT_ID
    from tasks import save_quote

    msg = update.channel_post
    if not msg:
        return

    # Только из канала чтения
    if msg.chat_id != READING_CHANNEL_ID:
        return

    # Игнорируем не-текстовые (файлы от pipeline)
    if not msg.text:
        return

    # Игнорируем сообщения от бота (по sender_chat — канал сам себе автор)
    # Текст от human приходит с author_signature или без sender_chat
    # Pipeline файлы приходят через bot API — у них нет author_signature
    if not msg.author_signature and msg.sender_chat:
        # Сообщение от канала (бот постит от имени канала) — пропускаем
        logger.info("Channel quote: skipping bot/channel message (no author_signature)")
        return

    quote_text = msg.text.strip()
    if not quote_text:
        return

    # Определяем источник: если это reply на другое сообщение, берём название оттуда
    source_name = "reading-channel"
    if msg.reply_to_message:
        # Reply на файл или сообщение — пробуем взять caption или текст
        reply = msg.reply_to_message
        if reply.document and reply.document.file_name:
            # Имя файла как источник (убираем расширение)
            fname = reply.document.file_name
            source_name = fname.rsplit('.', 1)[0] if '.' in fname else fname
        elif reply.text:
            # Первая строка текста как источник
            first_line = reply.text.split('\n')[0][:80]
            source_name = first_line

    logger.info(f"Channel quote: saving quote from '{source_name}', len={len(quote_text)}")
    result = save_quote(quote_text, source_name)

    if result:
        # Реакция на сообщение — подтверждение
        try:
            from telegram import ReactionTypeEmoji
            await msg.set_reaction([ReactionTypeEmoji(emoji="\U0001f4be")])  # 💾
        except Exception:
            pass  # Реакции могут не работать в каналах
    else:
        logger.error("Channel quote: failed to save")


# ── Voice message handler ────────────────────────────────────────────────────


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transcribe voice message via Gemini and process as text."""
    from google.genai import types
    from config import gemini_client, GEMINI_MODEL

    voice = update.message.voice
    if not voice:
        return

    if not gemini_client:
        await update.message.reply_text("Gemini недоступен, не могу распознать голосовое.")
        return

    await update.message.chat.send_action("typing")

    # Download .ogg voice file
    file = await voice.get_file()
    audio_bytes = await file.download_as_bytearray()

    # Transcribe via Gemini
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=[
                types.Part.from_bytes(data=bytes(audio_bytes), mime_type="audio/ogg"),
                types.Part(text="Транскрибируй это голосовое сообщение. Верни только текст, без комментариев."),
            ])],
        )
        transcript = response.text.strip() if response.text else None
    except Exception as e:
        logger.error(f"Voice transcription error: {e}")
        await update.message.reply_text("Не удалось распознать голосовое.")
        return

    if not transcript:
        await update.message.reply_text("Не удалось распознать голосовое.")
        return

    logger.info(f"Voice transcribed ({voice.duration}s): {transcript[:100]}")

    # Note mode — save transcript as note
    if context.user_data.get("note_mode"):
        buffer = context.user_data.get("note_buffer", [])
        buffer.append(f"[голосовое]: {transcript}")
        context.user_data["note_buffer"] = buffer
        try:
            from telegram import ReactionTypeEmoji
            await update.message.set_reaction([ReactionTypeEmoji(emoji="👍")])
        except Exception:
            pass
        return

    # Otherwise — process as regular text message
    # Inject transcript into message object and delegate to handle_message
    update.message.text = transcript
    await handle_message(update, context)


# ── Photo and message handlers ───────────────────────────────────────────────


async def handle_photo_note(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка фото: заметки (note_mode) или распознавание еды."""
    if context.user_data.get("note_mode"):
        caption = update.message.caption or "[фото без подписи]"
        buffer = context.user_data.get("note_buffer", [])
        buffer.append(f"[фото]: {caption}")
        context.user_data["note_buffer"] = buffer
        try:
            from telegram import ReactionTypeEmoji
            await update.message.set_reaction([ReactionTypeEmoji(emoji="👍")])
        except Exception:
            pass
        return
    # Not in note mode → food recognition
    await handle_food_photo(update, context)


async def handle_food_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Recognize food from photo via Gemini Vision."""
    # Download photo
    photo = update.message.photo[-1]  # largest size
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()

    caption = update.message.caption or None

    # Typing indicator
    await update.message.chat.send_action("typing")

    # Recognize
    recognition = recognize_food(bytes(photo_bytes), caption)
    confidence = recognition.get("confidence", 0.0)

    if confidence < 0.3:
        # Not food — silently ignore
        return

    # Try kitchen match
    dishes = load_kitchen_dishes()
    match = match_kitchen_dish(recognition.get("name", ""), dishes)
    entry = build_food_entry(recognition, match, caption)

    # Clear any stale pending food entry
    context.user_data.pop("pending_food", None)
    context.user_data["pending_food"] = entry

    text = format_food_result(entry)

    if confidence < 0.6:
        # Mid-confidence — ask if food
        await update.message.reply_text(
            f"Это еда?\n\n{text}",
            reply_markup=food_is_food_keyboard(),
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=food_confirm_keyboard(),
        )


async def handle_food_topic_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages in the Food topic of From Geek group."""
    from config import FOOD_TOPIC_ID, READING_GROUP_ID
    msg = update.message
    # Only react to messages in the Food topic
    if msg.chat_id != READING_GROUP_ID or msg.message_thread_id != FOOD_TOPIC_ID:
        return

    text = msg.text.strip()
    if not text:
        return

    # ── Reply-based weight correction (post-confirmation) ──
    # Detect: reply to bot food message + weight hint like "400г", "по 25г", "уточни: 400г"
    if msg.reply_to_message:
        replied = msg.reply_to_message
        is_food_msg = replied.text and replied.text.startswith("🍽")
        if is_food_msg:
            entry = context.user_data.get("last_confirmed_food")
            if entry:
                import re as _re
                # Parse "по Xг" (per-piece × count from entry name)
                per_piece = _re.search(r"по\s+(\d+)\s*г", text.lower())
                total = _re.search(r"(?:уточни[:\s]+)?(\d+)\s*г", text.lower())
                new_weight = None
                if per_piece:
                    piece_g = int(per_piece.group(1))
                    # Try to extract count from entry name (e.g. "16 роллов")
                    count_m = _re.search(r"(\d+)", entry.get("name", ""))
                    count = int(count_m.group(1)) if count_m else 1
                    new_weight = piece_g * count
                elif total:
                    new_weight = int(total.group(1))
                if new_weight and new_weight > 0:
                    from food import _rescale_entry
                    # Update last log entry for this dish
                    log_data = load_food_log()
                    today = datetime.now(TZ).strftime("%Y-%m-%d")
                    # Find last matching entry in today's log
                    for i in range(len(log_data["log"]) - 1, -1, -1):
                        e = log_data["log"][i]
                        if e.get("date") == today and e.get("name") == entry.get("name"):
                            _rescale_entry(e, new_weight)
                            save_food_log(log_data)
                            context.user_data["last_confirmed_food"] = e
                            result_text = format_food_result(e)
                            summary = format_daily_summary(log_data["log"], log_data.get("daily_targets"), today)
                            await msg.reply_text(f"{result_text}\n\n✅ Пересчитано\n\n{summary}")
                            return
                    await msg.reply_text("Не нашёл запись в логе за сегодня.")
                    return

    # ── Food weight correction mode ──
    if context.user_data.get("food_weight_correcting"):
        expire = context.user_data.get("food_weight_expire")
        if expire and datetime.now(TZ) > expire:
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
        elif text.lower() == "отмена":
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
            entry = context.user_data.get("pending_food")
            if entry:
                result_text = format_food_result(entry)
                await msg.reply_text(result_text, reply_markup=food_confirm_keyboard())
            else:
                await msg.reply_text("Отменено.")
            return
        else:
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
            try:
                new_weight = int(text.replace("г", "").replace("g", ""))
            except ValueError:
                await msg.reply_text("Не понял. Напиши число в граммах.")
                return
            entry = context.user_data.get("pending_food")
            if entry:
                from food import _rescale_entry
                _rescale_entry(entry, new_weight)
                result_text = format_food_result(entry)
                await msg.reply_text(result_text, reply_markup=food_confirm_keyboard())
            else:
                await msg.reply_text("Данные потеряны.")
            return

    # ── Food naming mode (save custom dish with chosen name) ──
    if context.user_data.get("food_naming"):
        expire = context.user_data.get("food_naming_expire")
        if expire and datetime.now(TZ) > expire:
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            context.user_data.pop("last_confirmed_food", None)
        elif text.lower() == "отмена":
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            context.user_data.pop("last_confirmed_food", None)
            await msg.reply_text("Не сохраняю.")
            return
        else:
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            entry = context.user_data.pop("last_confirmed_food", None)
            if entry:
                custom_name = text
                log_data = load_food_log()
                if "custom_dishes" not in log_data:
                    log_data["custom_dishes"] = {}
                log_data["custom_dishes"][custom_name] = {
                    "kcal": entry["kcal"],
                    "protein": entry["protein"],
                    "fat": entry["fat"],
                    "carbs": entry["carbs"],
                    "fiber": entry["fiber"],
                }
                save_food_log(log_data)
                await msg.reply_text(f"⭐ «{custom_name}» сохранено как частое блюдо")
            else:
                await msg.reply_text("Данные потеряны.")
            return

    # Check custom dishes first (instant, no Gemini)
    log_data = load_food_log()
    custom = log_data.get("custom_dishes", {})
    custom_match = match_custom_dish(text, custom)

    if custom_match:
        entry = build_custom_entry(custom_match)
        context.user_data["pending_food"] = entry
        result_text = format_food_result(entry)
        await msg.reply_text(result_text, reply_markup=food_confirm_keyboard())
        return

    # Check kitchen DB
    dishes = load_kitchen_dishes()
    kitchen_match = match_kitchen_dish(text, dishes)
    if kitchen_match:
        recognition = {"name": kitchen_match.get("name", text), "confidence": 0.8}
        entry = build_food_entry(recognition, kitchen_match, text)
        context.user_data["pending_food"] = entry
        result_text = format_food_result(entry)
        await msg.reply_text(result_text, reply_markup=food_confirm_keyboard())
        return

    # Fall back to Gemini text-only recognition
    await msg.chat.send_action("typing")
    recognition = recognize_food(None, text)
    confidence = recognition.get("confidence", 0.0)

    if confidence < 0.3:
        await msg.reply_text("Не распознал. Попробуй точнее.")
        return

    entry = build_food_entry(recognition, None, text)
    entry["source"] = "text"
    context.user_data["pending_food"] = entry
    result_text = format_food_result(entry)

    if confidence < 0.6:
        await msg.reply_text(f"Это еда?\n\n{result_text}", reply_markup=food_is_food_keyboard())
    else:
        await msg.reply_text(result_text, reply_markup=food_confirm_keyboard())


async def handle_food_topic_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo messages in the Food topic of From Geek group."""
    from config import FOOD_TOPIC_ID, READING_GROUP_ID
    msg = update.message
    if msg.chat_id != READING_GROUP_ID or msg.message_thread_id != FOOD_TOPIC_ID:
        return

    # Download photo
    photo = msg.photo[-1]
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()
    caption = msg.caption or None

    await msg.chat.send_action("typing")
    recognition = recognize_food(bytes(photo_bytes), caption)
    confidence = recognition.get("confidence", 0.0)

    if confidence < 0.3:
        return  # not food, ignore

    # Try custom dishes if caption provided
    log_data = load_food_log()
    custom = log_data.get("custom_dishes", {})
    custom_match = match_custom_dish(recognition.get("name", ""), custom) if custom else None

    if custom_match:
        entry = build_custom_entry(custom_match)
    else:
        dishes = load_kitchen_dishes()
        kitchen_match = match_kitchen_dish(recognition.get("name", ""), dishes)
        entry = build_food_entry(recognition, kitchen_match, caption)

    context.user_data.pop("pending_food", None)
    context.user_data["pending_food"] = entry
    text = format_food_result(entry)

    if confidence < 0.6:
        await msg.reply_text(f"Это еда?\n\n{text}", reply_markup=food_is_food_keyboard())
    else:
        await msg.reply_text(text, reply_markup=food_confirm_keyboard())


async def handle_food_confirm(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user confirmed food entry."""
    entry = context.user_data.pop("pending_food", None)
    if not entry:
        await query.edit_message_text("Данные потеряны. Отправь фото ещё раз.")
        return

    log_data = load_food_log()
    log_data["log"].append(entry)
    save_food_log(log_data)

    summary = format_daily_summary(log_data["log"], log_data.get("daily_targets"), entry["date"])
    original = query.message.text or ""

    # Offer to save as custom dish if not already from custom/kitchen
    if entry.get("source") not in ("custom", "kitchen_match"):
        from keyboards import food_save_custom_keyboard
        context.user_data["last_confirmed_food"] = entry
        await query.edit_message_text(
            f"{original}\n\n✅ Записано\n\n{summary}",
            reply_markup=food_save_custom_keyboard(),
        )
    else:
        await query.edit_message_text(f"{original}\n\n✅ Записано\n\n{summary}")
    return


async def handle_food_cancel(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user rejected food entry."""
    context.user_data.pop("pending_food", None)
    await query.edit_message_text("Отменено.")


async def handle_food_correct(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user wants to correct food recognition."""
    context.user_data["food_correcting"] = True
    context.user_data["food_correct_expire"] = datetime.now(TZ) + timedelta(minutes=5)
    await query.edit_message_text("Напиши что это было (например: 'гречка с курицей').\nИли 'отмена' для отмены.")


async def handle_food_weight(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: user wants to correct weight. Recalculates KBJU proportionally."""
    entry = context.user_data.get("pending_food")
    if not entry:
        await query.answer("Данные потеряны.")
        return
    old_weight = entry.get("weight_g", 0)
    hint = f" (сейчас: {old_weight}г)" if old_weight else ""
    context.user_data["food_weight_correcting"] = True
    context.user_data["food_weight_expire"] = datetime.now(TZ) + timedelta(minutes=5)
    await query.edit_message_text(f"Сколько грамм?{hint}\nИли 'отмена'.")


async def handle_food_save_custom(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: ask user for custom dish name, then save."""
    entry = context.user_data.get("last_confirmed_food")
    if not entry:
        await query.answer("Данные потеряны.")
        return

    context.user_data["food_naming"] = True
    context.user_data["food_naming_expire"] = datetime.now(TZ) + timedelta(minutes=5)
    suggested = entry.get("name", "?")
    original = query.message.text or ""
    await query.edit_message_text(
        f"{original}\n\nПод каким именем сохранить? (например: «печенье bombbar»)\n"
        f"Или просто Enter/отправь «{suggested}» если ок.\n"
        f"«отмена» — не сохранять."
    )


async def handle_food_skip_custom(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback: decline saving as custom dish."""
    context.user_data.pop("last_confirmed_food", None)
    original = query.message.text or ""
    await query.edit_message_text(original)


async def morning_inspiration(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Утреннее вдохновение: три голоса (Indra + Maks + Ksenia) с учётом WHOOP + календаря.

    Запускается по расписанию утром. Шлёт 3 коротких сообщения после основного WHOOP.
    Каждая персона даёт 2-5 предложений: тёплое, практическое, по своей территории.
    """
    job = context.job
    chat_id = job.chat_id or OWNER_CHAT_ID

    if is_muted(chat_id):
        return

    try:
        # 1. WHOOP today's snapshot (short — we don't need the whole morning detail)
        whoop_lines = []
        try:
            rec = whoop_client.get_recovery_today()
            sleep = whoop_client.get_sleep_today()
            if rec:
                score = rec.get("score", {})
                rs = score.get("recovery_score")
                rhr = score.get("resting_heart_rate")
                hrv = score.get("hrv_rmssd_milli")
                if rs is not None:
                    color = "green" if rs >= 67 else ("yellow" if rs >= 34 else "red")
                    whoop_lines.append(f"Recovery: {rs}% ({color})")
                if rhr:
                    whoop_lines.append(f"RHR: {rhr} bpm")
                if hrv:
                    whoop_lines.append(f"HRV: {round(hrv, 1)} ms")
            if sleep:
                stage = sleep.get("score", {}).get("stage_summary", {})
                rem = stage.get("total_rem_sleep_time_milli", 0)
                deep = stage.get("total_slow_wave_sleep_time_milli", 0)
                light = stage.get("total_light_sleep_time_milli", 0)
                actual_h = round((rem + deep + light) / 3_600_000, 1)
                if actual_h:
                    whoop_lines.append(f"Сон: {actual_h}h (deep {round(deep/60_000)} min)")
        except Exception as e:
            logger.warning(f"morning_inspiration: WHOOP fetch failed: {e}")
        whoop_data = "\n".join(whoop_lines) if whoop_lines else "Нет данных"

        # 2. Calendar: сегодня + следующие 7 дней
        calendar_text = "Нет данных"
        week_load = "Нет данных"
        try:
            week_events = get_week_events()
            calendar_text = week_events  # Already formatted with СЕГОДНЯ/ЗАВТРА markers

            # Extract just today's portion for calendar_today (up to first double newline after СЕГОДНЯ)
            if "СЕГОДНЯ" in week_events:
                today_idx = week_events.index("СЕГОДНЯ")
                # Find next date header or end
                rest = week_events[today_idx:]
                lines = rest.split("\n")
                today_lines = []
                for i, line in enumerate(lines):
                    if i == 0:
                        today_lines.append(line)
                    elif line.startswith("  "):  # indented event
                        today_lines.append(line)
                    elif line.strip() == "":
                        continue
                    else:
                        break  # next day's header
                calendar_text = "\n".join(today_lines) if today_lines else "Без событий"

            # Count total events for the week load hint
            lines_all = week_events.split("\n")
            event_lines = [l for l in lines_all if l.startswith("  ")]
            weeks_count = len(event_lines)
            week_load = f"Всего событий на ближайшие 7 дней: {weeks_count}"
        except Exception as e:
            logger.warning(f"morning_inspiration: calendar fetch failed: {e}")

        # 3. Build shared context
        shared_context = MORNING_SHARED_CONTEXT.format(
            whoop_data=whoop_data,
            calendar_today=calendar_text,
            week_load=week_load,
        )

        # 4. Ask Indra — warm parasympathetic frame
        try:
            indra_system = INDRA_MORNING_INSPIRATION.format(shared_context=shared_context)
            indra_reply = await get_llm_response(
                user_message="Утреннее вдохновение через ПНЭИ-линзу. Коротко, тепло, предлагая.",
                custom_system=indra_system,
                max_tokens=500,
                skip_context=True,
                use_pro=True,
                no_continue=True,
            )
            indra_reply = re.sub(r'\[SAVE:[^\]]+\]', '', indra_reply or '').strip()
            if indra_reply:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"*Indra:*\n{indra_reply}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"morning_inspiration: Indra failed: {e}")

        # 5. Ask Maks — practical food/body anchor
        try:
            maks_system = MAKS_MORNING_INSPIRATION.format(shared_context=shared_context)
            maks_reply = await get_llm_response(
                user_message="Утренний практический якорь по еде/телу. Коротко.",
                custom_system=maks_system,
                max_tokens=500,
                skip_context=True,
                use_pro=True,
                no_continue=True,
            )
            maks_reply = (maks_reply or '').strip()
            if maks_reply:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"*Макс:*\n{maks_reply}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"morning_inspiration: Maks failed: {e}")

        # 6. Ask Ksenia — body / movement / DST
        try:
            ksenia_system = KSENIA_MORNING_INSPIRATION.format(shared_context=shared_context)
            ksenia_reply = await get_llm_response(
                user_message="Утренний телесный якорь с учётом ДСТ. Коротко.",
                custom_system=ksenia_system,
                max_tokens=500,
                skip_context=True,
                use_pro=True,
                no_continue=True,
            )
            ksenia_reply = (ksenia_reply or '').strip()
            if ksenia_reply:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"*Ксения:*\n{ksenia_reply}",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"morning_inspiration: Ksenia failed: {e}")

        logger.info(f"Sent morning inspiration (Indra+Maks+Ksenia) to {chat_id}")
    except Exception as e:
        logger.error(f"morning_inspiration top-level failed: {e}")


async def handle_food_quick_add(query, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Callback: instant log a custom dish from the Food quick keyboard.

    data format: 'fq:<index>' where index is position in sorted custom_dishes.
    """
    try:
        idx = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        await query.answer("Некорректный ID блюда.")
        return

    log_data = load_food_log()
    custom = log_data.get("custom_dishes", {})
    names = sorted(custom.keys())
    if idx < 0 or idx >= len(names):
        await query.answer("Блюдо не найдено.")
        return

    dish_name = names[idx]
    dish_data = custom[dish_name]
    dish = {"name": dish_name, **dish_data}

    # Build entry and commit immediately (no confirm step — quick add is the whole point)
    entry = build_custom_entry(dish)
    log_data["log"].append(entry)
    save_food_log(log_data)

    # Show short result + daily totals
    ca = entry.get("calcium", 0) or 0
    ca_str = f" | Ca: {ca}мг" if ca > 0 else ""
    line = (
        f"✅ {entry['name']}\n"
        f"Б: {entry['protein']}г | Ж: {entry['fat']}г | У: {entry['carbs']}г | "
        f"Клетч: {entry['fiber']}г{ca_str} | {entry['kcal']} kcal"
    )
    summary = format_daily_summary(log_data["log"], log_data.get("daily_targets"), entry["date"])
    await query.edit_message_text(f"{line}\n\n{summary}")


async def ns_checkin_prompt(context: ContextTypes.DEFAULT_TYPE) -> None:
    """21:00 daily job: ask how the nervous system is doing today."""
    from keyboards import ns_checkin_keyboard
    chat_id = context.job.chat_id or OWNER_CHAT_ID
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Как нервная система сегодня?",
            reply_markup=ns_checkin_keyboard(),
        )
    except Exception as e:
        logger.error(f"NS checkin prompt failed: {e}")


NS_STATE_LABELS = {
    "ns_ok": "ок",
    "ns_meh": "так себе",
    "ns_bad": "плохо",
    "ns_spasm": "спазм/боль",
}

NS_HELPED_LABELS = {
    "nsh_noshpa": "ношпа",
    "nsh_gaba": "валерьянка/ГАБА",
    "nsh_touch": "сорегуляция (поглаживание)",
    "nsh_hammock": "гамак",
    "nsh_other": "другое",
    "nsh_nothing": "ничего",
}


async def handle_ns_state(query, context) -> None:
    """Handle NS check-in state button."""
    from keyboards import ns_helped_keyboard
    from storage import save_ns_checkin

    state = NS_STATE_LABELS.get(query.data, query.data)
    context.user_data["ns_state"] = state

    if query.data == "ns_ok":
        save_ns_checkin(state)
        await query.edit_message_text(f"НС сегодня: {state}. Записано.")
    else:
        await query.edit_message_text(
            f"НС сегодня: {state}.\n\nЧто помогло (или помогает)?",
            reply_markup=ns_helped_keyboard(),
        )


async def handle_ns_helped(query, context) -> None:
    """Handle NS check-in helped button."""
    from storage import save_ns_checkin

    state = context.user_data.pop("ns_state", "не указано")
    helped = NS_HELPED_LABELS.get(query.data, query.data)

    if query.data == "nsh_other":
        context.user_data["ns_helped_waiting"] = True
        context.user_data["ns_state_saved"] = state
        await query.edit_message_text(
            f"НС сегодня: {state}.\nНапиши, что помогло (текстом):"
        )
    else:
        save_ns_checkin(state, helped)
        await query.edit_message_text(f"НС сегодня: {state}. Помогло: {helped}. Записано.")

        if state in ("плохо", "спазм/боль"):
            await query.message.reply_text(
                "Протокол при спазме: ношпа + валерьянка/ГАБА + сорегуляция (поглаживание). "
                "Позвать Индру? /indra"
            )


async def food_evening_summary(context: ContextTypes.DEFAULT_TYPE) -> None:
    """22:00 daily job: send food summary for the day + Maks commentary."""
    log_data = load_food_log()
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    summary = format_daily_summary(log_data["log"], log_data.get("daily_targets"), today)
    chat_id = context.job.chat_id or OWNER_CHAT_ID

    # Base summary (numbers)
    base_text = f"🍽 Итоги дня по еде:\n\n{summary}"

    # If there is no food data today, send only the stub
    if "Данных по еде за сегодня нет" in summary:
        await context.bot.send_message(chat_id=chat_id, text=base_text)
        return

    # Ask Maks for a short evening commentary
    try:
        from prompts import MAKS_FOOD_EVENING_PROMPT
        from llm import get_llm_response

        maks_system = MAKS_FOOD_EVENING_PROMPT.format(food_data=summary)
        maks_reply = await get_llm_response(
            user_message="Посмотри что у меня было за сегодня по еде. Короткий комментарий и один шаг на завтра.",
            custom_system=maks_system,
            max_tokens=600,
            skip_context=True,
            no_continue=True,
        )
        await context.bot.send_message(chat_id=chat_id, text=base_text)
        await context.bot.send_message(chat_id=chat_id, text=f"*Макс:*\n{maks_reply}", parse_mode="Markdown")
        return
    except Exception as e:
        logger.error(f"Maks evening commentary failed: {e}")

    await context.bot.send_message(chat_id=chat_id, text=base_text)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка текстовых сообщений."""
    user_message = update.message.text
    mode = context.user_data.get("mode", "geek")

    # ── NS check-in "other" text mode ──
    if context.user_data.get("ns_helped_waiting"):
        context.user_data.pop("ns_helped_waiting", None)
        state = context.user_data.pop("ns_state_saved", "не указано")
        from storage import save_ns_checkin
        save_ns_checkin(state, user_message.strip())
        await update.message.reply_text(f"НС: {state}. Помогло: {user_message.strip()}. Записано.")
        return

    # ── Food weight correction mode ──
    if context.user_data.get("food_weight_correcting"):
        expire = context.user_data.get("food_weight_expire")
        if expire and datetime.now(TZ) > expire:
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
        elif user_message and user_message.lower().strip() == "отмена":
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
            entry = context.user_data.get("pending_food")
            if entry:
                text = format_food_result(entry)
                await update.message.reply_text(text, reply_markup=food_confirm_keyboard())
            else:
                await update.message.reply_text("Отменено.")
            return
        elif user_message:
            context.user_data.pop("food_weight_correcting", None)
            context.user_data.pop("food_weight_expire", None)
            try:
                new_weight = int(user_message.strip().replace("г", "").replace("g", ""))
            except ValueError:
                await update.message.reply_text("Не понял. Напиши число в граммах.")
                return
            entry = context.user_data.get("pending_food")
            if entry:
                from food import _rescale_entry
                _rescale_entry(entry, new_weight)
                text = format_food_result(entry)
                await update.message.reply_text(text, reply_markup=food_confirm_keyboard())
            else:
                await update.message.reply_text("Данные потеряны.")
            return

    # ── Food naming mode (save custom dish with chosen name) ──
    if context.user_data.get("food_naming"):
        expire = context.user_data.get("food_naming_expire")
        if expire and datetime.now(TZ) > expire:
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            context.user_data.pop("last_confirmed_food", None)
        elif user_message and user_message.lower().strip() == "отмена":
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            context.user_data.pop("last_confirmed_food", None)
            await update.message.reply_text("Не сохраняю.")
            return
        elif user_message:
            context.user_data.pop("food_naming", None)
            context.user_data.pop("food_naming_expire", None)
            entry = context.user_data.pop("last_confirmed_food", None)
            if entry:
                custom_name = user_message.strip()
                log_data = load_food_log()
                if "custom_dishes" not in log_data:
                    log_data["custom_dishes"] = {}
                log_data["custom_dishes"][custom_name] = {
                    "kcal": entry["kcal"],
                    "protein": entry["protein"],
                    "fat": entry["fat"],
                    "carbs": entry["carbs"],
                    "fiber": entry["fiber"],
                }
                save_food_log(log_data)
                await update.message.reply_text(f"⭐ «{custom_name}» сохранено как частое блюдо")
            else:
                await update.message.reply_text("Данные потеряны.")
            return

    # ── Food correction mode (before all other routing) ──
    if context.user_data.get("food_correcting"):
        expire = context.user_data.get("food_correct_expire")
        if expire and datetime.now(TZ) > expire:
            # Expired — clear state and fall through
            context.user_data.pop("food_correcting", None)
            context.user_data.pop("food_correct_expire", None)
        elif user_message and user_message.lower().strip() == "отмена":
            context.user_data.pop("food_correcting", None)
            context.user_data.pop("food_correct_expire", None)
            await update.message.reply_text("Отменено.")
            return
        elif user_message:
            context.user_data.pop("food_correcting", None)
            context.user_data.pop("food_correct_expire", None)
            # Re-recognize with text as caption (no photo)
            await update.message.chat.send_action("typing")
            recognition = recognize_food(None, user_message)
            confidence = recognition.get("confidence", 0.0)
            if confidence < 0.3:
                await update.message.reply_text("Не удалось распознать. Попробуй описать точнее.")
                return
            dishes = load_kitchen_dishes()
            match = match_kitchen_dish(recognition.get("name", ""), dishes)
            entry = build_food_entry(recognition, match, user_message)
            context.user_data["pending_food"] = entry
            text = format_food_result(entry)
            if confidence < 0.6:
                await update.message.reply_text(
                    f"Это еда?\n\n{text}",
                    reply_markup=food_is_food_keyboard("fix"),
                )
            else:
                await update.message.reply_text(text, reply_markup=food_confirm_keyboard("fix"))
            return

    # Обработка кнопок reply keyboard
    if user_message == "🔥 Dashboard":
        await dashboard_command(update, context)
        return
    elif user_message == "🍽 Food":
        # Show quick-add keyboard with custom dishes
        from keyboards import food_quick_keyboard
        log_data = load_food_log()
        custom = log_data.get("custom_dishes", {})
        if not custom:
            await update.message.reply_text(
                "Частых блюд пока нет. Отправь фото или описание еды текстом — после подтверждения предложу сохранить."
            )
            return
        await update.message.reply_text(
            f"Частые блюда ({len(custom)}). Клик — записать приём.",
            reply_markup=food_quick_keyboard(custom),
        )
        return
    elif user_message == "⚡ Шаги":
        await next_steps_command(update, context)
        return
    elif user_message in ("➕ Add", "📝 Note"):
        context.user_data["note_mode"] = True
        context.user_data["note_buffer"] = []
        await update.message.reply_text(
            "Режим заметки. Пересылай сообщения или пиши текст.\n"
            "Когда закончишь — нажми Готово.",
            reply_markup=get_note_mode_keyboard()
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
        logger.info(f"Note mode: received message, buffer size before={len(buffer)}")

        text = update.message.text or update.message.caption or ""
        if text:
            buffer.append(text)
            context.user_data["note_buffer"] = buffer
            logger.info(f"Note mode: added message to buffer, size after={len(buffer)}")

            # Тихий сбор: реакция вместо ответа
            try:
                from telegram import ReactionTypeEmoji
                await update.message.set_reaction([ReactionTypeEmoji(emoji="👍")])
            except Exception:
                pass
        else:
            logger.warning(f"Note mode: received message with no text or caption")
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

    # ── "Сегодня" shortcut: message starting with "сегодня" → add tasks to Сегодня ──
    if user_message and user_message.lower().startswith("сегодня"):
        # Strip "сегодня" prefix and any following punctuation
        rest = re.sub(r'^сегодня[\s:,\-—]*', '', user_message, flags=re.IGNORECASE).strip()
        if rest:
            # Split into tasks: by newlines first, then by commas if single line
            lines = [l.strip() for l in rest.split("\n") if l.strip()]
            if len(lines) == 1 and "," in lines[0]:
                lines = [t.strip() for t in lines[0].split(",") if t.strip()]
            # Clean up bullet prefixes
            tasks = []
            for line in lines:
                task = line.lstrip("-*•").strip()
                if task and task[0].isdigit():
                    task = task.lstrip("0123456789").lstrip(".)").strip()
                if task:
                    tasks.append(task)
            if tasks:
                added = 0
                for task in tasks:
                    if add_task_to_zone(task, "сегодня"):
                        added += 1
                if added == 1:
                    await update.message.reply_text(f"Добавлено в Сегодня: {tasks[0]}")
                else:
                    task_list = "\n".join(f"• {t}" for t in tasks[:added])
                    await update.message.reply_text(f"Добавлено {added} задач в Сегодня:\n{task_list}")
                return

    # ── Reply-based routing: if replying to Indra's message → Indra responds ──
    reply_msg = update.message.reply_to_message
    if reply_msg:
        chat_id = update.effective_chat.id
        indra_msg_id = context.bot_data.get(f"indra_msg_{chat_id}")
        if indra_msg_id and reply_msg.message_id == indra_msg_id:
            try:
                patterns = load_whoop_patterns()
                baselines = load_whoop_baselines()
                last_session = load_latest_indra_session()

                indra_system = INDRA_WHOOP_DAILY_PROMPT.format(
                    patterns_context=patterns,
                    baselines_context=baselines,
                    last_indra_session=last_session,
                )

                # Include Indra's original message as context
                indra_original = reply_msg.text or ""
                indra_reply_prompt = f"Ты написала:\n{indra_original}\n\nHuman ответила:\n{user_message}"

                indra_response = await get_llm_response(
                    indra_reply_prompt,
                    mode="geek",
                    max_tokens=500,
                    skip_context=True,
                    custom_system=indra_system,
                    use_pro=True,
                )
                indra_response = re.sub(r'\[SAVE:[^\]]+\]', '', indra_response).strip()
                if indra_response:
                    sent = await update.message.reply_text(indra_response)
                    context.bot_data[f"indra_msg_{chat_id}"] = sent.message_id
                    logger.info(f"Indra reply-based response, msg_id={sent.message_id}")
                return
            except Exception as e:
                logger.error(f"Indra reply routing failed: {e}")
                # Fall through to normal handling

        # ── Reply-based routing: Captain ──
        captain_msg_id = context.bot_data.get(f"captain_msg_{chat_id}")
        if captain_msg_id and reply_msg.message_id == captain_msg_id:
            try:
                priority_tasks = _get_priority_tasks()
                current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

                captain_system = CAPTAIN_REPLY_PROMPT.format(
                    tasks_context=priority_tasks,
                    current_time=current_time,
                )

                captain_original = reply_msg.text or ""
                captain_reply_prompt = f"Ты написала:\n{captain_original}\n\nHuman ответила:\n{user_message}"

                captain_response = await get_llm_response(
                    captain_reply_prompt,
                    mode="geek",
                    max_tokens=600,
                    skip_context=True,
                    custom_system=captain_system,
                    use_pro=True,
                )
                captain_response = re.sub(r'\[SAVE:[^\]]+\]', '', captain_response).strip()
                if captain_response:
                    sent = await update.message.reply_text(captain_response)
                    context.bot_data[f"captain_msg_{chat_id}"] = sent.message_id
                    logger.info(f"Captain reply-based response, msg_id={sent.message_id}")
                return
            except Exception as e:
                logger.error(f"Captain reply routing failed: {e}")
                # Fall through to normal handling

    # История диалога: последние 20 сообщений (10 пар user+assistant)
    history = context.user_data.get("history", [])

    # Sleep protocol: трёхуровневая эскалация
    sleep_level = get_sleep_level()

    response = await get_llm_response(
        user_message, mode=mode, history=history,
        use_pro=_is_health_topic(user_message),
    )

    # Проверяем есть ли предложение сохранить
    clean_response, save_type, zone_or_title, content = parse_save_tag(response)

    # Проверяем есть ли REMIND-тег
    remind_response, remind_name, remind_text = parse_remind_tag(clean_response or response)
    if remind_name:
        clean_response = remind_response

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
    elif remind_name:
        # LLM detected a reminder request — resolve name and show time buttons
        is_self = remind_name in ("мне", "себе", "себя")
        if is_self:
            target_key = "_self"
            label = "тебе"
        else:
            target_key = FAMILY_ALIASES.get(remind_name)
            label = f"@{target_key}" if target_key else None

        if target_key:
            context.user_data["pending_remind"] = {"text": remind_text}
            time_kb = get_remind_time_keyboard(remind_text, target_key)
            await update.message.reply_text(
                clean_response + f"\n\n— Когда напомнить {label}?",
                reply_markup=time_kb,
            )
        else:
            # Unknown name — fall back to regular response
            await update.message.reply_text(
                clean_response + f"\n\n— Не знаю кто такой «{remind_name}». Добавь в family."
            )
    else:
        await update.message.reply_text(response)


# ── Translation topic handlers ───────────────────────────────────────────


def recognize_cube_face(photo_bytes: bytes) -> dict:
    """Read exercise name and reps from a movement cube face photo via Gemini Vision."""
    from config import gemini_client, GEMINI_MODEL
    from google.genai import types as gtypes

    if not gemini_client:
        return {"exercise": None}

    prompt = (
        "Это грань куба с упражнением. Прочитай текст на картинке.\n"
        "Верни JSON строго такого вида:\n"
        '{"exercise": "название упражнения", "reps": "количество/время как написано на кубике"}\n'
        "Если текст не читается — верни {\"exercise\": null}.\n"
        "Только JSON, без пояснений."
    )
    parts = [
        gtypes.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"),
        gtypes.Part(text=prompt),
    ]
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[gtypes.Content(parts=parts)],
        )
        import re as _re, json as _json
        raw = response.text.strip()
        raw = _re.sub(r'^```(?:json)?\s*', '', raw)
        raw = _re.sub(r'\s*```$', '', raw)
        return _json.loads(raw)
    except Exception as e:
        logger.error(f"Cube face recognition error: {e}")
        return {"exercise": None}


async def handle_movement_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle cube face photos in the movement topic → log exercise."""
    from config import READING_GROUP_ID, MOVEMENT_TOPIC_ID, MOVEMENT_LOG_FILE
    from storage import get_writing_file, save_writing_file
    from datetime import datetime, timezone
    import pytz

    msg = update.message
    if msg.chat_id != READING_GROUP_ID or msg.message_thread_id != MOVEMENT_TOPIC_ID:
        return

    photo = msg.photo[-1]
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()

    await msg.chat.send_action("typing")
    result = recognize_cube_face(bytes(photo_bytes))
    exercise = result.get("exercise")
    reps = result.get("reps", "")

    if not exercise:
        await msg.reply_text("Не удалось прочитать грань кубика.")
        return

    tz = pytz.timezone("Asia/Tbilisi")
    now = datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    entry_line = f"- {time_str} — {exercise} ({reps})" if reps else f"- {time_str} — {exercise}"

    # Read or create log file
    existing = get_writing_file(MOVEMENT_LOG_FILE)
    if not existing:
        existing = "---\ntype: movement-log\n---\n\n# Movement Log\n\n"

    # Find today's section or append
    date_header = f"## {date_str}"
    if date_header in existing:
        # Insert entry after the date header
        lines = existing.splitlines()
        new_lines = []
        inserted = False
        for i, line in enumerate(lines):
            new_lines.append(line)
            if not inserted and line.strip() == date_header:
                new_lines.append(entry_line)
                inserted = True
        new_content = "\n".join(new_lines) + "\n"
    else:
        new_content = existing.rstrip("\n") + f"\n\n{date_header}\n\n{entry_line}\n"

    saved = save_writing_file(MOVEMENT_LOG_FILE, new_content, f"movement: {exercise} {date_str}")
    label = f"{exercise} {reps}".strip()
    if saved:
        try:
            from telegram import ReactionTypeEmoji
            await msg.set_reaction([ReactionTypeEmoji(emoji="💪")])
        except Exception:
            pass
        await msg.reply_text(f"Записано: {label}")
    else:
        await msg.reply_text(f"Записано: {label} (не удалось сохранить в файл)")


async def handle_translate_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages in the Translate topic — auto-translate RU↔EN or formulate."""
    from config import TRANSLATE_TOPIC_ID, READING_GROUP_ID
    from translate import translate_text, formulate_text

    msg = update.message
    if msg.chat_id != READING_GROUP_ID or msg.message_thread_id != TRANSLATE_TOPIC_ID:
        return

    text = msg.text.strip()
    if not text:
        return

    # ── "Formulate in English" mode: waiting for style choice ──
    if context.user_data.get("formulate_style_pending"):
        # User already chose style via callback, this shouldn't happen
        # But if they type instead of clicking — treat as new message
        context.user_data.pop("formulate_style_pending", None)

    # ── Trigger: "помоги сформулировать" ──
    formulate_triggers = ("помоги сформулировать", "сформулируй", "помоги написать на английском")
    text_lower = text.lower()
    if any(trigger in text_lower for trigger in formulate_triggers):
        # Extract the actual content after the trigger phrase
        content = text
        for trigger in formulate_triggers:
            idx = text_lower.find(trigger)
            if idx != -1:
                content = text[idx + len(trigger):].strip().lstrip(":")
                break
        if not content:
            await msg.reply_text("Что сформулировать? Напиши текст после команды.")
            return

        context.user_data["formulate_text"] = content
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("tumblr", callback_data="trstyle:tumblr"),
                InlineKeyboardButton("переписка", callback_data="trstyle:dm"),
            ]
        ])
        await msg.reply_text("Стиль?", reply_markup=kb)
        return

    # ── URL: fetch article + translate + send as spoiler chunks ──
    # Use entities for reliable URL extraction (handles text_link and plain url)
    url = None
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "url":
                url = msg.text[entity.offset:entity.offset + entity.length]
                break
            elif entity.type == "text_link":
                url = entity.url
                break
    if not url:
        url_match = re.search(r'https?://\S+', text)
        if url_match:
            url = url_match.group(0).rstrip('.,)')
    if url:
        await msg.chat.send_action("typing")
        from translate import fetch_and_translate_url
        from html import escape
        chunks = await fetch_and_translate_url(url)
        for chunk in chunks:
            safe = escape(chunk)
            try:
                await msg.reply_text(f"<blockquote expandable>{safe}</blockquote>", parse_mode="HTML")
            except Exception:
                await msg.reply_text(safe)
        return

    # ── Default: auto-translate ──
    await msg.chat.send_action("typing")
    result = translate_text(text)
    if result:
        await msg.reply_text(result)
    else:
        await msg.reply_text("Не удалось перевести.")


async def handle_translate_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle photo/screenshot messages in the Translate topic — OCR + translate RU↔EN."""
    from config import TRANSLATE_TOPIC_ID, READING_GROUP_ID
    from translate import translate_image

    msg = update.message
    if msg.chat_id != READING_GROUP_ID or msg.message_thread_id != TRANSLATE_TOPIC_ID:
        return

    photo = msg.photo[-1]
    file = await photo.get_file()
    photo_bytes = await file.download_as_bytearray()
    caption = msg.caption or None

    await msg.chat.send_action("typing")
    result = translate_image(bytes(photo_bytes), caption)
    if result:
        await msg.reply_text(result)
    else:
        await msg.reply_text("Не удалось распознать и перевести.")
