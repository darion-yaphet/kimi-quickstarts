#!/usr/bin/env python3
"""Run a local Kimi-backed replacement for Managed Agents conversation state."""

import argparse
import json
import os
from pathlib import Path

from kimi_local import LocalRuntime, PROFILES, SessionStore, get_profile
from kimi_local.http_api import make_server
from kimi_local.provider import KimiProvider


DEFAULT_DB = Path(__file__).parent / ".kimi" / "sessions.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Local Kimi session service for the managed-agents examples"
    )
    result.add_argument(
        "--profile", choices=sorted(PROFILES), default="assistant-ui"
    )
    result.add_argument("--db", default=os.environ.get("KIMI_SESSIONS_DB", str(DEFAULT_DB)))
    result.add_argument("--session", help="Resume an existing local session")
    result.add_argument("--message", help="Send one message and exit")
    result.add_argument("--title", default="New session")
    result.add_argument("--serve", action="store_true", help="Run the HTTP API")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8765)
    result.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind; add authentication before using this",
    )
    result.add_argument("--status", action="store_true", help="Print profile capability status")
    result.add_argument("--list-profiles", action="store_true")
    return result


def _runtime(database: str) -> LocalRuntime:
    return LocalRuntime(SessionStore(database), KimiProvider)


def _session(runtime: LocalRuntime, args) -> dict:
    if args.session:
        session = runtime.store.get_session(args.session)
        if not session:
            raise SystemExit(f"Unknown local session: {args.session}")
        return session
    return runtime.create_session(args.profile, args.title)


def interactive(runtime: LocalRuntime, args) -> None:
    session = _session(runtime, args)
    profile = get_profile(session["profile"])
    print(f"Profile: {profile.name}")
    print(f"Session: {session['id']}")
    print(profile.integration_status)
    print("Type /exit to stop. Conversation state is stored in SQLite.\n")
    while True:
        try:
            message = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if message in {"/exit", "/quit"}:
            return
        if not message:
            continue
        try:
            event = runtime.chat(session["id"], message)
            print(f"kimi> {event['content']}\n")
        except Exception as error:
            print(f"error> {error}\n")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.list_profiles:
        for name in sorted(PROFILES):
            print(name)
        return 0
    if args.status:
        print(json.dumps(get_profile(args.profile).public_dict(), indent=2))
        return 0

    runtime = _runtime(args.db)
    if args.serve:
        if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
            raise SystemExit(
                "Remote binds require --allow-remote. This local API has no authentication."
            )
        server = make_server(runtime, args.host, args.port)
        print(f"Local Kimi session API: http://{args.host}:{server.server_port}")
        print("No hosted Agents, Sessions, vault, cron, OAuth, or sandbox API is emulated.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    if args.message is not None:
        session = _session(runtime, args)
        event = runtime.chat(session["id"], args.message)
        print(
            json.dumps(
                {"session": session, "event": event}, ensure_ascii=False, indent=2
            )
        )
        return 0

    interactive(runtime, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
