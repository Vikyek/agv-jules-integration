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
import subprocess
from jules_manager import list_sessions, get_session_activities, send_message, archive_session, _make_request

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
    try:
        curses.curs_set(0)
    except Exception:
        pass
    try:
        curses.use_default_colors()
    except Exception:
        pass
    try:
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_RED, -1)
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
    except Exception:
        try:
            curses.init_pair(1, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)
        except Exception:
            pass

    selected_idx = 0
    sessions_cache = []
    last_fetch = 0
    action_msg = ""
    cfg = load_config()

    # Startup check: If system autostart is disabled, open with listener turned off
    enabled_check = subprocess.run(["systemctl", "--user", "is-enabled", "jules-listener.service"], capture_output=True, text=True)
    is_autostart = "enabled" in enabled_check.stdout.strip()
    if not is_autostart:
        subprocess.run(["systemctl", "--user", "stop", "jules-listener.service"], capture_output=True, text=True)

    while True:
        stdscr.clear()
        height, width = stdscr.getmaxyx()

        # Check service status live
        svc_check = subprocess.run(["systemctl", "--user", "is-active", "jules-listener.service"], capture_output=True, text=True)
        svc_active = svc_check.stdout.strip() == "active"
        svc_str = "RUNNING" if svc_active else "STOPPED"

        auto_check = subprocess.run(["systemctl", "--user", "is-enabled", "jules-listener.service"], capture_output=True, text=True)
        auto_enabled = "enabled" in auto_check.stdout.strip()
        auto_str = "ENABLED" if auto_enabled else "DISABLED"

        # Header
        header_str = " 🤖 GOOGLE JULES API MANAGER & LISTENER TUI "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header_str.center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Mode line
        mode_str = f" Mode: [{cfg.get('mode', 'continuous').upper()}] | Service: [{svc_str}] | Autostart: [{auto_str}] | Refreshing sessions... "
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(1, 2, mode_str[:width-4])
        stdscr.attroff(curses.color_pair(1))

        # Fetch sessions periodically or on start
        now = time.time()
        if now - last_fetch > 10 or not sessions_cache:
            res = list_sessions()
            sessions_cache = res.get("sessions", []) if isinstance(res, dict) else []
            last_fetch = now

        # Draw session table
        stdscr.addstr(3, 2, "ACTIVE & HISTORICAL SESSIONS:", curses.A_BOLD)
        max_rows = min(height - 9, len(sessions_cache))
        
        if not sessions_cache:
            stdscr.addstr(5, 4, "No active sessions found.", curses.color_pair(3))
        else:
            for i in range(max_rows):
                s = sessions_cache[i]
                sid = s.get("id") or s.get("name", "").split("/")[-1]
                state = s.get("state", "UNKNOWN")
                title = s.get("title") or s.get("prompt", "").replace("\n", " ")
                display_title = title[:width - 45]

                color = curses.color_pair(2) if "COMPLETED" in state or "SUCCEEDED" in state else (curses.color_pair(3) if "FEEDBACK" in state or "INPUT" in state else curses.color_pair(1))
                
                line = f"{' >' if i == selected_idx else '  '} [{sid[:12]}] {state:<22} | {display_title}"
                if i == selected_idx:
                    stdscr.attron(curses.A_REVERSE)
                    stdscr.addstr(5 + i, 2, line[:width-4])
                    stdscr.attroff(curses.A_REVERSE)
                else:
                    stdscr.attron(color)
                    stdscr.addstr(5 + i, 2, line[:width-4])
                    stdscr.attroff(color)

        # Action notification message line (rendered above keybinding tips)
        if action_msg:
            stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
            stdscr.addstr(height - 3, 2, action_msg[:width-4])
            stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        # Persistent Footer / Keybindings bar
        key_tips = f"Keybindings: [s] {'stop' if svc_active else 'start'} service | [b] {'disable' if auto_enabled else 'enable'} autostart | [m] mode | [r] refresh | [a] archive | [q] quit"
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height - 2, 2, key_tips[:width-4])
        stdscr.attroff(curses.color_pair(1))

        stdscr.refresh()
        stdscr.timeout(1000)
        key = stdscr.getch()

        if key in (ord('q'), ord('Q')):
            # On TUI exit: If system autostart is disabled, automatically close background service
            if not auto_enabled:
                subprocess.run(["systemctl", "--user", "stop", "jules-listener.service"], capture_output=True, text=True)
            break
        elif key == curses.KEY_UP and selected_idx > 0:
            selected_idx -= 1
        elif key == curses.KEY_DOWN and selected_idx < len(sessions_cache) - 1:
            selected_idx += 1
        elif key in (ord('s'), ord('S')):
            action_msg = toggle_systemd_service()
            last_fetch = 0
        elif key in (ord('b'), ord('B')):
            action_msg = toggle_systemd_autostart()
            last_fetch = 0
        elif key in (ord('r'), ord('R')):
            last_fetch = 0
            action_msg = "Refreshed session list & cookies."
        elif key in (ord('m'), ord('M')):
            modes = ["continuous", "once", "paused"]
            curr = cfg.get("mode", "continuous")
            nxt = modes[(modes.index(curr) + 1) % len(modes)]
            cfg["mode"] = nxt
            save_config(cfg)
            action_msg = f"Listener mode updated to: {nxt.upper()}"
        elif key in (ord('a'), ord('A')) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            archive_session(sid)
            last_fetch = 0
            action_msg = f"Archived session {sid[:12]}"
        elif key in (curses.KEY_ENTER, 10, 13) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            action_msg = prompt_reply(stdscr, sid)
            last_fetch = 0
