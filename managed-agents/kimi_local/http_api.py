"""Small loopback HTTP API for the local session runtime."""

import json
import socket
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .profiles import PROFILES, get_profile


MAX_BODY_BYTES = 1_000_000
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
WEB_ROOT = Path(__file__).with_name("web")


def _hostname(authority: str) -> str | None:
    try:
        return urlparse(f"//{authority}").hostname
    except ValueError:
        return None


def handler_for(runtime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KimiQuickstartsLocal/1"

        def _json(self, status: int, payload) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._bytes(status, body, "application/json; charset=utf-8")

        def _bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length") from error
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("Request body must be between 1 and 1000000 bytes")
            try:
                payload = json.loads(
                    self.rfile.read(length),
                    parse_constant=self._invalid_constant,
                    parse_float=self._finite_float,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("Request body must be valid JSON") from error
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        @staticmethod
        def _invalid_constant(value):
            raise ValueError(f"Non-finite JSON number is not allowed: {value}")

        @staticmethod
        def _finite_float(value):
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ValueError("Non-finite JSON numbers are not allowed")
            return parsed

        def _request_allowed(self) -> bool:
            host = _hostname(self.headers.get("Host", ""))
            if host not in LOOPBACK_HOSTS:
                self._json(403, {"error": "non_loopback_host_rejected"})
                return False

            origin = self.headers.get("Origin")
            if origin:
                try:
                    parsed = urlparse(origin)
                except ValueError:
                    parsed = None
                if (
                    parsed is None
                    or parsed.scheme != "http"
                    or parsed.netloc.lower() != self.headers.get("Host", "").lower()
                ):
                    self._json(403, {"error": "foreign_origin_rejected"})
                    return False
            return True

        def _require_json(self) -> bool:
            if self.headers.get_content_type() != "application/json":
                self._json(415, {"error": "content_type_must_be_application_json"})
                return False
            return True

        def _segments(self) -> list[str]:
            return [part for part in urlparse(self.path).path.split("/") if part]

        def do_GET(self):
            if not self._request_allowed():
                return
            try:
                parsed = urlparse(self.path)
                parts = self._segments()
                if parsed.path == "/":
                    return self._bytes(
                        200,
                        (WEB_ROOT / "index.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                if parsed.path == "/health":
                    return self._json(200, {"ok": True, "runtime": "local-kimi"})
                if parsed.path == "/v1/capabilities":
                    return self._json(200, runtime.capabilities())
                query = parse_qs(parsed.query)
                if parsed.path == "/v1/knowledge/documents":
                    if runtime.knowledge is None:
                        raise ValueError("Knowledge ingestion is not configured")
                    return self._json(
                        200,
                        {
                            "documents": runtime.knowledge.list_documents(
                                query.get("collection", ["default"])[0]
                            )
                        },
                    )
                if parsed.path == "/v1/knowledge/search":
                    if runtime.knowledge is None:
                        raise ValueError("Knowledge ingestion is not configured")
                    return self._json(
                        200,
                        {
                            "results": runtime.knowledge.search(
                                query.get("q", [""])[0],
                                collection=query.get("collection", ["default"])[0],
                                limit=int(query.get("limit", ["5"])[0]),
                            )
                        },
                    )
                if parsed.path in {"/v1/schedules", "/v1/schedules/runs"}:
                    if runtime.scheduler is None:
                        raise ValueError("Scheduling is not configured")
                    if parsed.path.endswith("/runs"):
                        return self._json(
                            200,
                            {
                                "runs": runtime.scheduler.list_runs(
                                    query.get("job_id", [None])[0]
                                )
                            },
                        )
                    return self._json(200, {"jobs": runtime.scheduler.list_jobs()})
                if len(parts) == 3 and parts[:2] == ["v1", "schedules"]:
                    if runtime.scheduler is None:
                        raise ValueError("Scheduling is not configured")
                    job = runtime.scheduler.get_job(parts[2])
                    return (
                        self._json(200, {"job": job})
                        if job
                        else self._json(404, {"error": "schedule_not_found"})
                    )
                if len(parts) >= 3 and parts[:3] == ["v1", "sandbox", "jobs"]:
                    if runtime.sandbox is None:
                        raise ValueError("Sandbox execution is not configured")
                    if len(parts) == 3:
                        return self._json(
                            200,
                            {
                                "jobs": runtime.sandbox.list_jobs(
                                    session_id=query.get("session_id", [None])[0]
                                )
                            },
                        )
                    if len(parts) == 4:
                        job = runtime.sandbox.get_job(parts[3])
                        return (
                            self._json(200, {"job": job})
                            if job
                            else self._json(404, {"error": "sandbox_job_not_found"})
                        )
                if parsed.path == "/v1/profiles":
                    return self._json(
                        200,
                        {
                            "profiles": [
                                PROFILES[name].public_dict()
                                for name in sorted(PROFILES)
                            ]
                        },
                    )
                if parsed.path == "/v1/sessions":
                    profile = parse_qs(parsed.query).get("profile", [None])[0]
                    if profile:
                        get_profile(profile)
                    return self._json(
                        200, {"sessions": runtime.store.list_sessions(profile)}
                    )
                if len(parts) == 3 and parts[:2] == ["v1", "sessions"]:
                    session = runtime.store.get_session(parts[2])
                    if not session:
                        return self._json(404, {"error": "session_not_found"})
                    return self._json(200, {"session": session})
                if (
                    len(parts) == 4
                    and parts[:2] == ["v1", "sessions"]
                    and parts[3] == "events"
                ):
                    if not runtime.store.get_session(parts[2]):
                        return self._json(404, {"error": "session_not_found"})
                    return self._json(200, {"events": runtime.store.events(parts[2])})
                return self._json(404, {"error": "not_found"})
            except ValueError as error:
                return self._json(400, {"error": str(error)})

        def do_POST(self):
            if not self._request_allowed() or not self._require_json():
                return
            try:
                parts = self._segments()
                payload = self._body()
                if parts == ["v1", "sessions"]:
                    session = runtime.create_session(
                        payload.get("profile", "assistant-ui"),
                        payload.get("title", "New session"),
                        payload.get("knowledge_collection"),
                    )
                    return self._json(201, {"session": session})
                if parts == ["v1", "knowledge", "documents"]:
                    return self._json(201, runtime.ingest_knowledge(payload))
                if parts == ["v1", "schedules"]:
                    if runtime.scheduler is None:
                        raise ValueError("Scheduling is not configured")
                    fields = {
                        "profile",
                        "prompt",
                        "interval_seconds",
                        "run_at",
                        "title",
                        "knowledge_collection",
                    }
                    if set(payload) - fields or "prompt" not in payload:
                        raise ValueError(
                            "Provide prompt and supported schedule fields only"
                        )
                    profile = payload.get("profile", "daily-brief")
                    get_profile(profile)
                    return self._json(
                        201,
                        {
                            "job": runtime.scheduler.add_job(
                                **{**payload, "profile": profile}
                            )
                        },
                    )
                if parts == ["v1", "schedules", "tick"]:
                    if runtime.scheduler is None:
                        raise ValueError("Scheduling is not configured")
                    return self._json(200, {"runs": runtime.scheduler.run_due()})
                if (
                    len(parts) == 4
                    and parts[:2] == ["v1", "schedules"]
                    and parts[3] in {"enable", "disable"}
                ):
                    if runtime.scheduler is None:
                        raise ValueError("Scheduling is not configured")
                    runtime.scheduler.set_enabled(parts[2], parts[3] == "enable")
                    return self._json(200, {"job": runtime.scheduler.get_job(parts[2])})
                if parts == ["v1", "sandbox", "recover"]:
                    if runtime.sandbox is None:
                        raise ValueError("Sandbox execution is not configured")
                    return self._json(200, {"jobs": runtime.sandbox.recover_orphans()})
                if parts == ["v1", "sandbox", "jobs"]:
                    if runtime.sandbox is None:
                        raise ValueError("Sandbox execution is not configured")
                    if set(payload) - {"code", "language", "session_id"}:
                        raise ValueError(
                            "Provide only code, language and optional session_id"
                        )
                    if not isinstance(payload.get("language", "python"), str):
                        raise ValueError("language must be a string")
                    session_id = payload.get("session_id")
                    if session_id is not None and (
                        not isinstance(session_id, str)
                        or not runtime.store.get_session(session_id)
                    ):
                        raise ValueError("Provide an existing session_id or omit it")
                    job = runtime.sandbox.submit(
                        payload.get("code"),
                        payload.get("language", "python"),
                        session_id=session_id,
                    )
                    return self._json(201, {"job": job})
                if (
                    len(parts) == 5
                    and parts[:3] == ["v1", "sandbox", "jobs"]
                    and parts[4] == "run"
                ):
                    if runtime.sandbox is None:
                        raise ValueError("Sandbox execution is not configured")
                    return self._json(200, {"job": runtime.sandbox.run_job(parts[3])})
                if (
                    len(parts) == 4
                    and parts[:2] == ["v1", "sessions"]
                    and parts[3] == "messages"
                ):
                    event = runtime.chat(parts[2], payload.get("content", ""))
                    return self._json(200, {"event": event})
                return self._json(404, {"error": "not_found"})
            except KeyError:
                resource = (
                    "schedule"
                    if parts[:2] == ["v1", "schedules"]
                    else "sandbox_job"
                    if parts[:2] == ["v1", "sandbox"]
                    else "session"
                )
                return self._json(404, {"error": f"{resource}_not_found"})
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            except Exception as error:
                return self._json(
                    502,
                    {"error": "provider_error", "details": str(error)},
                )

        def do_DELETE(self):
            if not self._request_allowed():
                return
            parts = self._segments()
            if len(parts) == 4 and parts[:3] == ["v1", "knowledge", "documents"]:
                if runtime.knowledge is None:
                    return self._json(
                        400, {"error": "Knowledge ingestion is not configured"}
                    )
                try:
                    deleted = runtime.knowledge.delete_document(parts[3])
                except ValueError as error:
                    return self._json(400, {"error": str(error)})
                return (
                    self._json(200, {"deleted": True})
                    if deleted
                    else self._json(404, {"error": "document_not_found"})
                )
            return self._json(404, {"error": "not_found"})

        def log_message(self, format_string, *args):
            # Keep default operational logs, but never log request bodies.
            super().log_message(format_string, *args)

    return Handler


def make_server(runtime, host: str, port: int) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("The local runtime only supports loopback binding")
    server_type = ThreadingHTTPServer
    if host == "::1":

        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        server_type = IPv6Server
    return server_type((host, port), handler_for(runtime))
