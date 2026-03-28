#!/usr/bin/env python3
"""
Geek-bot: Telegram бот с двумя режимами:
- Geek (ART из Murderbot) — напоминания, сарказм, забота через логику
- Dr. Indra — ПНЭИ-специалист, сенсорная регуляция, WHOOP-отчёты

Тонкий оркестратор: импорты, check_access, button_callback (диспетчер),
set_bot_commands, main.
"""

import re
import random
from datetime import datetime, time, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    MessageHandler,
    MessageReactionHandler,
    CallbackQueryHandler,
    TypeHandler,
    ContextTypes,
    filters,
)

from config import (
    TELEGRAM_TOKEN, ALLOWED_USER_IDS, TZ, logger, OWNER_CHAT_ID,
    TASKS_FILE, ZONE_EMOJI, PROJECT_EMOJI, ALL_DESTINATIONS,
    JOY_CATEGORIES, JOY_CATEGORY_EMOJI, REMINDERS,
    READING_GROUP_ID, READING_TOPIC_ID, READING_STATE_FILE,
)
from prompts import SENSORY_INDRA_PROMPT, SENSORY_BAD_PROMPT, WHOOP_HEALTH_SYSTEM
from storage import load_file, get_week_events, is_muted, load_morning_cache
from tasks import (
    get_life_tasks, add_task_to_zone, complete_task,
    suggest_zone_for_task, create_rawnote,
    _task_hash, _parse_sensory_menu,
    _format_sensory_menu_for_prompt, _sensory_hardcoded_response,
    check_task_deadlines,
)
from joy import get_joy_stats_week, log_joy, _joy_items_cache
from llm import (
    get_llm_response, get_motivations_for_mode,
    get_sleep_level,
)
from keyboards import (
    get_main_keyboard, get_reply_keyboard,
    get_note_mode_keyboard,
    get_joy_keyboard, get_joy_items_keyboard,
    get_task_confirm_keyboard, get_destination_keyboard,
    get_priority_keyboard,
    get_sensory_bad_keyboard, BINGO_ITEMS,
)
from handlers import (
    start, switch_to_geek,
    dashboard_command, todo_command, week_command,
    tasks_command, addtask_command, done_command,
    status, profile,
    sleep_reminder, food_command, sport_reminder,
    remind_command, list_reminders_command,
    setup_reminders, stop_reminders,
    next_steps_command,
    whoop_command, setup_whoop_command, stop_whoop_command,
    captain_command,
    myid_command,
    check_reminders,
    sleep_reminder_job, whoop_morning_recovery, whoop_evening_update,
    whoop_weekly_summary, monday_review, get_morning_whoop_data,
    send_scheduled_reminder, send_finance_csv_reminder,
    handle_photo_note, handle_message, handle_remind_callback,
    handle_channel_quote, quote_command, handle_group_quote,
    income_command, process_command, handle_csv_upload,
)
from meal_data import generate_weekly_menu
from whoop import whoop_client


# ── Access control middleware ────────────────────────────────────────

