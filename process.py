#!/usr/bin/env python3
"""
Финансовый процессор: Zen Money + PayPal + Credo SMS → единый CSV + monthly summary.

Использование:
    python process.py 2026-01              # один месяц
    python process.py 2025                  # весь год
    python process.py 2026-01 --dry-run     # только показать, не записывать

Ищет raw файлы в finance/raw/{year}/ по паттернам:
    zen*.csv, pp*.csv (или paypal*.csv), credo_sms*.csv
"""

import argparse
import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# === ХЕЛПЕР ДЛЯ ПОДДЕРЖКИ io.StringIO ===

def _open_source(source, encoding="utf-8-sig"):
    """Открыть файл (Path/str) или вернуть file-like object как есть."""
    if isinstance(source, (str, Path)):
        return open(source, "r", encoding=encoding)
    return source  # io.StringIO и т.д.


def set_rates(rates):
    """Установить курсы валют извне (для вызова из адаптера)."""
    global LOADED_RATES
    LOADED_RATES = rates


# === ПУТИ ===
SCRIPT_DIR = Path(__file__).parent
FINANCE_DIR = SCRIPT_DIR.parent
CATEGORIES_FILE = SCRIPT_DIR / "categories.json"
RAW_DIR = FINANCE_DIR / "raw"
PROCESSED_DIR = FINANCE_DIR / "processed"
SUMMARIES_DIR = FINANCE_DIR / "summaries"

# === КУРСЫ ВАЛЮТ ===
# Автозагрузка с floatrates.com, кэш на 24ч, fallback на случай ошибок
CACHE_FILE = SCRIPT_DIR / "exchange_rates_cache.json"
FLOATRATES_API = "http://www.floatrates.com/daily/rub.json"
CACHE_MAX_AGE_HOURS = 24

# Запасные курсы если API недоступен
FALLBACK_RATES = {"GEL": 28.5, "USD": 76.7, "EUR": 90.4, "GBP": 104.1, "RUB": 1.0}

# Загруженные курсы (заполняется в main)
LOADED_RATES = None


def fetch_floatrates():
    """Загружает курсы с floatrates.com. Возвращает dict или None при ошибке."""
    try:
        with urllib.request.urlopen(FLOATRATES_API, timeout=10) as response:
            data = json.loads(response.read().decode())

        rates = {
            "GEL": round(data.get("gel", {}).get("inverseRate", FALLBACK_RATES["GEL"]), 2),
            "USD": round(data.get("usd", {}).get("inverseRate", FALLBACK_RATES["USD"]), 2),
            "EUR": round(data.get("eur", {}).get("inverseRate", FALLBACK_RATES["EUR"]), 2),
            "GBP": round(data.get("gbp", {}).get("inverseRate", FALLBACK_RATES["GBP"]), 2),
            "RUB": 1.0,
        }
        return rates
    except Exception as e:
        print(f"  ! Не удалось загрузить курсы: {e}")
        return None


def load_exchange_rates():
    """Загружает курсы: кэш (если свежий) → API → fallback."""
    # Проверяем кэш
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            cached_time = datetime.fromisoformat(cache.get("timestamp", "2000-01-01"))
            age_hours = (datetime.now() - cached_time).total_seconds() / 3600
            if age_hours < CACHE_MAX_AGE_HOURS:
                rates = cache.get("rates", FALLBACK_RATES)
                print(f"  Курсы из кэша ({age_hours:.1f}ч): USD={rates['USD']}, EUR={rates['EUR']}, GEL={rates['GEL']}")
                return rates
        except Exception:
            pass  # Битый кэш — идём дальше

    # Загружаем с API
    print("  Загрузка курсов с floatrates.com...")
    rates = fetch_floatrates()

    if rates:
        # Сохраняем в кэш
        try:
            cache_data = {"timestamp": datetime.now().isoformat(), "rates": rates}
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # Не смогли записать кэш — не страшно
        print(f"  Курсы обновлены: USD={rates['USD']}, EUR={rates['EUR']}, GEL={rates['GEL']}, GBP={rates['GBP']}")
        return rates

    # Fallback
    print(f"  Используются резервные курсы: USD={FALLBACK_RATES['USD']}, EUR={FALLBACK_RATES['EUR']}")
    return FALLBACK_RATES


def load_categories(json_str=None):
    """Загружает категории из JSON-строки или файла."""
    if json_str:
        return json.loads(json_str)
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_rate(currency, date_str):
    """Получить курс валюты к RUB на дату."""
    rates = LOADED_RATES if LOADED_RATES else FALLBACK_RATES
    return rates.get(currency, 1.0)


