"""Profile-aware local conversation runtime."""

import threading

from .profiles import get_profile


class LocalRuntime:
    def __init__(self, store, provider_factory):
        self.store = store
        self.provider_factory = provider_factory
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(session_id, threading.Lock())

    def create_session(self, profile_name: str, title: str = "New session") -> dict:
        get_profile(profile_name)
        if not isinstance(title, str):
            raise ValueError("Session title must be a string")
        return self.store.create_session(profile_name, title)

    def chat(self, session_id: str, content: str) -> dict:
        if not isinstance(content, str):
            raise ValueError("Message content must be a string")
        with self._lock_for(session_id):
            return self._chat_locked(session_id, content)

    def _chat_locked(self, session_id: str, content: str) -> dict:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be empty")
        if len(content) > 100_000:
            raise ValueError("Message content exceeds the 100000-character limit")

        session = self.store.get_session(session_id)
        if not session:
            raise KeyError(session_id)
        profile = get_profile(session["profile"])

        self.store.add_event(session_id, "message", "user", content)
        self.store.set_status(session_id, "running")
        try:
            provider = self.provider_factory()
            reply = provider.complete(
                (
                    "You are running in kimi-quickstarts' local session service. "
                    "You have no hosted agent, sandbox, vault, scheduler, OAuth, web, "
                    "or external messaging access. Never claim an external action ran. "
                    + profile.instructions
                ),
                self.store.messages(session_id),
            )
            event = self.store.add_event(
                session_id,
                "message",
                "assistant",
                reply,
                {"profile": profile.name},
            )
            self.store.set_status(session_id, "idle")
            return event
        except Exception as error:
            self.store.add_event(
                session_id,
                "error",
                metadata={"error_type": type(error).__name__, "message": str(error)},
            )
            self.store.set_status(session_id, "error")
            raise
