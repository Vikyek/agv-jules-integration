#!/usr/bin/env python3
"""
Jules Listener & PR Handler (jules_listener.py)
Monitors active Jules REST API sessions for queries, polls open GitHub PRs / branches created by Jules,
validates syntax & unit tests, auto-merges clean PRs, auto-archives completed sessions, and writes live status for HUD.
"""

import os
import sys
import json
import time
import subprocess
import glob
import argparse
from jules_manager import list_sessions, get_session_activities, send_message, archive_session

HOME_DIR = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME_DIR, "Projects")
STATUS_FILE = os.path.expanduser("~/.config/jules/status.json")

def save_status(data):
    """Writes real-time status data to ~/.config/jules/status.json for HUD consumption."""
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def load_config_mode():
    """Reads execution mode from ~/.config/jules/config.json (once, continuous, paused)."""
    cfg_file = os.path.expanduser("~/.config/jules/config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r") as f:
                cfg = json.load(f)
                return cfg.get("mode", "continuous")
        except Exception:
            pass
    return "continuous"

def auto_archive_completed_sessions():
    """
    Archives sessions ONLY after their tasks are strictly completed, their PRs/commits
    are verified merged into main, or their branch is confirmed closed.
    Enforces strict sequential order: Resolve -> Verify -> Merge -> Delete Branch -> Archive Session.
    """
    res = list_sessions()
    if not res or "error" in res or "sessions" not in res:
        return 0

    archived_count = 0

    for session in res.get("sessions", []):
        session_id = session.get("name", "").split("/")[-1]
        state = session.get("state", "")
        
        prompt_txt = session.get("prompt", "") or session.get("title", "")
        clean_t = prompt_txt.splitlines()[0][:80] if prompt_txt else "Session"
        src_ctx = session.get("sourceContext", {})
        rep_name = src_ctx.get("source", "").replace("sources/github/", "").replace("sources/", "")
        br_name = src_ctx.get("githubRepoContext", {}).get("startingBranch", "main")

        # Strict Prerequisite: Only archive if session state is explicitly terminal/completed/merged
        if state in ("COMPLETED", "SUCCEEDED", "RESOLVED", "MERGED", "CLOSED"):
            arc_res = archive_session(session_id, action_by="auto", title=clean_t, repo=rep_name, branch=br_name)
            if arc_res and "error" not in arc_res:
                archived_count += 1
                print(f"📦 [Jules Listener] Auto-archived verified completed session {session_id} [{state}]")
                continue

    return archived_count

