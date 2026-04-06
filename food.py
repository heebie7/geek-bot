"""
Food photo recognition and nutrition tracking.

Core logic: Gemini Vision recognition, kitchen DB matching,
entry building, formatting. No Telegram dependencies.
"""

import json
import re
from datetime import datetime
from typing import Optional

from google.genai import types

from config import gemini_client, GEMINI_MODEL, TZ, DEFAULT_FOOD_TARGETS, logger
from prompts import FOOD_RECOGNITION_PROMPT, FOOD_TEXT_ONLY_PROMPT


def recognize_food(photo_bytes: Optional[bytes], caption: Optional[str]) -> dict:
    """Recognize food from photo and/or caption via Gemini Vision.

    Args:
        photo_bytes: JPEG image bytes, or None for text-only recognition.
        caption: Optional text hint (dish name, description).

    Returns:
        dict with keys: name, kcal, protein, fat, carbs, fiber, portion, confidence.
        On failure returns confidence=0.0.
    """
    if not gemini_client:
        logger.error("No Gemini client for food recognition")
        return {"confidence": 0.0}

    if photo_bytes:
        prompt_text = FOOD_RECOGNITION_PROMPT.format(caption=caption or "не указано")
    else:
        prompt_text = FOOD_TEXT_ONLY_PROMPT.format(caption=caption or "не указано")

    parts = []
    if photo_bytes:
        parts.append(types.Part.from_bytes(data=photo_bytes, mime_type="image/jpeg"))
    parts.append(types.Part(text=prompt_text))

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=parts)],
        )
        raw = response.text.strip()
        # Strip markdown code fences
        raw = re.sub(r'^```(?:json)?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result = json.loads(raw)
        # Ensure required fields
        for field in ("name", "kcal", "protein", "fat", "carbs", "fiber", "confidence"):
            if field not in result:
                if field == "confidence":
                    result["confidence"] = 0.5
                elif field == "name":
                    result["name"] = "Неизвестное блюдо"
                else:
                    result[field] = 0
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Food recognition JSON parse error: {e}")
        return {"confidence": 0.0}
    except Exception as e:
        logger.error(f"Food recognition error: {e}")
        return {"confidence": 0.0}


def match_custom_dish(name: str, custom_dishes: dict) -> Optional[dict]:
    """Match against user's personal frequent dishes.

    Returns dish dict with KBJU, or None.
    """
    if not name or not custom_dishes:
        return None
    norm_name = name.lower().strip()
    for dish_name, dish_data in custom_dishes.items():
        if dish_name.lower() == norm_name or norm_name in dish_name.lower() or dish_name.lower() in norm_name:
            return {"name": dish_name, **dish_data}
    return None


def match_kitchen_dish(name: str, dishes: list) -> Optional[dict]:
    """Fuzzy match recognized dish name against kitchen DB.

    Simple substring matching on normalized names.
    Returns dish dict with int KBJU, or None.
    """
    if not name or not dishes:
        return None
    norm_name = name.lower().strip()
    for dish in dishes:
        dish_name = dish.get("name", "").lower().strip()
        if not dish_name:
            continue
        if dish_name in norm_name or norm_name in dish_name:
            return dish
    return None


def build_food_entry(recognition: dict, match: Optional[dict], caption: Optional[str]) -> dict:
    """Assemble a food log entry from recognition result and optional kitchen match."""
    now = datetime.now(TZ)
    entry = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "meal": get_meal_type(now.hour),
        "name": recognition.get("name", "Неизвестное блюдо"),
        "matched_dish": None,
        "kcal": recognition.get("kcal", 0),
        "protein": recognition.get("protein", 0),
        "fat": recognition.get("fat", 0),
        "carbs": recognition.get("carbs", 0),
        "fiber": recognition.get("fiber", 0),
        "portion": recognition.get("portion", "standard"),
        "source": "vision",
        "caption": caption,
    }
    if match:
        entry["matched_dish"] = match.get("name")
        entry["kcal"] = match.get("kcal", entry["kcal"])
        entry["protein"] = match.get("protein", entry["protein"])
        entry["fat"] = match.get("fat", entry["fat"])
        entry["carbs"] = match.get("carbs", entry["carbs"])
        # fiber always from Gemini (kitchen DB doesn't track it)
        entry["source"] = "kitchen_match"
    elif caption:
        entry["source"] = "vision+caption"
    return entry


def build_custom_entry(dish: dict) -> dict:
    """Build a food log entry from a custom/frequent dish. No Gemini call."""
    now = datetime.now(TZ)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "meal": get_meal_type(now.hour),
        "name": dish.get("name", "?"),
        "matched_dish": dish.get("name"),
        "kcal": dish.get("kcal", 0),
        "protein": dish.get("protein", 0),
        "fat": dish.get("fat", 0),
        "carbs": dish.get("carbs", 0),
        "fiber": dish.get("fiber", 0),
        "portion": "standard",
        "source": "custom",
        "caption": None,
    }


def format_food_result(entry: dict) -> str:
    """Format a food entry for display in Telegram."""
    source_label = {
        "kitchen_match": "kitchen DB",
        "custom": "частое блюдо",
        "vision": "Gemini Vision",
        "vision+caption": "Gemini Vision + подпись",
        "text": "текст",
    }
    lines = [
        f"🍽 {entry['name']}",
        f"Б: {entry['protein']}г | Ж: {entry['fat']}г | У: {entry['carbs']}г | Клетч: {entry['fiber']}г | {entry['kcal']} kcal",
        f"Источник: {source_label.get(entry['source'], entry['source'])}",
    ]
    return "\n".join(lines)


def format_daily_summary(log: list, targets: Optional[dict], date: str) -> str:
    """Format daily nutrition summary with remaining targets."""
    if targets is None:
        targets = dict(DEFAULT_FOOD_TARGETS)

    today_entries = [e for e in log if e.get("date") == date]
    if not today_entries:
        return "Данных по еде за сегодня нет."

    totals = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0}
    for e in today_entries:
        for k in totals:
            totals[k] += e.get(k, 0)

    count = len(today_entries)
    summary = f"Приёмов пищи: {count}\n"
    summary += f"Б: {totals['protein']}/{targets['protein']}г | "
    summary += f"Ж: {totals['fat']}/{targets['fat']}г | "
    summary += f"У: {totals['carbs']}/{targets['carbs']}г | "
    summary += f"Клетч: {totals['fiber']}/{targets['fiber']}г | "
    summary += f"{totals['kcal']}/{targets['kcal']} kcal"

    # Remaining
    remaining = []
    for key, label in [("protein", "Б"), ("fiber", "Клетч"), ("kcal", "kcal")]:
        diff = targets[key] - totals[key]
        if diff > 0:
            unit = "г" if key != "kcal" else ""
            remaining.append(f"{label} {diff}{unit}")

    if remaining:
        summary += f"\nОсталось: {' | '.join(remaining)}"

    return summary


def get_meal_type(hour: int) -> str:
    """Determine meal type from hour of day."""
    if hour < 11:
        return "breakfast"
    elif hour < 16:
        return "lunch"
    elif hour < 23:
        return "dinner"
    else:
        return "snack"
