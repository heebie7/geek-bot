import json
from datetime import datetime, timedelta
from config import JOY_CATEGORIES, JOY_CATEGORY_EMOJI, TZ, logger
from storage import get_github_file, update_github_file


# Cache for joy items (to retrieve by index in callbacks)
_joy_items_cache = {}


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
