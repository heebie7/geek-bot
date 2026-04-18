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
        for field in ("name", "weight_g", "kcal", "protein", "fat", "carbs", "fiber", "calcium", "confidence"):
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


def _log_date(now: datetime) -> str:
    """Return log date: if before 05:00 → previous day."""
    from datetime import timedelta
    if now.hour < 5:
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def build_food_entry(recognition: dict, match: Optional[dict], caption: Optional[str]) -> dict:
    """Assemble a food log entry from recognition result and optional kitchen match."""
    now = datetime.now(TZ)
    entry = {
        "date": _log_date(now),
        "time": now.strftime("%H:%M"),
        "meal": get_meal_type(now.hour),
        "name": recognition.get("name", "Неизвестное блюдо"),
        "matched_dish": None,
        "weight_g": recognition.get("weight_g", 0),
        "kcal": recognition.get("kcal", 0),
        "protein": recognition.get("protein", 0),
        "fat": recognition.get("fat", 0),
        "carbs": recognition.get("carbs", 0),
        "fiber": recognition.get("fiber", 0),
        "calcium": recognition.get("calcium", 0),
        "portion": recognition.get("portion", "standard"),
        "source": "vision",
        "caption": caption,
    }
    if match:
        entry["matched_dish"] = match.get("name")
        # Kitchen stores per-100g values — scale by weight_g
        weight_g = entry.get("weight_g", 0) or 0
        ratio = weight_g / 100 if weight_g else 1.0  # no weight → assume 100g serving
        for field in ("kcal", "protein", "fat", "carbs"):
            v = match.get(field)
            if v is not None:
                try:
                    entry[field] = round(float(v) * ratio, 1)
                except (TypeError, ValueError):
                    pass
        # fiber and calcium always from Gemini (kitchen DB doesn't track them reliably yet)
        entry["source"] = "kitchen_match"
    elif caption:
        entry["source"] = "vision+caption"

    # FatSecret lookup: supplement KBJU if weight is known and FS keys are configured
    weight_g = entry.get("weight_g", 0)
    if weight_g and weight_g > 0 and entry["source"] not in ("kitchen_match",):
        try:
            from fatsecret import lookup as fs_lookup
            fs = fs_lookup(entry["name"], weight_g)
            if fs:
                entry["kcal"] = fs["kcal"]
                entry["protein"] = fs["protein"]
                entry["fat"] = fs["fat"]
                entry["carbs"] = fs["carbs"]
                # Keep Gemini fiber/calcium if FS returns 0 (often missing)
                if fs["fiber"] > 0:
                    entry["fiber"] = fs["fiber"]
                if fs["calcium"] > 0:
                    entry["calcium"] = fs["calcium"]
                entry["source"] = entry["source"].replace("vision", "fatsecret")
                entry["fs_name"] = fs["fs_name"]
        except Exception as e:
            logger.debug(f"FatSecret lookup skipped: {e}")

    return entry


def build_custom_entry(dish: dict) -> dict:
    """Build a food log entry from a custom/frequent dish. No Gemini call."""
    now = datetime.now(TZ)
    return {
        "date": _log_date(now),
        "time": now.strftime("%H:%M"),
        "meal": get_meal_type(now.hour),
        "name": dish.get("name", "?"),
        "matched_dish": dish.get("name"),
        "kcal": dish.get("kcal", 0),
        "protein": dish.get("protein", 0),
        "fat": dish.get("fat", 0),
        "carbs": dish.get("carbs", 0),
        "fiber": dish.get("fiber", 0),
        "calcium": dish.get("calcium", 0),
        "portion": "standard",
        "source": "custom",
        "caption": None,
    }


def _rescale_entry(entry: dict, new_weight: int) -> None:
    """Rescale KBJU in-place proportionally to new weight."""
    old_weight = entry.get("weight_g", 0)
    if old_weight and old_weight != new_weight:
        ratio = new_weight / old_weight
        for field in ("kcal", "protein", "fat", "carbs", "fiber", "calcium"):
            entry[field] = round(entry.get(field, 0) * ratio)
    elif not old_weight and entry.get("source") == "kitchen_match":
        # Kitchen DB stores per-100g values; when weight was unknown at save time,
        # KBJU was stored as 100g-equivalent → rescale to actual weight now
        ratio = new_weight / 100
        for field in ("kcal", "protein", "fat", "carbs", "fiber", "calcium"):
            entry[field] = round(entry.get(field, 0) * ratio)
    entry["weight_g"] = new_weight


