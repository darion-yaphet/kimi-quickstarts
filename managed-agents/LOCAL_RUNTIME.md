# Local knowledge, scheduling and Python execution

The Kimi runtime implements these services locally with SQLite and Docker.
It does not call Anthropic-hosted Agents, Sessions, Dreams, Vaults or cron APIs.
Existing sessions are migrated without removing their messages.

## Start

From the repository root, configure `.env.kimi.local` using `.env.kimi.example`,
then run:

```bash
./scripts/kimi-run managed-agents python -m pip install -r requirements.txt
./scripts/kimi-run managed-agents python kimi.py --serve
```

Open `http://127.0.0.1:8765/`. The tabs expose chat, knowledge, schedules and
Python jobs. The service is loopback-only; the old unauthenticated
`--allow-remote` mode has been removed. State defaults to
`managed-agents/.kimi/sessions.db`. Set `KIMI_SESSIONS_DB` or `--db` to use a
different SQLite file. The `.kimi` directory is ignored by Git.

## Knowledge ingestion and answers

```bash
# These commands run inside managed-agents, with the Kimi environment loaded.
python kimi.py knowledge ingest knowledge
python kimi.py knowledge search '退款期限'
python kimi.py --profile knowledge-wiki --message '演示退款期限是多少？请引用来源。'

# A separate collection can be attached to any newly created session.
python kimi.py knowledge --collection team ingest /path/to/team-documents
python kimi.py --profile daily-brief --knowledge-collection team --message 'Summarize the release notes'
```

Files supported: UTF-8 TXT, Markdown, JSON, CSV and HTML. PDF extraction uses
`pdftotext` when installed; otherwise PDF ingestion returns an explicit missing
tool error. No embedding service or vector-database account is needed. Search
uses a local inverted term index with English words and Chinese character/
bigram tokens; it is lexical retrieval, not semantic embeddings or a hosted
Dreams pipeline.

The source SHA-256 makes ingestion idempotent. Changed content replaces that
source's chunks and search terms transactionally. Citation IDs contain the
document ID, version hash and chunk number. Retrieval returns source labels,
line numbers or PDF page numbers. Chat injects matching excerpts into Kimi and
persists the exact retrieved evidence in the assistant event; the page displays
the citations. Document text is treated as untrusted data, not instructions.
Chat uses a bounded retrieval query from the start of the user message while
sending the complete message to Kimi.

Browser uploads accept text files up to 750 KB. Local CLI ingestion accepts up
to 10 MiB per file, 5 MiB of extracted text, 1,000 documents per directory and
4,000 chunks per document. Direct search accepts at most 256 unique query terms.
Directory results include per-file `errors` and a `failed` count: valid files
remain indexed if another file fails. The CLI exits nonzero on partial failure.

Server-side path ingestion is restricted to `KIMI_DOCUMENT_ROOT` (default
`managed-agents/knowledge`). Uploaded text does not require a host path. CLI
paths are explicitly chosen by the caller. Sources/collections remain separate.

## Persistent scheduling

```bash
# First execution is due immediately; repeat every day while a worker is running.
python kimi.py schedule add --profile daily-brief --collection default \
  --prompt 'Summarize the ingested release notes and cite sources' --every 86400

# One-time execution; timestamp must include a timezone.
python kimi.py schedule add --profile daily-brief --prompt 'Prepare the brief' \
  --at '2030-01-02T09:00:00+08:00'

python kimi.py schedule list
python kimi.py schedule runs
python kimi.py schedule disable JOB_ID
python kimi.py schedule enable JOB_ID
python kimi.py schedule tick
python kimi.py worker
```

`--serve` runs the scheduler automatically. `--no-scheduler` serves the API
without executing due schedules; `worker` can run separately against the same
database. A worker process must remain running. A recurring interval is between
1 and 31,536,000 seconds; this implementation uses intervals and one-time
timestamps rather than five-field cron expressions. The browser converts the
operator's local date/time to UTC for storage.

SQLite atomic claims and renewable leases prevent ordinary concurrent workers
from claiming the same due slot. Failed attempts are recorded with bounded
exponential backoff; a failure after session creation retains that session ID.
After a crash, expired leases can be reclaimed and the abandoned attempt is
recorded as failed. Recovery is **at-least-once**, not exactly-once. Recurring
jobs skip missed slots rather than replaying an unbounded backlog. A completed
one-time job must be recreated with a new time rather than simply re-enabled.

