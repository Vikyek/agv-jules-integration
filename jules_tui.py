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

def check_session_stuck_or_plan_loop(session_id, preloaded_activities=None):
    """
    Inspects session activity stream to detect if worker is stuck or trapped in a plan/patch loop
    (e.g., multiple planGenerated, repeated failing patches, or un-stuck request needed).
    Returns (is_stuck, reason_msg).
    """
    if not session_id:
        return False, ""
    try:
        if preloaded_activities and isinstance(preloaded_activities, dict):
            activities_list = preloaded_activities.get("activities", [])
        else:
            res = get_session_activities(session_id)
            activities_list = res.get("activities", []) if isinstance(res, dict) else []

        plan_count = 0
        patch_fail_count = 0
        duplicate_patch_signatures = set()

        for act in activities_list:
            if "planGenerated" in act:
                plan_count += 1
            if "artifacts" in act:
                for art in act.get("artifacts", []):
                    patch_str = str(art.get("changeSet", {}).get("gitPatch", {}).get("unidiffPatch", ""))
                    if patch_str:
                        if patch_str in duplicate_patch_signatures:
                            patch_fail_count += 1
                        duplicate_patch_signatures.add(patch_str)

        if plan_count >= 2:
            return True, f"PLAN_LOOP 🔄 ({plan_count} plans)"
        if patch_fail_count >= 2:
            return True, "STUCK_PATCH ⚠️"
    except Exception:
        pass
    return False, ""

def check_session_pr_status(session):
    """
    Checks PR details for a session, returning status flags:
    has_pr, pr_number, status_checks_failing, has_review_issues, mergeable, needs_update, url
    """
    default_res = {
        "has_pr": False, "pr_number": None, "status_checks_failing": False,
        "has_review_issues": False, "mergeable": "UNKNOWN", "needs_update": False, "url": ""
    }
    if not session:
        return default_res
    sid = session.get("id") or session.get("name", "").split("/")[-1]
    src_ctx = session.get("sourceContext", {})
    rep_name = src_ctx.get("source", "").replace("sources/github/", "").replace("sources/", "")
    if not rep_name:
        rep_name = "paru-wrapper"
    projects_dir = os.path.expanduser("~/Projects")
    repo_path = os.path.join(projects_dir, os.path.basename(rep_name))
    if not os.path.exists(os.path.join(repo_path, ".git")):
        return default_res
    try:
        res = subprocess.run(["gh", "pr", "list", "--state", "all", "--json", "number,title,headRefName,url,mergeable,reviewDecision,statusCheckRollup,comments,reviews"], cwd=repo_path, capture_output=True, text=True)
        if res.returncode == 0:
            prs = json.loads(res.stdout)
            for pr in prs:
                branch = pr.get("headRefName", "")
                title = pr.get("title", "")
                if sid in branch or sid in title or branch.endswith(sid):
                    num = pr.get("number")
                    url = pr.get("url", "")
                    mergeable = pr.get("mergeable", "UNKNOWN")
                    review_decision = pr.get("reviewDecision", "")
                    
                    # 1. Check CI status checks
                    checks_failing = False
                    status_checks = pr.get("statusCheckRollup", [])
                    for check in status_checks:
                        st = check.get("status", "")
                        con = check.get("conclusion", "")
                        if st == "COMPLETED" and con in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"):
                            checks_failing = True
                            break

                    # 2. Check comments and review issues
                    has_review_issues = review_decision in ("CHANGES_REQUESTED",)
                    for comment in pr.get("comments", []):
                        body = comment.get("body", "")
                        if "issue" in body.lower() or "blocking" in body.lower() or "sourcery" in body.lower():
                            has_review_issues = True
                            break
                    for review in pr.get("reviews", []):
                        state = review.get("state", "")
                        if state == "CHANGES_REQUESTED":
                            has_review_issues = True
                            break

                    # 3. Check if branch needs update / rebase
                    needs_update = mergeable in ("CONFLICTING", "BEHIND")

                    return {
                        "has_pr": True,
                        "pr_number": num,
                        "status_checks_failing": checks_failing,
                        "has_review_issues": has_review_issues,
                        "mergeable": mergeable,
                        "needs_update": needs_update,
                        "url": url
                    }
    except Exception:
        pass
    return default_res