def to_rub(amount, currency, date_str):
    """Конвертировать сумму в RUB."""
    rate = get_rate(currency, date_str)
    return round(amount * rate, 2)


def strip_surname(description):
    """
    Убирает фамилию из description, оставляя только имя.
    Работает для форматов:
      "Incoming: NATALIA FOMINCEVA" → "Incoming: Natalia"
      "PAYS*MEDVEDEVA, PRAPION" → "PAYS*Prapion"
      "ULANOVA, ANNA" → "Anna"
      "ALEKSANDR SELIVANOV" → "Aleksandr"
    """
    desc = description.strip()

    # Формат "Incoming: FIRSTNAME LASTNAME"
    if desc.startswith("Incoming: "):
        parts = desc[len("Incoming: "):].split()
        if len(parts) >= 2:
            return f"Incoming: {parts[0].capitalize()}"
        return desc

    # Формат "PAYS*LASTNAME, FIRSTNAME"
    if "PAYS*" in desc:
        prefix_end = desc.index("PAYS*") + len("PAYS*")
        name_part = desc[prefix_end:]
        if ", " in name_part:
            _, firstname = name_part.split(", ", 1)
            return f"{desc[:prefix_end]}{firstname.strip().capitalize()}"
        return desc

    # Формат "LASTNAME, FIRSTNAME"
    if ", " in desc:
        _, firstname = desc.split(", ", 1)
        return firstname.strip().capitalize()

    # Формат "FIRSTNAME LASTNAME" (два слова, оба uppercase)
    parts = desc.split()
    if len(parts) == 2 and parts[0].isupper() and parts[1].isupper():
        return parts[0].capitalize()

    # Формат "Firstname Lastname" (два слова, mixed case, оба начинаются с заглавной)
    if len(parts) == 2 and parts[0][0].isupper() and parts[1][0].isupper():
        # Не трогаем если похоже на название компании (содержит точку, цифры и т.д.)
        if not any(c in desc for c in '.0123456789'):
            return parts[0]

    return desc


# =============================================================================
# ПАРСЕРЫ ИСТОЧНИКОВ
# =============================================================================

