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
    TZ, logger, OWNER_CHAT_ID,
    USER_CONTEXT_FILE, TASKS_FILE,
    ZONE_EMOJI, PROJECT_EMOJI, ALL_DESTINATIONS,
    JOY_CATEGORIES, JOY_CATEGORY_EMOJI,
    REMINDERS, SLEEP_PROMPTS,
)
from prompts import SENSORY_LEYA_PROMPT, WHOOP_HEALTH_SYSTEM
from storage import (
    load_file, get_writing_file, save_writing_file,
    get_week_events, register_family_member, get_family_chat_id,
    add_reminder, get_due_reminders, parse_remind_time,
    get_reminders, is_muted,
)
from tasks import (
    get_life_tasks, add_task_to_zone, complete_task,
    suggest_zone_for_task, create_rawnote, parse_save_tag,
    _task_hash, _get_priority_tasks, _parse_sensory_menu,
    _get_random_sensory_suggestion, _format_sensory_menu_for_prompt,
    _sensory_hardcoded_response, check_task_deadlines,
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
)
from finance import handle_csv_upload, income_command, process_command  # noqa: F401 — re-exported for bot.py
from whoop import whoop_client
from meal_data import generate_weekly_menu


# ── Command handlers ─────────────────────────────────────────────────────────


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



# ── Job functions ────────────────────────────────────────────────────────────


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
                entry_parts.append(f"- Sleep: {actual_h}h (perf {perf}%, eff {eff}%)")
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
            rem = stage.get("total_rem_sleep_time_milli", 0)
            deep = stage.get("total_slow_wave_sleep_time_milli", 0)
            light = stage.get("total_light_sleep_time_milli", 0)
            actual_ms = rem + deep + light
            sleep_hours = round(actual_ms / 3_600_000, 1) if actual_ms else 0

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

            text = await get_llm_response(prompt, mode="geek", max_tokens=600, skip_context=True, custom_system=WHOOP_HEALTH_SYSTEM, use_pro=True)
            text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()
        else:
            text = data_text

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
            data_parts.append(f"Сон: {sleep_hours}h (in bed {in_bed_h}h, performance {perf}%)")

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
        wo_names = [wo.get("sport_name", "?") for wo in workouts_yesterday] if workouts_yesterday else []
        context.bot_data[f"morning_{chat_id}"] = {
            "sleep_hours": sleep_hours,
            "strain": strain,
            "recovery": recovery_score,
            "trend": trend_direction,
            "prev_avg": prev_avg,
            "workouts_yesterday": wo_names,
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
        week_sleep = whoop_client.get_sleep_week()
        week_workouts = whoop_client.get_workouts_week()

        data_parts = []

        # Recovery / HRV / RHR
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

        # Sleep
        if week_sleep:
            sleep_hours = []
            sleep_perfs = []
            for sl in week_sleep:
                ss = sl.get("score", {})
                stage = ss.get("stage_summary", {})
                rem = stage.get("total_rem_sleep_time_milli", 0)
                deep = stage.get("total_slow_wave_sleep_time_milli", 0)
                light = stage.get("total_light_sleep_time_milli", 0)
                actual_h = round((rem + deep + light) / 3_600_000, 1)
                if actual_h > 0:
                    sleep_hours.append(actual_h)
                perf = ss.get("sleep_performance_percentage")
                if perf is not None:
                    sleep_perfs.append(perf)
            if sleep_hours:
                avg_sleep = round(sum(sleep_hours) / len(sleep_hours), 1)
                min_sleep = min(sleep_hours)
                max_sleep = max(sleep_hours)
                under_7 = sum(1 for h in sleep_hours if h < 7)
                data_parts.append(f"Сон avg: {avg_sleep}h (min {min_sleep}h, max {max_sleep}h), дней < 7h: {under_7}/{len(sleep_hours)}")
            if sleep_perfs:
                avg_perf = round(sum(sleep_perfs) / len(sleep_perfs))
                data_parts.append(f"Sleep performance avg: {avg_perf}%")

        # Strain (avg/min/max instead of raw list)
        if week_cycles:
            strains = [round(c.get("score", {}).get("strain", 0), 1) for c in week_cycles]
            avg_strain = round(sum(strains) / len(strains), 1)
            data_parts.append(f"Day strain avg: {avg_strain} (min {min(strains)}, max {max(strains)})")

        # Workouts (real data, not strain guessing)
        if week_workouts:
            from collections import Counter
            sport_counts = Counter(wo.get("sport_name", "Unknown") for wo in week_workouts)
            wo_summary = ", ".join(f"{name} x{count}" for name, count in sport_counts.most_common())
            days_with_workouts = len(set(
                wo.get("start", "")[:10] for wo in week_workouts if wo.get("start")
            ))
            data_parts.append(f"Тренировки: {wo_summary} ({days_with_workouts} дней из 7)")
        else:
            data_parts.append("Тренировки: нет за неделю")

        # Body
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
        avg_recovery = 0
        if week_records:
            scores = [r.get("score", {}).get("recovery_score") for r in week_records if r.get("score", {}).get("recovery_score") is not None]
            avg_recovery = round(sum(scores) / len(scores)) if scores else 0
        weekly_mode = "recovery" if avg_recovery < 34 else ("moderate" if avg_recovery < 67 else "normal")
        weekly_motivations = get_motivations_for_mode(weekly_mode, 0, 0, avg_recovery)

        prompt = f"""Еженедельный отчёт WHOOP:
{data_str}

Ты — Geek (ART из Murderbot Diaries). Сделай подробный еженедельный отчёт.

Если подходят, используй 1-2 из этих фраз (подставь числа из данных):
{weekly_motivations}

Что обязательно проанализировать:
- Recovery тренд — улучшается или ухудшается
- Сон — подробно: сколько спала в среднем, сколько дней недосып, как это влияет на recovery и работу с клиентами
- Тренировки — сколько было, какие, есть ли паттерн пропусков
- Рекомендации на следующую неделю — конкретные

Цвет зон recovery: green (67-100%), yellow (34-66%), red (0-33%).
Не выдумывай персонажей, которых нет в предложенных фразах.
Без эмодзи. На русском. 8-12 предложений."""

        text = await get_llm_response(prompt, mode="geek", max_tokens=1200, skip_context=True, custom_system=WHOOP_HEALTH_SYSTEM, use_pro=True)
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



# ── Photo and message handlers ───────────────────────────────────────────────


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

    # Late night: append level-appropriate sleep nudge
    if sleep_level > 0:
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