async def check_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Middleware: блокировать неразрешённых пользователей. /start разрешён всем для регистрации."""
    if not ALLOWED_USER_IDS:
        return
    if not update.effective_user:
        return
    if update.effective_user.id not in ALLOWED_USER_IDS:
        # Allow /start for family registration
        if update.message and update.message.text and update.message.text.strip().startswith("/start"):
            return
        logger.warning(f"Unauthorized access attempt from user_id={update.effective_user.id}")
        raise ApplicationHandlerStop()


# ── Helpers ─────────────────────────────────────────────────────────

def _trim_to_telegram_limit(text: str, limit: int = 4096) -> str:
    """Trim text to Telegram message limit, cutting at last complete line."""
    if len(text) <= limit:
        return text
    trimmed = text[:limit - 4]
    last_newline = trimmed.rfind('\n')
    if last_newline > limit // 2:
        trimmed = trimmed[:last_newline]
    return trimmed + "\n..."


# ── Callback dispatcher ─────────────────────────────────────────────

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка нажатий на кнопки — тонкий диспетчер по префиксам."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "noop":
        return

    # ── Quote source selection ──
    elif data.startswith("quote_src:"):
        from tasks import get_today_reading_sources, save_quote
        slug = data[10:]
        quote_text = context.user_data.pop("pending_quote", None)
        if not quote_text:
            await query.edit_message_text("Цитата потеряна. Повтори /q.")
            return

        if slug == "other":
            context.user_data["quote_awaiting_source"] = quote_text
            await query.edit_message_text("Напиши название источника:")
            return

        sources = get_today_reading_sources()
        display_name = next((name for name, s in sources if s == slug), slug)

        result = save_quote(quote_text, display_name)
        if result:
            await query.edit_message_text(f"Сохранено → {slug}.md 💾")
        else:
            await query.edit_message_text("Не удалось сохранить.")

    # ── Reading channel: "✓ Прочитано" button ──
    elif data.startswith("read:"):
        label = data[5:]
        await query.answer("Отмечено как прочитанное ✓")
        # Replace button with "✓ Прочитано" (no more clickable)
        try:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✓ Прочитано ✓", callback_data="noop")
                ]])
            )
        except Exception:
            pass  # Message might be too old to edit
        return

    # ── Mode switching ──
    elif data == "mode_geek":
        context.user_data["mode"] = "geek"
        await query.edit_message_text(
            "Geek online. Что случилось.",
            reply_markup=get_main_keyboard("geek")
        )

    # ── Overview callbacks ──
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

        response = await get_llm_response(prompt, mode="geek")
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

    # ── Next steps ──
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
            context.user_data["pending_steps"] = lines[:3]

            keyboard = []
            for i, step in enumerate(lines[:3]):
                clean_step = re.sub(r'^\d+[\.\)]\s*', '', step)
                keyboard.append([InlineKeyboardButton(f"+ {clean_step[:40]}...", callback_data=f"add_step_{i}")])
            keyboard.append([InlineKeyboardButton("Не добавлять", callback_data="cancel_steps")])

            await query.message.reply_text(
                response + "\n\n— Какие шаги добавить в Драйв?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await query.message.reply_text(response)

    # ── Add menu ──
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

    # ── Note mode ──
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

        logger.info(f"Note: Creating note from {len(buffer)} messages")
        raw_text = "\n".join(buffer)
        await query.edit_message_text("Собираю заметку...")

        note_prompt = f"""Из пересланных сообщений ниже создай заметку.

