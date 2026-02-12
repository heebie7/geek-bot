# Monitoring Guide for Note Saving

## 🔍 How to Monitor Note Saving with New Logging

### View Bot Logs

#### Option 1: Live Log Output (Best for Testing)
```bash
# Watch logs as they happen
tail -f bot.log | grep -i "note\|writing"
```

#### Option 2: Search for Specific Operations
```bash
# Find all note-related logs
grep -i "note" bot.log

# Find GitHub write operations
grep -i "save_writing_file\|writing repo" bot.log

# Find errors
grep -i "error\|failed\|warning" bot.log
```

#### Option 3: Timestamps and Sequences
```bash
# See chronological order with timestamps
grep "Note\|write_file" bot.log | tail -20
```

## 📊 Expected Log Sequences

### Successful Note Save Flow
```
[INFO] Note: Creating note from 3 messages
[INFO] save_writing_file: filepath=writing/rawnotes/2026-02-13-my-note.md, msg='Add note: My Note'
[INFO] save_writing_file: Got repo heebie7/Writing-space
[INFO] save_writing_file: File not found (GithubException), will create
[INFO] save_writing_file: Successfully created new file writing/rawnotes/2026-02-13-my-note.md
[INFO] Saved writing/rawnotes/2026-02-13-my-note.md to Writing repo successfully
[INFO] Note: title='My Note', body_len=254
[INFO] Note: save result=True
```

### Failed Note Save (Missing Token)
```
[INFO] Note: Creating note from 3 messages
[INFO] save_writing_file: filepath=writing/rawnotes/2026-02-13-my-note.md, msg='Add note: My Note'
[WARNING] No GitHub token, cannot save to Writing repo
[INFO] Note: title='My Note', body_len=254
[INFO] Note: save result=False
```

### Failed Note Save (Auth Error)
```
[INFO] Note: Creating note from 3 messages
[INFO] save_writing_file: filepath=writing/rawnotes/2026-02-13-my-note.md, msg='Add note: My Note'
[INFO] save_writing_file: Got repo heebie7/Writing-space
[ERROR] Writing repo write error: Bad credentials
[ERROR] Traceback: ...GithubException: 401 Unauthorized...
```

## 🎯 What to Look For

### Normal Operations
- ✓ "Note: Creating note from X messages"
- ✓ "Got repo heebie7/Writing-space"
- ✓ "Successfully created new file" OR "Successfully updated"
- ✓ "Note: save result=True"

### Configuration Issues
- ✗ "No GitHub token, cannot save to Writing repo"
- ✓ Add GITHUB_TOKEN to .env

### Authentication Issues
- ✗ "Bad credentials" or "401 Unauthorized"
- ✓ Token is invalid, generate new one

### Permission Issues
- ✗ "403 Forbidden"
- ✓ Token doesn't have repo permissions

### Network Issues
- ✗ Connection timeout, SSL errors
- ✓ Check GitHub status page

## 🧪 Testing Workflow

### 1. Test Before Restarting Bot
```bash
python3 test_note_save.py
```

### 2. Monitor Logs During Testing
```bash
# In another terminal
tail -f bot.log | grep -i note
```

### 3. Simulate User Flow
1. Start bot
2. Send `/start`
3. Press "➕ Add"
4. Select "📝 Note"
5. Type/forward several messages
6. Press "✅ Готово"
7. Check logs for the full sequence

### 4. Verify in Repository
```bash
# Check if file was created in Writing-space repo
# Look in: writing/rawnotes/ folder for YYYY-MM-DD-*.md files
```

## 📈 Performance Monitoring

### Expected Timings
- Message collection: instant (< 100ms per message)
- LLM processing: 1-3 seconds
- GitHub write: 1-2 seconds
- Total: 3-6 seconds

### Slow Performance Check
- If LLM is slow: Check API quota/rate limits
- If GitHub is slow: Check network connection
- If total > 10s: Something is wrong, check logs

## 🚨 Troubleshooting Using Logs

### Problem: "Ошибка сохранения" (Save error)
**Look for**: "Note: save result=False"
**Check**:
1. Is GITHUB_TOKEN set? `grep "No GitHub token" bot.log`
2. Is token valid? `grep "Bad credentials" bot.log`
3. Is there a network error? `grep "Connection" bot.log`

### Problem: Buffer shows empty
**Look for**: "Note: Creating note from 0 messages"
**Check**:
1. Are messages being added? `grep "added message to buffer" bot.log`
2. Do messages have text? `grep "received message with no text" bot.log`

### Problem: Title parsing failed
**Look for**: Title is empty or malformed
**Check**:
1. LLM response format: Is first line empty?
2. Title cleaning: Check `lstrip("# ")` logic
3. LLM prompt: Ensure it produces title on first line

## 🔧 Debug Commands

### Enable Debug Logging (if needed)
```python
# In config.py, change to:
logging.basicConfig(level=logging.DEBUG)
# This shows ALL debug messages, very verbose
```

### Extract Note Logs
```bash
# Get all note operations for one user session
grep -A5 -B5 "Note mode" bot.log > note_session.log
```

### Check GitHub Repo Access
```bash
# Verify token works
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user
```

## 📋 Checklist

- [ ] GITHUB_TOKEN is set in .env
- [ ] test_note_save.py passes
- [ ] Logs show successful create/update
- [ ] Files appear in Writing-space repo
- [ ] Messages are collected correctly
- [ ] Title is parsed correctly
- [ ] No auth errors in logs
- [ ] No network errors in logs

## ℹ️ Additional Resources

- [GitHub Token Scopes](https://docs.github.com/en/developers/apps/building-github-apps/scopes-for-github-apps)
- [PyGithub Documentation](https://pygithub.readthedocs.io/)
- [Writing-space Repository](https://github.com/heebie7/Writing-space)
