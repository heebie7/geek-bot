import re
import random
import time
import hashlib
from datetime import datetime, timedelta
from config import ZONE_EMOJI, PROJECT_EMOJI, PROJECT_HEADERS, ALL_DESTINATIONS, TZ, logger
from storage import get_writing_file, save_writing_file

# In-memory cache for tasks.md to avoid GitHub API call on every button press
_tasks_cache = {"content": None, "ts": 0}
_TASKS_CACHE_TTL = 300  # 5 minutes


def _invalidate_tasks_cache():
    """Reset tasks cache after writes."""
    _tasks_cache["content"] = None
    _tasks_cache["ts"] = 0


def get_life_tasks() -> str:
    """Получить задачи из life/tasks.md в Writing workspace (cached 5 min)."""
    now = time.time()
    if _tasks_cache["content"] and (now - _tasks_cache["ts"]) < _TASKS_CACHE_TTL:
        logger.debug("tasks.md served from cache")
        return _tasks_cache["content"]

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

    _tasks_cache["content"] = content
    _tasks_cache["ts"] = now
    return content


def get_today_tasks() -> list:
    """Получить открытые задачи из секции ## Сегодня."""
    content = get_life_tasks()
    in_section = False
    tasks = []
    for line in content.split('\n'):
        if line.strip() == '## Сегодня':
            in_section = True
            continue
        if in_section:
            if line.startswith('## '):
                break
            if line.startswith('- [ ] '):
                tasks.append(line[6:].strip())
    return tasks


def clear_today_section() -> bool:
    """Удалить все открытые задачи из секции ## Сегодня."""
    content = get_life_tasks()
    lines = content.split('\n')
    result_lines = []
    in_section = False
    for line in lines:
        if line.strip() == '## Сегодня':
            in_section = True
            result_lines.append(line)
            continue
        if in_section:
            if line.startswith('## ') or line.startswith('---'):
                in_section = False
                result_lines.append(line)
            elif line.startswith('- [ ] '):
                pass  # Drop open tasks
            else:
                result_lines.append(line)
        else:
            result_lines.append(line)
    new_content = '\n'.join(result_lines)
    result = save_writing_file("life/tasks.md", new_content, "Clear today section")
    _invalidate_tasks_cache()
    return result


async def today_morning_prompt(context) -> None:
    """11:00 — показать незавершённое с вчера, предложить очистить."""
    chat_id = context.job.chat_id
    try:
        tasks = get_today_tasks()
        if not tasks:
            return
        task_list = "\n".join(f"• {t}" for t in tasks)
        text = f"Осталось с вчера в «Сегодня»:\n\n{task_list}\n\nОчистить?"
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Очистить", callback_data="clear_today"),
            InlineKeyboardButton("Оставить", callback_data="keep_today"),
        ]])
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"today_morning_prompt error: {e}")


async def today_evening_review(context) -> None:
    """21:00 — показать задачи из «Сегодня», предложить очистить."""
    chat_id = context.job.chat_id
    try:
        tasks = get_today_tasks()
        if not tasks:
            return
        task_list = "\n".join(f"• {t}" for t in tasks)
        text = f"Вечерний разбор «Сегодня»:\n\n{task_list}\n\nОчистить секцию?"
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Очистить всё", callback_data="clear_today"),
            InlineKeyboardButton("Оставить", callback_data="keep_today"),
        ]])
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"today_evening_review error: {e}")


