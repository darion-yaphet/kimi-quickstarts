"""Walk through local replacements using fixtures, never external connectors."""

import json
from datetime import datetime, timezone
from pathlib import Path

from .profiles import PROFILES


FIXTURES = Path(__file__).resolve().parent.parent / "demo-fixtures"


def run_demo(runtime, output: Path) -> dict:
    if runtime.capabilities()["mode"] != "demo":
        raise ValueError("Demo workflows require the offline demo provider")

    cases = json.loads((FIXTURES / "scenarios.json").read_text(encoding="utf-8"))
    if {case["profile"] for case in cases} != set(PROFILES):
        raise ValueError("Demo scenarios must cover every local profile")

    collection = "offline-demo"
    ingestion = runtime.knowledge.ingest(FIXTURES / "product.md", collection)
    results = []
    for case in cases:
        session = runtime.create_session(case["profile"], case["title"], collection)
        reply = runtime.chat(session["id"], case["message"])
        results.append({
            "profile": case["profile"],
            "input": case["message"],
            "input_kind": "local_fixture",
            "session_id": session["id"],
            "reply": reply["content"],
            "sources": reply["metadata"]["sources"],
            "delivery": "simulated_local_output_only",
        })

    # Exercise real persistence and scheduling with an offline response factory.
    job = runtime.scheduler.add_job(
        "daily-brief", "整理示例产品的退款期限和本周发布记录。",
        run_at=datetime.now(timezone.utc), title="Offline demo brief",
        knowledge_collection=collection,
    )
    runtime.scheduler.run_due()
    runs = runtime.scheduler.list_runs(job["id"])
    if not runs or runs[0]["status"] != "succeeded":
        raise RuntimeError("Demo scheduled run failed")

    # A second role receives the first role's actual saved output.
    plan = next(result for result in results if result["profile"] == "roadtrip-planner")
    reviewer = runtime.create_session("roadtrip-planner", "Offline plan review")
    review = runtime.chat(
        reviewer["id"], "Review this sample draft for missing live data:\n" + plan["reply"]
    )
    report = {
        "mode": "demo",
        "database": str(runtime.store.path),
        "output": str(output.resolve()),
        "notice": "离线模拟示例；没有模型推理、远程消息投递、OAuth 或代码执行。",
        "knowledge_ingestion": ingestion,
        "scenarios": results,
        "schedule": {"job": job, "runs": runs},
        "review": {"session_id": reviewer["id"], "reply": review["content"]},
        "session_tools": {
            "transport": "direct_local_calls_not_mcp",
            "list_sessions": len(runtime.store.list_sessions()),
            "replayed_events": len(runtime.store.events(plan["session_id"])),
        },
        "sandbox": {"available": False, "reason": "No code execution in demo mode"},
        "vault": {"available": False, "reason": "Fixtures need no credentials"},
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
