#!/usr/bin/env python3
"""
Jules Conky / Status Bar HUD Display (jules_hud.py)
Reads ~/.config/jules-vanager/status.json and outputs formatted status metrics.
Supports raw text (for vlfstatus / i3bar), ANSI colorized output, or continuous streaming mode.
"""

import os
import sys
import json
import time
import argparse

STATUS_FILE = os.path.expanduser("~/.config/jules-vanager/status.json")

def load_status():
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return None

def format_hud(data, fmt="ansi"):
    if not data:
        return "🤖 JULES | Offline" if fmt == "text" else "\033[1;30m🤖 JULES | Offline\033[0m"

    mode = data.get("mode", "unknown").upper()
    queries_cnt = data.get("pending_queries_count", 0)
    prs_cnt = data.get("handled_prs_count", 0)
    archived_cnt = data.get("archived_count", 0)
    updated_at = data.get("updated_at", "").split(" ")[-1]

    if fmt == "text":
        q_str = f" Queries:{queries_cnt}" if queries_cnt > 0 else ""
        pr_str = f" PRs:{prs_cnt}" if prs_cnt > 0 else ""
        return f"🤖 JULES [{mode}]{q_str}{pr_str} ({updated_at})"

    # ANSI Colorized format for Conky / Terminal HUD overlay
    mode_color = "\033[1;32m" if mode == "CONTINUOUS" else ("\033[1;33m" if mode == "ONCE" else "\033[1;31m")
    query_part = f" \033[1;33m⚠️ Queries: {queries_cnt}\033[0m" if queries_cnt > 0 else " \033[0;32m✓ Queries: 0\033[0m"
    pr_part = f" \033[1;36m📦 PRs: {prs_cnt}\033[0m" if prs_cnt > 0 else ""
    arc_part = f" \033[0;35m📁 Archived: {archived_cnt}\033[0m" if archived_cnt > 0 else ""

    return f"\033[1m🤖 JULES HUD\033[0m [{mode_color}{mode}\033[0m]{query_part}{pr_part}{arc_part} \033[0;30m({updated_at})\033[0m"

def main():
    parser = argparse.ArgumentParser(description="Jules Conky/Status Bar HUD Display")
    parser.add_argument("--format", choices=["ansi", "text"], default="ansi", help="Output format: ansi or text")
    parser.add_argument("--watch", action="store_true", help="Continuously refresh and print stream")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds when --watch is used")
    args = parser.parse_args()

    if args.watch:
        try:
            while True:
                data = load_status()
                print("\033[H\033[J" + format_hud(data, args.format), end="\n", flush=True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    else:
        data = load_status()
        print(format_hud(data, args.format))

if __name__ == "__main__":
    main()
