"""
Meal database and weekly menu generator.
Data mirrors family kitchen.md in Obsidian.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

ALL = {"А", "Н", "Т", "К"}


@dataclass
class Meal:
    name: str
    category: str  # "белок", "гарнир", "овощи", "целое", "личное_А"
    eaters: set
    ingredients: list
    kcal: int = 0
    protein: int = 0
    fat: int = 0
    carbs: int = 0


# === БЕЛКОВОЕ ===

PROTEINS = [
    Meal("Покупной холодец", "белок", ALL - {"Н"},
         ["холодец"], 180, 20, 10, 0),
    Meal("Котлеты", "белок", ALL,
         ["фарш", "лук", "хлеб", "яйцо"], 220, 15, 15, 8),
    Meal("Свинина", "белок", ALL,
         ["свинина"], 250, 22, 18, 0),
    Meal("Фарш", "белок", ALL,
         ["фарш"], 230, 18, 17, 0),
    Meal("Куриные ноги", "белок", ALL,
         ["куриные ноги"], 200, 18, 14, 0),
    Meal("Кура в соусе", "белок", ALL,
         ["курица", "соус"], 180, 20, 8, 5),
    Meal("Паштет из филе", "белок", ALL,
         ["куриное филе", "масло", "специи"], 160, 18, 9, 1),
    Meal("Exponenta", "белок", ALL,
         ["напиток"], 80, 15, 0, 5),
    Meal("Милкшейк", "белок", ALL,
         ["молоко", "протеин"], 200, 20, 5, 20),
    Meal("Сырники запечённые", "белок", ALL,
         ["творог", "яйцо", "мука"], 180, 14, 5, 18),
    Meal("Омлет", "белок", ALL,
         ["яйца", "молоко"], 150, 12, 10, 2),
    Meal("Творог", "белок", ALL,
         ["творог"], 120, 16, 5, 3),
    Meal("Йогурт", "белок", ALL,
         ["йогурт"], 100, 8, 3, 12),
]

# === ГАРНИР ===

SIDES = [
    Meal("Гречка", "гарнир", ALL - {"Т"},
         ["гречка"], 130, 4, 1, 25),
    Meal("Рис", "гарнир", ALL - {"Т"},
         ["рис"], 130, 3, 0, 28),
    Meal("Картошка запечённая", "гарнир", ALL,
         ["картофель", "масло"], 150, 2, 5, 25),
    Meal("Макароны", "гарнир", ALL,
         ["макароны"], 160, 5, 1, 30),
    Meal("Горошек", "гарнир", ALL,
         ["зелёный горошек"], 80, 5, 0, 14),
    Meal("Пюре", "гарнир", ALL,
         ["картофель", "молоко", "масло"], 120, 2, 4, 17),
    Meal("Картошка", "гарнир", ALL,
         ["картофель"], 130, 2, 3, 22),
    Meal("Гренки", "гарнир", ALL,
         ["хлеб", "масло"], 200, 5, 8, 28),
]

# === ОВОЩНОЕ ===

VEGGIES = [
    Meal("Капустный салат", "овощи", ALL,
         ["капуста", "морковь", "уксус"], 50, 2, 0, 10),
    Meal("Овощи", "овощи", ALL,
         ["сезонные овощи"], 60, 2, 1, 10),
    Meal("Салат Вальдорф", "овощи", ALL,
         ["сельдерей", "яблоко", "орехи", "майонез"], 180, 3, 14, 12),
]

# === ЦЕЛОЕ БЛЮДО ===

COMPLETE_MEALS = [
    Meal("Борщ", "целое", ALL,
         ["свёкла", "капуста", "картофель", "мясо"], 250, 12, 10, 25),
    Meal("Чечевичный суп", "целое", ALL,
         ["чечевица", "лук", "морковь", "специи"], 200, 12, 3, 30),
    Meal("Пельмени", "целое", ALL,
         ["пельмени"], 280, 12, 12, 30),
    Meal("Салат из тунца", "целое", {"А", "К"},
         ["тунец", "яйцо", "кукуруза", "майонез"], 220, 18, 12, 10),
    Meal("Суп Фо", "целое", ALL,
         ["лапша", "бульон", "говядина", "зелень"], 300, 18, 8, 35),
    Meal("Киш с брокколи и лососем", "целое", ALL,
         ["тесто", "брокколи", "лосось", "яйца"], 320, 18, 18, 22),
    Meal("Бигус", "целое", ALL,
         ["капуста", "мясо", "колбаса", "специи"], 280, 15, 16, 15),
    Meal("Капустно-куриный пирог", "целое", ALL,
         ["капуста", "курица", "тесто"], 260, 14, 12, 22),
    Meal("Тортилья", "целое", ALL,
         ["тортилья", "начинка", "сыр", "соус"], 300, 15, 14, 28),
    Meal("Рис с морепродуктами", "целое", ALL,
         ["рис", "морепродукты", "овощи"], 320, 22, 8, 38),
    Meal("Рагу", "целое", ALL,
         ["мясо", "картофель", "морковь", "лук"], 250, 14, 10, 25),
    Meal("Сэндвич гриль", "целое", ALL,
         ["хлеб", "сыр", "начинка"], 300, 12, 14, 28),
    Meal("Овсянка с протеином", "целое", ALL,
         ["овсянка", "протеин", "молоко"], 250, 18, 5, 35),
    Meal("Запеканка", "целое", ALL,
         ["творог", "яйца", "мука"], 220, 10, 8, 28),
    Meal("Гранола", "целое", ALL,
         ["овсянка", "орехи", "мёд", "сухофрукты"], 200, 6, 8, 28),
]

# === ЛИЧНОЕ А ===

PERSONAL_A_MEALS = [
    Meal("Egg bites", "личное_А", {"А"},
         ["яйца", "творог", "йогурт", "зелень"], 370, 43, 0, 0),
    Meal("Лобио домашнее", "личное_А", {"А"},
         ["фасоль"], 270, 19, 0, 0),
    Meal("Бутеры без сыра", "личное_А", {"А"},
         ["диетхлеб", "курица"], 115, 12, 0, 0),
    Meal("Врап с пудингом", "личное_А", {"А"},
         ["лаваш", "ванильный пудинг"], 115, 11, 0, 0),
    Meal("Домашняя шаурма (1/2)", "личное_А", {"А"},
         ["лаваш", "курица", "овощи"], 140, 14, 0, 0),
    Meal("Ванильный пудинг", "личное_А", {"А"},
         ["молоко", "творог", "казеин"], 63, 8, 0, 0),
    Meal("Свинина air fryer", "личное_А", {"А"},
         ["свинина"], 190, 19, 0, 0),
]


# === ГЕНЕРАТОР МЕНЮ ===

DAY_NAMES = ["Понедельник", "Вторник", "Среда", "Четверг",
             "Пятница", "Суббота", "Воскресенье"]


def _pick_unique(pool: list, used: set) -> Meal:
    """Pick a meal from pool, preferring unused ones."""
    unused = [m for m in pool if m.name not in used]
    if unused:
        return random.choice(unused)
    return random.choice(pool)


def _pick_family_meal(used_proteins, used_sides, used_complete):
    """Pick either protein+side(+veggie) or a complete meal for the family."""
    use_complete = random.random() < 0.4

    if use_complete:
        # Prefer meals everyone eats
        family_complete = [m for m in COMPLETE_MEALS if m.eaters == ALL]
        if not family_complete:
            family_complete = COMPLETE_MEALS
        meal = _pick_unique(family_complete, used_complete)
        used_complete.add(meal.name)
        return meal.name
    else:
        p = _pick_unique(PROTEINS, used_proteins)
        used_proteins.add(p.name)
        # Pick side compatible with the protein's eaters
        compatible_sides = [s for s in SIDES if p.eaters.issubset(s.eaters)]
        if not compatible_sides:
            compatible_sides = SIDES
        s = _pick_unique(compatible_sides, used_sides)
        used_sides.add(s.name)
        parts = [p.name, s.name]
        # 35% chance to add a veggie
        if random.random() < 0.35 and VEGGIES:
            v = random.choice(VEGGIES)
            parts.append(v.name)
        return " + ".join(parts)


def generate_weekly_menu() -> str:
    """Generate a random weekly meal plan. Returns HTML-formatted string for Telegram."""
    tz = ZoneInfo("Asia/Tbilisi")
    today = datetime.now(tz).date()
    monday = today - timedelta(days=today.weekday())
    week_dates = [monday + timedelta(days=i) for i in range(7)]

    used_proteins = set()
    used_sides = set()
    used_complete = set()
    used_personal = set()

    # Pick 1-2 delivery slots (not Friday dinner, not Sunday)
    eligible = []
    for i in range(7):
        if i == 6:  # Sunday — бабушка
            continue
        eligible.append((i, "lunch"))
        if i != 4:  # Friday dinner = шаурма
            eligible.append((i, "dinner"))

    num_delivery = random.choice([1, 2])
    delivery_slots = set(random.sample(eligible, min(num_delivery, len(eligible))))

    lines = ["МЕНЮ НА НЕДЕЛЮ", ""]

    for day_idx in range(7):
        date_str = week_dates[day_idx].strftime("%d.%m")
        day_name = DAY_NAMES[day_idx]

        # Sunday = бабушка
        if day_idx == 6:
            lines.append(f"{day_name} {date_str}")
            lines.append("  Бабушка приносит еду")
            lines.append("")
            continue

        lines.append(f"{day_name} {date_str}")

        for meal_time, label in [("lunch", "Обед"), ("dinner", "Ужин")]:
            # Friday dinner = шаурма + кино
            if day_idx == 4 and meal_time == "dinner":
                lines.append(f"  {label}:  Шаурма + кино")
                continue

            # Delivery slot
            if (day_idx, meal_time) in delivery_slots:
                lines.append(f"  {label}:  Заказ (доставка)")
                if meal_time == "lunch":
                    a_meal = _pick_unique(PERSONAL_A_MEALS, used_personal)
                    used_personal.add(a_meal.name)
                    lines.append(
                        f"    А: {a_meal.name} ({a_meal.kcal} ккал, {a_meal.protein}г б)"
                    )
                continue

            # Regular meal
            meal_str = _pick_family_meal(used_proteins, used_sides, used_complete)
            lines.append(f"  {label}:  {meal_str}")

            # Add personal A meal for lunches
            if meal_time == "lunch":
                a_meal = _pick_unique(PERSONAL_A_MEALS, used_personal)
                used_personal.add(a_meal.name)
                lines.append(
                    f"    А: {a_meal.name} ({a_meal.kcal} ккал, {a_meal.protein}г б)"
                )

        lines.append("")

    text = "\n".join(lines)
    return f"<pre>{text}</pre>"
