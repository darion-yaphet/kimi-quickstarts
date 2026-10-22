"""Small loopback HTTP API for the local session runtime."""

import json
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


def handler_for(runtime, loopback_only: bool = True):
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
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
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
                payload = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("Request body must be valid JSON") from error
            if not isinstance(payload, dict):
                raise ValueError("Request body must be a JSON object")
            return payload

        def _request_allowed(self) -> bool:
            if not loopback_only:
                return True

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
                    or parsed.scheme not in {"http", "https"}
                    or parsed.hostname not in LOOPBACK_HOSTS
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
                if parsed.path == "/v1/profiles":
                    return self._json(
                        200,
                        {"profiles": [PROFILES[name].public_dict() for name in sorted(PROFILES)]},
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
                    return self._json(
                        200, {"events": runtime.store.events(parts[2])}
                    )
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
                    )
                    return self._json(201, {"session": session})
                if (
                    len(parts) == 4
                    and parts[:2] == ["v1", "sessions"]
                    and parts[3] == "messages"
                ):
                    event = runtime.chat(parts[2], payload.get("content", ""))
                    return self._json(200, {"event": event})
                return self._json(404, {"error": "not_found"})
            except KeyError:
                return self._json(404, {"error": "session_not_found"})
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            except Exception as error:
                return self._json(
                    502,
                    {"error": "provider_error", "details": str(error)},
                )

        def log_message(self, format_string, *args):
            # Keep default operational logs, but never log request bodies.
            super().log_message(format_string, *args)

    return Handler


def make_server(runtime, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        (host, port), handler_for(runtime, loopback_only=host in LOOPBACK_HOSTS)
    )