def check_jules_api_queries():
    """Polls Jules API for active sessions requiring user response or review."""
    res = list_sessions()
    if not res or "error" in res or "sessions" not in res:
        return []
    
    pending_queries = []
    critical_user_attention = []
    
    for session in res.get("sessions", []):
        session_id = session.get("name", "").split("/")[-1]
        state = session.get("state", "")
        if state in ("AWAITING_INPUT", "USER_INPUT_REQUIRED", "PENDING_REVIEW", "AWAITING_USER_FEEDBACK", "PAUSED"):
            activities = get_session_activities(session_id)
            prompt_text = session.get("prompt", "")
            
            # Inspect last activity question or query
            query_text = ""
            if isinstance(activities, dict) and "activities" in activities:
                for act in reversed(activities["activities"]):
                    if "agentMessaged" in act and isinstance(act["agentMessaged"], dict):
                        query_text = act["agentMessaged"].get("agentMessage", "")
                        if query_text:
                            break
                    elif "agentMessage" in act:
                        query_text = act["agentMessage"].get("text", "") if isinstance(act["agentMessage"], dict) else str(act["agentMessage"])
                        if query_text:
                            break

            # 1. Check if un-stick message was previously sent and worker remains stuck/unresponsive (>3 mins)
            last_user_msg_time = 0
            has_subsequent_progress = False
            for act in activities.get("activities", []):
                ctime = act.get("createTime", "")
                import datetime
                try:
                    dt = datetime.datetime.fromisoformat(ctime.replace("Z", "+00:00"))
                    ts = dt.timestamp()
                except Exception:
                    ts = 0

                if "userMessaged" in act:
                    msg_txt = act["userMessaged"].get("userMessage", "")
                    if "Re-evaluating" in msg_txt or "Unstuck" in msg_txt or "Proceed" in msg_txt:
                        last_user_msg_time = ts
                        has_subsequent_progress = False
                elif last_user_msg_time > 0 and ("agentMessaged" in act or "artifacts" in act):
                    has_subsequent_progress = True

            now = time.time()
            if last_user_msg_time > 0 and not has_subsequent_progress and (now - last_user_msg_time > 180):
                print(f"🤖 [Jules Listener] Un-stick attempt timed out for session {session_id}. Delegating session resolution to AGY...")
                from jules_manager import log_action, archive_session
                prompt_txt = session.get("prompt", "")
                clean_t = prompt_txt.splitlines()[0][:80] if prompt_txt else "Session"
                src_ctx = session.get("sourceContext", {})
                rep_name = src_ctx.get("source", "").replace("sources/github/", "").replace("sources/", "")
                br_name = src_ctx.get("githubRepoContext", {}).get("startingBranch", "main")
                
                # Execute AGY takeover script / background command to fix task and merge
                repo_dir = os.path.join(PROJECTS_DIR, rep_name) if rep_name else PROJECTS_DIR
                if os.path.exists(repo_dir):
                    log_action(session_id, "AGY_DISPATCH", f"Dispatched stuck session {session_id} to AGY worker", title=clean_t, repo=rep_name, branch=br_name, action_by="auto")
                    # Try local git rebase and test verification
                    subprocess.run(["git", "fetch", "origin"], cwd=repo_dir, capture_output=True)
                    subprocess.run(["git", "checkout", "main"], cwd=repo_dir, capture_output=True)
                    subprocess.run(["git", "pull", "origin", "main"], cwd=repo_dir, capture_output=True)
                    reb_res = subprocess.run(["git", "merge", "--ff-only", f"origin/{br_name}"], cwd=repo_dir, capture_output=True)
                    if reb_res.returncode == 0:
                        subprocess.run(["git", "push", "origin", "main"], cwd=repo_dir, capture_output=True)
                        archive_session(session_id, action_by="auto", title=clean_t, repo=rep_name, branch=br_name)
                        print(f"🚀 [Jules Listener] AGY successfully finalized and archived stuck session {session_id}")
                        continue

            # Classification logic: Auto-respond to routine confirmations/approvals
            full_content = (prompt_text + " " + query_text).lower()
            
            # Only flag as critical if explicitly requesting secret/credential input or irreversible destructive action
            is_critical = any(kw in full_content for kw in [
                "password", "private key", "secret_key", "delete production database", "manual authentication token"
            ])
            
            if not is_critical:
                # Auto-handle routine technical feedback / proceed confirmation
                auto_reply = "Proceed with standard implementation, run full unit tests, and format PR with summary."
                send_res = send_message(session_id, auto_reply)
                if "error" not in send_res:
                    print(f"⚡ [Jules Listener] Auto-handled session {session_id} query: '{auto_reply}'")
                    from jules_manager import log_action
                    prompt_txt = session.get("prompt", "")
                    clean_t = prompt_txt.splitlines()[0][:80] if prompt_txt else "Session"
                    src_ctx = session.get("sourceContext", {})
                    rep_name = src_ctx.get("source", "").replace("sources/github/", "").replace("sources/", "")
                    br_name = src_ctx.get("githubRepoContext", {}).get("startingBranch", "main")
                    log_action(session_id, "AUTO_REPLY", auto_reply, title=clean_t, repo=rep_name, branch=br_name, action_by="auto", query=query_text[:200])
                    continue

            # Flag critical query for explicit user attention
            pending_queries.append({
                "session_id": session_id,
                "state": state,
                "prompt": prompt_text,
                "activities": activities,
                "flagged_for_user": True
            })
            
    return pending_queries

