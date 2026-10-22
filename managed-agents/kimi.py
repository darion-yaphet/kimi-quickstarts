#!/usr/bin/env python3
"""Run a local Kimi-backed replacement for Managed Agents conversation state."""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from kimi_local import LocalRuntime, PROFILES, SessionStore, get_profile
from kimi_local.http_api import make_server
from kimi_local.provider import KimiProvider
from kimi_local.knowledge import KnowledgeStore
from kimi_local.sandbox import SandboxExecutor
from kimi_local.scheduler import Scheduler


DEFAULT_DB = Path(__file__).parent / ".kimi" / "sessions.db"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Local Kimi session service for the managed-agents examples"
    )
    result.add_argument("--profile", choices=sorted(PROFILES), default="assistant-ui")
    result.add_argument(
        "--db", default=os.environ.get("KIMI_SESSIONS_DB", str(DEFAULT_DB))
    )
    result.add_argument("--session", help="Resume an existing local session")
    result.add_argument("--message", help="Send one message and exit")
    result.add_argument("--title", default="New session")
    result.add_argument(
        "--knowledge-collection", help="Retrieve evidence from this local collection"
    )
    result.add_argument("--serve", action="store_true", help="Run the HTTP API")
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8765)
    result.add_argument(
        "--no-scheduler",
        action="store_true",
        help="Do not run scheduled jobs while serving HTTP",
    )
    result.add_argument(
        "--poll-seconds",
        type=float,
        default=1,
        help="Worker poll interval (0.1–60 seconds)",
    )
    result.add_argument(
        "--status", action="store_true", help="Print profile capability status"
    )
    result.add_argument("--list-profiles", action="store_true")
    commands = result.add_subparsers(dest="command")
    knowledge = commands.add_parser(
        "knowledge", help="Ingest and search local documents"
    )
    knowledge.add_argument("--collection", default="default")
    actions = knowledge.add_subparsers(dest="action", required=True)
    actions.add_parser("ingest").add_argument("path")
    actions.add_parser("search").add_argument("query")
    actions.add_parser("list")
    actions.add_parser("delete").add_argument("document_id")
    schedule = commands.add_parser(
        "schedule", help="Persist and inspect scheduled Kimi runs"
    )
    actions = schedule.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("--profile", choices=sorted(PROFILES), default="daily-brief")
    add.add_argument("--prompt", required=True)
    add.add_argument("--title", default="Scheduled run")
    add.add_argument("--collection")
    add.add_argument("--every", type=float, help="Fixed interval in seconds")
    add.add_argument(
        "--at", help="Timezone-aware ISO timestamp; one-shot or interval start"
    )
    actions.add_parser("list")
    actions.add_parser("runs").add_argument("--job")
    for action in ("enable", "disable"):
        actions.add_parser(action).add_argument("job_id")
    actions.add_parser("tick")
    commands.add_parser("worker", help="Run persisted scheduled jobs until interrupted")
    sandbox = commands.add_parser(
        "sandbox", help="Execute and inspect isolated Python jobs"
    )
    actions = sandbox.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--code")
    source.add_argument("--file")
    source.add_argument("--job", help="Execute an existing queued job")
    actions.add_parser("list")
    actions.add_parser("get").add_argument("job_id")
    actions.add_parser("capabilities")
    actions.add_parser(
        "recover", help="Reconcile only expired or unconfirmed owned containers"
    )
    return result


def _runtime(database: str) -> LocalRuntime:
    runtime = LocalRuntime(
        SessionStore(database),
        KimiProvider,
        knowledge=KnowledgeStore(database),
        sandbox=SandboxExecutor(
            database,
            image=os.environ.get("KIMI_SANDBOX_IMAGE", "python:3.12-alpine"),
            timeout_seconds=float(os.environ.get("KIMI_SANDBOX_TIMEOUT", "10")),
        ),
        document_root=os.environ.get(
            "KIMI_DOCUMENT_ROOT", str(Path(__file__).parent / "knowledge")
        ),
    )
    runtime.scheduler = Scheduler(database, runtime.run_scheduled)
    return runtime