def get_unassigned_jules_prs(active_sessions):
    """
    Scans local repositories in ~/Projects for open Jules PRs or feature branches
    that do NOT have an active API session assigned to them, creating synthetic session objects.
    """
    active_sids = set()
    for s in active_sessions:
        sid = s.get("id") or s.get("name", "").split("/")[-1]
        active_sids.add(sid)

    unassigned_items = []
    projects_dir = os.path.expanduser("~/Projects")
    if not os.path.exists(projects_dir):
        return unassigned_items

    for repo_name in os.listdir(projects_dir):
        repo_path = os.path.join(projects_dir, repo_name)
        if not (os.path.isdir(repo_path) and os.path.exists(os.path.join(repo_path, ".git"))):
            continue

        try:
            res = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,url,mergeable,statusCheckRollup,comments,reviews"], cwd=repo_path, capture_output=True, text=True)
            if res.returncode != 0:
                continue
            prs = json.loads(res.stdout)
            for pr in prs:
                title = pr.get("title", "")
                branch = pr.get("headRefName", "")
                num = pr.get("number")
                url = pr.get("url", "")
                
                is_jules = (
                    "jules" in branch.lower() 
                    or "jules" in title.lower() 
                    or title.startswith(("🛡️", "⚡", "🔌", "🌈", "📜", "📦", "🎨", "🧪"))
                    or (any(char.isdigit() for char in branch.split("-")[-1]) and len(branch.split("-")[-1]) >= 15)
                )
                if not is_jules:
                    continue

                # Check if this PR is tied to an active session
                extracted_sid = branch.split("-")[-1] if "-" in branch else ""
                if extracted_sid in active_sids:
                    continue

                # Virtual session representation for leftover PR
                synthetic_sid = extracted_sid if (extracted_sid and len(extracted_sid) >= 15) else f"pr-{repo_name}-{num}"
                unassigned_items.append({
                    "id": synthetic_sid,
                    "name": f"sessions/{synthetic_sid}",
                    "title": f"[{repo_name}] PR #{num}: {title}",
                    "state": "UNASSIGNED_PR",
                    "is_unassigned_pr": True,
                    "pr_number": num,
                    "repo": repo_name,
                    "branch": branch,
                    "url": url,
                    "prompt": f"Unassigned Jules PR #{num} in {repo_name} ({branch}): {title}\nURL: {url}",
                    "sourceContext": {
                        "source": f"sources/github/Vikyek/{repo_name}",
                        "githubRepoContext": {"startingBranch": branch}
                    }
                })
        except Exception:
            pass

    return unassigned_items

