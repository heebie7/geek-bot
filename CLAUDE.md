# Geek-bot — Claude Code Project Guide

## What is this?

Personal Telegram bot with two AI personas (Geek/ART from Murderbot + Leya/coach-navigator). Python, deployed on Railway.

## Architecture

```
bot.py          — orchestrator: main(), button_callback() dispatcher, check_access()
handlers.py     — command handlers, job functions, handle_message()
config.py       — env vars, constants, LLM clients (leaf module, no local imports)
prompts.py      — system prompts: GEEK_PROMPT, LEYA_PROMPT, WHOOP_HEALTH_SYSTEM, SENSORY_LEYA_PROMPT
storage.py      — GitHub I/O, mute, family, reminders, calendar
tasks.py        — task CRUD, projects, sensory menu, rawnotes
llm.py          — get_llm_response(), health detection, motivations, WHOOP context
keyboards.py    — inline + reply keyboard builders
finance.py      — CSV upload, /income, /process
finance_processor.py — adapter: GitHub ↔ process.py
process.py      — CSV parsers (zen/paypal/wolt/credo_sms)
joy.py          — Joy tracking (log, stats)
whoop.py        — WHOOP API v2 client (OAuth2, token refresh)
meal_data.py    — meal database + weekly menu generator
```

## Dependency graph (no cycles)

```
config.py, prompts.py       ← leaf modules
storage.py                  ← config
tasks.py                    ← config, storage (lazy import llm in suggest_zone_for_task)
keyboards.py                ← config, tasks
joy.py                      ← config, storage
llm.py                      ← config, prompts, storage, whoop
finance.py                  ← config, storage
handlers.py                 ← all above + whoop, meal_data
bot.py                      ← handlers, all above
```

**Only potential cycle:** tasks↔llm — resolved via lazy import inside `suggest_zone_for_task()`.

## LLM routing

Dual Gemini models with OpenAI fallback:

1. **Gemini 2.5 Pro** — for WHOOP commands and health topics (smarter analysis)
2. **Gemini 2.5 Flash** — primary for everything else (fast, cheap)
3. **OpenAI GPT-4o-mini** — fallback if Gemini fails

Same `gemini_client` serves both models — just different model string selected via `use_pro` parameter.

Routing logic in `llm.py`:
- `use_pro=True` → Gemini 2.5 Pro, fall back to OpenAI
- `use_pro=False` (default) → Gemini 2.5 Flash, fall back to OpenAI
- Health topic auto-detection: `_is_health_topic()` checks 35 keywords (RU+EN)

Call sites that use Gemini Pro (`use_pro=True`):
- `/whoop` command (handlers.py) + `custom_system=WHOOP_HEALTH_SYSTEM`
- Morning recovery report (bot.py, morning callback) + `custom_system=WHOOP_HEALTH_SYSTEM`
- Weekly WHOOP summary (handlers.py) + `custom_system=WHOOP_HEALTH_SYSTEM`
- Regular chat messages — only when `_is_health_topic(user_message)` is True

## How to add a new feature

1. **New command:** Add handler function in `handlers.py` → register in `bot.py:main()` with `CommandHandler`
2. **New button:** Add keyboard in `keyboards.py` → add callback case in `bot.py:button_callback()`
3. **New LLM prompt:** Add to `prompts.py` → use as `custom_system` parameter in `get_llm_response()`
4. **New scheduled job:** Add function in `handlers.py` → register in `bot.py:main()` via `job_queue`

## Environment variables

Required on Railway:
```
TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, OPENAI_API_KEY,
GITHUB_TOKEN, GITHUB_REPO, WRITING_REPO,
WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET,
ALLOWED_USER_IDS, GOOGLE_CALENDAR_ID
```

Optional: `GEMINI_MODEL`, `GEMINI_PRO_MODEL`, `OPENAI_MODEL`

WHOOP tokens (access + refresh) are in `whoop_tokens.json` on GitHub, not in env vars.

## Files to IGNORE

- **`bot_original.py`** — old backup, NOT used in production. Do NOT read, edit, or grep. Waste of tokens.

## Common pitfalls

- **`load_dotenv()` without `override=True`:** Won't override existing env vars. On Railway this is fine (vars set directly). Locally can cause issues if env already has empty values.
- **Gemini 2.0 Flash deprecated:** Shutting down March 31, 2026. Use `gemini-2.5-flash` (default).
- **SAVE tags in LLM output:** Always strip with `re.sub(r'\[SAVE:[^\]]+\]', '', text)` for scheduled messages.
- **WHOOP 401:** Auto-handled: force reload from GitHub → retry → full OAuth refresh → save.

## Testing

```bash
# Verify all imports work
python3 -c "from bot import main; print('OK')"

# Test health detection
python3 -c "from llm import _is_health_topic; print(_is_health_topic('как мой сон?'))"  # True
python3 -c "from llm import _is_health_topic; print(_is_health_topic('добавь задачу'))"  # False
```

## Timezone

Everything uses `Asia/Tbilisi` (TZ in config.py). Scheduled jobs use this timezone.
