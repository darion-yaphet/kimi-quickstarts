"""Tests for local knowledge ingestion and lexical retrieval."""

import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kimi_local.knowledge import KnowledgeStore


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / "knowledge.db"
        self.store = KnowledgeStore(self.database)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_ingest_directory_searches_english_and_chinese_with_provenance(self):
        documents = self.root / "documents"
        documents.mkdir()
        (documents / "policy.md").write_text(
            "Refund policy\nCustomers can request a refund within thirty days.\n",
            encoding="utf-8",
        )
        (documents / "shipping.txt").write_text(
            "配送说明\n上海地区支持次日送达。\n", encoding="utf-8"
        )
        (documents / "ignored.bin").write_bytes(b"refund")

        result = self.store.ingest(documents, collection="support")

        self.assertEqual(
            (result["created"], result["updated"], result["unchanged"]), (2, 0, 0)
        )
        english = self.store.search("refund policy", collection="support")
        chinese = self.store.search("上海次日送达", collection="support")
        self.assertEqual(len(english), 1)
        self.assertIn("thirty days", english[0]["content"])
        self.assertEqual(english[0]["line_start"], 1)
        self.assertEqual(english[0]["line_end"], 2)
        self.assertIsNone(english[0]["page"])
        self.assertTrue(english[0]["citation_id"].startswith(english[0]["document_id"]))
        self.assertIn("上海地区", chinese[0]["content"])

    def test_directory_ingest_reports_partial_success_and_per_file_errors(self):
        documents = self.root / "batch"
        documents.mkdir()
        (documents / "a-valid.txt").write_text(
            "first searchable record", encoding="utf-8"
        )
        (documents / "b-invalid.json").write_text('{"broken":', encoding="utf-8")
        (documents / "c-valid.md").write_text(
            "second searchable record", encoding="utf-8"
        )

        result = self.store.ingest(documents, collection="batch")

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(result["documents"]), 2)
        self.assertEqual(result["errors"][0]["error_type"], "ValueError")
        self.assertTrue(result["errors"][0]["source"].endswith("b-invalid.json"))
        self.assertIn("Invalid JSON", result["errors"][0]["error"])
        self.assertEqual(len(self.store.list_documents("batch")), 2)
        self.assertTrue(self.store.search("second searchable", "batch"))

    def test_idempotent_update_replaces_old_chunks_and_survives_restart(self):
        source = "handbook://returns"
        first = self.store.ingest_text(
            "Old process: request a paper form.", source, collection="manual"
        )
        unchanged = self.store.ingest_text(
            "Old process: request a paper form.", source, collection="manual"
        )
        updated = self.store.ingest_text(
            "New process: submit the online portal form.", source, collection="manual"
        )

        self.assertEqual(first["status"], "created")
        self.assertEqual(unchanged["status"], "unchanged")
        self.assertEqual(updated["status"], "updated")
        self.assertEqual(first["document"]["id"], updated["document"]["id"])
        self.assertEqual(
            first["document"]["created_at"], updated["document"]["created_at"]
        )
        self.assertNotEqual(
            first["document"]["content_hash"], updated["document"]["content_hash"]
        )
        self.assertEqual(self.store.search("paper", collection="manual"), [])

        reopened = KnowledgeStore(self.database)
        found = reopened.search("online portal", collection="manual")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], source)
        self.assertEqual(found[0]["content_hash"], updated["document"]["content_hash"])

    def test_replacement_changes_versioned_citation_and_provenance_snapshot(self):
        source = "guide://account"
        first = self.store.ingest_text("heading\nold snapshot", source)
        first_result = self.store.search("old snapshot")[0]

        updated = self.store.ingest_text("heading\nnew snapshot\nlast line", source)
        new_result = self.store.search("new snapshot")[0]

        self.assertEqual(first["document"]["id"], updated["document"]["id"])
        self.assertNotEqual(first_result["citation_id"], new_result["citation_id"])
        self.assertIn(
            updated["document"]["content_hash"][:12], new_result["citation_id"]
        )
        self.assertEqual(
            new_result["content_hash"], updated["document"]["content_hash"]
        )
        self.assertEqual((new_result["line_start"], new_result["line_end"]), (1, 3))
        self.assertEqual(self.store.search("old"), [])

    def test_common_plural_forms_match_singular_queries(self):
        self.store.ingest_text("Refunds follow our policies.", "policy.md")
        self.assertTrue(self.store.search("refund policy"))

    def test_legacy_index_is_retokenized_even_when_content_is_unchanged(self):
        result = self.store.ingest_text("Refunds follow our policies.", "policy.md")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("DELETE FROM knowledge_metadata")
            connection.execute("DELETE FROM knowledge_terms")
            connection.execute(
                "UPDATE knowledge_chunks SET search_tokens = 'refunds follow our policies'"
            )
        reopened = KnowledgeStore(self.database)
        self.assertTrue(reopened.search("refund policy"))
        self.assertEqual(
            reopened.ingest_text("Refunds follow our policies.", "policy.md")["status"],
            "unchanged",
        )
        self.assertEqual(
            reopened.search("refund policy")[0]["document_id"], result["document"]["id"]
        )

    def test_indexed_search_finds_late_source_and_bounds_query_terms(self):
        for number in range(40):
            self.store.ingest_text("ordinary content", f"source-{number:03d}", "large")
        self.store.ingest_text("uniquelateterm evidence", "zzzz-last-source", "large")

        result = self.store.search("uniquelateterm", "large")
        self.assertEqual([item["source"] for item in result], ["zzzz-last-source"])
        oversized_query = " ".join(f"term{number}" for number in range(257))
        with self.assertRaisesRegex(ValueError, "256-term"):
            self.store.search(oversized_query, "large")

    def test_collections_are_isolated_and_delete_cascades(self):
        visible = self.store.ingest_text("shared secret phrase", "one", "alpha")
        self.store.ingest_text("shared secret phrase", "two", "beta")

        alpha = self.store.search("secret phrase", "alpha")
        self.assertEqual([item["source"] for item in alpha], ["one"])
        self.assertEqual(
            [item["source"] for item in self.store.search("secret phrase", "beta")],
            ["two"],
        )
        self.assertTrue(self.store.delete_document(visible["document"]["id"]))
        self.assertFalse(self.store.delete_document(visible["document"]["id"]))
        self.assertEqual(self.store.search("secret phrase", "alpha"), [])
        self.assertEqual(self.store.list_documents("alpha"), [])
        self.assertEqual(len(self.store.list_documents("beta")), 1)

    def test_html_omits_scripts_and_preserves_source_line(self):
        page = self.root / "guide.html"
        page.write_text(
            "<html>\n<body>\n<h1>Account guide</h1>\n"
            "<script>private_token_should_not_index</script>\n"
            "<p>Reset your password from Settings.</p>\n</body>\n</html>",
            encoding="utf-8",
        )
        self.store.ingest(page)

        result = self.store.search("reset password")
        self.assertEqual(len(result), 1)
        self.assertNotIn("private_token", result[0]["content"])
        self.assertEqual(result[0]["line_start"], 3)
        self.assertEqual(result[0]["line_end"], 5)

    def test_invalid_inputs_and_symlinks_are_rejected(self):
        unsupported = self.root / "file.exe"
        unsupported.write_text("content", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported file type"):
            self.store.ingest(unsupported)
        with self.assertRaises(FileNotFoundError):
            self.store.ingest(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "Collection"):
            self.store.ingest_text("hello", "source", "invalid collection")
        with self.assertRaisesRegex(ValueError, "Limit"):
            self.store.search("hello", limit=0)

        target = self.root / "target.txt"
        target.write_text("outside", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("Symbolic links are unavailable")
        with self.assertRaisesRegex(ValueError, "Symbolic links"):
            self.store.ingest(link)

    def test_pdf_has_a_clear_optional_tool_error(self):
        document = self.root / "manual.pdf"
        document.write_bytes(b"%PDF-1.4\n")
        with patch("kimi_local.knowledge.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "requires.*pdftotext"):
                self.store.ingest(document)

    def test_pdf_extraction_uses_the_hashed_byte_snapshot(self):
        document = self.root / "manual.pdf"
        raw = b"%PDF captured snapshot"
        document.write_bytes(raw)
        completed = SimpleNamespace(stdout=b"first page\fsecond page")
        with patch("kimi_local.knowledge.shutil.which", return_value="/bin/pdftotext"):
            with patch(
                "kimi_local.knowledge.subprocess.run", return_value=completed
            ) as run:
                result = self.store.ingest(document)

        run.assert_called_once_with(
            ["/bin/pdftotext", "-layout", "-", "-"],
            input=raw,
            check=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result["documents"][0]["byte_size"], len(raw))
        pages = self.store.search("second page")
        self.assertEqual(pages[0]["page"], 2)


if __name__ == "__main__":
    unittest.main()