def format_food_result(entry: dict) -> str:
    """Format a food entry for display in Telegram."""
    source_label = {
        "kitchen_match": "kitchen DB",
        "custom": "частое блюдо",
        "vision": "Gemini Vision",
        "vision+caption": "Gemini Vision + подпись",
        "fatsecret": "FatSecret",
        "fatsecret+caption": "FatSecret + подпись",
        "text": "текст",
    }
    weight = entry.get('weight_g', 0)
    weight_str = f" (~{weight}г)" if weight else ""
    ca = entry.get('calcium', 0) or 0
    ca_str = f" | Ca: {ca}мг" if ca > 0 else ""
    lines = [
        f"🍽 {entry['name']}{weight_str}",
        f"Б: {entry['protein']}г | Ж: {entry['fat']}г | У: {entry['carbs']}г | Клетч: {entry['fiber']}г{ca_str} | {entry['kcal']} kcal",
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

    totals = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0, "calcium": 0}
    for e in today_entries:
        for k in totals:
            totals[k] += e.get(k, 0) or 0

    ca_target = targets.get("calcium", 1100)

    count = len(today_entries)
    summary = f"Приёмов пищи: {count}\n"
    summary += f"Б: {totals['protein']}/{targets['protein']}г | "
    summary += f"Ж: {totals['fat']}/{targets['fat']}г | "
    summary += f"У: {totals['carbs']}/{targets['carbs']}г | "
    summary += f"Клетч: {totals['fiber']}/{targets['fiber']}г | "
    summary += f"{totals['kcal']}/{targets['kcal']} kcal\n"
    summary += f"Ca: {totals['calcium']}/{ca_target}мг"

    # Remaining
    remaining = []
    for key, label, unit in [
        ("protein", "Б", "г"),
        ("fiber", "Клетч", "г"),
        ("calcium", "Ca", "мг"),
        ("kcal", "kcal", ""),
    ]:
        target_val = targets.get(key)
        if target_val is None:
            continue
        diff = target_val - totals.get(key, 0)
        if diff > 0:
            remaining.append(f"{label} {diff}{unit}")

    if remaining:
        summary += f"\nОсталось: {' | '.join(remaining)}"

    return summary


_MEAL_RU = {
    "breakfast": "завтрак", "lunch": "обед", "dinner": "ужин",
    "snack": "перекус", "ужин": "ужин", "обед": "обед",
    "завтрак": "завтрак", "перекус": "перекус",
}


def format_daily_log_for_telegram(log: list, targets: Optional[dict], date: str) -> str:
    """Itemized daily log for Telegram: each entry on its own line + totals."""
    if targets is None:
        targets = dict(DEFAULT_FOOD_TARGETS)

    today_entries = [e for e in log if e.get("date") == date]
    if not today_entries:
        return "Данных по еде за сегодня нет."

    lines = [f"📋 {date}:"]
    totals = {"kcal": 0, "protein": 0, "fat": 0, "carbs": 0, "fiber": 0, "calcium": 0}

    for e in today_entries:
        for k in totals:
            totals[k] += e.get(k, 0) or 0
        time = e.get("time", "—")
        meal = _MEAL_RU.get(e.get("meal", ""), e.get("meal", ""))
        name = e.get("name", "?")
        kcal = round(e.get("kcal", 0) or 0)
        weight = e.get("weight_g")
        wstr = f" {weight}г" if weight else ""
        lines.append(f"• {time} {meal} {name}{wstr} — {kcal} ккал")

    lines.append("")
    lines.append(
        f"Итого: {round(totals['kcal'])} ккал | "
        f"Б{round(totals['protein'])} | Ж{round(totals['fat'])} | "
        f"У{round(totals['carbs'])} | Клетч{round(totals['fiber'])} | "
        f"Ca{round(totals['calcium'])}"
    )

    remaining = []
    for key, label in [("protein", "Б"), ("fiber", "Клетч"), ("calcium", "Ca"), ("kcal", "ккал")]:
        tv = targets.get(key)
        if tv is None:
            continue
        diff = tv - totals.get(key, 0)
        if diff > 0:
            remaining.append(f"{label} -{round(diff)}")
    if remaining:
        lines.append("Осталось: " + " | ".join(remaining))

    return "\n".join(lines)


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


# Keyword detection for edit intent — used by handler to route text
# to parse_edit_command() before the normal "new food" flow.
EDIT_VERBS = r"^(убери|удали|замени|переименуй|перенеси|смени|поменяй|измени)\b"


def is_edit_command(text: str) -> bool:
    """Return True if text starts with an edit-intent verb."""
    return bool(re.match(EDIT_VERBS, text.strip().lower()))


