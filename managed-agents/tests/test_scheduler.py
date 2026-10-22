"""Tests for the persistent local scheduler."""

import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kimi_local.scheduler import Scheduler


UTC = timezone.utc


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "scheduler.db"
        self.clock = MutableClock(datetime(2026, 1, 2, 3, 4, tzinfo=UTC))
        self.calls = []

        def runner(job):
            self.calls.append(job)
            return {"answer": job["prompt"]}

        self.scheduler = Scheduler(self.database, runner, clock=self.clock)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_one_shot_persists_and_runs_once(self):
        due = self.clock.value + timedelta(minutes=1)
        job = self.scheduler.add_job(
            "daily-brief",
            "Prepare the brief",
            run_at=due.isoformat(),
            title="Morning brief",
            knowledge_collection="team-notes",
        )
        self.assertEqual(job["run_at"], due.isoformat())
        self.assertEqual(job["next_run_at"], due.isoformat())
        self.assertTrue(job["enabled"])
        self.assertEqual(self.scheduler.run_due(), [])

        self.clock.value = due
        runs = self.scheduler.run_due()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "succeeded")
        self.assertEqual(runs[0]["result"], {"answer": "Prepare the brief"})
        self.assertEqual(self.calls[0]["_run_id"], runs[0]["id"])
        self.assertFalse(self.scheduler.get_job(job["id"])["enabled"])
        self.assertIsNone(self.scheduler.get_job(job["id"])["next_run_at"])
        self.assertEqual(self.scheduler.run_due(), [])

        reopened = Scheduler(self.database, lambda job: {})
        self.assertEqual(reopened.get_job(job["id"])["title"], "Morning brief")
        self.assertEqual(
            reopened.list_runs(job["id"])[0]["result"]["answer"], "Prepare the brief"
        )

    def test_recurring_job_uses_fixed_interval_and_skips_missed_slots(self):
        job = self.scheduler.add_job("sentry", "Triage", interval_seconds=60)
        self.assertEqual(job["next_run_at"], self.clock.value.isoformat())

        first = self.scheduler.run_due()
        self.assertEqual(first[0]["status"], "succeeded")
        self.assertEqual(
            self.scheduler.get_job(job["id"])["next_run_at"],
            (self.clock.value + timedelta(seconds=60)).isoformat(),
        )

        self.clock.value += timedelta(seconds=185)
        self.scheduler.run_due()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            self.scheduler.get_job(job["id"])["next_run_at"],
            (datetime(2026, 1, 2, 3, 8, tzinfo=UTC)).isoformat(),
        )

    def test_invalid_intervals_and_collections_are_not_persisted(self):
        for interval in (float("nan"), float("inf"), -float("inf"), 0.000000001):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.scheduler.add_job(
                    "daily-brief", "Prompt", interval_seconds=interval
                )
        with self.assertRaises(ValueError):
            self.scheduler.add_job(
                "daily-brief",
                "Prompt",
                interval_seconds=60,
                knowledge_collection="invalid collection",
            )
        self.assertEqual(self.scheduler.list_jobs(), [])

    def test_oversized_prompt_is_rejected_before_persistence(self):
        with self.assertRaisesRegex(ValueError, "100000"):
            self.scheduler.add_job("daily-brief", "x" * 100001, interval_seconds=60)
        self.assertEqual(self.scheduler.list_jobs(), [])

    def test_completed_one_shot_cannot_be_reenabled_without_new_time(self):
        job = self.scheduler.add_job("daily-brief", "Prompt", run_at=self.clock.value)
        self.scheduler.run_due()
        with self.assertRaisesRegex(ValueError, "Completed one-shot"):
            self.scheduler.set_enabled(job["id"], True)
        self.assertFalse(self.scheduler.get_job(job["id"])["enabled"])

    def test_failure_is_recorded_and_retried_with_bounded_backoff(self):
        attempts = 0

        def broken(job):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("temporary failure")

        scheduler = Scheduler(
            self.database,
            broken,
            clock=self.clock,
            retry_base_seconds=10,
            retry_max_seconds=20,
        )
        job = scheduler.add_job("slack", "Draft", run_at=self.clock.value)
        run = scheduler.run_due()[0]
        self.assertEqual(run["status"], "failed")
        self.assertEqual(
            run["error"], {"type": "RuntimeError", "message": "temporary failure"}
        )
        self.assertEqual(
            scheduler.get_job(job["id"])["next_run_at"],
            (self.clock.value + timedelta(seconds=10)).isoformat(),
        )
        self.assertEqual(scheduler.run_due(), [])

        self.clock.value += timedelta(seconds=10)
        scheduler.run_due()
        self.assertEqual(attempts, 2)
        self.assertEqual(
            scheduler.get_job(job["id"])["next_run_at"],
            (self.clock.value + timedelta(seconds=20)).isoformat(),
        )

    def test_two_instances_atomically_claim_a_due_job(self):
        calls = []
        calls_lock = threading.Lock()

        def runner(job):
            with calls_lock:
                calls.append(job["id"])
            return {"ok": True}

        first = Scheduler(self.database, runner, clock=self.clock)
        second = Scheduler(self.database, runner, clock=self.clock)
        job = first.add_job("linear", "Summarize", run_at=self.clock.value)
        barrier = threading.Barrier(3)
        results = []

        def run(scheduler):
            barrier.wait()
            results.extend(scheduler.run_due())

        threads = [
            threading.Thread(target=run, args=(item,)) for item in (first, second)
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(calls, [job["id"]])
        self.assertEqual(len(results), 1)
        self.assertEqual(len(first.list_runs(job["id"])), 1)

    def test_heartbeat_prevents_reclaim_of_a_slow_live_runner(self):
        started = threading.Event()
        release = threading.Event()

        def slow(job):
            started.set()
            release.wait(timeout=2)
            return {"ok": True}

        first = Scheduler(self.database, slow, lease_seconds=0.15)
        second_calls = []
        second = Scheduler(
            self.database,
            lambda job: second_calls.append(job) or {"ok": True},
            lease_seconds=0.15,
        )
        first.add_job("sentry", "Slow task", run_at=datetime.now(UTC))
        thread = threading.Thread(target=first.run_due)
        thread.start()
        self.assertTrue(started.wait(timeout=1))
        time.sleep(0.3)

        self.assertEqual(second.run_due(), [])
        self.assertEqual(second_calls, [])
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_expired_crash_lease_is_recovered_and_audited(self):
        job = self.scheduler.add_job("slack", "Recover", run_at=self.clock.value)
        claimed_job, _, abandoned_run_id = self.scheduler._claim_due(self.clock.value)
        self.assertEqual(claimed_job["id"], job["id"])

        self.clock.value += timedelta(seconds=self.scheduler.lease_seconds + 1)
        recovered = self.scheduler.run_due()
        self.assertEqual(recovered[0]["status"], "succeeded")
        runs = self.scheduler.list_runs(job["id"])
        abandoned = next(run for run in runs if run["id"] == abandoned_run_id)
        self.assertEqual(abandoned["status"], "failed")
        self.assertEqual(abandoned["error"]["type"], "LeaseExpired")

    def test_validation_and_enable_controls(self):
        with self.assertRaisesRegex(ValueError, "require run_at"):
            self.scheduler.add_job("slack", "Prompt")
        with self.assertRaisesRegex(ValueError, "positive"):
            self.scheduler.add_job("slack", "Prompt", interval_seconds=0)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.scheduler.add_job("slack", "Prompt", run_at=datetime(2026, 1, 1))

        job = self.scheduler.add_job(
            "slack", "Prompt", run_at=self.clock.value + timedelta(seconds=1)
        )
        self.assertFalse(self.scheduler.set_enabled(job["id"], False)["enabled"])
        self.clock.value += timedelta(seconds=2)
        self.assertEqual(self.scheduler.run_due(), [])
        with self.assertRaises(KeyError):
            self.scheduler.set_enabled("missing", True)


if __name__ == "__main__":
    unittest.main()