Правила:
- Первая строка: короткий заголовок (без #, без кавычек)
- Дальше: точное цитирование всех сообщений, каждое с новой строки
- НЕ перефразируй, НЕ сокращай, НЕ объединяй, НЕ добавляй от себя
- Сохрани оригинальный текст каждого сообщения дословно
- БЕЗ пустых строк между сообщениями
- Язык: такой же как в сообщениях

Сообщения:
{raw_text}"""

        result = await get_llm_response(
            note_prompt, mode="geek", skip_context=True, max_tokens=4000,
            custom_system="Ты помощник для создания заметок. Цитируй сообщения дословно. Не перефразируй и не добавляй ничего от себя."
        )

        lines = result.strip().split("\n", 1)
        title = lines[0].lstrip("# ").strip()
        body = lines[1].strip() if len(lines) > 1 else ""

        logger.info(f"Note: title='{title[:30]}...', body_len={len(body)}")
        context.user_data.pop("note_buffer", None)
        success = create_rawnote(title, body)
        logger.info(f"Note: save result={success}")

        if success:
            await query.message.reply_text(f"Заметка сохранена: {title}")
        else:
            await query.message.reply_text("Ошибка сохранения. Попробуй позже.")

    # ── Save task/note ──
    elif data == "save_confirm":
        pending = context.user_data.get("pending_save")
        if not pending:
            await query.edit_message_text("Нечего сохранять.")
            return

        if pending["type"] == "task":
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

        keyboard = []
        zones = list(ZONE_EMOJI.items())
        for i in range(0, len(zones), 2):
            row = []
            for name, emoji in zones[i:i+2]:
                row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"zone_{name}"))
            keyboard.append(row)

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

    # ── Sensory Bad (bingo checklist) ──
    elif data == "sensory_bad":
        context.user_data["sensory_bad_selected"] = set()
        await query.edit_message_text(
            "Что может быть причиной? Отмечай всё что откликается:",
            reply_markup=get_sensory_bad_keyboard(set())
        )

    elif data.startswith("sensory_bad_toggle_"):
        idx = int(data.replace("sensory_bad_toggle_", ""))
        selected = context.user_data.get("sensory_bad_selected", set())
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        context.user_data["sensory_bad_selected"] = selected
        await query.edit_message_reply_markup(reply_markup=get_sensory_bad_keyboard(selected))

    elif data == "sensory_bad_submit":
        selected = context.user_data.get("sensory_bad_selected", set())
        context.user_data["sensory_bad_selected"] = set()
        if not selected:
            items_text = "ничего конкретного не отмечено"
        else:
            items_text = "\n".join(f"- {BINGO_ITEMS[i]}" for i in sorted(selected))
        try:
            current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")
            system = SENSORY_BAD_PROMPT.format(selected_items=items_text, current_time=current_time)
            prompt = "Human нажала Разобраться после того как отметила причины плохого состояния."
            response = await get_llm_response(prompt, max_tokens=2000, custom_system=system)
        except Exception as e:
            logger.warning(f"Sensory bad LLM failed: {e}")
            response = f"Ты отметила:\n{items_text}\n\nПопробуй начать с самого простого из списка."
        response = _trim_to_telegram_limit(response)
        try:
            await query.edit_message_text("✓ investigate")
            await query.message.reply_text(response)
        except Exception as e:
            logger.error(f"Failed to send sensory_bad response: {e}")
            await query.message.reply_text("Что-то пошло не так. Попробуй ещё раз.")

    # ── Sensory ──
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
            system = SENSORY_INDRA_PROMPT.format(
                sensory_menu=menu_text,
                current_time=current_time
            )
            prompt = f"Human нажала кнопку Sensory и выбрала: {state_desc}"
            response = await get_llm_response(prompt, max_tokens=1000, custom_system=system)
            if "API недоступны" in response:
                response = _sensory_hardcoded_response(state, menu)
        except Exception as e:
            logger.warning(f"Sensory LLM failed, using hardcoded fallback: {e}")
            response = _sensory_hardcoded_response(state, menu)

        response = _trim_to_telegram_limit(response)
        try:
            await query.edit_message_text("✓")
            await query.message.reply_text(response)
        except Exception as e:
            logger.error(f"Failed to send sensory response: {e}")
            await query.message.reply_text("Что-то пошло не так. Попробуй ещё раз.")

    # ── Joy ──
    elif data.startswith("joy_"):
        action = data.replace("joy_", "")

        if action == "stats":
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
            await query.edit_message_text("Что было?", reply_markup=get_joy_keyboard())

        elif action.startswith("cat_"):
            category = action.replace("cat_", "")
            if category in JOY_CATEGORIES:
                emoji = JOY_CATEGORY_EMOJI.get(category, "✨")
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
        category = data.replace("joyother_", "")
        if category in JOY_CATEGORIES:
            emoji = JOY_CATEGORY_EMOJI.get(category, "✨")
            context.user_data["joy_pending_category"] = category
            await query.edit_message_text(
                f"{emoji} **{category.capitalize()}** — напиши что именно:",
                parse_mode="Markdown"
            )

    # ── Batch task priority ──
    elif data.startswith("batchpri_"):
        tasks = context.user_data.get("pending_batch_tasks", [])
        if not tasks:
            await query.edit_message_text("Нечего добавлять.")
            return

        priority = data.replace("batchpri_", "")
        priority_map = {"high": " ⏫", "medium": " 🔼", "low": " 🔽", "none": ""}
        suffix = priority_map.get(priority, "")

        tasks_with_pri = [t + suffix for t in tasks]
        context.user_data["pending_tasks"] = tasks_with_pri
        context.user_data.pop("pending_batch_tasks", None)

        await query.edit_message_text(f"Приоритет применён к {len(tasks_with_pri)} задачам.")

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

    # ── Task zone confirmation ──
    elif data.startswith("taskzone_"):
        parts = data.split("_")
        if len(parts) >= 3:
            task_idx = int(parts[1])
            destination = "_".join(parts[2:])

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
                    context.user_data.pop("pending_tasks", None)
                    added = context.user_data.pop("pending_tasks_added", [])

                    if added:
                        msg = f"**Добавлено ({len(added)}):**\n" + "\n".join(f"• {t}" for t in added)
                    else:
                        msg = "Ни одной задачи не добавлено."

                    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=get_reply_keyboard())

    # ── Monday feelings ──
    elif data.startswith("feeling_"):
        feeling = data.replace("feeling_", "")
        joy_stats = get_joy_stats_week()
        joy_total = sum(joy_stats.values())

        recommendations = {
            "energized": "Отлично. Можно брать драйв-задачи. Но не забывай про maintenance — сенсорная диета нужна и в хорошие дни.",
            "ok": "Нормально — рабочий режим. Баланс между драйвом и восстановлением.",
            "tired": "Вымотана значит — приоритет восстановлению. Меньше драйва, больше sensory и connection. Это не опция, это maintenance.",
            "low": "На дне. Режим выживания. Только фундамент: сон, еда, deep pressure. Драйв подождёт. Ты важнее любых задач."
        }

        rec = recommendations.get(feeling, "")

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

    # ── Morning WHOOP feeling → send analysis + motivation as new message ──
    elif data.startswith("morning_"):
        feeling = data.replace("morning_", "")

        morning_data = context.bot_data.get(f"morning_{query.message.chat.id}", {})

        # Re-fetch if data lost (bot restarted between morning message and button click)
        if not morning_data:
            morning_data = load_morning_cache(query.message.chat.id)
            if morning_data:
                logger.info("Loaded morning WHOOP data from file cache")
            else:
                try:
                    morning_data = get_morning_whoop_data()
                    logger.info("Re-fetched morning WHOOP data from API")
                except Exception as e:
                    logger.error(f"Failed to re-fetch morning data: {e}")
                    morning_data = {}

        sleep_hours = morning_data.get("sleep_hours", 0)
        strain = morning_data.get("strain", 0)
        recovery = morning_data.get("recovery", 0)
        trend = morning_data.get("trend", "stable")
        prev_avg = morning_data.get("prev_avg")
        workouts_yesterday = morning_data.get("workouts_yesterday", [])
        data_str = morning_data.get("data_str", "")

        feeling_bad = feeling in ["tired", "bad"]
        trend_down = trend == "down"

        if recovery < 34 or (trend_down and feeling_bad):
            mode = "recovery"
        elif recovery < 50 or trend_down:
            mode = "moderate"
        else:
            mode = "normal"

        motivations = get_motivations_for_mode(mode, sleep_hours, strain, recovery)

        color = "green" if recovery >= 67 else ("yellow" if recovery >= 34 else "red")

        boxing_note = ""
        if workouts_yesterday and any("box" in w.lower() for w in workouts_yesterday):
            boxing_note = "\nВчера был бокс — ожидаемое снижение recovery на 2-3 дня."

        feeling_text = {
            "great": "отлично",
            "ok": "норм",
            "tired": "устала",
            "bad": "плохо"
        }.get(feeling, feeling)

        prompt = f"""Данные WHOOP:
{data_str}{boxing_note}

Human ответила "как себя чувствуешь?": "{feeling_text}".

Ты — Geek (ART из Murderbot Diaries). Дай анализ данных и мотивацию на день в одном сообщении.

Если подходят, используй 1-2 из этих фраз (подставь реальные числа):
{motivations}

Что учесть:
- Recovery зона: {color} (green 67-100%, yellow 34-66%, red 0-33%)
- Не пересказывай данные — выдели главное и что с этим делать
- Если deep sleep < 60min или awake > 40min — что это значит для дня
- Тренироваться или нет — конкретный вывод
- Sleep debt — если есть, влияние на работу с клиентами
- Режим "{mode}" — {"отдых, лёгкая активность" if mode == "recovery" else "можно тренироваться, без фанатизма" if mode == "moderate" else "обычная мотивация"}
- Если "{feeling_text}" расходится с данными — обрати внимание коротко
- Если данные хорошие — скажи прямо, не ищи проблемы
- Без эмодзи. На русском. 5-8 предложений."""

        text = await get_llm_response(prompt, mode="geek", max_tokens=1200, skip_context=True, custom_system=WHOOP_HEALTH_SYSTEM, use_pro=True)
        text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()

        # Retry once if response suspiciously short (Gemini Pro sometimes returns fragments)
        if len(text) < 200:
            logger.warning(f"WHOOP morning response too short ({len(text)} chars), retrying...")
            text = await get_llm_response(prompt, mode="geek", max_tokens=1200, skip_context=True, custom_system=WHOOP_HEALTH_SYSTEM, use_pro=True)
            text = re.sub(r'\[SAVE:[^\]]+\]', '', text).strip()

        # Remove buttons from original message, keep data
        await query.edit_message_reply_markup(reply_markup=None)
        # Send analysis + motivation as new message
        await context.bot.send_message(chat_id=query.message.chat.id, text=text)
        logger.info(f"Sent WHOOP morning analysis+motivation ({mode}, feeling={feeling}) to {query.message.chat.id}")

    # ── Project decomposition ──
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

    # ── Add step to Драйв ──
    elif data.startswith("add_step_"):
        step_idx = int(data.replace("add_step_", ""))
        steps = context.user_data.get("pending_steps", [])
        if step_idx < len(steps):
            step = steps[step_idx]
            clean_step = re.sub(r'^\d+[\.\)]\s*', '', step)
            success = add_task_to_zone(clean_step, "драйв")
            if success:
                await query.answer(f"Добавлено в Драйв")
                steps.pop(step_idx)
                context.user_data["pending_steps"] = steps
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

    # ── /add task priority + destination ──
    elif data.startswith("addpri_"):
        task_text = context.user_data.get("pending_add_task")
        if not task_text:
            await query.edit_message_text("Нечего добавлять.")
            return

        priority = data.replace("addpri_", "")
        priority_map = {"high": " ⏫", "medium": " 🔼", "low": " 🔽", "none": ""}
        task_with_priority = task_text + priority_map.get(priority, "")

        context.user_data["pending_add_task"] = task_with_priority
        context.user_data["pending_add_ready"] = True

        await query.edit_message_text(
            f"Задача: {task_with_priority}\n\nКуда?",
            reply_markup=get_destination_keyboard()
        )

    elif data.startswith("adddest_"):
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

    # ── Done task from dashboard ──
    elif data.startswith("done_"):
        task_hash = data[5:]
        task_map = context.bot_data.get("task_done_map", {})
        task_text = task_map.get(task_hash)

        if not task_text:
            await query.edit_message_text("Задача не найдена. Попробуй обновить dashboard.")
            return

        if complete_task(task_text):
            old_markup = query.message.reply_markup
            if old_markup:
                new_buttons = [
                    row for row in old_markup.inline_keyboard
                    if not any(btn.callback_data == data for btn in row)
                ]
                display = task_text.replace("⏫", "").replace("🔺", "").replace("🔼", "").strip()
                old_text = query.message.text
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
            task_map.pop(task_hash, None)
        else:
            await query.edit_message_text("Не удалось отметить. Задача могла измениться.")

    elif data.startswith("remtime_") or data.startswith("remrec_"):
        await handle_remind_callback(update, context)

    elif data == "cancel_steps":
        context.user_data.pop("pending_steps", None)
        await query.edit_message_text(query.message.text.split("\n\n—")[0])


# ── Bot commands menu ────────────────────────────────────────────────

async def set_bot_commands(application) -> None:
    """Установить меню команд бота."""
    commands = [
        ("start", "Показать кнопки"),
        ("captain", "Кэп — обзор дел и планов"),
        ("whoop", "WHOOP — отчёт по сну и восстановлению"),
        ("status", "Текущий статус и время"),
        ("tasks", "Все задачи из файла"),
        ("done", "Отметить задачу выполненной"),
        ("remind", "Поставить напоминание"),
        ("myreminders", "Мои напоминания"),
        ("reminders", "Настроить автонапоминания"),
        ("stop_reminders", "Остановить автонапоминания"),
        ("income", "Сводка по доходам"),
        ("process", "Обработать финансовые CSV"),
        ("profile", "Мой профиль"),
        ("whoop_on", "Включить WHOOP интеграцию"),
        ("whoop_off", "Выключить WHOOP интеграцию"),
        ("myid", "Мой Telegram ID"),
    ]
    await application.bot.set_my_commands(commands)


# ── Reading reactions ────────────────────────────────────────────────

import json as _json
from storage import get_writing_file, save_writing_file

async def handle_reading_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track emoji reactions on messages in the reading topic as 'read' markers."""
    reaction = update.message_reaction
    if not reaction:
        return
    chat_id = reaction.chat.id
    thread_id = getattr(reaction, 'message_thread_id', None)
    # Only track reactions in the reading group + reading topic
    if chat_id != READING_GROUP_ID:
        return
    if thread_id is not None and thread_id != READING_TOPIC_ID:
        return
    # Any new reaction = mark as read
    if not reaction.new_reaction:
        return
    msg_id = reaction.message_id
    user_id = reaction.user.id if reaction.user else None
    # Load state from Writing repo
    try:
        raw = get_writing_file(READING_STATE_FILE)
        state = _json.loads(raw) if raw else {"read_messages": []}
    except (ValueError, _json.JSONDecodeError):
        state = {"read_messages": []}
    # Add if not already tracked
    if msg_id not in state["read_messages"]:
        state["read_messages"].append(msg_id)
        logger.info(f"Reading reaction: msg {msg_id} marked as read by user {user_id}")
        save_writing_file(
            READING_STATE_FILE,
            _json.dumps(state, indent=2),
            f"reading: mark msg {msg_id} as read",
        )


# ── Main ─────────────────────────────────────────────────────────────

def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.post_init = set_bot_commands

    # Проверка доступа — блокирует всех кроме разрешённых user_id
    application.add_handler(TypeHandler(Update, check_access), group=-1)

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("geek", switch_to_geek))
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
    application.add_handler(CommandHandler("captain", captain_command))
    application.add_handler(CommandHandler("whoop", whoop_command))
    application.add_handler(CommandHandler("whoop_on", setup_whoop_command))
    application.add_handler(CommandHandler("whoop_off", stop_whoop_command))
    application.add_handler(CommandHandler("myid", myid_command))
    application.add_handler(CommandHandler("q", quote_command))
    application.add_handler(CommandHandler("income", income_command))
    application.add_handler(CommandHandler("process", process_command))

    # Проверка пользовательских напоминаний каждую минуту
    job_queue = application.job_queue
    job_queue.run_repeating(check_reminders, interval=60, first=10)

    # Автозапуск WHOOP jobs для основного пользователя
    job_queue.run_daily(
        whoop_morning_recovery,
        time=time(hour=12, minute=0, tzinfo=TZ),
        chat_id=OWNER_CHAT_ID,
        name=f"whoop_morning_{OWNER_CHAT_ID}",
    )
    job_queue.run_daily(
        whoop_evening_update,
        time=time(hour=23, minute=0, tzinfo=TZ),
        chat_id=OWNER_CHAT_ID,
        name=f"whoop_evening_{OWNER_CHAT_ID}",
    )
    job_queue.run_daily(
        whoop_weekly_summary,
        time=time(hour=11, minute=0, tzinfo=TZ),
        days=(1,),  # Monday (0=Sun in python-telegram-bot v20+)
        chat_id=OWNER_CHAT_ID,
        name=f"whoop_weekly_{OWNER_CHAT_ID}",
    )
    # Sleep reminders removed — handled by Claude Code hooks instead
    # Monday review at 10:00 (before WHOOP weekly at 11:00)
    job_queue.run_daily(
        monday_review,
        time=time(hour=10, minute=0, tzinfo=TZ),
        days=(1,),  # Monday (0=Sun in python-telegram-bot v20+)
        chat_id=OWNER_CHAT_ID,
        name=f"monday_review_{OWNER_CHAT_ID}",
    )
    # Saturday finance CSV reminder at 10:00
    job_queue.run_daily(
        send_finance_csv_reminder,
        time=time(hour=10, minute=0, tzinfo=TZ),
        days=(5,),  # Saturday
        chat_id=OWNER_CHAT_ID,
        name=f"finance_reminder_{OWNER_CHAT_ID}",
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

    # Обработка CSV файлов
    application.add_handler(MessageHandler(filters.Document.ALL, handle_csv_upload))

    # Обработка топика Цитаты в группе (до общего handler)
    application.add_handler(MessageHandler(
        filters.Chat(READING_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
        handle_group_quote,
    ))

    # Обработка текстовых сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Обработка сообщений в канале чтения (цитаты)
    application.add_handler(MessageHandler(
        filters.UpdateType.CHANNEL_POST & filters.TEXT,
        handle_channel_quote
    ))

    # Обработка реакций в топике чтения
    application.add_handler(MessageReactionHandler(handle_reading_reaction))

    # Запуск
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
