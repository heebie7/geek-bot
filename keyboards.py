from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from config import ZONE_EMOJI, PROJECT_EMOJI, ALL_DESTINATIONS, JOY_CATEGORIES, JOY_CATEGORY_EMOJI
from tasks import _parse_sensory_menu


def get_task_confirm_keyboard(task_index: int, suggested: str) -> InlineKeyboardMarkup:
    """Keyboard for confirming task destination (zone or project)."""
    # First row: confirm suggestion
    emoji = ALL_DESTINATIONS.get(suggested, "📋")
    keyboard = [
        [InlineKeyboardButton(f"✅ {emoji} {suggested.capitalize()}", callback_data=f"taskzone_{task_index}_{suggested}")],
    ]

    # Zones row (excluding suggested)
    other_zones = [z for z in ZONE_EMOJI.keys() if z != suggested]
    row = []
    for zone in other_zones:
        e = ZONE_EMOJI[zone]
        row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{zone}"))
    keyboard.append(row)

    # Projects row (excluding suggested)
    other_projects = [p for p in PROJECT_EMOJI.keys() if p != suggested]
    row = []
    for proj in other_projects[:4]:  # max 4 per row
        e = PROJECT_EMOJI[proj]
        row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{proj}"))
    keyboard.append(row)
    if len(other_projects) > 4:
        row = []
        for proj in other_projects[4:]:
            e = PROJECT_EMOJI[proj]
            row.append(InlineKeyboardButton(f"{e}", callback_data=f"taskzone_{task_index}_{proj}"))
        keyboard.append(row)

    # Skip button
    keyboard.append([InlineKeyboardButton("⏭ Пропустить", callback_data=f"taskzone_{task_index}_skip")])

    return InlineKeyboardMarkup(keyboard)


def get_destination_keyboard(callback_prefix: str = "adddest_") -> InlineKeyboardMarkup:
    """Keyboard for choosing zone or project as task destination.

    callback_prefix allows reuse for different flows (adddest_ for /add, taskzone_ for button Add).
    """
    keyboard = []

    # Row 1: main zones (3 per row)
    zones = list(ZONE_EMOJI.items())
    row = []
    for name, emoji in zones[:3]:
        row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"{callback_prefix}{name}"))
    keyboard.append(row)

    row = []
    for name, emoji in zones[3:]:
        row.append(InlineKeyboardButton(f"{emoji} {name.capitalize()}", callback_data=f"{callback_prefix}{name}"))
    keyboard.append(row)

    # Separator label
    keyboard.append([InlineKeyboardButton("— Проекты —", callback_data="noop")])

    # Projects (2 per row)
    projects = list(PROJECT_EMOJI.items())
    for i in range(0, len(projects), 2):
        row = []
        for name, emoji in projects[i:i+2]:
            short_name = name.replace("-", " ").capitalize()
            row.append(InlineKeyboardButton(f"{emoji} {short_name}", callback_data=f"{callback_prefix}{name}"))
        keyboard.append(row)

    return InlineKeyboardMarkup(keyboard)