def toggle_systemd_service():
    check = subprocess.run(["systemctl", "--user", "is-active", "jules-listener.service"], capture_output=True, text=True)
    is_active = check.stdout.strip() == "active"
    if is_active:
        res = subprocess.run(["systemctl", "--user", "stop", "jules-listener.service"], capture_output=True, text=True)
        return "🛑 Stopped background Jules listener service."
    else:
        res = subprocess.run(["systemctl", "--user", "start", "jules-listener.service"], capture_output=True, text=True)
        return "🚀 Started background Jules listener service."

def toggle_systemd_autostart():
    check = subprocess.run(["systemctl", "--user", "is-enabled", "jules-listener.service"], capture_output=True, text=True)
    is_enabled = "enabled" in check.stdout.strip()
    if is_enabled:
        res = subprocess.run(["systemctl", "--user", "disable", "jules-listener.service"], capture_output=True, text=True)
        return "🔒 Disabled system autostart for Jules listener."
    else:
        res = subprocess.run(["systemctl", "--user", "enable", "jules-listener.service"], capture_output=True, text=True)
        return "⚡ Enabled system autostart for Jules listener."

# TODO: Future Reimplementation - Prompt user for Knowledge rule and update via jules_scraper
# def prompt_knowledge_update(stdscr):
#     height, width = stdscr.getmaxyx()
#     curses.echo()
#     try:
#         curses.curs_set(1)
#     except Exception:
#         pass
#     stdscr.addstr(height - 3, 2, " " * (width - 4))
#     stdscr.addstr(height - 3, 2, "Add Jules Knowledge Rule (e.g. repo:rule): ", curses.color_pair(3) | curses.A_BOLD)
#     stdscr.refresh()
#     
#     msg_bytes = stdscr.getstr(height - 3, 44, width - 48)
#     curses.noecho()
#     try:
#         curses.curs_set(0)
#     except Exception:
#         pass
#     
#     text = msg_bytes.decode('utf-8').strip()
#     if text and ":" in text:
#         repo, rule = text.split(":", 1)
#         import subprocess
#         res = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "jules_scraper.py"), "update", "--repo", repo.strip(), "--setting", "knowledge", "--value", json.dumps({"rule": rule.strip()})], capture_output=True, text=True)
#         return "Updated Knowledge rule."
#     return "Cancelled Knowledge input (format repo:rule required)."

import textwrap

