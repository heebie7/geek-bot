import random
from datetime import datetime
from google import genai
from config import (
    gemini_client, openai_client,
    GEMINI_MODEL, GEMINI_PRO_MODEL, OPENAI_MODEL,
    TZ, logger, USER_CONTEXT_FILE, LEYA_CONTEXT_FILE,
)
from prompts import GEEK_PROMPT, LEYA_PROMPT
from storage import load_file, get_writing_file
from tasks import get_life_tasks
from whoop import whoop_client


# Cache for motivations (loaded once)
_motivations_cache = None


def get_motivations() -> str:
    """Get motivations from Writing repo context/motivations.md. Cached."""
    global _motivations_cache
    if _motivations_cache is not None:
        return _motivations_cache

    content = get_writing_file("context/motivations.md")
    if content:
        _motivations_cache = content
        logger.info("Loaded motivations from Writing repo")
    else:
        _motivations_cache = ""
        logger.warning("Failed to load motivations")
    return _motivations_cache


def get_motivations_for_whoop(sleep_hours: float, strain: float) -> str:
    """Get relevant motivations based on WHOOP data. Returns 2-3 quotes."""
    import random
    content = get_motivations()
    if not content:
        return ""

    lines = content.split("\n")
    sleep_quotes = []
    exercise_quotes = []
    sleep_praise = []
    exercise_praise = []

    current_section = None
    for line in lines:
        if line.startswith("## Про сон"):
            current_section = "sleep"
        elif line.startswith("## Про бокс"):
            current_section = "exercise"
        elif line.startswith("## Похвала за сон"):
            current_section = "sleep_praise"
        elif line.startswith("## Похвала за бокс") or line.startswith("## Похвала за тренировку"):
            current_section = "exercise_praise"
        elif line.startswith("## "):
            current_section = None
        elif line.startswith("> ") and current_section:
            quote = line[2:].strip()
            if current_section == "sleep":
                sleep_quotes.append(quote)
            elif current_section == "exercise":
                exercise_quotes.append(quote)
            elif current_section == "sleep_praise":
                sleep_praise.append(quote)
            elif current_section == "exercise_praise":
                exercise_praise.append(quote)

    result = []

    # Pick based on data
    if sleep_hours < 7 and sleep_quotes:
        result.extend(random.sample(sleep_quotes, min(2, len(sleep_quotes))))
    elif sleep_hours >= 7 and sleep_praise:
        result.append(random.choice(sleep_praise))

    if strain < 5 and exercise_quotes:
        result.extend(random.sample(exercise_quotes, min(2, len(exercise_quotes))))
    elif strain >= 5 and exercise_praise:
        result.append(random.choice(exercise_praise))

    return "\n\n".join(result) if result else ""