def open_session_pr(session):
    """Looks up and opens the GitHub PR associated with the task/session if created."""
    if not session:
        return "No session selected."

    sid = session.get("id") or session.get("name", "").split("/")[-1]
    src_ctx = session.get("sourceContext", {})
    rep_name = src_ctx.get("source", "").replace("sources/github/", "").replace("sources/", "")
    
    if not rep_name:
        rep_name = "paru-wrapper"

    projects_dir = os.path.expanduser("~/Projects")
    repo_path = os.path.join(projects_dir, os.path.basename(rep_name))

    if not os.path.exists(os.path.join(repo_path, ".git")):
        return f"Local repo path not found: {repo_path}"

    try:
        res = subprocess.run(["gh", "pr", "list", "--state", "all", "--json", "number,title,headRefName,url"], cwd=repo_path, capture_output=True, text=True)
        if res.returncode == 0:
            prs = json.loads(res.stdout)
            matching_pr = None
            for pr in prs:
                branch = pr.get("headRefName", "")
                title = pr.get("title", "")
                if sid in branch or sid in title or branch.endswith(sid):
                    matching_pr = pr
                    break

            if matching_pr:
                pr_url = matching_pr.get("url")
                import webbrowser
                webbrowser.open(pr_url)
                return f"🔗 Opened GitHub PR #{matching_pr.get('number')}: {pr_url}"
            else:
                return f"⚠️ No PR created yet for session {sid[:8]}..."
    except Exception as e:
        return f"Error checking PR: {e}"

    return f"⚠️ PR not created yet for session {sid[:8]}..."

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
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    except Exception:
        pass
    try:
        curses.init_pair(1, curses.COLOR_YELLOW, -1)           # Main text (Yellow)
        curses.init_pair(2, curses.COLOR_GREEN, -1)            # Completed state (Green)
        curses.init_pair(3, curses.COLOR_RED, -1)              # Feedback / Warning (Red)
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_YELLOW) # Topbar: Yellow background with Red text
        curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_YELLOW) # Highlighted selection: Yellow background with Black text
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_RED)    # Unresolvable / Critical Alert: Red background with Black text
    except Exception:
        try:
            curses.init_pair(1, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_RED, curses.COLOR_YELLOW)
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_RED)
        except Exception:
            pass

    selected_idx = 0
    sessions_cache = []
    details_cache = {}
    last_fetch = 0
    action_msg = ""
    cfg = load_config()

    # Startup check: If system autostart is disabled, open with listener turned off
    enabled_check = subprocess.run(["systemctl", "--user", "is-enabled", "jules-listener.service"], capture_output=True, text=True)
    is_autostart = "enabled" in enabled_check.stdout.strip()
    if not is_autostart:
        subprocess.run(["systemctl", "--user", "stop", "jules-listener.service"], capture_output=True, text=True)

    listen_frames = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
    braille_swirl = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]

    # Track manual service stop triggers
    user_stopped_service = False
    prev_svc_active = None

    view_archived = False

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

        # Unexpected Service Stop Detection
        unexpected_stop = (not svc_active) and (prev_svc_active is True) and (not user_stopped_service)
        if unexpected_stop:
            action_msg = "🚨 WARNING: Listener service stopped unexpectedly! Check logs or press [s] to restart."

        if svc_active:
            user_stopped_service = False
        prev_svc_active = svc_active

        # Subtle clean Braille spinner animation for active service
        curr_frame_idx = int(time.time() * 6)
        braille_icon = braille_swirl[curr_frame_idx % len(braille_swirl)]
        listen_icon = listen_frames[curr_frame_idx % len(listen_frames)] if svc_active else "OFF"

        # Header (Yellow background normally, Red background with Black text if unexpected stop or service stopped)
        header_color = curses.color_pair(6) | curses.A_BOLD if (not svc_active and not user_stopped_service) else curses.color_pair(4) | curses.A_BOLD
        header_title = " 󱚝 JULES MANAGER " if width < 80 else " 󱚝 GOOGLE JULES API MANAGER & LISTENER TUI "
        stdscr.attron(header_color)
        stdscr.addstr(0, 0, header_title.center(width)[:width])
        stdscr.attroff(header_color)

        # Mode line (Centered) - Service Disabled / Stopped shown in Red
        if width < 80:
            mode_str = f"Svc:[{svc_str} {listen_icon}] Auto:[{auto_str[:3]}]"
        else:
            mode_str = f"Mode: [{cfg.get('mode', 'continuous').upper()}] | Service: [{svc_str} {listen_icon}] | Autostart: [{auto_str}]"
            
        mode_color = curses.color_pair(6) | curses.A_BOLD if not svc_active else curses.color_pair(1) | curses.A_BOLD
        stdscr.attron(mode_color)
        stdscr.addstr(1, 0, mode_str.center(width)[:width])
        stdscr.attroff(mode_color)

        # Fetch and preload all sessions & activities into memory
        now = time.time()
        if now - last_fetch > 10 or not sessions_cache:
            res = list_sessions(include_archived=False)
            raw_sessions = res.get("sessions", []) if isinstance(res, dict) else []
            sessions_cache = [s for s in raw_sessions if s.get("state") not in ("ARCHIVED", "CLOSED")]
            
            # Append unassigned leftover Jules PRs from local repos
            unassigned_prs = get_unassigned_jules_prs(sessions_cache)
            sessions_cache.extend(unassigned_prs)
            
            # Preload full activities for all cached sessions
            for s in sessions_cache:
                sid = s.get("id") or s.get("name", "").split("/")[-1]
                if sid not in details_cache:
                    if s.get("is_unassigned_pr"):
                        details_cache[sid] = {
                            "session": s,
                            "activities": {"activities": [{"agentMessaged": {"agentMessage": f"Unassigned Jules PR #{s.get('pr_number')} in {s.get('repo')} ({s.get('branch')})"}}]}
                        }
                    else:
                        details_cache[sid] = {
                            "session": s,
                            "activities": get_session_activities(sid)
                        }
            last_fetch = now

        # Multi-line footer keybindings bar wrapping calculation (using non-breaking spaces \\u00A0 between keybind badge and label)
        if width < 80:
            raw_tips = f"[s]\u00A0{'stop' if svc_active else 'start'} | [u]\u00A0unstuck | [b]\u00A0auto | [p]\u00A0open\u00A0PR | [w]\u00A0web | [h]\u00A0history | [v]\u00A0archived | [q]\u00A0quit"
        else:
            raw_tips = f"Keybindings: [s]\u00A0{'stop' if svc_active else 'start'}\u00A0service | [u]\u00A0unstuck\u00A0session | [b]\u00A0autostart | [p]\u00A0open\u00A0PR | [w]\u00A0open\u00A0Jules\u00A0web | [h]\u00A0history\u00A0log | [v]\u00A0archived\u00A0collection | [q]\u00A0quit"

        raw_footer_lines = textwrap.wrap(raw_tips, max(20, width - 4)) or [raw_tips]
        footer_lines = [l.strip().lstrip("|").rstrip("|").strip() for l in raw_footer_lines]
        footer_height = len(footer_lines)

        # Draw active session table
        stdscr.addstr(3, 1, "ACTIVE SESSIONS:", curses.A_BOLD)
        
        # Calculate available vertical space for sessions list
        available_height = height - 5 - footer_height - (1 if action_msg else 0)
        curr_y = 5
        session_row_map = {}

        if not sessions_cache:
            stdscr.addstr(5, 2, "No active sessions found.", curses.color_pair(3))
        else:
            for i in range(len(sessions_cache)):
                if curr_y >= 4 + available_height:
                    break

                start_y = curr_y
                s = sessions_cache[i]
                local_num = i + 1
                state = s.get("state", "UNKNOWN")
                raw_title = s.get("title", "")
                if not raw_title or len(raw_title) > 100 or "\n" in raw_title:
                    title_lines = [l.strip() for l in (raw_title or s.get("prompt", "")).splitlines() if l.strip()]
                    raw_title = title_lines[0] if title_lines else "Untitled Session"
                    if ("Security Vulnerability" in raw_title or "Performance Optimization" in raw_title or "Testing Improvement" in raw_title) and len(title_lines) > 1:
                        for line in title_lines[1:]:
                            if "Issue:" in line or "File:" in line:
                                raw_title = line
                                break

                clean_title = raw_title.lstrip("#").strip().replace("\n", " ")
                title = clean_title

                pr_st = check_session_pr_status(s)
                has_pr = pr_st["has_pr"]
                pr_failed = pr_st["status_checks_failing"]
                pr_issues = pr_st["has_review_issues"]
                pr_conflict = pr_st["needs_update"]

                sid = s.get("id") or s.get("name", "").split("/")[-1]
                pre_acts = details_cache.get(sid, {}).get("activities")
                is_stuck, stuck_reason = check_session_stuck_or_plan_loop(sid, preloaded_activities=pre_acts)

                if is_stuck:
                    color = curses.color_pair(6) | curses.A_BOLD  # Red background for plan loop / stuck
                    display_state = f"{stuck_reason}"
                elif s.get("is_unassigned_pr"):
                    color = curses.color_pair(1) | curses.A_BOLD
                    display_state = "UNASSIGNED_PR 🌿"
                elif pr_failed or pr_issues or pr_conflict:
                    color = curses.color_pair(6) | curses.A_BOLD  # Highlighted Red background with Black text
                    if pr_failed:
                        display_state = "PR_CHECK_FAIL ❌"
                    elif pr_conflict:
                        display_state = "PR_CONFLICT ⚠️"
                    else:
                        display_state = "PR_ISSUES ⚠️"
                elif ("COMPLETED" in state or "SUCCEEDED" in state or "RESOLVED" in state or "ARCHIVED" in state) and has_pr:
                    color = curses.color_pair(2)
                    display_state = "PR_CREATED 🔗"
                elif "COMPLETED" in state or "SUCCEEDED" in state or "RESOLVED" in state or "ARCHIVED" in state:
                    color = curses.color_pair(2)
                    display_state = state
                elif "FEEDBACK" in state or "INPUT" in state or "REVIEW" in state or "PAUSED" in state:
                    color = curses.color_pair(6) | curses.A_BOLD  # Highlighted Red background with Black text
                    display_state = f"{state} ⚠️"
                elif ("IN_PROGRESS" in state or "RUNNING" in state) and has_pr:
                    color = curses.color_pair(1) | curses.A_BOLD
                    display_state = f"PR_CREATED {braille_icon}"
                elif "IN_PROGRESS" in state or "RUNNING" in state:
                    color = curses.color_pair(1) | curses.A_BOLD
                    display_state = f"{state} {braille_icon}"
                else:
                    color = curses.color_pair(1)
                    display_state = state

                prefix = ">" if i == selected_idx else " "

                if width < 80:
                    short_state = display_state.replace("AWAITING_USER_FEEDBACK", "FEEDBACK").replace("IN_PROGRESS", "RUNNING").replace("STUCK_PATCH", "STUCK").replace("UNASSIGNED_PR", "PR_UNASSIGNED")
                    meta_prefix = f"{prefix} [#{local_num}] {short_state[:14]} | "
                elif width < 120:
                    short_state = display_state.replace("AWAITING_USER_FEEDBACK", "FEEDBACK")
                    meta_prefix = f"{prefix} [#{local_num:<2}] {short_state:<20} | "
                else:
                    meta_prefix = f"{prefix} [#{local_num:<2}] {display_state:<28} | "

                meta_len = len(meta_prefix)
                title_width = max(10, width - meta_len - 3)
                wrapped_title = textwrap.wrap(title, title_width) or [title]

                # Draw first line with metadata prefix
                line1 = f"{meta_prefix}{wrapped_title[0]}"[:width-2]
                if i == selected_idx:
                    stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                    stdscr.addstr(curr_y, 1, line1)
                    stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.attron(color)
                    stdscr.addstr(curr_y, 1, line1)
                    stdscr.attroff(color)

                curr_y += 1

                # Draw wrapped title lines indented under the title column
                for extra_line in wrapped_title[1:]:
                    if curr_y >= 4 + available_height:
                        break
                    indented_line = f"{' ' * meta_len}{extra_line}"[:width-2]
                    if i == selected_idx:
                        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                        stdscr.addstr(curr_y, 1, indented_line)
                        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                    else:
                        stdscr.attron(color)
                        stdscr.addstr(curr_y, 1, indented_line)
                        stdscr.attroff(color)
                    curr_y += 1

                session_row_map[i] = (start_y, curr_y - 1)

        # Action notification message line
        if action_msg:
            msg_y = height - 1 - footer_height
            if 0 <= msg_y < height:
                try:
                    stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
                    stdscr.addstr(msg_y, 1, action_msg[:width-2])
                    stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)
                except Exception:
                    pass

        # Persistent Multi-Line Footer / Keybindings bar (Centered safely)
        for idx, f_line in enumerate(footer_lines):
            f_y = height - footer_height + idx
            if 0 <= f_y < height:
                try:
                    stdscr.attron(curses.color_pair(1))
                    stdscr.addstr(f_y, 0, f_line.center(width)[:width-1])
                    stdscr.attroff(curses.color_pair(1))
                except Exception:
                    pass
        stdscr.attroff(curses.color_pair(1))

        stdscr.refresh()
        stdscr.timeout(150)
        key = stdscr.getch()

        if key == curses.KEY_MOUSE:
            try:
                _, mx, my, _, bstate = curses.getmouse()
                if my == 1:
                    # Click top mode bar to toggle service
                    action_name = "Stop background listener service?" if svc_active else "Start background listener service?"
                    if prompt_confirm(stdscr, action_name):
                        if svc_active:
                            user_stopped_service = True
                        action_msg = toggle_systemd_service()
                        last_fetch = 0
                else:
                    # Mouse click on session row
                    for idx_item, (sy, ey) in session_row_map.items():
                        if sy <= my <= ey:
                            if selected_idx == idx_item:
                                # Double click / click already selected -> Open inspection modal
                                curr_s = sessions_cache[selected_idx]
                                sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
                                pre_data = details_cache.get(sid)
                                action_msg = prompt_reply(stdscr, sid, selected_idx + 1, preloaded_data=pre_data)
                                last_fetch = 0
                            else:
                                selected_idx = idx_item
                            break
            except Exception:
                pass
        elif key in (ord('q'), ord('Q')):
            # Persistent session history retention: Do NOT kill background service or delete cache on TUI exit
            break
        elif key == curses.KEY_UP and selected_idx > 0:
            selected_idx -= 1
        elif key == curses.KEY_DOWN and selected_idx < len(sessions_cache) - 1:
            selected_idx += 1
        elif key in (ord('s'), ord('S')):
            action_name = "Stop background listener service?" if svc_active else "Start background listener service?"
            if prompt_confirm(stdscr, action_name):
                if svc_active:
                    user_stopped_service = True
                action_msg = toggle_systemd_service()
                last_fetch = 0
            else:
                action_msg = "Cancelled service toggle."
        elif key in (ord('b'), ord('B')):
            action_name = "Disable system autostart?" if auto_enabled else "Enable system autostart?"
            if prompt_confirm(stdscr, action_name):
                action_msg = toggle_systemd_autostart()
                last_fetch = 0
            else:
                action_msg = "Cancelled autostart toggle."
        elif key in (ord('w'), ord('W')):
            import webbrowser
            url = "https://jules.google.com"
            if sessions_cache and selected_idx < len(sessions_cache):
                curr_s = sessions_cache[selected_idx]
                sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
                url = f"https://jules.google.com/task/{sid}"
            webbrowser.open(url)
            action_msg = f"🌐 Opened Jules web page: {url}"
        elif key in (ord('h'), ord('H')):
            action_msg = prompt_action_history_panel(stdscr)
            last_fetch = 0
        elif key in (ord('v'), ord('V')):
            action_msg = prompt_archived_panel(stdscr)
            last_fetch = 0
        elif key in (ord('u'), ord('U')) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            if prompt_confirm(stdscr, f"Unstuck session #{selected_idx + 1}?"):
                msg_txt = "Re-evaluating task: Line 393 in paru-wrapper already uses double quotes around \"$@\". Run tests, finalize PR, and submit."
                res = send_message(sid, msg_txt)
                last_fetch = 0
                if "error" not in res:
                    action_msg = f"⚡ Sent un-stick instruction to session #{selected_idx + 1}"
                else:
                    action_msg = f"Error sending un-stick message: {res.get('error')}"
            else:
                action_msg = "Cancelled un-sticking session."
        elif key in (ord('r'), ord('R')):
            last_fetch = 0
            action_msg = "Refreshed session list."
        elif key in (ord('m'), ord('M')):
            modes = ["continuous", "once", "paused"]
            curr = cfg.get("mode", "continuous")
            nxt = modes[(modes.index(curr) + 1) % len(modes)]
            cfg["mode"] = nxt
            save_config(cfg)
            action_msg = f"Listener mode updated to: {nxt.upper()}"
        elif key in (ord('a'), ord('A')) and sessions_cache:
            if prompt_confirm(stdscr, f"Archive session #{selected_idx + 1}?"):
                curr_s = sessions_cache[selected_idx]
                sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
                archive_session(sid)
                last_fetch = 0
                action_msg = f"Archived session #{selected_idx + 1}"
            else:
                action_msg = "Cancelled archiving session."
        elif key in (ord('p'), ord('P')) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            action_msg = open_session_pr(curr_s)
        elif key in (curses.KEY_ENTER, 10, 13) and sessions_cache:
            curr_s = sessions_cache[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            pre_data = details_cache.get(sid)
            action_msg = prompt_reply(stdscr, sid, selected_idx + 1, preloaded_data=pre_data)
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

def prompt_confirm(stdscr, question):
    """Displays a full-screen confirmation modal prompt returning True if user presses 'y'/'Y'."""
    stdscr.timeout(-1)
    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        header = " ⚠️ CONFIRM ACTION "
        stdscr.attron(curses.color_pair(4) | curses.A_BOLD)
        stdscr.addstr(0, 0, header.center(width)[:width])
        stdscr.attroff(curses.color_pair(4) | curses.A_BOLD)

        stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(height // 2 - 1, 0, question.center(width)[:width])
        stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        prompt_str = "Press [y] to Confirm | Press [n] or [ESC] to Cancel"
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height // 2 + 1, 0, prompt_str.center(width)[:width])
        stdscr.attroff(curses.color_pair(1))

        stdscr.refresh()
        ch = stdscr.getch()
        if ch in (ord('y'), ord('Y')):
            stdscr.timeout(1000)
            return True
        elif ch in (ord('n'), ord('N'), 27):
            stdscr.timeout(1000)
            return False

def prompt_action_history_panel(stdscr):
    """Displays a dedicated full-screen Global Action History Log panel specifying Title, Repo, Branch, and Action Type."""
    stdscr.timeout(-1)
    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        # Header
        header = " 📜 GLOBAL ACTION & EVENT HISTORY LOG "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header[:width].center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        footer_tips = "Press [ESC] to return to active sessions list"
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height - 1, 0, footer_tips.center(width)[:width-1])
        stdscr.attroff(curses.color_pair(1))

        # Read global action logs
        log_entries = []
        try:
            log_file = os.path.expanduser("~/.config/jules/agy_actions.json")
            if os.path.exists(log_file):
                with open(log_file, "r") as f:
                    all_actions = json.load(f)
                    for sid, events in all_actions.items():
                        for ev in events:
                            log_entries.append({
                                "session_id": sid,
                                "timestamp": ev.get("timestamp", ""),
                                "action": ev.get("action", "EVENT"),
                                "message": ev.get("message", ""),
                                "title": ev.get("title", ""),
                                "repo": ev.get("repo", ""),
                                "branch": ev.get("branch", ""),
                                "action_by": ev.get("action_by", "manual"),
                                "query": ev.get("query", "")
                            })
        except Exception:
            pass

        log_entries.sort(key=lambda x: x.get("timestamp", ""), reverse=True)

        stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
        stdscr.addstr(2, 2, "RECORDED ACTIONS (TITLE, REPO, BRANCH & ACTION METADATA):")
        stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        max_line_width = max(20, width - 6)
        formatted_lines = []

        if not log_entries:
            formatted_lines.append("No recorded action history found.")
        else:
            for item in log_entries:
                ts = item["timestamp"]
                act = item["action"]
                msg = item["message"]
                sid = item["session_id"][:12]
                title = item.get("title") or f"Session {sid}"
                repo = item.get("repo") or "repository"
                branch = item.get("branch") or "branch"
                by_tag = item.get("action_by", "manual").upper()

                header_line = f"[{ts}] [{act}] [{by_tag}] Task: '{title}'"
                repo_line = f"   ↳ Repo: {repo} | Branch: {branch} | ID: {sid}"
                msg_line = f"   ↳ Detail: {msg}"

                formatted_lines.extend(textwrap.wrap(header_line, max_line_width))
                formatted_lines.extend(textwrap.wrap(repo_line, max_line_width))
                formatted_lines.extend(textwrap.wrap(msg_line, max_line_width))
                if item.get("query"):
                    formatted_lines.extend(textwrap.wrap(f"   ↳ Query context: {item['query'][:120]}", max_line_width))
                formatted_lines.append("")

        available_height = max(3, height - 5)
        display_lines = formatted_lines[:available_height]

        for idx, l in enumerate(display_lines):
            stdscr.addstr(4 + idx, 3, l[:width-4])

        stdscr.refresh()
        ch = stdscr.getch()
        if ch == 27:  # ESC key
            stdscr.timeout(1000)
            return "Returned to active sessions."

def prompt_archived_panel(stdscr):
    """Displays a dedicated full-screen Archived Sessions Collection panel (stored archived session objects with auto vs manual tags)."""
    stdscr.timeout(-1)
    selected_idx = 0
    status_msg = ""

    # Load persistent action log to check manual vs auto archiving tags
    auto_archived_ids = set()
    try:
        log_file = os.path.expanduser("~/.config/jules/agy_actions.json")
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                all_actions = json.load(f)
                for sid, events in all_actions.items():
                    for ev in events:
                        if ev.get("action") == "ARCHIVE_SESSION" and ev.get("action_by") == "auto":
                            auto_archived_ids.add(sid)
    except Exception:
        pass

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        # Header
        header = " 📦 ARCHIVED SESSIONS COLLECTION "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header[:width].center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Fetch archived sessions
        res = list_sessions(include_archived=True)
        raw_sessions = res.get("sessions", []) if isinstance(res, dict) else []
        archived_sessions = [s for s in raw_sessions if s.get("archived") or s.get("state") in ("ARCHIVED", "COMPLETED", "SUCCEEDED", "RESOLVED", "MERGED", "CLOSED")]
        archived_sessions.sort(key=lambda x: x.get("updateTime") or x.get("createTime") or "")

        footer_tips = "Keybindings: [u] unarchive | [Enter] inspect details | [ESC] return to active sessions"
        stdscr.attron(curses.color_pair(1))
        stdscr.addstr(height - 1, 0, footer_tips.center(width)[:width-1])
        stdscr.attroff(curses.color_pair(1))

        if status_msg:
            stdscr.attron(curses.color_pair(3) | curses.A_BOLD)
            stdscr.addstr(height - 2, 2, status_msg[:width-4])
            stdscr.attroff(curses.color_pair(3) | curses.A_BOLD)

        available_height = height - 4 - (1 if status_msg else 0)
        curr_y = 2

        if not archived_sessions:
            stdscr.addstr(3, 2, "No archived sessions found in collection.", curses.color_pair(3))
        else:
            if selected_idx >= len(archived_sessions):
                selected_idx = max(0, len(archived_sessions) - 1)

            for i in range(len(archived_sessions)):
                if curr_y >= 2 + available_height:
                    break

                s = archived_sessions[i]
                sid = s.get("id") or s.get("name", "").split("/")[-1]
                local_num = i + 1
                state = s.get("state", "ARCHIVED")
                arch_tag = "AUTO" if sid in auto_archived_ids else "MANUAL"
                
                raw_title = s.get("title", "")
                if not raw_title or len(raw_title) > 100 or "\n" in raw_title:
                    title_lines = [l.strip() for l in (raw_title or s.get("prompt", "")).splitlines() if l.strip()]
                    raw_title = title_lines[0] if title_lines else "Untitled Session"
                clean_title = raw_title.lstrip("#").strip().replace("\n", " ")

                prefix = ">" if i == selected_idx else " "
                meta_prefix = f"{prefix} [#{local_num:<2}] [{arch_tag:<6}] {state:<12} | "
                meta_len = len(meta_prefix)
                title_width = max(10, width - meta_len - 3)
                wrapped_title = textwrap.wrap(clean_title, title_width) or [clean_title]

                line1 = f"{meta_prefix}{wrapped_title[0]}"[:width-2]
                if i == selected_idx:
                    stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                    stdscr.addstr(curr_y, 1, line1)
                    stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.attron(curses.color_pair(2))
                    stdscr.addstr(curr_y, 1, line1)
                    stdscr.attroff(curses.color_pair(2))

                curr_y += 1
                for extra_line in wrapped_title[1:]:
                    if curr_y >= 2 + available_height:
                        break
                    indented_line = f"{' ' * meta_len}{extra_line}"[:width-2]
                    if i == selected_idx:
                        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                        stdscr.addstr(curr_y, 1, indented_line)
                        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
                    else:
                        stdscr.attron(curses.color_pair(2))
                        stdscr.addstr(curr_y, 1, indented_line)
                        stdscr.attroff(curses.color_pair(2))
                    curr_y += 1

        stdscr.refresh()
        ch = stdscr.getch()

        if ch == 27:  # ESC key
            stdscr.timeout(1000)
            return "Returned to active sessions."
        elif ch == curses.KEY_UP and selected_idx > 0:
            selected_idx -= 1
        elif ch == curses.KEY_DOWN and selected_idx < len(archived_sessions) - 1:
            selected_idx += 1
        elif ch in (ord('u'), ord('U')) and archived_sessions:
            from jules_manager import unarchive_session
            curr_s = archived_sessions[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            if prompt_confirm(stdscr, f"Unarchive session #{selected_idx + 1}?"):
                unarchive_session(sid)
                status_msg = f"Unarchived session #{selected_idx + 1}"
            else:
                status_msg = "Cancelled unarchiving."
        elif ch in (curses.KEY_ENTER, 10, 13) and archived_sessions:
            curr_s = archived_sessions[selected_idx]
            sid = curr_s.get("id") or curr_s.get("name", "").split("/")[-1]
            status_msg = prompt_reply(stdscr, sid, selected_idx + 1)

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

def prompt_reply(stdscr, session_id, local_num, preloaded_data=None):
    # Use preloaded details if available, otherwise fetch ONCE
    if preloaded_data:
        sess = preloaded_data.get("session", {})
        activities = preloaded_data.get("activities", {})
    else:
        activities = get_session_activities(session_id)
        sess = _make_request(f"sessions/{session_id}") if '_make_request' in globals() else {}
    
    # Read persistent AGY action log for this session
    agy_logs = []
    try:
        log_file = os.path.expanduser("~/.config/jules/agy_actions.json")
        if os.path.exists(log_file):
            with open(log_file, "r") as f:
                all_actions = json.load(f)
                agy_logs = all_actions.get(session_id, [])
    except Exception:
        pass

    # Full screen modal loop (blocking, no timeout, handles resize)
    stdscr.timeout(-1)
    input_text = ""
    reply_active = False
    status_err = ""

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.clear()

        # Build query lines with dynamic word wrapping based on current width
        max_line_width = max(20, width - 8)
        q_lines = []
        
        title_str = sess.get("title") or f"Session #{local_num}"
        if title_str:
            q_lines.extend(textwrap.wrap(f"Title: {title_str}", max_line_width))
            
        state_str = sess.get("state", "RUNNING")
        braille_swirl = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
        curr_spin = braille_swirl[int(time.time() * 8) % len(braille_swirl)]
        
        prompt_raw = sess.get("prompt", "")
        if prompt_raw:
            q_lines.append(f"--- Original Task Prompt [{state_str} {curr_spin}] ---")
            for pl in prompt_raw.splitlines()[:4]:
                q_lines.extend(textwrap.wrap(pl, max_line_width) or [""])

        # Render AGY Processing & Auto-Handling History Log
        if agy_logs:
            q_lines.append("")
            q_lines.append("=== 🤖 AGY PROCESSING & AUTOMATED ACTIONS LOG ===")
            for log_entry in agy_logs:
                ts = log_entry.get("timestamp", "")
                act = log_entry.get("action", "PROCESSED")
                msg = log_entry.get("message", "")
                q_lines.extend(textwrap.wrap(f"[{ts}] {act}: {msg}", max_line_width))

        if isinstance(activities, dict) and "activities" in activities:
            q_lines.append("")
            q_lines.append("=== 📜 COMPLETE SESSION ACTION & ACTIVITY TIMELINE ===")
            for act in activities["activities"]:
                ctime = act.get("createTime", "")[:19].replace("T", " ")
                if "agentMessaged" in act and isinstance(act["agentMessaged"], dict):
                    msg_text = act["agentMessaged"].get("agentMessage", "").strip()
                    if msg_text:
                        q_lines.extend(textwrap.wrap(f"[{ctime}] 🤖 Agent: {msg_text}", max_line_width))
                elif "agentMessage" in act:
                    msg_text = act["agentMessage"].get("text", "") if isinstance(act["agentMessage"], dict) else str(act["agentMessage"])
                    if msg_text.strip():
                        q_lines.extend(textwrap.wrap(f"[{ctime}] 🤖 Agent: {msg_text.strip()}", max_line_width))
                elif "userMessaged" in act and isinstance(act["userMessaged"], dict):
                    msg_text = act["userMessaged"].get("userMessage", "").strip()
                    if msg_text:
                        q_lines.extend(textwrap.wrap(f"[{ctime}] 👤 User: {msg_text}", max_line_width))
                elif "progressUpdated" in act and isinstance(act["progressUpdated"], dict):
                    title = act["progressUpdated"].get("title", "").strip()
                    desc = act["progressUpdated"].get("description", "").strip()
                    if title or desc:
                        info = f"{title}: {desc}" if (title and desc) else (title or desc)
                        q_lines.extend(textwrap.wrap(f"[{ctime}] ⚙️ Progress: {info}", max_line_width))
                elif "planGenerated" in act and "plan" in act["planGenerated"]:
                    steps = act["planGenerated"]["plan"].get("steps", [])
                    step_strs = [s.get('title', '') for s in steps]
                    q_lines.extend(textwrap.wrap(f"[{ctime}] 📋 Plan Generated: {', '.join(step_strs)}", max_line_width))

        # Modal Header
        header = f" 🔍 SESSION INSPECTION & LOGS [#{local_num}] "
        stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
        stdscr.addstr(0, 0, header[:width].center(width))
        stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)

        # Draw content panel
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(2, 2, "SESSION DETAILS, AGY LOGS & JULES QUERY:", curses.A_BOLD)
        stdscr.attroff(curses.color_pair(3))

        # Calculate max lines that fit above input prompt
        max_lines_allowed = max(3, height - (8 if reply_active else 6))
        
        # Display the bottom-most lines of q_lines if content exceeds available height
        if len(q_lines) > max_lines_allowed:
            display_lines = q_lines[-max_lines_allowed:]
        else:
            display_lines = q_lines

        for i, line_str in enumerate(display_lines):
            stdscr.addstr(4 + i, 4, line_str[:width-5])

        # Bottom Controls / Reply Prompt area
        if reply_active:
            prompt_y = max(6, height - 4)
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(prompt_y, 2, "Your Reply (Press ENTER to send, ESC to cancel reply mode):"[:width-4])
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

            input_display = f"> {input_text}"[:width-4]
            stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
            stdscr.addstr(prompt_y + 1, 2, f"{input_display:<{width-4}}")
            stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
        else:
            prompt_y = max(6, height - 2)
            stdscr.attron(curses.color_pair(1) | curses.A_BOLD)
            stdscr.addstr(prompt_y, 0, "Press [r] to reply | Press [p] open PR | Press [a] to archive | Press [ESC] to return".center(width)[:width])
            stdscr.attroff(curses.color_pair(1) | curses.A_BOLD)

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
            if reply_active:
                reply_active = False
                input_text = ""
                status_err = ""
                continue
            else:
                stdscr.timeout(1000)
                return "Exited session inspection panel."
        elif not reply_active and ch in (ord('r'), ord('R')):
            reply_active = True
            status_err = ""
            continue
        elif not reply_active and ch in (ord('p'), ord('P')):
            status_err = open_session_pr(sess)
            continue
        elif not reply_active and ch in (ord('a'), ord('A')):
            if prompt_confirm(stdscr, f"Archive session #{local_num}?"):
                archive_session(session_id)
                stdscr.timeout(1000)
                return f"Archived session #{local_num}"
            else:
                status_err = "Cancelled archiving session."
                continue
        elif reply_active and ch in (curses.KEY_ENTER, 10, 13):
            if input_text.strip():
                res = send_message(session_id, input_text.strip())
                stdscr.timeout(1000)
                if "error" not in res:
                    return f"Sent response to session {session_id[:12]}"
                return f"Error sending message: {res.get('error')}"
            else:
                status_err = "Please enter a non-empty response or press ESC to cancel."
        elif reply_active and ch in (curses.KEY_BACKSPACE, 127, 8):
            input_text = input_text[:-1]
        elif reply_active and 32 <= ch <= 126:
            input_text += chr(ch)

def kill_previous_tui_instances():
    """Closes any other running jules_tui.py / jules-tui process instances and Kitty wrapper windows (excluding self and self's parent shell)."""
    my_pid = os.getpid()
    my_ppid = os.getppid()
    try:
        res = subprocess.run(["pgrep", "-f", "jules_tui|jules-tui"], capture_output=True, text=True)
        if res.returncode == 0:
            for pid_str in res.stdout.strip().split():
                try:
                    pid = int(pid_str)
                    if pid != my_pid and pid != my_ppid:
                        os.kill(pid, 9)
                except Exception:
                    pass
    except Exception:
        pass

def main():
    kill_previous_tui_instances()
    try:
        curses.wrapper(draw_menu)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
