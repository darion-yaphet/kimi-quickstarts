"""Exercise offline examples at the CLI, persistence and HTTP boundaries."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from kimi import _runtime, main, worker
from kimi_local.http_api import make_server
from kimi_local.profiles import PROFILES
from kimi_local.provider import KimiProvider


class DemoWorkflowTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to evaluate browser JavaScript")
    def test_browser_initialization_skips_demo_sandbox_and_disables_controls(self):
        page = (Path(__file__).parents[1] / "kimi_local" / "web" / "index.html").read_text()
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r'''
const vm = require('node:vm');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const nodes = new Map();
const requested = [];
function node() {
  return { value: 'default', textContent: '', disabled: false,
           addEventListener() {}, append() {}, replaceChildren() {} };
}
const document = {
  querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, node());
    return nodes.get(selector);
  },
  querySelectorAll() { return []; },
  createElement: node,
};
const context = {
  document,
  async fetch(path) {
    requested.push(path);
    if (path.startsWith('/v1/sandbox')) throw Error('Unexpected sandbox request');
    const payload = path === '/v1/capabilities'
      ? { mode: 'demo', sandbox: { available: false } }
      : { profiles: [], sessions: [], documents: [], jobs: [] };
    return { ok: true, async json() { return payload; } };
  },
};
const pending = vm.runInNewContext(fs.readFileSync(0, 'utf8'), context);
Promise.resolve(pending).then(() => {
  assert.equal(nodes.get('#notice').textContent, '');
  assert.match(nodes.get('header h1').textContent, /Offline Demo/);
  for (const id of ['submit', 'refresh', 'recover', 'code']) {
    assert.equal(nodes.get('#sandbox-' + id).disabled, true);
  }
  assert(requested.includes('/v1/schedules'));
  assert(requested.some(path => path.startsWith('/v1/knowledge/documents')));
}).catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(
            [shutil.which("node"), "-e", harness], input=script,
            text=True, capture_output=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_runs_every_profile_without_credentials_network_or_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "demo.db"
            output = Path(directory) / "results.json"
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("socket.create_connection", side_effect=AssertionError("network")),
                patch("subprocess.Popen", side_effect=AssertionError("subprocess")),
                patch.object(KimiProvider, "__init__", side_effect=AssertionError("live model")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(["--db", str(database), "demo", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["mode"], "demo")
            self.assertEqual({case["profile"] for case in report["scenarios"]}, set(PROFILES))
            for case in report["scenarios"]:
                self.assertIn("OFFLINE DEMO", case["reply"])
                self.assertEqual(case["delivery"], "simulated_local_output_only")
            knowledge = next(case for case in report["scenarios"] if case["profile"] == "knowledge-wiki")
            self.assertTrue(knowledge["sources"])
            self.assertIn("十四天", knowledge["reply"])
            self.assertIn(knowledge["sources"][0]["citation_id"], knowledge["reply"])
            self.assertEqual(report["schedule"]["runs"][0]["status"], "succeeded")
            self.assertFalse(report["sandbox"]["available"])
            # Reopening preserves each session and its actual event history.
            runtime = _runtime(str(database), demo=True)
            self.assertEqual(len(runtime.store.list_sessions()), 13)
            self.assertEqual(len(runtime.store.events(knowledge["session_id"])), 2)

    def test_demo_defaults_to_separate_database_even_with_live_environment(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"KIMI_SESSIONS_DB": "/unused/live.db"}),
            patch("kimi.DEMO_DB", Path(directory) / "demo.db"),
            contextlib.redirect_stdout(io.StringIO()) as captured,
        ):
            self.assertEqual(main(["--demo", "--message", "hello"]), 0)
            self.assertIn("OFFLINE DEMO", captured.getvalue())
            self.assertTrue((Path(directory) / "demo.db").exists())

    def test_demo_rejects_execution_and_worker_does_not_probe_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "demo.db")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--demo", "--db", database, "sandbox", "run", "--code", "print(2)"]), 1)
            runtime = _runtime(database, demo=True)
            stop = threading.Event()
            stop.set()
            with patch("subprocess.Popen", side_effect=AssertionError("Docker")):
                worker(runtime, stop, 0.1)
                self.assertFalse(runtime.capabilities()["sandbox"]["available"])

    def test_http_exposes_demo_mode_and_returns_simulated_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _runtime(str(Path(directory) / "demo.db"), demo=True)
            server = make_server(runtime, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                with urlopen(base + "/v1/capabilities", timeout=5) as response:
                    self.assertEqual(json.load(response)["mode"], "demo")
                session = runtime.create_session("slack")
                request = Request(
                    base + f"/v1/sessions/{session['id']}/messages",
                    data=json.dumps({"content": "hello"}).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=5) as response:
                    self.assertIn("OFFLINE DEMO", response.read().decode())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
