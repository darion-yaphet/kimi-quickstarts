"""
Agent Session Logic
===================

Core agent interaction functions for running autonomous coding sessions.
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from claude_code_sdk import ClaudeSDKClient

from client import create_client
from progress import print_session_header, print_progress_summary
from prompts import get_initializer_prompt, get_coding_prompt, copy_spec_to_project


# Configuration
AUTO_CONTINUE_DELAY_SECONDS = 3
MINIMUM_FEATURE_COUNT = 200
DEFAULT_MAX_CONSECUTIVE_FAILURES = 3


def _load_valid_feature_list(project_dir: Path) -> Optional[list[dict]]:
    """Load a structurally valid, non-empty feature list."""
    feature_list_path = project_dir / "feature_list.json"
    if not feature_list_path.is_file():
        return None

    try:
        features = json.loads(feature_list_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(features, list) or not features:
        return None
    if not all(
        isinstance(feature, dict)
        and isinstance(feature.get("category"), str)
        and isinstance(feature.get("description"), str)
        and isinstance(feature.get("steps"), list)
        and isinstance(feature.get("passes"), bool)
        for feature in features
    ):
        return None

    return features


def all_features_are_complete(project_dir: Path) -> bool:
    """Return whether a valid, non-empty feature list is fully passing."""
    features = _load_valid_feature_list(project_dir)
    return (
        features is not None
        and len(features) >= MINIMUM_FEATURE_COUNT
        and all(feature["passes"] for feature in features)
    )


def initialization_artifacts_are_valid(project_dir: Path) -> bool:
    """Return whether the initializer produced the files needed by later agents."""
    required_text_files = (project_dir / "init.sh", project_dir / "README.md")

    try:
        text_files_are_valid = all(
            path.is_file() and bool(path.read_text(encoding="utf-8").strip())
            for path in required_text_files
        )
    except (OSError, UnicodeDecodeError):
        return False
    if not text_files_are_valid:
        return False

    features = _load_valid_feature_list(project_dir)
    return features is not None and len(features) >= MINIMUM_FEATURE_COUNT


async def run_agent_session(
    client: ClaudeSDKClient,
    message: str,
    project_dir: Path,
) -> tuple[str, str]:
    """
    Run a single agent session using Claude Agent SDK.

    Args:
        client: Claude SDK client
        message: The prompt to send
        project_dir: Project directory path

    Returns:
        (status, response_text) where status is:
        - "continue" if agent should continue working
        - "error" if an error occurred
    """
    print("Sending prompt to Claude Agent SDK...\n")

    try:
        # Send the query
        await client.query(message)

        # Collect response text and show tool use
        response_text = ""
        async for msg in client.receive_response():
            msg_type = type(msg).__name__

            # Handle AssistantMessage (text and tool use)
            if msg_type == "AssistantMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "TextBlock" and hasattr(block, "text"):
                        response_text += block.text
                        print(block.text, end="", flush=True)
                    elif block_type == "ToolUseBlock" and hasattr(block, "name"):
                        print(f"\n[Tool: {block.name}]", flush=True)
                        if hasattr(block, "input"):
                            input_str = str(block.input)
                            if len(input_str) > 200:
                                print(f"   Input: {input_str[:200]}...", flush=True)
                            else:
                                print(f"   Input: {input_str}", flush=True)

            # Handle UserMessage (tool results)
            elif msg_type == "UserMessage" and hasattr(msg, "content"):
                for block in msg.content:
                    block_type = type(block).__name__

                    if block_type == "ToolResultBlock":
                        result_content = getattr(block, "content", "")
                        is_error = getattr(block, "is_error", False)

                        # Check if command was blocked by security hook
                        if "blocked" in str(result_content).lower():
                            print(f"   [BLOCKED] {result_content}", flush=True)
                        elif is_error:
                            # Show errors (truncated)
                            error_str = str(result_content)[:500]
                            print(f"   [Error] {error_str}", flush=True)
                        else:
                            # Tool succeeded - just show brief confirmation
                            print("   [Done]", flush=True)

        print("\n" + "-" * 70 + "\n")
        return "continue", response_text

    except Exception as e:
        print(f"Error during agent session: {e}")
        return "error", str(e)


async def run_autonomous_agent(
    project_dir: Path,
    model: str,
    max_iterations: Optional[int] = None,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
) -> None:
    """
    Run the autonomous agent loop.

    Args:
        project_dir: Directory for the project
        model: Kimi model to use through Claude Code
        max_iterations: Maximum number of iterations (None for unlimited)
        max_consecutive_failures: Stop after this many consecutive failed sessions
    """
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be at least 1")

    print("\n" + "=" * 70)
    print("  AUTONOMOUS CODING AGENT DEMO")
    print("=" * 70)
    print(f"\nProject directory: {project_dir}")
    print(f"Model: {model}")
    if max_iterations:
        print(f"Max iterations: {max_iterations}")
    else:
        print("Max iterations: Unlimited (will run until completion)")
    print(f"Consecutive failure limit: {max_consecutive_failures}")
    print()

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Only coding agents may continue after the initializer has produced a
    # complete, readable handoff. A partial or failed initializer run retries
    # with a fresh context on the next iteration.
    is_first_run = not initialization_artifacts_are_valid(project_dir)

    if is_first_run:
        print("Fresh start - will use initializer agent")
        print()
        print("=" * 70)
        print("  NOTE: First session takes 10-20+ minutes!")
        print("  The agent is generating 200 detailed test cases.")
        print("  This may appear to hang - it's working. Watch for [Tool: ...] output.")
        print("=" * 70)
        print()
        # Copy the app spec into the project directory for the agent to read
        copy_spec_to_project(project_dir)
    else:
        print("Continuing existing project")
        print_progress_summary(project_dir)

    # Main loop
    iteration = 0
    consecutive_failures = 0
    termination_reason = ""

    while True:
        # Avoid creating another paid SDK session once the persisted source of
        # truth says every feature has passed.
        if all_features_are_complete(project_dir):
            termination_reason = "All features are complete"
            print(f"\n{termination_reason}")
            break

        # Check max iterations
        if max_iterations and iteration >= max_iterations:
            termination_reason = f"Reached max iterations ({max_iterations})"
            print(f"\n{termination_reason}")
            print("To continue, run the script again without --max-iterations")
            break

        iteration += 1

        # Print session header
        is_first_run = not initialization_artifacts_are_valid(project_dir)
        print_session_header(iteration, is_first_run)

        # Choose prompt based on session type
        if is_first_run:
            prompt = get_initializer_prompt()
        else:
            prompt = get_coding_prompt()

        # Client construction and context startup can fail before
        # run_agent_session gets a chance to normalize SDK errors.
        try:
            client = create_client(project_dir, model)
            async with client:
                status, _response = await run_agent_session(
                    client, prompt, project_dir
                )
        except Exception as exc:
            print(f"Session setup failed: {exc}")
            status = "error"

        if all_features_are_complete(project_dir):
            termination_reason = "All features are complete"
            print(f"\n{termination_reason}")
            break

        initializer_incomplete = (
            is_first_run and not initialization_artifacts_are_valid(project_dir)
        )
        session_failed = status == "error" or initializer_incomplete

        # Handle status
        if session_failed:
            consecutive_failures += 1
            if initializer_incomplete:
                print("\nInitializer did not produce a valid project handoff")
            else:
                print("\nSession encountered an error")

            if consecutive_failures >= max_consecutive_failures:
                termination_reason = (
                    "Reached consecutive failure limit "
                    f"({max_consecutive_failures})"
                )
                print(f"{termination_reason}; stopping automatic retries")
                break

            print(
                "Will retry with a fresh session "
                f"({consecutive_failures}/{max_consecutive_failures})..."
            )
            await asyncio.sleep(AUTO_CONTINUE_DELAY_SECONDS)
        else:
            consecutive_failures = 0
            print(f"\nAgent will auto-continue in {AUTO_CONTINUE_DELAY_SECONDS}s...")
            print_progress_summary(project_dir)
            await asyncio.sleep(AUTO_CONTINUE_DELAY_SECONDS)

        # Small delay between sessions
        if max_iterations is None or iteration < max_iterations:
            print("\nPreparing next session...\n")
            await asyncio.sleep(1)

    # Final summary
    print("\n" + "=" * 70)
    if all_features_are_complete(project_dir):
        print("  SESSION COMPLETE")
    else:
        print("  SESSION STOPPED")
    print("=" * 70)
    print(f"\nProject directory: {project_dir}")
    if termination_reason:
        print(f"Reason: {termination_reason}")
    print_progress_summary(project_dir)

    # Print instructions for running the generated application
    print("\n" + "-" * 70)
    print("  TO RUN THE GENERATED APPLICATION:")
    print("-" * 70)
    print(f"\n  cd {project_dir.resolve()}")
    print("  ./init.sh           # Run the setup script")
    print("  # Or manually:")
    print("  npm install && npm run dev")
    print("\n  Then open http://localhost:3000 (or check init.sh for the URL)")
    print("-" * 70)

    print("\nDone!")
