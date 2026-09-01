#!/usr/bin/env python3
"""
Jules Terminal UI (jules_tui.py)
Curses-based interactive TUI for inspecting active Jules sessions, monitoring activities,
responding to session queries, and configuring listener execution mode.
"""

import curses
import os
import sys
import json
import time
from jules_manager import list_sessions, get_session_activities, send_message, archive_session

CONFIG_FILE = os.path.expanduser("~/.config/jules/config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"mode": "continuous", "interval": 60}

def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def draw_menu(stdscr):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_CYAN, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_RED, -1)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)

    selected_idx = 0
    sessions_cache = []
    last_fetch = 0
    status_msg = "Press 'r' to refresh, 'a' to archive, 'm' to cycle mode, 'q' to quit."
    cfg = load_config()

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()

        # Header
        header_str = " 🤖 GOOGLE JULES API MANAGER & LISTENER TUI "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header_str.center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Mode line
        mode_str = f" Mode: [{cfg.get('mode', 'continuous').upper()}] | Refreshing sessions... "
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(1, 2, mode_str)
        stdscr.attroff(curses.color_pair(1))

        # Fetch sessions periodically or on start
        now = time.time()
        if now - last_fetch > 10 or not sessions_cache:
            res = list_sessions()
            sessions_cache = res.get("sessions", []) if isinstance(res, dict) else []
            last_fetch = now

        # Draw session table
        stdscr.addstr(3, 2, "ACTIVE & HISTORICAL SESSIONS:", curses.A_BOLD)
        max_rows = min(height - 8, len(sessions_cache))
        
        if not sessions_cache:
            stdscr.addstr(5, 4, "No active sessions found.", curses.color_pair(3))
        else:
            for i in range(max_rows):
                s = sessions_cache[i]
                sid = s.get("id") or s.get("name", "").split("/")[-1]
                state = s.get("state", "UNKNOWN")
                prompt = s.get("prompt", "").replace("\n", " ")[:width - 45]

                color = curses.color_pair(2) if "COMPLETED" in state or "SUCCEEDED" in state else (curses.color_pair(3) if "FEEDBACK" in state or "INPUT" in state else curses.color_pair(1))
                
                line = f"{' >' if i == selected_idx else '  '} [{sid[:12]}] {state:<22} | {prompt}"
                if i == selected_idx:
                    stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(5 + i, 2, line[:width-4])
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    stdscr.attron(color)
                    stdscr.addstr(5 + i, 2, line[:width-4])
                    stdscr.attroff(color)

        # Footer / Command bar
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height - 2, 2, status_msg[:width-4])
        stdscr.attroff(curses.color_pair(1))

        stdscr.refresh()
        stdscr.timeout(1000)
        key = stdscr.getch()

        if key in (ord('q'), ord('Q')):
            break
        elif key == curses.KEY_UP and selected_idx > 0:
            selected_idx -= 1
        elif key == curses.KEY_DOWN and selected_idx < len(sessions_cache) - 1:
            selected_idx += 1
        elif key in (ord('k'), ord('K')):
            status_msg = prompt_knowledge_update(stdscr)
            last_fetch = 0
        elif key in (ord('r'), ord('R')):
            last_fetch = 0
            status_msg = "Refreshed session list & cookies."
        elif key in (ord('m'), ord('M')):
            modes = ["continuous", "once", "paused"]
            curr = cfg.get("mode", "continuous")
            nxt = modes[(modes.index(curr) + 1) % len(modes)]
            cfg["mode"] = nxt
            save_config(cfg)
            status_msg = f"Listener mode updated to: {nxt.upper()}"
        elif key in (ord('a'), ord('A')) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            archive_session(sid)
            last_fetch = 0
            status_msg = f"Archived session {sid[:12]}"
        elif key in (curses.KEY_ENTER, 10, 13) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            status_msg = prompt_reply(stdscr, sid)
            last_fetch = 0

def prompt_knowledge_update(stdscr):
    height, width = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.addstr(height - 3, 2, " " * (width - 4))
    stdscr.addstr(height - 3, 2, "Add Jules Knowledge Rule (e.g. repo:rule): ", curses.color_pair(3) | curses.A_BOLD)
    stdscr.refresh()
    
    msg_bytes = stdscr.getstr(height - 3, 44, width - 48)
    curses.noecho()
    curses.curs_set(0)
    
    text = msg_bytes.decode('utf-8').strip()
    if text and ":" in text:
        repo, rule = text.split(":", 1)
        import subprocess
        res = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "jules_scraper.py"), "update", "--repo", repo.strip(), "--setting", "knowledge", "--value", json.dumps({"rule": rule.strip()})], capture_output=True, text=True)
        return "Updated Knowledge rule."
    return "Cancelled Knowledge input (format repo:rule required)."

def prompt_reply(stdscr, session_id):
    height, width = stdscr.getmaxyx()
    curses.echo()
    curses.curs_set(1)
    stdscr.addstr(height - 3, 2, " " * (width - 4))
    stdscr.addstr(height - 3, 2, f"Enter response for [{session_id[:12]}]: ", curses.color_pair(3) | curses.A_BOLD)
    stdscr.refresh()
    
    msg_bytes = stdscr.getstr(height - 3, 36, width - 40)
    curses.noecho()
    curses.curs_set(0)
    
    text = msg_bytes.decode('utf-8').strip()
    if text:
        res = send_message(session_id, text)
        if "error" not in res:
            return f"Sent response to session {session_id[:12]}"
        return f"Error sending message: {res.get('error')}"
    return "Cancelled prompt input."

def main():
    try:
        curses.wrapper(draw_menu)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