def parse_zen(filepath, categories, target_period):
    """
    Парсит Zen Money CSV.

    Логика определения типа транзакции:
    - outcome > 0 И income == 0 → расход
    - income > 0 И outcome == 0 → доход
    - outcome > 0 И income > 0 → перевод между счетами

    Дедупликация: Zen Money экспорт иногда содержит дубли.
    Строки с одинаковым (date, payee, outcome, income, outcomeAccount, incomeAccount) пропускаются.
    """
    rows = []
    cat_map_exp = categories["zen"]["expense"]
    cat_map_inc = categories["zen"]["income"]
    payee_exp_override = categories["zen"].get("payee_expense_override", {})
    seen = set()  # для дедупликации

    with _open_source(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            date_str = row.get("date", "").strip()
            if not date_str:
                continue

            # Фильтр по периоду
            if not date_str.startswith(target_period):
                continue

            category_name = row.get("categoryName", "").strip().strip('"')
            payee = row.get("payee", "").strip().strip('"')

            # Переводы через Золотую Корону / КоронаПэй — это transfer, не расход
            payee_lower = payee.lower()
            is_korona = "золотая корона" in payee_lower or "koronapay" in payee_lower
            comment = row.get("comment", "").strip().strip('"')
            outcome_acc = row.get("outcomeAccountName", "").strip().strip('"')
            outcome = row.get("outcome", "0").strip().strip('"').replace(",", ".")
            outcome_curr = row.get("outcomeCurrencyShortTitle", "").strip().strip('"')
            income_acc = row.get("incomeAccountName", "").strip().strip('"')
            income = row.get("income", "0").strip().strip('"').replace(",", ".")
            income_curr = row.get("incomeCurrencyShortTitle", "").strip().strip('"')

            try:
                outcome_val = float(outcome) if outcome else 0
                income_val = float(income) if income else 0
            except ValueError:
                continue

            # Дедупликация: пропускаем строки с идентичными ключевыми полями
            dedup_key = (date_str, payee, outcome, income, outcome_acc, income_acc)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            description = payee or comment or category_name or ""
            # Обрезать длинные описания от банков
            description = description.strip()[:80]

            # Определяем тип транзакции
            if outcome_val > 0 and income_val > 0:
                # Перевод между счетами
                rows.append({
                    "date": date_str,
                    "type": "transfer",
                    "category": "transfer",
                    "description": f"{outcome_acc} → {income_acc}: {description}",
                    "amount": outcome_val,
                    "currency": outcome_curr,
                    "amount_rub": to_rub(outcome_val, outcome_curr, date_str),
                    "source": "zenmoney",
                    "account": outcome_acc,
                })
            elif outcome_val > 0:
                # Расход (или transfer если Корона)
                if is_korona:
                    cat = "transfer"
                    tx_type = "transfer"
                else:
                    if not category_name:
                        if "Озон" in outcome_acc or "Ozon" in outcome_acc:
                            cat = "transfer"
                            tx_type = "transfer"
                        elif "Tinkoff" in outcome_acc:
                            cat = "transfer"
                            tx_type = "transfer"
                        else:
                            cat = "other_expense"
                            tx_type = "expense"
                    else:
                        cat = cat_map_exp.get(category_name, "other_expense")
                        tx_type = "expense"
                    # Переопределение по payee (приоритет над категорией)
                    if payee in payee_exp_override:
                        cat = payee_exp_override[payee]
                rows.append({
                    "date": date_str,
                    "type": tx_type,
                    "category": cat,
                    "description": description,
                    "amount": outcome_val,
                    "currency": outcome_curr,
                    "amount_rub": to_rub(outcome_val, outcome_curr, date_str),
                    "source": "zenmoney",
                    "account": outcome_acc,
                })
            elif income_val > 0:
                # Доход
                cat = cat_map_inc.get(category_name, "other_income")
                rows.append({
                    "date": date_str,
                    "type": "income",
                    "category": cat,
                    "description": description,
                    "amount": income_val,
                    "currency": income_curr,
                    "amount_rub": to_rub(income_val, income_curr, date_str),
                    "source": "zenmoney",
                    "account": income_acc,
                })

    return rows


def parse_paypal(filepath, categories, target_period):
    """
    Парсит PayPal CSV.

    Фильтрует:
    - Currency Conversion строки (дубли)
    - Account Hold строки
    - Считает Gross (не Net) как сумму операции
    """
    rows = []
    sub_map = categories["paypal"]["subscriptions"]
    merchant_map = categories["paypal"].get("merchants", {})
    types_ignore = set(categories["paypal"]["types_conversion"] + categories["paypal"]["types_ignore"])

    with _open_source(filepath, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        # Автодетект формата: "Type" (2026 EU) vs "Description" (2025 US)
        fields = reader.fieldnames or []
        is_eu_format = "Type" in fields and "Description" not in fields
        type_col = "Type" if is_eu_format else "Description"
        date_fmt = "%d/%m/%Y" if is_eu_format else "%m/%d/%Y"

        for row in reader:
            description = row.get(type_col, "").strip().strip('"')

            # Пропускаем конвертации и холды
            if description in types_ignore:
                continue

            # Парсим дату
            date_raw = row.get("Date", "").strip().strip('"')
            if not date_raw:
                continue
            try:
                dt = datetime.strptime(date_raw, date_fmt)
            except ValueError:
                # Fallback: пробуем альтернативный формат
                alt_fmt = "%m/%d/%Y" if is_eu_format else "%d/%m/%Y"
                try:
                    dt = datetime.strptime(date_raw, alt_fmt)
                except ValueError:
                    continue
            date_str = dt.strftime("%Y-%m-%d")

            # Фильтр по периоду
            if not date_str.startswith(target_period):
                continue

            currency = row.get("Currency", "").strip().strip('"')
            gross_str = row.get("Gross", "0").strip().strip('"')
            # Поддержка EU формата: "-8,00" → "-8.00"
            if "." not in gross_str and "," in gross_str:
                gross_str = gross_str.replace(",", ".")
            else:
                gross_str = gross_str.replace(",", "")
            name = row.get("Name", "").strip().strip('"')

            try:
                gross = float(gross_str)
            except ValueError:
                continue

            if gross == 0:
                continue

            # Определяем категорию
            if gross > 0:
                # Доход
                tx_type = "income"
                cat = "work_income"
                desc = name or description
            else:
                # Расход
                tx_type = "expense"
                gross = abs(gross)
                desc = name or description

                # Категоризация расходов (порядок: подписки → мерчанты → fallback)
                cat = "other_expense"
                name_lower = (name or "").lower()

                # 1. Подписки (по description type + имени)
                if description in ("Subscription Payment", "PreApproved Payment Bill User Payment"):
                    for sub_name, sub_cat in sub_map.items():
                        if sub_name.lower() in name_lower:
                            cat = sub_cat
                            break
                    else:
                        cat = "subscriptions"

                # 2. Мерчанты (по имени получателя)
                if cat == "other_expense" or cat == "subscriptions":
                    for merchant_name, merchant_cat in merchant_map.items():
                        if merchant_name.lower() in name_lower:
                            cat = merchant_cat
                            break

                # 3. Fallback по типу описания
                if cat == "other_expense":
                    if description == "Express Checkout Payment":
                        cat = "shopping"

            # Анонимизация: убираем фамилии клиентов
            if cat not in ("other_expense", "other_income"):
                desc = strip_surname(desc)

            tx_id = row.get("Transaction ID", "").strip().strip('"')
            rows.append({
                "date": date_str,
                "type": tx_type,
                "category": cat,
                "description": desc[:80],
                "amount": gross,
                "currency": currency,
                "amount_rub": to_rub(gross, currency, date_str),
                "source": "paypal",
                "account": "PayPal",
                "_tx_id": tx_id,
            })

    return rows


def parse_credo_sms(filepath, categories, target_period):
    """
    Парсит CSV из extract_sms.py (банковские SMS Credo Bank).

    Формат CSV: date,time,type,amount,currency,merchant,card,balance,raw_body

    Дедупликация:
    - PayPal-операции из SMS пропускаются (PayPal CSV точнее)
    """
    rows = []
    paypal_skipped = 0
    sms_cat = categories.get("credo_sms", {})
    merchant_map = sms_cat.get("merchants", {})
    type_map = sms_cat.get("type_mapping", {})

    with _open_source(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row.get("date", "").strip()
            if not date_str:
                continue

            # Фильтр по периоду
            if not date_str.startswith(target_period):
                continue

            sms_type = row.get("type", "").strip()
            amount_str = row.get("amount", "0").strip()
            currency = row.get("currency", "").strip()
            merchant = row.get("merchant", "").strip()
            card = row.get("card", "").strip()

            try:
                amount = float(amount_str)
            except ValueError:
                continue

            if amount <= 0:
                continue

            # Пропускаем PayPal-операции (PayPal CSV точнее)
            if "PAYPAL" in merchant.upper():
                paypal_skipped += 1
                continue

            # Определяем тип для unified формата
            unified_type = type_map.get(sms_type, "expense")

            # Категоризация по мерчанту
            category = "other_expense"
            if unified_type == "transfer":
                category = "transfer"
            elif sms_type == "utility":
                category = "utilities"
            elif sms_type == "commission":
                category = "interest"
            elif sms_type == "cash_out":
                category = "cash_out"
            elif unified_type == "income":
                category = "other_income"
                # Проверяем мерчант и для доходов (KORONAPAY → transfer, etc)
                merchant_upper = merchant.upper()
                for pattern, cat in merchant_map.items():
                    if pattern.upper() in merchant_upper:
                        category = cat
                        break
            else:
                merchant_upper = merchant.upper()
                for pattern, cat in merchant_map.items():
                    if pattern.upper() in merchant_upper:
                        category = cat
                        break

            # Анонимизация: убираем фамилии для категоризированных персональных операций
            desc = merchant[:80]
            if category not in ("other_expense", "other_income"):
                desc = strip_surname(desc)

            rows.append({
                "date": date_str,
                "type": unified_type,
                "category": category,
                "description": desc,
                "amount": amount,
                "currency": currency,
                "amount_rub": to_rub(amount, currency, date_str),
                "source": "credo_sms",
                "account": f"Credo *{card}" if card else "Credo",
            })

    if paypal_skipped > 0:
        print(f"  Credo SMS: пропущено {paypal_skipped} PayPal-операций (есть PayPal CSV)")

    return rows


# =============================================================================
# ПАРСИНГ WOLT CSV
# =============================================================================

def parse_wolt(filepath, categories, period):
    """
    Парсит Wolt CSV (vendor,date,total,currency,items_count,file_name,month,year,category).
    Сервисные строки (Сервис Wolt, Подписка Wolt+) пропускаются.
    """
    wolt_map = categories.get("wolt", {})

    if len(period) == 7:
        period_month = period
    else:
        period_month = None

    rows = []
    skipped_service = 0

    with _open_source(filepath, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        for row in reader:
            if len(row) < 9:
                continue
            vendor, date_str, total_str, currency, _, _, month_str, year_str, wolt_cat = row[:9]
            wolt_cat = wolt_cat.strip()

            if period_month and not date_str.startswith(period_month[:7]):
                if period_month[:4] != date_str[:4]:
                    continue
                if period_month != date_str[:7]:
                    continue
            if not period_month and date_str[:4] != period:
                continue

            if wolt_cat in ("Сервис Wolt", "Подписка Wolt+"):
                skipped_service += 1
                continue

            try:
                amount = float(total_str.replace(",", "."))
            except Exception:
                continue

            if amount <= 0:
                continue

            category = wolt_map.get(wolt_cat, "other_expense")
            amount_rub = to_rub(amount, currency, date_str)

            rows.append({
                "date": date_str,
                "type": "expense",
                "category": category,
                "description": vendor[:80],
                "amount": amount,
                "currency": currency,
                "amount_rub": amount_rub,
                "source": "wolt",
                "account": "Wolt",
            })

    if skipped_service:
        print(f"  Wolt: пропущено {skipped_service} сервисных строк")
    return rows


# =============================================================================
# ПОИСК ФАЙЛОВ
# =============================================================================

def find_raw_files(year):
    """Ищет raw файлы для указанного года."""
    year_dir = RAW_DIR / str(year)
    if not year_dir.exists():
        print(f"Директория {year_dir} не найдена")
        return {}

    files = {}
    zen_candidates = []
    for f in year_dir.iterdir():
        name = f.name.lower()
        if name.startswith("zen") and name.endswith(".csv"):
            zen_candidates.append(f)
        elif (name.startswith("pp") or name.startswith("paypal") or name.startswith("download")) and name.endswith(".csv"):
            files.setdefault("paypal_files", []).append(f)
        elif name.startswith("credo_sms") and name.endswith(".csv"):
            files["credo_sms"] = f

    # Если несколько zen файлов — берём последний по дате в имени (zen_YYYY-MM-DD_*) или по mtime
    if zen_candidates:
        import re as _re
        def _zen_key(f):
            m = _re.search(r'zen_(\d{4}-\d{2}-\d{2})', f.name)
            return (1, m.group(1)) if m else (0, str(f.stat().st_mtime))
        files["zen"] = sorted(zen_candidates, key=_zen_key)[-1]
        if len(zen_candidates) > 1:
            print(f"  [zen] найдено {len(zen_candidates)} файлов, используется: {files['zen'].name}")

    return files


# =============================================================================
# ГЕНЕРАЦИЯ SUMMARY
# =============================================================================

RU_MONTHS = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь",
}


def generate_monthly_summary(rows, period, categories):
    """Генерирует markdown summary для месяца."""
    display = categories["display_names"]

    income_rows = [r for r in rows if r["type"] == "income"]
    expense_rows = [r for r in rows if r["type"] == "expense"]
    transfer_rows = [r for r in rows if r["type"] == "transfer"]

    # Суммы по категориям
    income_by_cat = defaultdict(float)
    for r in income_rows:
        income_by_cat[r["category"]] += r["amount_rub"]

    expense_by_cat = defaultdict(float)
    for r in expense_rows:
        expense_by_cat[r["category"]] += r["amount_rub"]

    # Суммы по источникам
    expense_by_source = defaultdict(float)
    for r in expense_rows:
        expense_by_source[r["source"]] += r["amount_rub"]

    total_income = sum(income_by_cat.values())
    total_expense = sum(expense_by_cat.values())
    balance = total_income - total_expense

    # Определяем название месяца
    if len(period) == 7:  # YYYY-MM
        month_num = int(period[5:7])
        year = period[:4]
        title = f"{RU_MONTHS[month_num]} {year}"
    else:
        title = period

    lines = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(f"*Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    # Баланс
    sign = "+" if balance >= 0 else ""
    lines.append(f"## Баланс: {sign}{balance:,.0f} R")
    lines.append("")
    lines.append(f"- Доходы: **{total_income:,.0f} R**")
    lines.append(f"- Расходы: **{total_expense:,.0f} R**")
    lines.append(f"- Переводы между счетами: {len(transfer_rows)} операций")
    lines.append("")

    # Доходы
    lines.append("## Доходы")
    lines.append("")
    lines.append("| Категория | Сумма R |")
    lines.append("|-----------|---------|")
    for cat, amount in sorted(income_by_cat.items(), key=lambda x: -x[1]):
        name = display.get(cat, cat)
        lines.append(f"| {name} | {amount:,.0f} |")
    lines.append(f"| **Итого** | **{total_income:,.0f}** |")
    lines.append("")

    # Расходы
    lines.append("## Расходы")
    lines.append("")
    lines.append("| Категория | Сумма R | % |")
    lines.append("|-----------|---------|---|")
    for cat, amount in sorted(expense_by_cat.items(), key=lambda x: -x[1]):
        name = display.get(cat, cat)
        pct = (amount / total_expense * 100) if total_expense > 0 else 0
        lines.append(f"| {name} | {amount:,.0f} | {pct:.1f}% |")
    lines.append(f"| **Итого** | **{total_expense:,.0f}** | |")
    lines.append("")

    # По источникам
    lines.append("## Расходы по источникам")
    lines.append("")
    lines.append("| Источник | Сумма R |")
    lines.append("|----------|---------|")
    for src, amount in sorted(expense_by_source.items(), key=lambda x: -x[1]):
        lines.append(f"| {src} | {amount:,.0f} |")
    lines.append("")

    # Топ расходов
    lines.append("## Топ-10 расходов")
    lines.append("")
    lines.append("| Дата | Описание | Сумма | Валюта | R |")
    lines.append("|------|----------|-------|--------|---|")
    top_expenses = sorted(expense_rows, key=lambda x: -x["amount_rub"])[:10]
    for r in top_expenses:
        lines.append(f"| {r['date']} | {r['description']} | {r['amount']:,.2f} | {r['currency']} | {r['amount_rub']:,.0f} |")
    lines.append("")

    # Заметки
    lines.append("## Заметки")
    lines.append("")
    lines.append("- ")
    lines.append("")

    return "\n".join(lines)


def generate_yearly_summary(rows, year, categories):
    """Генерирует markdown summary для года."""
    display = categories["display_names"]

    # Группировка по месяцам
    months_data = defaultdict(list)
    for r in rows:
        month_key = r["date"][:7]
        months_data[month_key].append(r)

    lines = []
    lines.append(f"# Финансовая сводка {year}")
    lines.append("")
    lines.append(f"*Сгенерировано: {datetime.now().strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    # Таблица по месяцам
    lines.append("## Помесячный баланс")
    lines.append("")
    lines.append("| Месяц | Доходы | Расходы | Баланс |")
    lines.append("|-------|--------|---------|--------|")

    total_inc_year = 0
    total_exp_year = 0

    for m in range(1, 13):
        key = f"{year}-{m:02d}"
        month_rows = months_data.get(key, [])
        if not month_rows:
            continue
        inc = sum(r["amount_rub"] for r in month_rows if r["type"] == "income")
        exp = sum(r["amount_rub"] for r in month_rows if r["type"] == "expense")
        bal = inc - exp
        sign = "+" if bal >= 0 else ""
        total_inc_year += inc
        total_exp_year += exp
        lines.append(f"| {RU_MONTHS[m]} | {inc:,.0f} | {exp:,.0f} | {sign}{bal:,.0f} |")

    bal_year = total_inc_year - total_exp_year
    sign_y = "+" if bal_year >= 0 else ""
    lines.append(f"| **Итого** | **{total_inc_year:,.0f}** | **{total_exp_year:,.0f}** | **{sign_y}{bal_year:,.0f}** |")
    lines.append(f"| *Среднее/мес* | *{total_inc_year/12:,.0f}* | *{total_exp_year/12:,.0f}* | |")
    lines.append("")

    # Расходы по категориям за год
    expense_rows = [r for r in rows if r["type"] == "expense"]
    expense_by_cat = defaultdict(float)
    for r in expense_rows:
        expense_by_cat[r["category"]] += r["amount_rub"]

    total_exp = sum(expense_by_cat.values())

    lines.append("## Расходы по категориям (год)")
    lines.append("")
    lines.append("| Категория | Сумма R | Среднее/мес | % |")
    lines.append("|-----------|---------|-------------|---|")
    for cat, amount in sorted(expense_by_cat.items(), key=lambda x: -x[1]):
        name = display.get(cat, cat)
        pct = (amount / total_exp * 100) if total_exp > 0 else 0
        lines.append(f"| {name} | {amount:,.0f} | {amount/12:,.0f} | {pct:.1f}% |")
    lines.append("")

    # Доходы по категориям
    income_rows = [r for r in rows if r["type"] == "income"]
    income_by_cat = defaultdict(float)
    for r in income_rows:
        income_by_cat[r["category"]] += r["amount_rub"]

    lines.append("## Доходы по категориям (год)")
    lines.append("")
    lines.append("| Категория | Сумма R | Среднее/мес |")
    lines.append("|-----------|---------|-------------|")
    for cat, amount in sorted(income_by_cat.items(), key=lambda x: -x[1]):
        name = display.get(cat, cat)
        lines.append(f"| {name} | {amount:,.0f} | {amount/12:,.0f} |")
    lines.append("")

    return "\n".join(lines)


# =============================================================================
# ЗАПИСЬ РЕЗУЛЬТАТОВ
# =============================================================================

CSV_FIELDS = ["date", "type", "category", "description", "amount", "currency", "amount_rub", "source", "account"]


def write_csv(rows, filepath):
    """Записывает нормализованные данные в CSV."""
    rows_sorted = sorted(rows, key=lambda x: x["date"])

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows_sorted)

    print(f"  CSV: {filepath} ({len(rows_sorted)} строк)")


def write_findoc_csv(rows, filepath, display_names):
    """
    Записывает CSV в формате Financial Doc плагина для Obsidian.

    Формат: Category,Subcategory,Value,TimeStamp,Extra
    - Category: тип (income/expense) с русским названием
    - Subcategory: категория расхода/дохода
    - Value: сумма в RUB (расходы как отрицательные)
    - TimeStamp: дата YYYY-MM-DD
    - Extra: описание транзакции
    """
    type_names = {"income": "Income", "expense": "Expenses", "transfer": "Transfer"}
    # Только доходы и расходы, без переводов (включая income с category=transfer)
    filtered = [r for r in rows if r["type"] in ("income", "expense") and r["category"] != "transfer"]
    filtered_sorted = sorted(filtered, key=lambda x: x["date"])

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Subcategory", "Value", "TimeStamp", "Extra"])
        for r in filtered_sorted:
            cat_display = display_names.get(r["category"], r["category"])
            value = r["amount_rub"] if r["type"] == "income" else -r["amount_rub"]
            writer.writerow([
                type_names.get(r["type"], r["type"]),
                cat_display,
                round(value, 2),
                r["date"],
                r["description"],
            ])

    print(f"  FinDoc CSV: {filepath} ({len(filtered_sorted)} строк)")


def write_findoc_pie_csv(rows, filepath, display_names):
    """
    Записывает агрегированный CSV для pie charts в Financial Doc.

    Одна строка на подкатегорию с суммой.
    Формат: Category,Subcategory,Value,TimeStamp,Extra
    - Category и Subcategory: одинаковые = название подкатегории
    - Value: сумма в RUB (положительная)
    - TimeStamp: первый день периода
    - Extra: тип (расход/доход)

    Отдельные файлы для расходов и доходов не нужны —
    findoc модель фильтрует по Category.
    """
    from collections import defaultdict

    # Агрегируем по (тип, подкатегория)
    totals = defaultdict(float)
    first_date = None
    for r in rows:
        if r["type"] not in ("income", "expense"):
            continue
        if r["category"] == "transfer":
            continue
        cat_display = display_names.get(r["category"], r["category"])
        totals[(r["type"], cat_display)] += r["amount_rub"]
        if first_date is None or r["date"] < first_date:
            first_date = r["date"]

    if first_date is None:
        first_date = "2026-01-01"

    with open(filepath, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Category", "Subcategory", "Value", "TimeStamp", "Extra"])
        for (tx_type, cat_name), total in sorted(totals.items(), key=lambda x: -x[1]):
            type_label = "расход" if tx_type == "expense" else "доход"
            writer.writerow([
                cat_name,
                cat_name,
                round(abs(total), 2),
                first_date,
                type_label,
            ])

    print(f"  FinDoc Pie CSV: {filepath} ({len(totals)} категорий)")


def write_summary(text, filepath):
    """Записывает summary markdown."""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  Summary: {filepath}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Финансовый процессор")
    parser.add_argument("period", help="Период: YYYY-MM (месяц) или YYYY (год)")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, не записывать")
    args = parser.parse_args()

    period = args.period

    # Определяем год и тип периода
    if len(period) == 4:
        year = period
        is_year = True
    elif len(period) == 7:
        year = period[:4]
        is_year = False
    else:
        print(f"Неверный формат периода: {period}. Используйте YYYY или YYYY-MM")
        sys.exit(1)

    # Загружаем курсы валют
    global LOADED_RATES
    LOADED_RATES = load_exchange_rates()

    # Загружаем категории
    categories = load_categories()

    # Ищем файлы
    raw_files = find_raw_files(year)
    if not raw_files:
        print(f"Не найдены raw файлы в {RAW_DIR / year}")
        sys.exit(1)

    print(f"\nПериод: {period}")
    print(f"Найдены файлы:")
    for source, path in raw_files.items():
        if isinstance(path, list):
            for p in path:
                print(f"  {source}: {p.name}")
        else:
            print(f"  {source}: {path.name}")
    print()

    # Парсим каждый источник
    # Порядок важен для дедупликации:
    # 1. Credo SMS — точные данные по грузинским картам
    # 2. Zen Money — RUB операции (точные), GEL операции (неточные, дедуплицируются)
    # 3. PayPal — точные данные
    all_rows = []
    has_credo_sms = False

    if "credo_sms" in raw_files:
        credo_rows = parse_credo_sms(
            raw_files["credo_sms"], categories, period,
        )
        has_credo_sms = len(credo_rows) > 0
        print(f"Credo SMS: {len(credo_rows)} транзакций")
        all_rows.extend(credo_rows)

    if "zen" in raw_files:
        zen_rows = parse_zen(
            raw_files["zen"], categories, period,
        )
        # Если есть Credo SMS — фильтруем GEL-операции из Zen Money (SMS точнее)
        if has_credo_sms:
            before = len(zen_rows)
            zen_rows = [r for r in zen_rows if r["currency"] != "GEL" or r["type"] == "transfer"]
            gel_skipped = before - len(zen_rows)
            if gel_skipped > 0:
                print(f"  Zen Money: пропущено {gel_skipped} GEL-операций (есть Credo SMS)")
        print(f"Zen Money: {len(zen_rows)} транзакций")
        all_rows.extend(zen_rows)

    if "paypal_files" in raw_files:
        seen_tx_ids = set()
        pp_all = []
        for pp_file in sorted(raw_files["paypal_files"], key=lambda f: f.name):
            pp_rows = parse_paypal(pp_file, categories, period)
            for r in pp_rows:
                tx_id = r.pop("_tx_id", "")
                if tx_id and tx_id in seen_tx_ids:
                    continue
                if tx_id:
                    seen_tx_ids.add(tx_id)
                pp_all.append(r)
        print(f"PayPal: {len(pp_all)} транзакций ({len(raw_files['paypal_files'])} файлов)")
        all_rows.extend(pp_all)

    print(f"\nВсего: {len(all_rows)} транзакций")

    # Статистика
    income_total = sum(r["amount_rub"] for r in all_rows if r["type"] == "income")
    expense_total = sum(r["amount_rub"] for r in all_rows if r["type"] == "expense")
    transfer_count = sum(1 for r in all_rows if r["type"] == "transfer")
    print(f"Доходы: {income_total:,.0f} R")
    print(f"Расходы: {expense_total:,.0f} R")
    print(f"Переводы: {transfer_count} операций")
    print(f"Баланс: {income_total - expense_total:+,.0f} R")

    # Непривязанные категории
    unknown_exp = [r for r in all_rows if r["category"] == "other_expense" and r["type"] == "expense"]
    unknown_inc = [r for r in all_rows if r["category"] == "other_income" and r["type"] == "income"]
    if unknown_exp:
        print(f"\n  Нераспознанные расходы: {len(unknown_exp)}")
        for r in unknown_exp[:5]:
            print(f"    {r['date']} {r['description']} {r['amount']} {r['currency']}")
    if unknown_inc:
        print(f"\n  Нераспознанные доходы: {len(unknown_inc)}")
        for r in unknown_inc[:5]:
            print(f"    {r['date']} {r['description']} {r['amount']} {r['currency']}")

    if args.dry_run:
        print("\n--dry-run: файлы не записаны")
        return

    # Создаём директории
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARIES_DIR.mkdir(parents=True, exist_ok=True)

    # Записываем CSV (основной формат)
    csv_path = PROCESSED_DIR / f"{period}.csv"
    write_csv(all_rows, csv_path)

    # Записываем CSV для Financial Doc плагина
    findoc_path = PROCESSED_DIR / f"{period}-findoc.csv"
    write_findoc_csv(all_rows, findoc_path, categories["display_names"])

    # Записываем CSV для pie charts (Category = подкатегория)
    findoc_pie_path = PROCESSED_DIR / f"{period}-findoc-pie.csv"
    write_findoc_pie_csv(all_rows, findoc_pie_path, categories["display_names"])

    # Генерируем и записываем summary
    if is_year:
        summary = generate_yearly_summary(all_rows, year, categories)
    else:
        summary = generate_monthly_summary(all_rows, period, categories)

    summary_path = SUMMARIES_DIR / f"{period}.md"
    write_summary(summary, summary_path)

    print("\nГотово.")


if __name__ == "__main__":
    main()
