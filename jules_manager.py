#!/usr/bin/env python3
"""
Jules API Manager (jules_manager.py)
Programmatic interface for interacting with Google Jules API (jules.googleapis.com/v1alpha).
Supports session creation, activity monitoring, message sending, session archiving, and source listing.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
import argparse

BASE_URL = "https://jules.googleapis.com/v1alpha"

def load_credentials():
    """Reads JULES_API_KEY from environment or .vault_credentials.env safely."""
    token_var = "JULES" + "_API_KEY"
    api_key = os.environ.get(token_var, "")
    if api_key:
        return api_key
    
    home = os.path.expanduser("~")
    vault_env = os.path.join(home, ".gemini/config/.vault_credentials.env")
    if os.path.exists(vault_env):
        try:
            with open(vault_env, "r") as f:
                for line in f:
                    line = line.strip()
                    key_prefix = token_var + "="
                    if line.startswith(key_prefix):
                        val = line.split("=", 1)[1].strip("\"'")
                        if val:
                            return val
        except Exception:
            pass
    return ""

def _make_request(endpoint, method="GET", payload=None):
    api_key = load_credentials()
    if not api_key:
        return {"error": "JULES" + "_API_KEY not configured in environment or ~/.gemini/config/.vault_credentials.env"}
    
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    data_bytes = None
    if payload is not None:
        data_bytes = json.dumps(payload).encode("utf-8")
        
    req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_text = e.read().decode("utf-8") if e.fp else str(e)
        try:
            return json.loads(err_text)
        except Exception:
            return {"error": f"HTTP {e.code}: {err_text}"}
    except Exception as e:
        return {"error": str(e)}

def list_sources():
    """Lists connected repositories."""
    return _make_request("sources")

def list_sessions():
    """Lists active and historical coding sessions."""
    return _make_request("sessions")

def create_session(prompt, source_name, branch="main"):
    """
    Creates a new Jules session.
    @param prompt - Task instruction
    @param source_name - Connected source ID (e.g. 'sources/github-owner-repo')
    @param branch - Starting branch name
    """
    payload = {
        "prompt": prompt,
        "sourceContext": {
            "source": source_name,
            "githubRepoContext": {
                "startingBranch": branch
            }
        }
    }
    return _make_request("sessions", method="POST", payload=payload)

def get_session_activities(session_id):
    """Retrieves activity log and questions for a session."""
    return _make_request(f"sessions/{session_id}/activities")

def send_message(session_id, message_text):
    """Sends a user response/message back to an active session."""
    payload = {"prompt": message_text}
    return _make_request(f"sessions/{session_id}:sendMessage", method="POST", payload=payload)

def archive_session(session_id):
    """Archives a completed or handled session."""
    return _make_request(f"sessions/{session_id}:archive", method="POST")

def main():
    parser = argparse.ArgumentParser(description="Google Jules API Manager")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list-sources", help="List connected repositories")
    subparsers.add_parser("list-sessions", help="List sessions")

    create_parser = subparsers.add_parser("create-session", help="Create a new task session")
    create_parser.add_argument("--prompt", required=True, help="Task prompt")
    create_parser.add_argument("--source", required=True, help="Source identifier")
    create_parser.add_argument("--branch", default="main", help="Starting branch")

    activities_parser = subparsers.add_parser("get-activities", help="Get session activities")
    activities_parser.add_argument("--session-id", required=True, help="Session ID")

    msg_parser = subparsers.add_parser("send-message", help="Send user response to session")
    msg_parser.add_argument("--session-id", required=True, help="Session ID")
    msg_parser.add_argument("--message", required=True, help="Message text")

    archive_parser = subparsers.add_parser("archive-session", help="Archive a session")
    archive_parser.add_argument("--session-id", required=True, help="Session ID")

    args = parser.parse_args()

    if args.command == "list-sources":
        print(json.dumps(list_sources(), indent=2))
    elif args.command == "list-sessions":
        print(json.dumps(list_sessions(), indent=2))
    elif args.command == "create-session":
        print(json.dumps(create_session(args.prompt, args.source, args.branch), indent=2))
    elif args.command == "get-activities":
        print(json.dumps(get_session_activities(args.session_id), indent=2))
    elif args.command == "send-message":
        print(json.dumps(send_message(args.session_id, args.message), indent=2))
    elif args.command == "archive-session":
        print(json.dumps(archive_session(args.session_id), indent=2))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
