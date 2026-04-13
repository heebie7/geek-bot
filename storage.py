"""
Storage module for geek-bot.

Handles all persistent data access: GitHub file I/O, Writing-space repo,
mute settings, family registry, reminders, and Google Calendar integration.
"""

import os
import json
import re
import base64
from datetime import datetime, timedelta, timezone

from github import Github
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

from config import (
    GITHUB_TOKEN, GITHUB_REPO, WRITING_REPO, BASE_DIR,
    REMINDERS_FILE, FAMILY_FILE, MUTE_FILE,
    CALENDAR_ID, SCOPES,
    TZ, logger,
    MORNING_CACHE_FILE,
    WHOOP_PATTERNS_PATH, WHOOP_BASELINES_PATH, INDRA_SESSIONS_DIR,
    FOOD_LOG_FILE, KITCHEN_REPO, KITCHEN_DATA_FILE, DEFAULT_FOOD_TARGETS,
    NS_CHECKIN_FILE,
)


# === MORNING WHOOP CACHE ===

def save_morning_cache(chat_id: int, data: dict) -> None:
    """Сохранить данные утреннего WHOOP-отчёта в файл.

    Переживает рестарт бота, но не redeployment Railway.
    """
    try:
        cache = {}
        if os.path.exists(MORNING_CACHE_FILE):
            with open(MORNING_CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
        cache[str(chat_id)] = data
        with open(MORNING_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"morning_cache save error: {e}")


def load_morning_cache(chat_id: int) -> dict:
    """Загрузить данные утреннего WHOOP-отчёта из файла."""
    try:
        if not os.path.exists(MORNING_CACHE_FILE):
            return {}
        with open(MORNING_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        return cache.get(str(chat_id), {})
    except Exception as e:
        logger.error(f"morning_cache load error: {e}")
        return {}


# === FILE I/O ===

def load_file(filepath: str, default: str = "") -> str:
    """Загрузить файл или вернуть default."""
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    return default


def get_github_file(filepath: str) -> str:
    """Получить файл из GitHub."""
    if not GITHUB_TOKEN:
        return load_file(os.path.join(BASE_DIR, filepath), "Файл не найден.")
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(GITHUB_REPO)
        content = repo.get_contents(filepath)
        return content.decoded_content.decode('utf-8-sig')
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


# === WRITING WORKSPACE ===

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
        if content.encoding == "none":
            # Файл >1MB — get_contents не отдаёт содержимое, скачиваем через raw URL
            import requests as _req
            resp = _req.get(content.download_url,
                            headers={"Authorization": f"token {GITHUB_TOKEN}"})
            resp.raise_for_status()
            text = resp.content.decode('utf-8-sig')
            logger.info(f"Successfully read {filepath} via download_url ({len(resp.content)} bytes)")
        else:
            text = content.decoded_content.decode('utf-8-sig')
            logger.info(f"Successfully read {filepath} ({len(content.decoded_content)} bytes)")
        return text
    except Exception as e:
        logger.error(f"Writing repo read error for {filepath} from {WRITING_REPO}: {e}")
        return ""


def list_writing_dir(dirpath: str) -> dict:
    """Получить список файлов в директории Writing-space репо.
    Возвращает dict {filename: filepath} или {} при ошибке."""
    if not GITHUB_TOKEN:
        logger.warning("No GITHUB_TOKEN for Writing repo")
        return {}
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(WRITING_REPO)
        contents = repo.get_contents(dirpath)
        if not isinstance(contents, list):
            return {}
        return {item.name: item.path for item in contents if item.type == "file"}
    except Exception as e:
        logger.error(f"Writing repo list error for {dirpath}: {e}")
        return {}


def save_writing_file(filepath: str, new_content: str, message: str) -> bool:
    """Сохранить/обновить файл в Writing-space репо."""
    logger.info(f"save_writing_file: filepath={filepath}, msg='{message}'")
    if not GITHUB_TOKEN:
        logger.warning("No GitHub token, cannot save to Writing repo")
        return False
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(WRITING_REPO)
        logger.info(f"save_writing_file: Got repo {WRITING_REPO}")

        file_exists = False
        try:
            # Файл существует — обновляем
            content = repo.get_contents(filepath)
            file_exists = True
            logger.info(f"save_writing_file: File exists, updating {filepath}")
        except Exception as check_e:
            logger.info(f"save_writing_file: File not found ({check_e.__class__.__name__}), will create")

        if file_exists:
            try:
                repo.update_file(filepath, message, new_content, content.sha)
                logger.info(f"save_writing_file: Successfully updated {filepath}")
            except Exception as e:
                logger.error(f"save_writing_file: Failed to update {filepath}: {e}")
                raise
        else:
            try:
                repo.create_file(filepath, message, new_content)
                logger.info(f"save_writing_file: Successfully created new file {filepath}")
            except Exception as e:
                logger.error(f"save_writing_file: Failed to create {filepath}: {e}")
                raise

        logger.info(f"Saved {filepath} to Writing repo successfully")
        return True
    except Exception as e:
        logger.error(f"Writing repo write error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False



# === MUTE SETTINGS ===

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



# === FAMILY ===

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



# === REMINDERS ===

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


def add_reminder(chat_id: int, remind_at: datetime, text: str, from_user: str = None, recurring: str = None) -> bool:
    """Добавить напоминание. recurring: 'daily', 'weekdays', 'weekly' или None."""
    reminders = get_reminders()
    reminder = {
        "chat_id": chat_id,
        "remind_at": remind_at.isoformat(),
        "text": text,
        "created_at": datetime.now(TZ).isoformat(),
    }
    if from_user:
        reminder["from_user"] = from_user
    if recurring:
        reminder["recurring"] = recurring
    reminders.append(reminder)
    return save_reminders(reminders)


def _next_recurring(remind_at: datetime, recurring: str) -> datetime:
    """Calculate next occurrence for a recurring reminder."""
    if recurring == "daily":
        return remind_at + timedelta(days=1)
    elif recurring == "weekdays":
        next_dt = remind_at + timedelta(days=1)
        while next_dt.weekday() >= 5:  # Skip Sat/Sun
            next_dt += timedelta(days=1)
        return next_dt
    elif recurring == "weekly":
        return remind_at + timedelta(weeks=1)
    return remind_at + timedelta(days=1)


def get_due_reminders() -> list:
    """Получить напоминания, которые пора отправить. Recurring пересоздаются."""
    reminders = get_reminders()
    now = datetime.now(TZ)
    due = []
    remaining = []

    for r in reminders:
        remind_at = datetime.fromisoformat(r["remind_at"])
        if remind_at <= now:
            due.append(r)
            # Reschedule recurring reminders
            recurring = r.get("recurring")
            if recurring:
                next_r = dict(r)
                next_r["remind_at"] = _next_recurring(remind_at, recurring).isoformat()
                remaining.append(next_r)
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


# === INDRA & WHOOP ANALYTICS ===

def _strip_frontmatter(text: str) -> str:
    """Strip YAML frontmatter from markdown text."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text


def load_whoop_patterns() -> str:
    """Load confirmed WHOOP patterns from Writing repo."""
    text = get_writing_file(WHOOP_PATTERNS_PATH)
    if not text:
        return "Паттерны не загружены."
    return _strip_frontmatter(text)


def load_whoop_baselines() -> str:
    """Load personal WHOOP baselines from Writing repo."""
    text = get_writing_file(WHOOP_BASELINES_PATH)
    if not text:
        return "Baselines не загружены."
    return _strip_frontmatter(text)


def load_latest_indra_session(max_age_days: int = 7) -> str:
    """Load the most recent Indra session file if within max_age_days."""
    files = list_writing_dir(INDRA_SESSIONS_DIR)
    if not files:
        return "Нет записей Indra-сессий."

    # Filter to date-prefixed .md files, sort descending
    dated = sorted(
        [f for f in files.keys() if re.match(r'\d{4}-\d{2}-\d{2}', f)],
        reverse=True,
    )
    if not dated:
        return "Нет записей Indra-сессий."

    # Check if most recent is within max_age_days
    latest_name = dated[0]
    date_str = latest_name[:10]
    try:
        file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        today = datetime.now(TZ).date()
        if (today - file_date).days > max_age_days:
            return "Последняя Indra-сессия старше 7 дней."
    except ValueError:
        pass

    content = get_writing_file(files[latest_name])
    if not content:
        return "Не удалось загрузить последнюю Indra-сессию."

    # Truncate if too long
    if len(content) > 2000:
        content = content[:2000] + "\n...(обрезано)"

    return content


def load_indra_sessions_week() -> str:
    """Load all Indra session files from the last 7 days."""
    files = list_writing_dir(INDRA_SESSIONS_DIR)
    if not files:
        return "Нет записей Indra-сессий за неделю."

    today = datetime.now(TZ).date()
    week_ago = today - timedelta(days=7)

    sessions = []
    for name in sorted(files.keys()):
        if not re.match(r'\d{4}-\d{2}-\d{2}', name):
            continue
        date_str = name[:10]
        try:
            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < week_ago:
            continue

        content = get_writing_file(files[name])
        if content:
            if len(content) > 1500:
                content = content[:1500] + "\n...(обрезано)"
            sessions.append(f"### {name}\n{content}")

    if not sessions:
        return "Нет записей Indra-сессий за последние 7 дней."

    return "\n\n".join(sessions)


# === GOOGLE CALENDAR ===

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


# === NS CHECK-IN ===

def save_ns_checkin(state: str, helped: str = "", notes: str = "") -> bool:
    """Save NS check-in to Writing repo as markdown file."""
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    filepath = NS_CHECKIN_FILE.format(date=today)
    content = f"state: {state}\nhelped: {helped}\nnotes: {notes}\n"
    return save_writing_file(filepath, content, f"NS check-in {today}: {state}")


# === FOOD TRACKING ===

_kitchen_cache = None
_kitchen_cache_date = None


def load_food_log() -> dict:
    """Load food log from Writing repo. Returns default structure if not found."""
    raw = get_writing_file(FOOD_LOG_FILE)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse food log: {e}")
    return {"daily_targets": dict(DEFAULT_FOOD_TARGETS), "log": []}


def save_food_log(data: dict) -> bool:
    """Save food log to Writing repo via save_writing_file (handles SHA)."""
    content = json.dumps(data, ensure_ascii=False, indent=2)
    return save_writing_file(FOOD_LOG_FILE, content, "food log update")


def load_kitchen_dishes() -> list:
    """Load dishes from family-kitchen repo. Cached daily. KBJU cast to int."""
    global _kitchen_cache, _kitchen_cache_date
    today = datetime.now(TZ).date()
    if _kitchen_cache is not None and _kitchen_cache_date == today:
        return _kitchen_cache

    if not GITHUB_TOKEN:
        logger.warning("No GITHUB_TOKEN for kitchen repo")
        return []
    try:
        g = Github(GITHUB_TOKEN)
        repo = g.get_repo(KITCHEN_REPO)
        content = repo.get_contents(KITCHEN_DATA_FILE)
        raw = base64.b64decode(content.content).decode("utf-8")
        data = json.loads(raw)
        dishes = data.get("dishes", [])
        # Cast KBJU string fields to int
        for dish in dishes:
            for field in ("kcal", "protein", "fat", "carbs"):
                if field in dish and isinstance(dish[field], str):
                    try:
                        dish[field] = int(dish[field])
                    except ValueError:
                        dish[field] = 0
        _kitchen_cache = dishes
        _kitchen_cache_date = today
        logger.info(f"Loaded {len(dishes)} dishes from kitchen repo")
        return dishes
    except Exception as e:
        logger.error(f"Failed to load kitchen dishes: {e}")
        return []

