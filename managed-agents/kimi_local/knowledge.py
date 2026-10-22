"""Dependency-free local knowledge ingestion and lexical retrieval."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_DOCUMENTS_PER_INGEST = 1_000
MAX_CHUNKS_PER_DOCUMENT = 4_000
CHUNK_CHARACTERS = 1_800
MAX_QUERY_CHARACTERS = 10_000
MAX_QUERY_TOKENS = 256
TOKENIZER_VERSION = "2"
SUPPORTED_SUFFIXES = {".txt", ".md", ".json", ".csv", ".html", ".htm", ".pdf"}
_WORD_RE = re.compile(r"[a-z0-9]+(?:['_-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_label(value: str, name: str, maximum: int = 1_024) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} cannot be empty")
    if "\x00" in value or len(value) > maximum:
        raise ValueError(f"Invalid {name.lower()}")
    return value


def _validate_collection(value: str) -> str:
    value = _validate_label(value, "Collection", 64)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Collection may contain only letters, numbers, '_' and '-'")
    return value


def _tokens(text: str) -> list[str]:
    normalized = text.casefold()
    tokens = _WORD_RE.findall(normalized)
    # Keep both forms so common singular/plural questions retrieve the same evidence.
    for word in list(tokens):
        if len(word) > 4 and word.endswith("ies"):
            tokens.append(word[:-3] + "y")
        elif (
            len(word) > 4
            and word.endswith("s")
            and not word.endswith(("ss", "us", "is"))
        ):
            tokens.append(word[:-1])
    for run in _CJK_RE.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[tuple[str, int, int | None]] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag.casefold() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag) -> None:
        if tag.casefold() in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data) -> None:
        if self._ignored_depth:
            return
        line = self.getpos()[0]
        for part in data.splitlines():
            cleaned = " ".join(part.split())
            if cleaned:
                self.lines.append((cleaned, line, None))


class KnowledgeStore:
    """Store documents and searchable chunks in a local SQLite database."""

    def __init__(self, database_path: str | Path):
        self.path = str(database_path)
        self._memory_connection: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._memory_connection.execute("PRAGMA foreign_keys=ON")
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    id TEXT PRIMARY KEY,
                    collection TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(collection, source)
                );
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    citation_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES knowledge_documents(id)
                        ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    search_tokens TEXT NOT NULL,
                    line_start INTEGER,
                    line_end INTEGER,
                    page INTEGER,
                    UNIQUE(document_id, ordinal)
                );
                CREATE TABLE IF NOT EXISTS knowledge_terms (
                    term TEXT NOT NULL,
                    citation_id TEXT NOT NULL REFERENCES knowledge_chunks(citation_id)
                        ON DELETE CASCADE,
                    PRIMARY KEY(term, citation_id)
                ) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS knowledge_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS knowledge_documents_collection
                    ON knowledge_documents(collection, source);
                CREATE INDEX IF NOT EXISTS knowledge_chunks_document
                    ON knowledge_chunks(document_id, ordinal);
                CREATE INDEX IF NOT EXISTS knowledge_terms_citation
                    ON knowledge_terms(citation_id);
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute(
                "SELECT value FROM knowledge_metadata WHERE key = 'tokenizer_version'"
            ).fetchone()
            if version is None or version["value"] != TOKENIZER_VERSION:
                connection.execute("DELETE FROM knowledge_terms")
                for row in connection.execute(
                    "SELECT citation_id, content FROM knowledge_chunks"
                ):
                    tokens = _tokens(row["content"])
                    connection.execute(
                        "UPDATE knowledge_chunks SET search_tokens = ? WHERE citation_id = ?",
                        (" ".join(tokens), row["citation_id"]),
                    )
                    connection.executemany(
                        "INSERT OR IGNORE INTO knowledge_terms(term, citation_id) VALUES (?, ?)",
                        ((term, row["citation_id"]) for term in set(tokens)),
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO knowledge_metadata(key, value) VALUES ('tokenizer_version', ?)",
                    (TOKENIZER_VERSION,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close(connection)

    @staticmethod
    def _document(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "collection": row["collection"],
            "source": row["source"],
            "content_hash": row["content_hash"],
            "byte_size": row["byte_size"],
            "chunk_count": row["chunk_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def ingest(self, path: str | Path, collection: str = "default") -> dict:
        collection = _validate_collection(collection)
        supplied = Path(path).expanduser()
        if supplied.is_symlink():
            raise ValueError(f"Symbolic links are not allowed: {supplied}")
        if not supplied.exists():
            raise FileNotFoundError(supplied)

        is_directory = supplied.is_dir()
        if supplied.is_file():
            paths = [supplied]
            root = supplied.parent.resolve()
        elif supplied.is_dir():
            root = supplied.resolve()
            paths = []
            for candidate in sorted(supplied.rglob("*")):
                if candidate.is_symlink():
                    continue
                if (
                    candidate.is_file()
                    and candidate.suffix.casefold() in SUPPORTED_SUFFIXES
                ):
                    paths.append(candidate)
                    if len(paths) > MAX_DOCUMENTS_PER_INGEST:
                        raise ValueError(
                            f"Directory exceeds the {MAX_DOCUMENTS_PER_INGEST}-document limit"
                        )
        else:
            raise ValueError(f"Path is not a regular file or directory: {supplied}")

        results = []
        errors = []
        for candidate in paths:
            try:
                resolved = candidate.resolve()
                resolved.relative_to(root)
                results.append(self._ingest_file(resolved, collection))
            except (OSError, RuntimeError, ValueError) as error:
                if not is_directory:
                    raise
                errors.append(
                    {
                        "source": str(candidate),
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                )

        return {
            "collection": collection,
            "documents": [result["document"] for result in results],
            "created": sum(result["status"] == "created" for result in results),
            "updated": sum(result["status"] == "updated" for result in results),
            "unchanged": sum(result["status"] == "unchanged" for result in results),
            "failed": len(errors),
            "errors": errors,
        }

    def _ingest_file(self, path: Path, collection: str) -> dict:
        suffix = path.suffix.casefold()
        if suffix not in SUPPORTED_SUFFIXES:
            raise ValueError(f"Unsupported file type: {suffix or '<none>'}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte limit: {path}")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds the {MAX_FILE_BYTES}-byte limit: {path}")
        content_hash = hashlib.sha256(raw).hexdigest()

        if suffix == ".pdf":
            entries = self._read_pdf(raw, path)
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as error:
                raise ValueError(f"File is not valid UTF-8: {path}") from error
            entries = self._read_text_type(text, suffix, path)
        chunks = self._chunk(entries)
        return self._store_document(
            str(path), collection, content_hash, len(raw), chunks
        )

    def ingest_text(self, text: str, source: str, collection: str = "default") -> dict:
        if not isinstance(text, str):
            raise ValueError("Text must be a string")
        source = _validate_label(source, "Source", 2_048)
        collection = _validate_collection(collection)
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_TEXT_BYTES:
            raise ValueError(f"Text exceeds the {MAX_TEXT_BYTES}-byte limit")
        entries = [
            (line, number, None) for number, line in enumerate(text.splitlines(), 1)
        ]
        chunks = self._chunk(entries)
        return self._store_document(
            source,
            collection,
            hashlib.sha256(encoded).hexdigest(),
            len(encoded),
            chunks,
        )

    @staticmethod
    def _read_text_type(
        text: str, suffix: str, path: Path
    ) -> list[tuple[str, int, int | None]]:
        if suffix == ".json":
            try:
                json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid JSON file {path}: {error.msg}") from error
        elif suffix == ".csv":
            try:
                list(csv.reader(io.StringIO(text), strict=True))
            except csv.Error as error:
                raise ValueError(f"Invalid CSV file {path}: {error}") from error
        elif suffix in {".html", ".htm"}:
            parser = _TextHTMLParser()
            parser.feed(text)
            parser.close()
            return parser.lines
        return [
            (line, number, None) for number, line in enumerate(text.splitlines(), 1)
        ]

    @staticmethod
    def _read_pdf(raw: bytes, path: Path) -> list[tuple[str, int, int | None]]:
        executable = shutil.which("pdftotext")
        if not executable:
            raise RuntimeError(
                "PDF ingestion requires the optional 'pdftotext' executable"
            )
        try:
            completed = subprocess.run(
                [executable, "-layout", "-", "-"],
                input=raw,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"Unable to extract PDF text: {path}") from error
        try:
            text = completed.stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"pdftotext returned invalid UTF-8 for {path}") from error
        entries = []
        for page_number, page in enumerate(text.split("\f"), 1):
            entries.extend(
                (line, number, page_number)
                for number, line in enumerate(page.splitlines(), 1)
            )
        return entries

    @staticmethod
    def _chunk(entries: list[tuple[str, int, int | None]]) -> list[dict]:
        chunks: list[dict] = []
        parts: list[str] = []
        start = end = None
        current_page = None
        length = 0

        def flush() -> None:
            nonlocal parts, start, end, length
            content = "\n".join(parts).strip()
            if content:
                chunks.append(
                    {
                        "content": content,
                        "line_start": start,
                        "line_end": end,
                        "page": current_page,
                    }
                )
            parts, start, end, length = [], None, None, 0

        for raw_line, line_number, page in entries:
            line = raw_line.strip()
            if not line:
                if parts and length >= CHUNK_CHARACTERS // 2:
                    flush()
                continue
            segments = [
                line[index : index + CHUNK_CHARACTERS]
                for index in range(0, len(line), CHUNK_CHARACTERS)
            ]
            for segment in segments:
                if parts and (
                    page != current_page or length + len(segment) + 1 > CHUNK_CHARACTERS
                ):
                    flush()
                if not parts:
                    start = line_number
                    current_page = page
                parts.append(segment)
                end = line_number
                length += len(segment) + 1
                if len(chunks) >= MAX_CHUNKS_PER_DOCUMENT:
                    raise ValueError(
                        f"Document exceeds the {MAX_CHUNKS_PER_DOCUMENT}-chunk limit"
                    )
        flush()
        if len(chunks) > MAX_CHUNKS_PER_DOCUMENT:
            raise ValueError(
                f"Document exceeds the {MAX_CHUNKS_PER_DOCUMENT}-chunk limit"
            )
        return chunks

    def _store_document(
        self,
        source: str,
        collection: str,
        content_hash: str,
        byte_size: int,
        chunks: list[dict],
    ) -> dict:
        identity = hashlib.sha256(f"{collection}\0{source}".encode()).hexdigest()[:32]
        document_id = f"kd_{identity}"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM knowledge_documents WHERE collection = ? AND source = ?",
                (collection, source),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                connection.commit()
                return {"status": "unchanged", "document": self._document(existing)}

            timestamp = _now()
            created_at = existing["created_at"] if existing else timestamp
            if existing:
                connection.execute(
                    "DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,)
                )
                status = "updated"
            else:
                status = "created"
            connection.execute(
                """
                INSERT INTO knowledge_documents(
                    id, collection, source, content_hash, byte_size, chunk_count,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    byte_size = excluded.byte_size,
                    chunk_count = excluded.chunk_count,
                    updated_at = excluded.updated_at
                """,
                (
                    document_id,
                    collection,
                    source,
                    content_hash,
                    byte_size,
                    len(chunks),
                    created_at,
                    timestamp,
                ),
            )
            for ordinal, chunk in enumerate(chunks, 1):
                citation_id = f"{document_id}:{content_hash[:12]}:{ordinal:04d}"
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        citation_id, document_id, ordinal, content, search_tokens,
                        line_start, line_end, page
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        citation_id,
                        document_id,
                        ordinal,
                        chunk["content"],
                        " ".join(_tokens(chunk["content"])),
                        chunk["line_start"],
                        chunk["line_end"],
                        chunk["page"],
                    ),
                )
                connection.executemany(
                    "INSERT INTO knowledge_terms(term, citation_id) VALUES (?, ?)",
                    ((term, citation_id) for term in set(_tokens(chunk["content"]))),
                )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE id = ?", (document_id,)
            ).fetchone()
            return {"status": status, "document": self._document(row)}
        except Exception:
            connection.rollback()
            raise
        finally:
            self._close(connection)

    def search(
        self, query: str, collection: str = "default", limit: int = 5
    ) -> list[dict]:
        query = _validate_label(query, "Query", MAX_QUERY_CHARACTERS)
        collection = _validate_collection(collection)
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("Limit must be an integer between 1 and 100")
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        unique_query = set(query_tokens)
        if len(unique_query) > MAX_QUERY_TOKENS:
            raise ValueError(f"Query exceeds the {MAX_QUERY_TOKENS}-term search limit")

        connection = self._connect()
        try:
            ordered_query = sorted(unique_query)
            placeholders = ",".join("?" for _ in ordered_query)
            rows = connection.execute(
                f"""
                SELECT c.*, d.collection, d.source, d.content_hash
                FROM knowledge_terms t
                JOIN knowledge_chunks c ON c.citation_id = t.citation_id
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE d.collection = ? AND t.term IN ({placeholders})
                GROUP BY c.citation_id
                ORDER BY d.source, c.ordinal
                """,
                (collection, *ordered_query),
            ).fetchall()
            total = connection.execute(
                """
                SELECT COUNT(*)
                FROM knowledge_chunks c
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE d.collection = ?
                """,
                (collection,),
            ).fetchone()[0]
            frequency_rows = connection.execute(
                f"""
                SELECT t.term, COUNT(*) AS frequency
                FROM knowledge_terms t
                JOIN knowledge_chunks c ON c.citation_id = t.citation_id
                JOIN knowledge_documents d ON d.id = c.document_id
                WHERE d.collection = ? AND t.term IN ({placeholders})
                GROUP BY t.term
                """,
                (collection, *ordered_query),
            ).fetchall()
        finally:
            self._close(connection)

        document_frequency = {row["term"]: row["frequency"] for row in frequency_rows}
        row_tokens = []
        for row in rows:
            tokens = row["search_tokens"].split()
            row_tokens.append(tokens)

        scored = []
        total = max(total, 1)
        normalized_query = " ".join(query.casefold().split())
        for row, tokens in zip(rows, row_tokens):
            counts = {token: tokens.count(token) for token in unique_query}
            matched = sum(count > 0 for count in counts.values())
            if not matched:
                continue
            score = 0.0
            for token, count in counts.items():
                if count:
                    inverse_frequency = (
                        math.log((total + 1) / (document_frequency.get(token, 0) + 1))
                        + 1
                    )
                    score += (1 + math.log(count)) * inverse_frequency
            score *= 1 + matched / len(unique_query)
            if normalized_query in " ".join(row["content"].casefold().split()):
                score += 3.0
            scored.append((score, row))

        scored.sort(key=lambda item: (-item[0], item[1]["citation_id"]))
        return [
            {
                "citation_id": row["citation_id"],
                "document_id": row["document_id"],
                "collection": row["collection"],
                "source": row["source"],
                "content_hash": row["content_hash"],
                "content": row["content"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "page": row["page"],
                "score": round(score, 6),
            }
            for score, row in scored[:limit]
        ]

    def list_documents(self, collection: str = "default") -> list[dict]:
        collection = _validate_collection(collection)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE collection = ? ORDER BY source
                """,
                (collection,),
            ).fetchall()
            return [self._document(row) for row in rows]
        finally:
            self._close(connection)

    def delete_document(self, document_id: str) -> bool:
        document_id = _validate_label(document_id, "Document ID", 200)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "DELETE FROM knowledge_documents WHERE id = ?", (document_id,)
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            self._close(connection)
