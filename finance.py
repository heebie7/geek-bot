import io
import re
import csv as csv_module
import asyncio
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes
from config import TZ, logger, WRITING_REPO
from storage import get_writing_file, save_writing_file


def detect_csv_type(filename: str, content: str = "") -> str | None:
    """Определить тип CSV по имени файла или по содержимому."""
    name = filename.lower()
    # По имени файла
    if name.startswith("zen") or "zenmoney" in name:
        return "zenmoney"
    if name.startswith("pp") or name.startswith("paypal") or name.startswith("download"):
        return "paypal"
    # По содержимому (заголовки CSV)
    if content:
        first_line = content.strip().split('\n')[0].lower()
        if "категория" in first_line or "category_name" in first_line:
            return "zenmoney"
        if "brutto" in first_line or "gross" in first_line or "paypal" in first_line:
            return "paypal"
    return None


def extract_year_from_csv(content: str) -> str:
    """Извлечь год из первой даты в CSV."""
    for line in content.strip().split('\n')[1:]:
        if not line.strip():
            continue
        m = re.search(r'(\d{4})-\d{2}-\d{2}', line)
        if m:
            return m.group(1)
        m = re.search(r'\d{2}\.\d{2}\.(\d{4})', line)
        if m:
            return m.group(1)
        m = re.search(r'\d{1,2}/\d{1,2}/(\d{4})', line)
        if m:
            return m.group(1)
        break
    return str(datetime.now(TZ).year)


async def handle_csv_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка загрузки CSV файлов."""
    logger.info("[DIAG] handle_csv_upload ENTERED")
    try:
        if not update.message:
            logger.debug("CSV handler: no message in update")
            return

        document = update.message.document
        if not document:
            logger.debug("CSV handler: no document in message")
            return

        filename = document.file_name or ""
        if not filename.lower().endswith('.csv'):
            logger.debug(f"CSV handler: file '{filename}' is not .csv, skipping")
            return

        logger.info(f"CSV upload received: {filename}")

        try:
            file = await document.get_file()
            file_bytes = await file.download_as_bytearray()
            content = bytes(file_bytes).decode('utf-8')
        except Exception as e:
            logger.error(f"CSV download error: {e}")
            await update.message.reply_text("Ошибка при скачивании файла.")
            return

        csv_type = detect_csv_type(filename, content)
        if not csv_type:
            await update.message.reply_text(
                "Не могу определить тип CSV.\n"
                "Имя файла должно начинаться с zen* или pp*/paypal*/Download*,\n"
                "или файл должен содержать заголовки Zen Money / PayPal."
            )
            return

        year = extract_year_from_csv(content)
        github_path = f"finance/raw/{year}/{filename}"

        result = await asyncio.to_thread(
            save_writing_file, github_path, content, f"Upload {csv_type} CSV: {filename}"
        )
        if result:
            await update.message.reply_text(f"✓ Сохранил {filename} → finance/raw/{year}/ ({csv_type})")
        else:
            await update.message.reply_text("Ошибка сохранения. Проверь GitHub токен.")

    except Exception as e:
        logger.error(f"CSV handler unexpected error: {e}", exc_info=True)
        try:
            await update.message.reply_text("Ошибка обработки CSV файла.")
        except Exception:
            pass


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /income [YYYY-MM] — доходы за работу."""
    now = datetime.now(TZ)

    if context.args:
        arg = context.args[0]
        try:
            year, month = int(arg[:4]), int(arg[5:7])
            start = datetime(year, month, 1)
            if month == 12:
                end = datetime(year + 1, 1, 1)
            else:
                end = datetime(year, month + 1, 1)
        except (ValueError, IndexError):
            await update.message.reply_text("Формат: /income YYYY-MM\nПример: /income 2026-01")
            return
    else:
        weekday = now.weekday()
        start = (now - timedelta(days=weekday)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        end = start + timedelta(days=7)

    # Какие месяцы нужны
    months_needed = set()
    months_needed.add((start.year, start.month))
    end_prev = end - timedelta(days=1)
    months_needed.add((end_prev.year, end_prev.month))

    # Загружаем платежи
    all_rows = []
    for y, m in months_needed:
        content = get_writing_file(f"finance/processed/{y:04d}-{m:02d}.csv")
        if content:
            reader = csv_module.DictReader(io.StringIO(content))
            all_rows.extend(list(reader))

    if not all_rows:
        await update.message.reply_text("Нет данных за этот период.")
        return

    # Фильтруем: work_income в нужном периоде
    lines = []
    total_rub = 0
    count = 0
    for row in all_rows:
        if row.get("category") != "work_income":
            continue
        try:
            row_date = datetime.strptime(row["date"], "%Y-%m-%d")
        except (ValueError, KeyError):
            continue
        if not (start <= row_date < end):
            continue

        amount_rub = float(row.get("amount_rub", 0))
        total_rub += amount_rub
        count += 1
        date_str = row_date.strftime("%d %b")
        desc = row.get("description", "?")
        lines.append(f"{desc} ({date_str}) — {amount_rub:,.0f} R")

    if not lines:
        period_str = f"{start.strftime('%d.%m')}–{(end - timedelta(days=1)).strftime('%d.%m')}"
        await update.message.reply_text(f"Нет доходов за работу за {period_str}.")
        return

    period_str = f"{start.strftime('%d %b')}–{(end - timedelta(days=1)).strftime('%d %b')}"
    header = f"Доход за работу ({period_str}):"
    footer = f"\nИтого: {total_rub:,.0f} R ({count} сессий)"

    report = header + "\n" + "\n".join(lines) + footer
    await update.message.reply_text(report)