def get_priority_keyboard(callback_prefix: str = "addpri_") -> InlineKeyboardMarkup:
    """Inline keyboard for priority selection."""
    keyboard = [
        [
            InlineKeyboardButton("Срочное ⏫", callback_data=f"{callback_prefix}high"),
            InlineKeyboardButton("Обычное 🔼", callback_data=f"{callback_prefix}medium"),
        ],
        [
            InlineKeyboardButton("Не срочное 🔽", callback_data=f"{callback_prefix}low"),
            InlineKeyboardButton("Без приоритета", callback_data=f"{callback_prefix}none"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_main_keyboard(mode: str = "geek"):
    """Главная клавиатура."""
    keyboard = [
        [
            InlineKeyboardButton("Шаги", callback_data="next_steps"),
            InlineKeyboardButton("Неделя", callback_data="week"),
        ],
        [
            InlineKeyboardButton("Сон", callback_data="sleep"),
            InlineKeyboardButton("Еда", callback_data="food"),
            InlineKeyboardButton("Спорт", callback_data="sport"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_reply_keyboard():
    """Постоянная клавиатура внизу чата."""
    keyboard = [
        [KeyboardButton("🔥 Dashboard"), KeyboardButton("🌉 Bridge"), KeyboardButton("⚡ Шаги")],
        [KeyboardButton("🧘 Sensory"), KeyboardButton("✨ Joy"), KeyboardButton("➕ Add")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_add_keyboard():
    """Inline keyboard для выбора: Task или Note."""
    keyboard = [[
        InlineKeyboardButton("📋 Task", callback_data="add_task"),
        InlineKeyboardButton("📝 Note", callback_data="add_note"),
    ]]
    return InlineKeyboardMarkup(keyboard)


def get_note_mode_keyboard():
    """Inline keyboard для режима заметки."""
    keyboard = [[
        InlineKeyboardButton("✅ Готово", callback_data="note_done"),
        InlineKeyboardButton("❌ Отмена", callback_data="note_cancel"),
    ]]
    return InlineKeyboardMarkup(keyboard)


BINGO_ITEMS = [
    "did not eat",
    "new hyperfixation and no time for it",
    "have not done a creative in 24 hrs",
    "Bad Sounds",
    "clothes are touching my body",
    "cold",
    "people",
    "one (1) comment is stuck in my brain like a popcorn kernel",
    "last time I drank water was ??????",
    "nervous nervous nervous nervous",
    "got a Slightly Worse grade than expected",
    "last hug was ??????",
    "slept a full 45 minutes",
    "lonely ............",
    "guts are shredding (again)",
    "have not seen sunlight in 24 hrs",
    "stuck inside",
    "too much screen time",
    "Yay Overwhelm",
    "room is disaster area",
    "have not talked to Person in a while",
    "bored",
    "imposter phenomenon (again)",
    "no current routine",
    "how long have I been working???",
    "Too Much Socialization",
    "something is actually wrong",
]


def get_sensory_keyboard():
    """Inline keyboard for sensory state selection."""
    keyboard = [
        [
            InlineKeyboardButton("🔴 Хочу орать", callback_data="sensory_emergency"),
            InlineKeyboardButton("🟡 Залипла", callback_data="sensory_unfreeze"),
        ],
        [
            InlineKeyboardButton("🟢 Inputs", callback_data="sensory_inputs"),
            InlineKeyboardButton("🖤 Плохо", callback_data="sensory_bad"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_sensory_bad_keyboard(selected: set):
    """Inline keyboard for the 'Плохо' bingo checklist."""
    keyboard = []
    for i, item in enumerate(BINGO_ITEMS):
        prefix = "☑️" if i in selected else "⬜"
        keyboard.append([InlineKeyboardButton(f"{prefix} {item}", callback_data=f"sensory_bad_toggle_{i}")])
    keyboard.append([InlineKeyboardButton("🔍 investigate", callback_data="sensory_bad_submit")])
    return InlineKeyboardMarkup(keyboard)


def get_joy_keyboard():
    """Inline keyboard for joy category selection."""
    keyboard = [
        [
            InlineKeyboardButton("🧘 Sensory", callback_data="joy_cat_sensory"),
            InlineKeyboardButton("🎨 Creativity", callback_data="joy_cat_creativity"),
        ],
        [
            InlineKeyboardButton("📺 Media", callback_data="joy_cat_media"),
            InlineKeyboardButton("💚 Connection", callback_data="joy_cat_connection"),
        ],
        [
            InlineKeyboardButton("📊 Статистика", callback_data="joy_stats"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_joy_items_keyboard(category: str) -> InlineKeyboardMarkup:
    """Inline keyboard with specific items for a joy category."""
    menu = _parse_sensory_menu()
    emoji = JOY_CATEGORY_EMOJI.get(category, "✨")

    # Map joy categories to sensory menu keys
    category_map = {
        "sensory": ["inputs", "emergency", "unfreeze"],  # Combine all sensory
        "creativity": ["creativity"],
        "media": ["media"],
        "connection": ["connection"]
    }

    items = []
    for key in category_map.get(category, []):
        items.extend(menu.get(key, []))

    # Create buttons - max 2 per row, truncate long items
    keyboard = []
    row = []
    for i, item in enumerate(items[:10]):  # Limit to 10 items
        # Truncate item name for button (max ~25 chars)
        short_item = item[:22] + "..." if len(item) > 25 else item
        # callback_data max 64 bytes, use index
        callback = f"joyitem_{category}_{i}"
        row.append(InlineKeyboardButton(short_item, callback_data=callback))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Add "Другое" button and back button
    keyboard.append([
        InlineKeyboardButton("✏️ Другое", callback_data=f"joyother_{category}"),
        InlineKeyboardButton("◀️ Назад", callback_data="joy_back"),
    ])

    return InlineKeyboardMarkup(keyboard)