def prompt_reply(stdscr, session_id):
    # Full screen modal loop (blocking, no timeout, handles resize)
    stdscr.timeout(-1)
    input_text = ""
    status_err = ""

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        activities = get_session_activities(session_id)
        sess = _make_request(f"sessions/{session_id}") if '_make_request' in globals() else {}
        
        # Build query lines with dynamic word wrapping based on current width
        max_line_width = max(20, width - 8)
        q_lines = []
        
        title_str = sess.get("title") or f"Session {session_id[:12]}"
        if title_str:
            q_lines.extend(textwrap.wrap(f"Title: {title_str}", max_line_width))
            
        prompt_raw = sess.get("prompt", "")
        if prompt_raw:
            q_lines.append("--- Original Task Prompt ---")
            for pl in prompt_raw.splitlines()[:6]:
                q_lines.extend(textwrap.wrap(pl, max_line_width) or [""])

        if isinstance(activities, dict) and "activities" in activities:
            # Extract last ending agent progress message / question / evaluation
            last_msg = ""
            for act in reversed(activities["activities"]):
                if "progressUpdated" in act and isinstance(act["progressUpdated"], dict):
                    desc = act["progressUpdated"].get("description", "")
                    title = act["progressUpdated"].get("title", "")
                    if desc or title:
                        last_msg = f"[{title}]\n{desc}" if title else desc
                        break
                elif "agentMessage" in act and isinstance(act["agentMessage"], dict):
                    last_msg = act["agentMessage"].get("text", "")
                    break
                elif "userMessage" in act and isinstance(act["userMessage"], dict):
                    last_msg = act["userMessage"].get("text", "")
                    break
                elif "planGenerated" in act and "plan" in act["planGenerated"]:
                    steps = act["planGenerated"]["plan"].get("steps", [])
                    step_strs = [f"Step {idx+1}: {s.get('title', '')}" for idx, s in enumerate(steps)]
                    last_msg = "Plan Steps:\n" + "\n".join(step_strs)
                    break

            if last_msg:
                q_lines.append("--- Latest Jules Agent Question / Evaluation ---")
                for l in last_msg.splitlines():
                    q_lines.extend(textwrap.wrap(l, max_line_width) or [""])

        # Modal Header
        header = f" ❓ JULES QUERY RESOLUTION PANEL [{session_id[:12]}] "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header[:width].center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Draw content panel
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(2, 2, "QUERY DETAILS & LATEST JULES QUESTION:", curses.A_BOLD)
        stdscr.attroff(curses.color_pair(3))

        max_display = min(height - 8, len(q_lines))
        for i in range(max_display):
            line_str = q_lines[i][:width-4]
            stdscr.addstr(4 + i, 4, line_str)

        # Input Prompt area at bottom
        prompt_y = max(6, height - 4)
        stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
        stdscr.addstr(prompt_y, 2, f"Your Reply (Press ENTER to send, ESC to cancel):"[:width-4])
        stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

        input_display = f"> {input_text}"[:width-4]
        stdscr.attron(curses.A_REVERSE)
        stdscr.addstr(prompt_y + 1, 2, f"{input_display:<{width-4}}")
        stdscr.attroff(curses.A_REVERSE)

        if status_err:
            stdscr.attron(curses.color_pair(4))
            stdscr.addstr(height - 1, 2, status_err[:width-4])
            stdscr.attroff(curses.color_pair(4))

        stdscr.refresh()
        ch = stdscr.getch()

        if ch == curses.KEY_RESIZE:
            curses.update_lines_cols()
            continue
        elif ch == 27:  # ESC key
            stdscr.timeout(1000)
            return "Cancelled query resolution modal."
        elif ch in (curses.KEY_ENTER, 10, 13):
            if input_text.strip():
                res = send_message(session_id, input_text.strip())
                stdscr.timeout(1000)
                if "error" not in res:
                    return f"Sent response to session {session_id[:12]}"
                return f"Error sending message: {res.get('error')}"
            else:
                status_err = "Please enter a non-empty response or press ESC to cancel."
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            input_text = input_text[:-1]
        elif 32 <= ch <= 126:
            input_text += chr(ch)

def main():
    try:
        curses.wrapper(draw_menu)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
