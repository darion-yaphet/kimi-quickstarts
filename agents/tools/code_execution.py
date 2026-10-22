"""Portable Python execution tool backed by an isolated Docker container."""

import asyncio
import os
import shutil
import uuid

from .base import Tool


class _OutputLimitExceeded(RuntimeError):
    pass


class CodeExecutionServerTool(Tool):
    """Execute Python in a constrained container.

    The historical class name is retained for import compatibility. This is a
    normal custom tool: the model sends a ``tool_use`` block and this process
    executes it locally through Docker. Python is never evaluated by the host
    interpreter.
    """

    def __init__(
        self,
        name: str = "code_execution",
        *,
        image: str | None = None,
        timeout_seconds: float = 10,
        max_code_chars: int = 20_000,
        max_output_bytes: int = 64 * 1024,
        docker_executable: str = "docker",
    ) -> None:
        super().__init__(
            name=name,
            description=(
                "Execute Python 3 code in an isolated Docker container with "
                "no network, a read-only filesystem, and bounded resources."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source code to execute.",
                    }
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        )
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_code_chars <= 0 or max_output_bytes <= 0:
            raise ValueError("code and output limits must be positive")
        self.image = image or os.environ.get(
            "CODE_EXECUTION_DOCKER_IMAGE", "python:3.12-alpine"
        )
        self.timeout_seconds = timeout_seconds
        self.max_code_chars = max_code_chars
        self.max_output_bytes = max_output_bytes
        self.docker_executable = docker_executable

    def _command(self, code: str, container_name: str) -> list[str]:
        return [
            self.docker_executable,
            "run",
            "--rm",
            "--pull=never",
            "--name",
            container_name,
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=128m",
            "--cpus=0.5",
            "--pids-limit=64",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m",
            self.image,
            "python",
            "-I",
            "-c",
            code,
        ]

    async def _remove_container(self, container_name: str) -> None:
        cleanup = await asyncio.create_subprocess_exec(
            self.docker_executable,
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await cleanup.wait()

    async def _collect_output(
        self,
        process: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes]:
        total = 0
        lock = asyncio.Lock()

        async def read(stream: asyncio.StreamReader | None) -> bytes:
            nonlocal total
            if stream is None:
                return b""
            chunks: list[bytes] = []
            while chunk := await stream.read(8192):
                async with lock:
                    total += len(chunk)
                    if total > self.max_output_bytes:
                        raise _OutputLimitExceeded
                chunks.append(chunk)
            return b"".join(chunks)

        stdout_task = asyncio.create_task(read(process.stdout))
        stderr_task = asyncio.create_task(read(process.stderr))
        try:
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            await process.wait()
            return stdout, stderr
        finally:
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    async def execute(self, code: str) -> str:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("code must be a non-empty string")
        if len(code) > self.max_code_chars:
            raise ValueError(f"code exceeds {self.max_code_chars} characters")
        if shutil.which(self.docker_executable) is None:
            raise RuntimeError(
                f"{self.docker_executable!r} was not found; install Docker and "
                f"pre-pull {self.image!r}"
            )

        container_name = f"kimi-agent-code-{uuid.uuid4().hex}"
        process = await asyncio.create_subprocess_exec(
            *self._command(code, container_name),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                self._collect_output(process), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            await self._remove_container(container_name)
            raise RuntimeError(
                f"Python execution exceeded {self.timeout_seconds:g} seconds"
            ) from None
        except _OutputLimitExceeded:
            process.kill()
            await process.wait()
            await self._remove_container(container_name)
            raise RuntimeError(
                f"Python output exceeded {self.max_output_bytes} bytes"
            ) from None

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        return (
            f"exit_code: {process.returncode}\n"
            f"stdout:\n{stdout_text or '[empty]'}\n"
            f"stderr:\n{stderr_text or '[empty]'}"
        )
