# ⚡ Quick Fix: Notes Not Saving

## The Issue
Кнопка "Note" не сохраняет заметки.

## The Cause
`GITHUB_TOKEN` отсутствует в `.env` файле.

## The Fix (2 минуты)

### Step 1: Create GitHub Token
1. Перейти на https://github.com/settings/tokens
2. Нажать "Generate new token (classic)"
3. Дать имя (например "geek-bot")
4. Выбрать `repo` scope
5. Нажать "Generate token"
6. **Скопировать токен** (выглядит как `ghp_xxxx...`)

### Step 2: Add to .env
```bash
# Отредактировать файл ~/.../geek-bot/.env
# Добавить строку:
GITHUB_TOKEN=ghp_xxxx...
```

### Step 3: Restart Bot
Перезагрузить бота.

### Step 4: Test
```bash
python3 test_note_save.py
```

Должно вывести:
```
✓ All tests passed! Note saving should work.
```

## Done! ✅

Теперь кнопка "Note" будет сохранять заметки в Writing-space репозитории.