def get_motivations_for_mode(mode: str, sleep_hours: float, strain: float, recovery: int) -> str:
    """Get motivations based on mode (recovery/moderate/normal) and data.

    Args:
        mode: "recovery", "moderate", or "normal"
        sleep_hours: hours of sleep
        strain: yesterday's strain
        recovery: recovery percentage

    Returns:
        2-3 motivation quotes for LLM to adapt
    """
    import random
    content = get_motivations()
    if not content:
        return ""

    lines = content.split("\n")
    recovery_quotes = []
    moderate_quotes = []
    sleep_quotes = []
    sleep_praise = []
    exercise_quotes = []
    exercise_praise = []
    feeling_good = []
    feeling_bad = []

    current_section = None
    for line in lines:
        if line.startswith("## Восстановительный режим"):
            current_section = "recovery"
        elif line.startswith("## Умеренный режим"):
            current_section = "moderate"
        elif line.startswith("## После \"Отлично\"") or line.startswith("## После \"Норм\""):
            current_section = "feeling_good"
        elif line.startswith("## После \"Устала\"") or line.startswith("## После \"Плохо\""):
            current_section = "feeling_bad"
        elif line.startswith("## Про сон"):
            current_section = "sleep"
        elif line.startswith("## Про бокс"):
            current_section = "exercise"
        elif line.startswith("## Похвала за сон"):
            current_section = "sleep_praise"
        elif line.startswith("## Похвала за тренировку"):
            current_section = "exercise_praise"
        elif line.startswith("## "):
            current_section = None
        elif line.startswith("> ") and current_section:
            quote = line[2:].strip()
            if current_section == "recovery":
                recovery_quotes.append(quote)
            elif current_section == "moderate":
                moderate_quotes.append(quote)
            elif current_section == "feeling_good":
                feeling_good.append(quote)
            elif current_section == "feeling_bad":
                feeling_bad.append(quote)
            elif current_section == "sleep":
                sleep_quotes.append(quote)
            elif current_section == "sleep_praise":
                sleep_praise.append(quote)
            elif current_section == "exercise":
                exercise_quotes.append(quote)
            elif current_section == "exercise_praise":
                exercise_praise.append(quote)

    result = []

    # Mode-specific quotes
    if mode == "recovery" and recovery_quotes:
        result.extend(random.sample(recovery_quotes, min(2, len(recovery_quotes))))
    elif mode == "moderate" and moderate_quotes:
        result.extend(random.sample(moderate_quotes, min(2, len(moderate_quotes))))
    else:
        # Normal mode - use classic sleep/exercise logic
        if sleep_hours < 7 and sleep_quotes:
            result.append(random.choice(sleep_quotes))
        elif sleep_hours >= 7 and sleep_praise:
            result.append(random.choice(sleep_praise))

        if strain < 5 and exercise_quotes:
            result.append(random.choice(exercise_quotes))
        elif strain >= 5 and exercise_praise:
            result.append(random.choice(exercise_praise))

    return "\n\n".join(result) if result else ""


def get_sleep_level() -> int:
    """Определить уровень напоминания о сне по текущему времени.

    Returns:
        0 — не время для напоминаний
        1 — мягкое (1:00-1:29)
        2 — настойчивое (1:30-1:59)
        3 — директива (2:00-5:59)
    """
    now = datetime.now(TZ)
    hour = now.hour
    minute = now.minute

    if hour == 1 and minute < 30:
        return 1
    elif hour == 1 and minute >= 30:
        return 2
    elif 2 <= hour < 6:
        return 3
    return 0


# Health-related keywords for routing to Gemini Pro
_HEALTH_KEYWORDS = {
    "sleep", "recovery", "hrv", "strain", "whoop", "rhr",
    "heart rate", "workout", "training", "exercise",
    "boxing", "cardio", "rest", "fatigue", "energy",
    "health", "overtraining",
    "сон", "восстановление", "пульс", "нагрузка", "тренировка",
    "бокс", "кардио", "отдых", "усталость", "энергия",
    "здоровье", "тело", "перетренированность", "спорт",
    "сердце", "давление", "рекавери", "стрейн",
}


def _is_health_topic(message: str) -> bool:
    """Check if user message is about health/fitness/WHOOP topics."""
    lower = message.lower()
    return any(kw in lower for kw in _HEALTH_KEYWORDS)