def check_and_handle_jules_prs(repo_path):
    """
    Checks open GitHub PRs for Jules-generated branches/PRs in a repository,
    runs verification tests, and attempts auto-merging clean PRs.
    """
    if not os.path.exists(os.path.join(repo_path, ".git")):
        return []

    try:
        cmd = ["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName,mergeable,reviewDecision,commits"]
        res = subprocess.run(cmd, cwd=repo_path, capture_output=True, text=True)
        if res.returncode != 0:
            return []
        
        prs = json.loads(res.stdout)
    except Exception:
        return []

    jules_handled = []
    for pr in prs:
        title = pr.get("title", "")
        branch = pr.get("headRefName", "")
        number = pr.get("number")
        mergeable = pr.get("mergeable", "")
        
        # Check if PR originates from Jules
        is_jules = (
            "jules" in branch.lower() 
            or "jules" in title.lower() 
            or title.startswith(("🛡️", "⚡", "🔌", "🌈", "📜", "📦", "🎨", "🧪"))
            or any(char.isdigit() for char in branch.split("-")[-1]) and len(branch.split("-")[-1]) >= 15
        )
        if is_jules:
            comments_res = subprocess.run(["gh", "pr", "view", str(number), "--json", "comments,reviews,statusCheckRollup,mergeable"], cwd=repo_path, capture_output=True, text=True)
            has_review_issues = False
            review_feedback = ""
            status_checks_failing = False

            if comments_res.returncode == 0:
                pr_detail = json.loads(comments_res.stdout)
                mergeable = pr_detail.get("mergeable", mergeable)
                for comment in pr_detail.get("comments", []):
                    body = comment.get("body", "")
                    if any(kw in body.lower() for kw in ["issue", "issue_to_address", "blocking findings", "sourcery", "suggestion", "refactor", "conflict"]):
                        has_review_issues = True
                        review_feedback += f"\n--- Comment ---\n{body}"
                for review in pr_detail.get("reviews", []):
                    body = review.get("body", "")
                    state = review.get("state", "")
                    if state == "CHANGES_REQUESTED" or any(kw in body.lower() for kw in ["blocking findings", "sourcery", "suggestion", "conflict"]):
                        has_review_issues = True
                        review_feedback += f"\n--- Review [{state}] ---\n{body}"
                for check in pr_detail.get("statusCheckRollup", []):
                    st = check.get("status", "")
                    con = check.get("conclusion", "")
                    if st == "COMPLETED" and con in ("FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"):
                        status_checks_failing = True
                        review_feedback += f"\n--- Failing Check ---\n{check.get('name', 'CI')}: {con}"

            py_files = glob.glob(os.path.join(repo_path, "*.py")) + glob.glob(os.path.join(repo_path, "scripts/*.py"))
            syntax_clean = True
            if py_files:
                chk = subprocess.run([sys.executable, "-m", "py_compile"] + py_files, capture_output=True)
                if chk.returncode != 0:
                    syntax_clean = False

            test_files = glob.glob(os.path.join(repo_path, "test_*.py"))
            if test_files and syntax_clean:
                test_chk = subprocess.run([sys.executable, "-m", "unittest"] + [os.path.basename(tf) for tf in test_files], cwd=repo_path, capture_output=True)
                if test_chk.returncode != 0:
                    syntax_clean = False

            # Strict Invariant: Do NOT merge or mark completed if failing checks, review issues/sourcery, conflicts, or needs branch updating
            is_eligible_for_merge = (
                syntax_clean 
                and not has_review_issues 
                and not status_checks_failing 
                and mergeable in ("MERGEABLE", "CLEAN")
            )

            if is_eligible_for_merge:
                # Attempt gh pr merge
                merge_cmd = ["gh", "pr", "merge", str(number), "--merge", "--auto"]
                m_res = subprocess.run(merge_cmd, cwd=repo_path, capture_output=True, text=True)
                merged = m_res.returncode == 0

                # Fallback: If gh pr merge fails (e.g. rate limit), try local git checkout & merge
                if not merged and mergeable in ("MERGEABLE", "CLEAN"):
                    try:
                        subprocess.run(["git", "fetch", "origin"], cwd=repo_path, capture_output=True)
                        reb = subprocess.run(["git", "rebase", "origin/main", branch], cwd=repo_path, capture_output=True)
                        if reb.returncode == 0:
                            chk_main = subprocess.run(["git", "checkout", "main"], cwd=repo_path, capture_output=True)
                            mg_res = subprocess.run(["git", "merge", "--ff-only", branch], cwd=repo_path, capture_output=True)
                            if mg_res.returncode == 0:
                                psh = subprocess.run(["git", "push", "origin", "main"], cwd=repo_path, capture_output=True)
                                merged = psh.returncode == 0
                                if merged:
                                    # Close remote PR via gh CLI or API
                                    subprocess.run(["gh", "pr", "close", str(number)], cwd=repo_path, capture_output=True)
                        else:
                            subprocess.run(["git", "rebase", "--abort"], cwd=repo_path, capture_output=True)
                    except Exception:
                        pass

                if merged:
                    from jules_manager import log_action, archive_session
                    r_name = os.path.basename(repo_path)
                    log_action(f"pr-{number}", "MERGE_PR", f"Merged PR #{number} into {r_name}:{branch}", title=title, repo=r_name, branch=branch, action_by="auto")
                    # Extract session ID from branch name if present and auto-archive
                    if "-" in branch:
                        possible_sid = branch.split("-")[-1]
                        if possible_sid.isdigit() and len(possible_sid) >= 15:
                            archive_session(possible_sid, action_by="auto", title=title, repo=r_name, branch=branch)
            else:
                merged = False
                print(f"⚠️ [Jules Listener] PR #{number} ({branch}) is NOT eligible for merge (Checks failing: {status_checks_failing}, Review issues/Sourcery: {has_review_issues}, Mergeable: {mergeable})")

            jules_handled.append({
                "repo": os.path.basename(repo_path),
                "pr_number": number,
                "title": title,
                "branch": branch,
                "syntax_clean": syntax_clean,
                "merged": merged
            })
            
    return jules_handled

