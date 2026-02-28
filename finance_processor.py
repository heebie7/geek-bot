"""
Адаптер: скачивает raw CSV из GitHub, запускает парсеры из process.py,
заливает результат обратно. Вызывается из /process команды бота.
"""

import io
import csv
import json
from pathlib import Path

from config import logger
from storage import list_writing_dir, get_writing_file, save_writing_file
from process import (
    parse_zen, parse_paypal, parse_credo_sms, parse_wolt,
    fetch_floatrates, set_rates, load_categories,
    generate_monthly_summary, generate_yearly_summary,
    CSV_FIELDS, FALLBACK_RATES,
)

CATEGORIES_FILE = Path(__file__).parent / "categories.json"


def _load_local_categories():
    """Загрузить categories.json из бандла в репо."""
    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _download_raw_files(year: str) -> dict:
    """Скачать raw CSV из GitHub, вернуть {source_type: content_string}."""
    import re

    dir_path = f"finance/raw/{year}"
    files = list_writing_dir(dir_path)
    if not files:
        return {}

    # Сортируем файлы по типу
    zen_candidates = {}  # name -> path
    paypal_files = {}
    credo_sms = {}
    wolt_files = {}

    for name, path in files.items():
        lower = name.lower()
        if lower.startswith("zen") and lower.endswith(".csv"):
            zen_candidates[name] = path
        elif (lower.startswith("pp") or lower.startswith("paypal") or lower.startswith("download")) and lower.endswith(".csv"):
            paypal_files[name] = path
        elif lower.startswith("credo_sms") and lower.endswith(".csv"):
            credo_sms[name] = path
        elif lower.startswith("wolt") and lower.endswith(".csv"):
            wolt_files[name] = path

    raw = {}

    # Zen Money: только последний файл (каждый экспорт — полный дамп)
    if zen_candidates:
        def _zen_sort_key(name):
            m = re.search(r'zen_(\d{4}-\d{2}-\d{2})', name)
            return (1, m.group(1)) if m else (0, name)

        latest_name = sorted(zen_candidates.keys(), key=_zen_sort_key)[-1]
        latest_path = zen_candidates[latest_name]
        content = get_writing_file(latest_path)
        if content:
            raw["zen"] = content
            if len(zen_candidates) > 1:
                logger.info(f"[zen] {len(zen_candidates)} files found, using latest: {latest_name}")
            else:
                logger.info(f"Downloaded zen: {latest_name} ({len(content)} bytes)")

    # Credo SMS: один файл (последний если несколько)
    if credo_sms:
        latest_name = sorted(credo_sms.keys())[-1]
        content = get_writing_file(credo_sms[latest_name])
        if content:
            raw["credo_sms"] = content
            logger.info(f"Downloaded credo_sms: {latest_name} ({len(content)} bytes)")

    # PayPal: все файлы (дедупликация по transaction ID в парсере)
    if paypal_files:
        pp_list = []
        for name in sorted(paypal_files.keys()):
            content = get_writing_file(paypal_files[name])
            if content:
                pp_list.append(content)
                logger.info(f"Downloaded paypal: {name} ({len(content)} bytes)")
        if pp_list:
            raw["paypal_files"] = pp_list

    # Wolt: все файлы
    if wolt_files:
        wolt_list = []
        for name in sorted(wolt_files.keys()):
            content = get_writing_file(wolt_files[name])
            if content:
                wolt_list.append(content)
                logger.info(f"Downloaded wolt: {name} ({len(content)} bytes)")
        if wolt_list:
            raw["wolt_files"] = wolt_list

    return raw


def _serialize_csv(rows: list) -> str:
    """Сериализовать список строк в CSV-строку."""
    rows_sorted = sorted(rows, key=lambda x: x["date"])
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(rows_sorted)
    return output.getvalue()