async def get_llm_response(user_message: str, mode: str = "geek", history: list = None, max_tokens: int = 800, skip_context: bool = False, custom_system: str = None, use_pro: bool = False) -> str:
    """Получить ответ от LLM. Gemini Flash primary, Gemini Pro для здоровья, OpenAI fallback.

    skip_context=True — не грузить tasks/whoop в system prompt (для команд где контекст уже в user_message).
    custom_system — полностью заменяет system prompt (для специализированных режимов вроде sensory).
    use_pro=True — использовать Gemini 2.5 Pro (для WHOOP/здоровья) вместо Flash.
    """
    current_time = datetime.now(TZ).strftime("%Y-%m-%d %H:%M, %A")

    if custom_system:
        system = custom_system
    else:
        if skip_context:
            tasks = ""
            whoop_data = ""
        else:
            tasks = get_life_tasks()
            whoop_data = _get_whoop_context()

        if mode == "leya":
            user_context = load_file(LEYA_CONTEXT_FILE, "Контекст не загружен.")
            system = LEYA_PROMPT.format(user_context=user_context, current_time=current_time, tasks=tasks, whoop_data=whoop_data)
        else:
            user_context = load_file(USER_CONTEXT_FILE, "Профиль не настроен.")
            system = GEEK_PROMPT.format(user_context=user_context, current_time=current_time, tasks=tasks, whoop_data=whoop_data)

    # Собираем контекст диалога
    if history is None:
        history = []

    # Select Gemini model: Pro for health/WHOOP, Flash for everything else
    model = GEMINI_PRO_MODEL if use_pro else GEMINI_MODEL

    # Try Gemini
    if gemini_client:
        try:
            # Gemini: передаём историю как список сообщений
            gemini_contents = []
            for msg in history:
                gemini_contents.append(genai.types.Content(
                    role="user" if msg["role"] == "user" else "model",
                    parts=[genai.types.Part(text=msg["content"])]
                ))
            gemini_contents.append(genai.types.Content(
                role="user",
                parts=[genai.types.Part(text=user_message)]
            ))

            response = gemini_client.models.generate_content(
                model=model,
                contents=gemini_contents,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=max_tokens,
                ),
            )
            if response.text:
                finish = getattr(response.candidates[0], 'finish_reason', 'UNKNOWN') if response.candidates else 'NO_CANDIDATES'
                logger.info(f"Gemini response OK ({model}), finish_reason={finish}, len={len(response.text)}")
                return response.text
            else:
                finish = getattr(response.candidates[0], 'finish_reason', 'UNKNOWN') if response.candidates else 'NO_CANDIDATES'
                logger.warning(f"Gemini {model} returned empty response, finish_reason={finish}, falling back to OpenAI")
        except Exception as e:
            logger.warning(f"Gemini API error, falling back to OpenAI: {e}")

    # Fallback to OpenAI
    if openai_client:
        try:
            # OpenAI: system + история + текущее сообщение
            messages = [{"role": "system", "content": system}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})
            messages.append({"role": "user", "content": user_message})

            response = openai_client.chat.completions.create(
                model=OPENAI_MODEL,
                max_tokens=max_tokens,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")

    return "Все API недоступны. Попробуй позже."


def _get_whoop_context() -> str:
    """Get WHOOP data as context string for LLM prompts."""
    try:
        parts = []
        rec = whoop_client.get_recovery_today()
        if rec:
            score = rec.get("score", {})
            rs = score.get("recovery_score")
            rhr = score.get("resting_heart_rate")
            hrv = score.get("hrv_rmssd_milli")
            if rs is not None:
                color = "green" if rs >= 67 else ("yellow" if rs >= 34 else "red")
                parts.append(f"Recovery сегодня: {rs}% ({color})")
            if rhr is not None:
                parts.append(f"RHR: {rhr} bpm")
            if hrv is not None:
                parts.append(f"HRV: {round(hrv, 1)} ms")

        sleep = whoop_client.get_sleep_today()
        if sleep:
            ss = sleep.get("score", {})
            stage = ss.get("stage_summary", {})
            rem = stage.get("total_rem_sleep_time_milli", 0)
            deep = stage.get("total_slow_wave_sleep_time_milli", 0)
            light = stage.get("total_light_sleep_time_milli", 0)
            actual_h = round((rem + deep + light) / 3_600_000, 1)
            perf = ss.get("sleep_performance_percentage")
            parts.append(f"Сон: {actual_h}h (performance {perf}%)")

        # Strain / boxing
        cycle = whoop_client.get_cycle_today()
        if cycle:
            strain = round(cycle.get("score", {}).get("strain", 0), 1)
            boxed = "да" if strain >= 5 else "нет"
            parts.append(f"Strain: {strain} (бокс: {boxed})")

        # Weekly averages
        week = whoop_client.get_recovery_week()
        if week:
            scores = [r.get("score", {}).get("recovery_score") for r in week if r.get("score", {}).get("recovery_score") is not None]
            if scores:
                avg = round(sum(scores) / len(scores))
                green = sum(1 for s in scores if s >= 67)
                red = sum(1 for s in scores if s < 34)
                parts.append(f"Recovery за неделю: avg {avg}% (green {green}/7, red {red}/7)")

        if parts:
            return "\n".join(parts)
        return "WHOOP: нет данных"
    except Exception as e:
        logger.debug(f"WHOOP context fetch failed: {e}")
        return "WHOOP: недоступен"
