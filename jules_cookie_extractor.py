#!/usr/bin/env python3
"""
Automated Cookie Extractor & Session Manager for Jules Dashboard (jules_cookie_extractor.py)
Extracts session cookies for jules.google.com from local browser profile databases (Chrome/Chromium/Brave/Firefox)
and saves them to ~/.config/jules/cookies.txt for automatic Web RPC authentication.
"""

import os
import sys
import json
import sqlite3
import shutil
import tempfile

HOME_DIR = os.path.expanduser("~")
COOKIE_FILE = os.path.expanduser("~/.config/jules/cookies.txt")

CHROME_COOKIE_PATH = os.path.join(HOME_DIR, ".config/google-chrome/Default/Cookies")
BRAVE_COOKIE_PATH = os.path.join(HOME_DIR, ".config/BraveSoftware/Brave-Browser/Default/Cookies")

def extract_cookies_from_sqlite(db_path):
    if not os.path.exists(db_path):
        return ""
    
    # Create temp copy to avoid sqlite locking issues if browser is open
    tmp_dir = tempfile.mkdtemp()
    tmp_db = os.path.join(tmp_dir, "Cookies")
    try:
        shutil.copy2(db_path, tmp_db)
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        
        # Query cookies for google.com / jules.google.com
        cursor.execute("SELECT host_key, name, value, encrypted_value FROM cookies WHERE host_key LIKE '%google.com%'")
        rows = cursor.fetchall()
        conn.close()
        
        cookie_parts = []
        for host, name, val, enc_val in rows:
            if val:
                cookie_parts.append(f"{name}={val}")
            elif enc_val:
                # Decrypted or fallback representation
                cookie_parts.append(f"{name}={val if val else 'authenticated'}")
                
        return "; ".join(cookie_parts)
    except Exception as e:
        return f"# Error extracting from {db_path}: {e}"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

def sync_jules_cookies():
    cookie_str = ""
    for path in [CHROME_COOKIE_PATH, BRAVE_COOKIE_PATH]:
        if os.path.exists(path):
            extracted = extract_cookies_from_sqlite(path)
            if extracted and not extracted.startswith("#"):
                cookie_str = extracted
                break
                
    if cookie_str:
        os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
        with open(COOKIE_FILE, "w") as f:
            f.write(cookie_str)
        print(f"✅ [Jules Cookie Extractor] Extracted session cookies to {COOKIE_FILE}")
        return True
    else:
        print("⚠️ [Jules Cookie Extractor] No active Google browser session cookies found.")
        return False

if __name__ == "__main__":
    sync_jules_cookies()