def add_task_to_zone(task: str, destination: str) -> bool:
    """Добавить задачу в зону или проект в life/tasks.md.

    destination может быть зоной (фундамент, драйв, ...) или проектом (geek-bot, ...).
    """
    tasks = get_life_tasks()

    # Маппинг зон на заголовки
    zone_headers = {
        "сегодня": "## Сегодня",
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

    result = save_writing_file("life/tasks.md", tasks, f"Add task: {task[:30]}")
    _invalidate_tasks_cache()
    return result


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

    result = save_writing_file("life/tasks.md", tasks, f"Complete: {task_line[:30]}")
    _invalidate_tasks_cache()
    return result


async def suggest_zone_for_task(task: str) -> str:
    """Use LLM to suggest which zone or project a task belongs to."""
    prompt = f"""Определи, куда относится задача. Варианты:

Зоны:
- сегодня: мелкие дела на сегодня, которые нужно сделать прямо сейчас или в течение дня
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
        from llm import get_llm_response
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


def create_rawnote(title: str, content: str) -> bool:
    """Создать заметку в writing/rawnotes/."""
    logger.info(f"create_rawnote: title='{title}', content_len={len(content)}")
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    # Создаём slug из заголовка
    slug = title.lower().replace(" ", "-")[:50]
    filename = f"writing/rawnotes/{today}-{slug}.md"

    note_content = f"# {title}\n\n{content}"
    logger.info(f"create_rawnote: saving to {filename}")
    result = save_writing_file(filename, note_content, f"Add note: {title[:30]}")
    logger.info(f"create_rawnote: result={result}")
    return result


def get_today_reading_sources() -> list:
    """Get today's reading sources from reading-queue.md.

    Returns list of (display_name, slug) tuples for book/chapter/file entries.
    Skips url entries and focus/status files.
    """
    content = get_writing_file("life/reading-queue.md")
    if not content:
        return []

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    lines = content.split('\n')

    sources = []
    in_today = False
    pending_type = None

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('## '):
            date_str = stripped[3:].strip()
            in_today = (date_str == today)
            continue

        if not in_today:
            continue

        if stripped.startswith('- chapter:') or stripped.startswith('- book:') or stripped.startswith('- file:'):
            pending_type = 'book'
        elif stripped.startswith('- url:'):
            pending_type = 'skip'
        elif stripped.startswith('context:') and pending_type == 'book':
            ctx = stripped[8:].strip()
            if ' — ' in ctx:
                display_name = ctx.split(' — ')[0].strip()
            elif ',' in ctx:
                display_name = ctx.split(',')[0].strip()
            else:
                display_name = ctx[:40]

            skip_keywords = ['капитанский', 'фокус', 'статус', 'focus', 'status', 'мостик']
            if any(kw in display_name.lower() for kw in skip_keywords):
                pending_type = None
                continue

            slug = re.sub(r'[^\w\s-]', '', display_name.lower())
            slug = re.sub(r'[\s]+', '-', slug)[:30].strip('-')

            if display_name and slug:
                if not any(s[1] == slug for s in sources):
                    sources.append((display_name, slug))
            pending_type = None

    return sources[:5]


def save_quote(quote_text: str, source_name: str = "unknown") -> bool:
    """Сохранить цитату в writing/research/quotes/.

    Один файл на источник — дописывает цитаты в конец.
    """
    logger.info(f"save_quote: source='{source_name}', len={len(quote_text)}")
    today = datetime.now(TZ).strftime("%Y-%m-%d")

    # Slug из названия источника
    slug = re.sub(r'[^\w\s-]', '', source_name.lower())
    slug = re.sub(r'[\s]+', '-', slug)[:60].strip('-')
    if not slug:
        slug = "misc"
    filename = f"writing/research/quotes/{slug}.md"

    # Проверяем, существует ли файл — если да, дописываем
    existing = get_writing_file(filename)

    if existing:
        # Дописываем цитату в конец
        new_content = existing.rstrip() + f"\n\n> {quote_text}\n— добавлено {today}"
        message = f"Add quote to {slug}"
    else:
        # Создаём новый файл
        new_content = (
            f"---\n"
            f"source: \"{source_name}\"\n"
            f"date: {today}\n"
            f"tags: []\n"
            f"---\n\n"
            f"> {quote_text}\n— добавлено {today}"
        )
        message = f"Create quotes: {slug}"

    result = save_writing_file(filename, new_content, message)
    logger.info(f"save_quote: result={result}")
    return result


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


def _task_hash(task_text: str) -> str:
    """Короткий хеш задачи для callback data (8 hex chars)."""
    return hashlib.md5(task_text.encode()).hexdigest()[:8]


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
            if creativity:
                response += "\n\n🎨 **Creativity:**\n" + "\n".join(f"• {item}" for item in creativity)
        else:
            response = "Сенсорное меню пустое."
    else:
        response = "Неизвестное состояние."

    return response


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


async def check_task_deadlines(context) -> None:
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