Schedules create local Kimi sessions and save their replies. They do not post
to Slack, change Linear issues, read Sentry or fetch external data. Those
connectors remain separate integrations.

## Docker sandbox

Prepare Docker and the fixed image once:

```bash
docker pull python:3.12-alpine
python kimi.py sandbox capabilities
python kimi.py sandbox run --code 'print(sum(range(10)))'
python kimi.py sandbox run --file /path/to/script.py
python kimi.py sandbox list
python kimi.py sandbox get JOB_ID
python kimi.py sandbox run --job JOB_ID
python kimi.py sandbox recover
```

When Docker and the configured image are available, the `self-hosted-sandboxes`
profile exposes an ordinary `run_python` tool to Kimi. Other profiles do not
gain code execution automatically. The browser can also queue and explicitly
run Python jobs. Results include the job ID, status, exit code, bounded stdout/
stderr, errors and per-attempt audit history.

Execution uses a non-root user, no container network, a read-only root,
bounded tmpfs, dropped capabilities, `no-new-privileges`, CPU/memory/process
limits and time/output/source caps. There are no host mounts, forwarded Kimi
credentials or host-Python fallback. Defaults are 10 seconds, 64 KiB output,
20,000 source characters, 128 MiB memory, 0.5 CPU and 64 processes. Use
`KIMI_SANDBOX_IMAGE` (a prepared tag or digest) and `KIMI_SANDBOX_TIMEOUT` for
operator configuration. Images are never pulled automatically during a job.
See the [Docker run reference](https://docs.docker.com/reference/cli/docker/container/run/)
for the underlying container controls.

Only the exact randomly named container with matching job/attempt/owner labels
can be removed. A cleanup that cannot be confirmed is recorded as
`cleanup_failed`, not as successful termination. Execution leases and orphan
reconciliation support recovery after worker crashes; recovery never touches a
live leased job or an unrelated container. A constant Python supervisor inside
the container independently enforces the time limit if its controller dies.
Docker being unavailable is a
reported backend state, not a reason to execute code on the host.

## HTTP endpoints

All POSTs require JSON. The API rejects foreign origins and non-loopback Host
headers; it has no remote authentication and therefore does not bind remotely.

| Method | Endpoint | Input / result |
| --- | --- | --- |
| GET | `/v1/capabilities` | Local services and actual Docker availability |
| GET / POST | `/v1/knowledge/documents` | List by `?collection=`; ingest `{text, source, collection}` or rooted `{path, collection}` |
| GET | `/v1/knowledge/search?q=...&collection=default` | Ranked chunks with citations |
| DELETE | `/v1/knowledge/documents/{id}` | Delete that document and its chunks |
| POST | `/v1/sessions` | Optional `knowledge_collection`; `knowledge-wiki` defaults to `default` |
| GET / POST | `/v1/schedules` | List or create `{profile, prompt, interval_seconds/run_at, knowledge_collection}` |
| GET | `/v1/schedules/{id}` | Schedule details |
| POST | `/v1/schedules/{id}/enable` or `/disable` | `{}` |
| POST | `/v1/schedules/tick` | Execute due jobs once |
| GET | `/v1/schedules/runs?job_id=...` | Persisted run history |
| GET / POST | `/v1/sandbox/jobs` | List or submit `{code, language:"python", session_id}` |
| GET | `/v1/sandbox/jobs/{id}` | Job and per-attempt audit |
| POST | `/v1/sandbox/jobs/{id}/run` | Execute a queued or eligible retry job |
| POST | `/v1/sandbox/recover` | Reconcile expired/unconfirmed owned containers |

## Verification

```bash
python -m unittest discover -s tests -v
KIMI_TEST_DOCKER=1 python -m unittest tests.test_sandbox -v
```

The ordinary suite uses deterministic providers and Docker process mocks. The
Docker opt-in test runs real containers after the image is prepared; CI has a
dedicated Docker job. No Kimi key is copied into CI. Live model validation uses
the operator's ignored local environment only.