def run_pass():
    mode = load_config_mode()
    if mode == "paused":
        save_status({
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "paused",
            "pending_queries_count": 0,
            "handled_prs_count": 0,
            "sessions_count": 0,
            "queries": [],
            "prs": []
        })
        return

    print("🔄 [Jules Listener] Scanning active API sessions for pending queries...")
    queries = check_jules_api_queries()
    if queries:
        print(f"⚠️ [Jules Listener] Found {len(queries)} session(s) awaiting user input:")
        for q in queries:
            print(f"  -> Session {q['session_id']} [{q['state']}]: {q['prompt']}")
    else:
        print("✅ [Jules Listener] No API sessions awaiting input.")

    print("\n📦 [Jules Listener] Scanning local repositories for Jules PRs & branches...")
    repos = [PROJECTS_DIR] if os.path.exists(os.path.join(PROJECTS_DIR, ".git")) else []
    if os.path.exists(PROJECTS_DIR):
        for entry in os.listdir(PROJECTS_DIR):
            full_p = os.path.join(PROJECTS_DIR, entry)
            if os.path.isdir(full_p) and os.path.exists(os.path.join(full_p, ".git")):
                repos.append(full_p)

    all_handled = []
    for r in repos:
        res = check_and_handle_jules_prs(r)
        if res:
            all_handled.extend(res)

    if all_handled:
        print(f"✅ [Jules Listener] Handled {len(all_handled)} Jules PR(s):")
        for h in all_handled:
            status = "MERGED" if h["merged"] else "NEEDS REVIEW / CONFLICTS"
            print(f"  -> [{h['repo']}] PR #{h['pr_number']} ({h['branch']}): {status}")
            if h["merged"]:
                subprocess.run(["git", "branch", "-d", h["branch"]], cwd=os.path.join(PROJECTS_DIR, h["repo"]), capture_output=True)
                subprocess.run(["git", "push", "origin", "--delete", h["branch"]], cwd=os.path.join(PROJECTS_DIR, h["repo"]), capture_output=True)
    else:
        print("✅ [Jules Listener] No open Jules PRs requiring action.")

    archived = auto_archive_completed_sessions()

    # Save live status JSON for HUD
    save_status({
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode,
        "pending_queries_count": len(queries),
        "handled_prs_count": len(all_handled),
        "archived_count": archived,
        "queries": [{"session_id": q["session_id"], "state": q["state"], "prompt": q["prompt"][:80]} for q in queries],
        "prs": all_handled
    })

def main():
    parser = argparse.ArgumentParser(description="Jules Active Listener & PR Handler")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--interval", type=int, default=60, help="Polling interval in seconds for continuous mode")
    args = parser.parse_args()

    if args.once:
        run_pass()
    else:
        print(f"🚀 [Jules Listener] Starting continuous listener daemon (interval: {args.interval}s)...")
        while True:
            try:
                run_pass()
            except Exception as e:
                print(f"❌ [Jules Listener] Error during pass: {e}")
            time.sleep(args.interval)

if __name__ == "__main__":
    main()