_EDIT_PARSE_PROMPT = """Ты парсер команд редактирования дневника питания.

Есть список записей за сегодня с индексами:
{entries_block}

Пользователь написал:
"{command}"

Определи, что нужно сделать. Верни строго JSON одной из форм:

1. Удалить запись:
{{"op": "remove", "target_idx": N}}

2. Изменить вес записи (и пересчитать КБЖУ пропорционально):
{{"op": "rescale", "target_idx": N, "new_weight_g": W}}

3. Переименовать запись:
{{"op": "rename", "target_idx": N, "new_name": "..."}}

4. Перенести запись в другой приём пищи:
{{"op": "move", "target_idx": N, "new_meal": "breakfast|lunch|dinner|snack"}}

5. Если не удалось однозначно определить цель:
{{"op": "ambiguous", "reason": "...короткое объяснение..."}}

6. Если цель не найдена в списке:
{{"op": "not_found", "reason": "...короткое объяснение..."}}

Правила:
- target_idx — индекс из списка выше (число)
- Для rescale: если пользователь пишет "по Xг" и в name есть число N — это N*X
- "обед"="lunch", "ужин"="dinner", "завтрак"="breakfast", "перекус"="snack"
- Только один JSON-объект, без markdown, без комментариев.
"""


def parse_edit_command(command: str, today_entries: list) -> dict:
    """Parse user's natural-language edit command via Gemini.

    Args:
        command: User text, e.g. "убери творог" or "замени 200г на 300".
        today_entries: List of today's food log entries (ordered).

    Returns:
        dict: {op, target_idx, ...} or {op: "error", reason: "..."}.
    """
    if not gemini_client:
        return {"op": "error", "reason": "Gemini недоступен"}

    if not today_entries:
        return {"op": "not_found", "reason": "Лог за сегодня пуст"}

    # Build compact numbered list
    lines = []
    for i, e in enumerate(today_entries):
        time = e.get("time", "—")
        meal = _MEAL_RU.get(e.get("meal", ""), e.get("meal", ""))
        name = e.get("name", "?")
        weight = e.get("weight_g") or 0
        wstr = f" {weight}г" if weight else ""
        kcal = round(e.get("kcal", 0) or 0)
        lines.append(f"[{i}] {time} {meal}: {name}{wstr} ({kcal} ккал)")
    entries_block = "\n".join(lines)

    prompt = _EDIT_PARSE_PROMPT.format(
        entries_block=entries_block, command=command
    )

    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[types.Content(parts=[types.Part(text=prompt)])],
        )
        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        if "op" not in result:
            return {"op": "error", "reason": "Нет поля op в ответе"}
        return result
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Edit command parse error: {e}")
        return {"op": "error", "reason": "Не распознал команду"}
    except Exception as e:
        logger.error(f"Edit command error: {e}")
        return {"op": "error", "reason": str(e)}


def apply_edit_op(op_result: dict, today_entries: list, log_data: dict) -> tuple[bool, str]:
    """Apply parsed edit op to log_data in-place.

    Args:
        op_result: Output of parse_edit_command().
        today_entries: Ordered list of today's entries (same refs as in log_data["log"]).
        log_data: Full food log dict (mutated in place).

    Returns:
        (success, human_message). Caller is responsible for save + regen.
    """
    op = op_result.get("op")

    if op == "not_found":
        return False, f"Не нашёл: {op_result.get('reason', '?')}"
    if op == "ambiguous":
        return False, f"Непонятно: {op_result.get('reason', '?')}"
    if op == "error":
        return False, f"Ошибка: {op_result.get('reason', '?')}"

    idx = op_result.get("target_idx")
    if idx is None or not isinstance(idx, int) or idx < 0 or idx >= len(today_entries):
        return False, "Индекс записи вне диапазона"

    target = today_entries[idx]

    if op == "remove":
        log_data["log"].remove(target)
        return True, f"Удалено: {target.get('name', '?')}"

    if op == "rescale":
        new_w = op_result.get("new_weight_g")
        if not isinstance(new_w, (int, float)) or new_w <= 0:
            return False, "Не указан корректный новый вес"
        _rescale_entry(target, int(new_w))
        return True, f"Пересчитано: {target.get('name', '?')} → {int(new_w)}г ({target.get('kcal')} ккал)"

    if op == "rename":
        new_name = op_result.get("new_name")
        if not new_name:
            return False, "Не указано новое название"
        old = target.get("name", "?")
        target["name"] = str(new_name).strip()
        return True, f"Переименовано: {old} → {target['name']}"

    if op == "move":
        new_meal = op_result.get("new_meal")
        if new_meal not in ("breakfast", "lunch", "dinner", "snack"):
            return False, "Неизвестный приём пищи"
        target["meal"] = new_meal
        return True, f"Перенесено: {target.get('name', '?')} → {_MEAL_RU.get(new_meal, new_meal)}"

    return False, f"Неизвестная операция: {op}"