def _session(runtime: LocalRuntime, args) -> dict:
    if args.session:
        session = runtime.store.get_session(args.session)
        if not session:
            raise SystemExit(f"Unknown local session: {args.session}")
        return session
    return runtime.create_session(args.profile, args.title, args.knowledge_collection)


def worker(runtime, stop, poll_seconds, schedules=True):
    next_recovery = 0.0
    while not stop.is_set():
        if time.monotonic() >= next_recovery:
            try:
                runtime.sandbox.recover_orphans()
            except Exception as error:
                print(f"Sandbox recovery error: {error}", file=sys.stderr)
            finally:
                next_recovery = time.monotonic() + 30
        if schedules:
            try:
                runtime.scheduler.run_due()
            except Exception as error:
                print(f"Scheduler error: {error}", file=sys.stderr)
        stop.wait(poll_seconds)


def feature_command(runtime, args):
    if args.command == "knowledge":
        if args.action == "ingest":
            # CLI paths are explicitly chosen by the caller; HTTP paths have a configured root.
            return runtime.knowledge.ingest(args.path, args.collection)
        if args.action == "search":
            return runtime.knowledge.search(args.query, args.collection)
        if args.action == "list":
            return runtime.knowledge.list_documents(args.collection)
        return {"deleted": runtime.knowledge.delete_document(args.document_id)}
    if args.command == "schedule":
        if args.action == "add":
            return runtime.scheduler.add_job(
                args.profile,
                args.prompt,
                interval_seconds=args.every,
                run_at=args.at,
                title=args.title,
                knowledge_collection=args.collection,
            )
        if args.action == "list":
            return runtime.scheduler.list_jobs()
        if args.action == "runs":
            return runtime.scheduler.list_runs(args.job)
        if args.action == "tick":
            return runtime.scheduler.run_due()
        return runtime.scheduler.set_enabled(args.job_id, args.action == "enable")
    if args.command == "sandbox":
        if args.action == "recover":
            return runtime.sandbox.recover_orphans()
        if args.action == "capabilities":
            return {
                **runtime.sandbox.capabilities(),
                "backend": runtime.sandbox.check_backend(),
            }
        if args.action == "list":
            return runtime.sandbox.list_jobs()
        if args.action == "get":
            job = runtime.sandbox.get_job(args.job_id)
            if not job:
                raise KeyError(args.job_id)
            return job
        if args.job:
            job_id = args.job
        else:
            if args.file:
                source = Path(args.file)
                if source.stat().st_size > runtime.sandbox.max_source_chars * 4:
                    raise ValueError("Sandbox source file exceeds the size limit")
                code = source.read_text(encoding="utf-8")
            else:
                code = args.code
            job_id = runtime.sandbox.submit(code)["id"]
        return runtime.sandbox.run_job(job_id)
    raise ValueError("Unknown feature command")


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
    if not 0.1 <= args.poll_seconds <= 60:
        raise SystemExit("--poll-seconds must be between 0.1 and 60")
    if args.command == "worker":
        try:
            worker(runtime, threading.Event(), args.poll_seconds)
        except KeyboardInterrupt:
            return 0
    if args.command:
        try:
            outcome = feature_command(runtime, args)
        except (ValueError, KeyError, RuntimeError, OSError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(outcome, ensure_ascii=False, indent=2))
        if (
            args.command == "knowledge"
            and isinstance(outcome, dict)
            and outcome.get("failed", 0)
        ):
            return 1
        if args.command == "sandbox" and args.action == "run":
            return 0 if outcome["status"] == "succeeded" else 1
        return 0
    if args.serve:
        if args.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit("The local runtime only supports loopback binding.")
        server = make_server(runtime, args.host, args.port)
        print(f"Local Kimi session API: http://{args.host}:{server.server_port}")
        print(
            "Local knowledge, persisted scheduling and Docker execution are available through /v1/."
        )
        stop = threading.Event()
        background = None
        background = threading.Thread(
            target=worker,
            args=(runtime, stop, args.poll_seconds, not args.no_scheduler),
            daemon=True,
        )
        background.start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()
            if background:
                background.join(timeout=2)
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