def process_period(period: str) -> str:
    """
    Обработать период (YYYY-MM или YYYY).
    Возвращает текстовый отчёт для Telegram.
    """
    # Определяем год и тип
    if len(period) == 4:
        year = period
        is_year = True
    elif len(period) == 7:
        year = period[:4]
        is_year = False
    else:
        return f"Неверный формат: {period}. Используй YYYY-MM или YYYY."

    # Курсы валют
    rates = fetch_floatrates()
    if not rates:
        rates = FALLBACK_RATES
        logger.warning("Using fallback exchange rates")
    set_rates(rates)

    # Категории (из бандла)
    categories = _load_local_categories()

    # Скачиваем raw файлы
    raw_files = _download_raw_files(year)
    if not raw_files:
        return f"Нет raw файлов в finance/raw/{year}/."

    sources_found = ", ".join(raw_files.keys())
    logger.info(f"Processing {period}: sources={sources_found}")

    # Парсим — порядок важен для дедупликации
    all_rows = []
    stats = []
    has_credo_sms = False

    # Порядок: Credo SMS → Wolt (заменяет Wolt-строки из Credo) → Zen → PayPal
    if "credo_sms" in raw_files:
        credo_rows = parse_credo_sms(
            io.StringIO(raw_files["credo_sms"]), categories, period,
        )
        has_credo_sms = len(credo_rows) > 0
        stats.append(f"Credo SMS: {len(credo_rows)}")
        all_rows.extend(credo_rows)

    if "wolt_files" in raw_files:
        wolt_all = []
        for wolt_content in raw_files["wolt_files"]:
            wolt_rows = parse_wolt(io.StringIO(wolt_content), categories, period)
            wolt_all.extend(wolt_rows)
        if wolt_all:
            # Удаляем Wolt-строки из Credo SMS — Wolt CSV точнее
            before = len(all_rows)
            all_rows = [
                r for r in all_rows
                if not (r["source"] == "credo_sms" and "wolt" in r["description"].lower())
            ]
            wolt_deduped = before - len(all_rows)
            stats.append(f"Wolt: {len(wolt_all)} (убрано {wolt_deduped} из Credo SMS)")
            all_rows.extend(wolt_all)

    if "zen" in raw_files:
        zen_rows = parse_zen(
            io.StringIO(raw_files["zen"]), categories, period,
        )
        if has_credo_sms:
            zen_rows = [r for r in zen_rows if r["currency"] != "GEL" or r["type"] == "transfer"]
        stats.append(f"Zen Money: {len(zen_rows)}")
        all_rows.extend(zen_rows)

    if "paypal_files" in raw_files:
        seen_tx_ids = set()
        pp_all = []
        for pp_content in raw_files["paypal_files"]:
            pp_rows = parse_paypal(io.StringIO(pp_content), categories, period)
            for r in pp_rows:
                tx_id = r.pop("_tx_id", "")
                if tx_id and tx_id in seen_tx_ids:
                    continue
                if tx_id:
                    seen_tx_ids.add(tx_id)
                pp_all.append(r)
        stats.append(f"PayPal: {len(pp_all)} ({len(raw_files['paypal_files'])} файлов)")
        all_rows.extend(pp_all)

    if not all_rows:
        return f"Нет транзакций за {period}."

    # Статистика
    income_total = sum(r["amount_rub"] for r in all_rows if r["type"] == "income")
    expense_total = sum(r["amount_rub"] for r in all_rows if r["type"] == "expense")
    transfer_count = sum(1 for r in all_rows if r["type"] == "transfer")

    # Сериализуем основной CSV
    csv_content = _serialize_csv(all_rows)
    csv_path = f"finance/processed/{period}.csv"
    save_writing_file(csv_path, csv_content, f"Process {period}: {len(all_rows)} transactions")

    # Генерируем summary
    if is_year:
        summary = generate_yearly_summary(all_rows, year, categories)
    else:
        summary = generate_monthly_summary(all_rows, period, categories)
    summary_path = f"finance/summaries/{period}.md"
    save_writing_file(summary_path, summary, f"Summary {period}")

    # Нераспознанные
    unknown_exp = [r for r in all_rows if r["category"] == "other_expense" and r["type"] == "expense"]
    unknown_inc = [r for r in all_rows if r["category"] == "other_income" and r["type"] == "income"]

    # Формируем отчёт для Telegram
    lines = [f"✓ Обработано {period}:"]
    lines.append("")
    for s in stats:
        lines.append(f"  {s}")
    lines.append(f"  Всего: {len(all_rows)}")
    lines.append("")
    lines.append(f"Доходы: {income_total:,.0f} R")
    lines.append(f"Расходы: {expense_total:,.0f} R")
    lines.append(f"Баланс: {income_total - expense_total:+,.0f} R")
    if transfer_count:
        lines.append(f"Переводы: {transfer_count}")

    if unknown_exp or unknown_inc:
        lines.append("")
        if unknown_exp:
            lines.append(f"Нераспознанные расходы: {len(unknown_exp)}")
        if unknown_inc:
            lines.append(f"Нераспознанные доходы: {len(unknown_inc)}")

    lines.append("")
    lines.append(f"Файлы: {csv_path}, {summary_path}")

    return "\n".join(lines)
