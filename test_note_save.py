#!/usr/bin/env python3
"""
Test script for note saving functionality
"""

import os
import sys
from datetime import datetime
from config import TZ, logger, GITHUB_TOKEN, WRITING_REPO
from storage import save_writing_file
from tasks import create_rawnote

print("=" * 60)
print("Testing Note Save Functionality")
print("=" * 60)

# Check environment
print(f"\n1. Checking environment:")
print(f"   GITHUB_TOKEN: {'✓ SET' if GITHUB_TOKEN else '✗ NOT SET'}")
print(f"   WRITING_REPO: {WRITING_REPO}")
print(f"   TZ: {TZ}")

# Test 1: Direct save_writing_file call
print(f"\n2. Testing save_writing_file directly:")
test_content = """# Test Note from Script

This is a test note created by test_note_save.py

- Point 1
- Point 2
- Point 3
"""

test_path = f"writing/rawnotes/test-{datetime.now(TZ).strftime('%H-%M-%S')}.md"
print(f"   Attempting to save: {test_path}")

result = save_writing_file(
    test_path,
    test_content,
    "Test: Note from test_note_save.py"
)
print(f"   Result: {'✓ SUCCESS' if result else '✗ FAILED'}")

# Test 2: create_rawnote function
print(f"\n3. Testing create_rawnote function:")
test_title = f"Test Note {datetime.now(TZ).strftime('%H:%M:%S')}"
test_body = """This is a test body
Created by test_note_save.py
To verify the note saving functionality"""

result2 = create_rawnote(test_title, test_body)
print(f"   Title: {test_title}")
print(f"   Body length: {len(test_body)}")
print(f"   Result: {'✓ SUCCESS' if result2 else '✗ FAILED'}")

print("\n" + "=" * 60)
print("Test Summary:")
print(f"  save_writing_file: {'✓' if result else '✗'}")
print(f"  create_rawnote: {'✓' if result2 else '✗'}")
print("=" * 60)

if result and result2:
    print("\n✓ All tests passed! Note saving should work.")
    sys.exit(0)
else:
    print("\n✗ Some tests failed. Check logs for details.")
    sys.exit(1)
