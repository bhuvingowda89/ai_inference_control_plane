"""Local CPU llama.cpp server ownership and measured inference."""
from __future__ import annotations
from dataclasses import asdict
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import subprocess
import time
from typing import Any
from urllib import error, request

from ..config import Config
from ..schemas import Request, Response


class BackendFailure(RuntimeError):
    """Harness, model-load, or server-protocol failure; the run is invalid."""


CLEANUP_POLL_INTERVAL_SECONDS = 0.1
LISTENER_CONNECT_TIMEOUT_SECONDS = 0.2


def _probe_listener(host: str, port: int, timeout: float = LISTENER_CONNECT_TIMEOUT_SECONDS) -> tuple[bool | None, str | None]:
    """Return True for an accepting listener, False for refusal, None if unknown."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, None
    except OSError as exc:
        if exc.errno == errno.ECONNREFUSED:
            return False, None
        return None, f"{type(exc).__name__}: {exc}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def preflight(config: Config) -> dict[str, Any]:
    real = config.real_runtime
    binary = shutil.which(real.binary)
    model = Path(real.model_path).resolve()
    checks: dict[str, Any] = {"binary_found": bool(binary), "model_exists": model.is_file(),
                              "port_available": False, "model_sha256_matches": False}
    if model.is_file():
        checks["model_sha256_matches"] = sha256_file(model) == real.model_sha256
    listener_detected, port_error = _probe_listener(real.host, real.port)
    checks["port_available"] = listener_detected is False
    if listener_detected is True:
        port_error = "active listener accepts connections"
    result = {"checks": checks, "valid": all(checks.values()),
              "binary": binary, "model_path": str(model), "expected_model_sha256": real.model_sha256,
              "port_error": port_error, "listener_detected": listener_detected}
    return result


class LlamaCppSession:
    """Own exactly one local server; never attach to a pre-existing process."""

    def __init__(self, config: Config, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.log = None
        self.counts = {"baseline": 0, "candidate": 0}
        self.startup_ms: float | None = None
        self.exit_status: int | None = None
        self.process_exited = False
        self.listener_closed = False
        self.listener_detected: bool | None = None
        self.listener_probe_error: str | None = None
        self.cleanup_attempts = 0
        self.owned_pid: int | None = None
        self.owns_process_group = False
        self.shutdown_success = False
        self.preflight_result: dict[str, Any] = {}
        self.args: list[str] = []
        self.version: str | None = None
        self.revision: str | None = None

    @property
    def base_url(self) -> str:
        r = self.config.real_runtime
        return f"http://{r.host}:{r.port}"

    def __enter__(self) -> "LlamaCppSession":
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.preflight_result = preflight(self.config)
        from ..artifacts import write_json
        write_json(self.run_dir / "preflight.json", self.preflight_result)
        if not self.preflight_result["valid"]:
            raise BackendFailure(f"real backend preflight failed: {self.preflight_result['checks']}; "
                                 f"port_error={self.preflight_result['port_error']}")
        r = self.config.real_runtime
        version = subprocess.run([self.preflight_result["binary"], "--version"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 timeout=r.startup_timeout_seconds, check=False).stdout
        self.version = version.strip()
        match = re.search(r"commit\s+([0-9a-f]+)", version)
        self.revision = match.group(1) if match else None
        self.args = [self.preflight_result["binary"], "-m", self.preflight_result["model_path"],
                     "--host", r.host, "--port", str(r.port), "--threads", str(r.thread_count),
                     "--ctx-size", str(r.context_size), "--batch-size", str(r.batch_size),
                     "--n-gpu-layers", "0", "--seed", str(self.config.experiment.seed)]
        self.log = (self.run_dir / "llama-server.log").open("wb")
        started = time.monotonic()
        self.process = subprocess.Popen(self.args, stdout=self.log, stderr=subprocess.STDOUT,
                                        start_new_session=os.name == "posix")
        self.owned_pid = self.process.pid
        self.owns_process_group = os.name == "posix"
        try:
            while time.monotonic() - started < r.startup_timeout_seconds:
                if self.process.poll() is not None:
                    raise BackendFailure(f"llama-server exited during startup: {self.process.returncode}")
                try:
                    with request.urlopen(self.base_url + "/health", timeout=1) as stream:
                        if stream.status == 200:
                            self.startup_ms = (time.monotonic() - started) * 1000
                            return self
                except (OSError, error.HTTPError):
                    pass
                time.sleep(0.1)
            raise BackendFailure("llama-server readiness timeout")
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def complete(self, prompt: str) -> tuple[str, bool, float, float, float, dict[str, Any]]:
        if self.process is None or self.process.poll() is not None:
            raise BackendFailure("llama-server process unavailable")
        r = self.config.real_runtime
        payload = {"messages": [{"role": "user", "content": prompt}], "temperature": r.temperature,
                   "seed": self.config.experiment.seed, "max_tokens": r.max_tokens,
                   "stream": False}
        req = request.Request(self.base_url + "/v1/chat/completions",
                              data=json.dumps(payload, sort_keys=True).encode("utf-8"),
                              headers={"Content-Type": "application/json"}, method="POST")
        start = time.monotonic()
        try:
            with request.urlopen(req, timeout=r.request_timeout_seconds) as stream:
                data = json.loads(stream.read().decode("utf-8"))
            end = time.monotonic()
            output = data["choices"][0]["message"]["content"]
            if not isinstance(output, str):
                raise BackendFailure("llama-server response content is not text")
            return output, True, start, end, (end - start) * 1000, data.get("usage", {})
        except error.URLError as exc:
            end = time.monotonic()
            if self.process.poll() is not None:
                raise BackendFailure("llama-server exited during inference") from exc
            return "", False, start, end, (end - start) * 1000, {"request_error": type(exc.reason).__name__}
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise BackendFailure("malformed llama-server response") from exc

    def _signal_owned(self, force: bool) -> None:
        """Signal only the live process/session launched by this instance."""
        if self.process is None or self.process.poll() is not None:
            return
        if self.owns_process_group and self.owned_pid is not None:
            try:
                if os.getpgid(self.owned_pid) == self.owned_pid:
                    os.killpg(self.owned_pid, signal.SIGKILL if force else signal.SIGTERM)
                elif force:
                    self.process.kill()
                else:
                    self.process.terminate()
            except ProcessLookupError:
                return
        elif force:
            self.process.kill()
        else:
            self.process.terminate()

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.process is None:
            return
        timeout = self.config.real_runtime.shutdown_timeout_seconds
        try:
            self._signal_owned(force=False)
            try:
                self.exit_status = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._signal_owned(force=True)
                try:
                    self.exit_status = self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.exit_status = self.process.poll()
            self.process_exited = self.process.poll() is not None
            deadline = time.monotonic() + timeout
            while True:
                self.cleanup_attempts += 1
                self.listener_detected, self.listener_probe_error = _probe_listener(
                    self.config.real_runtime.host, self.config.real_runtime.port,
                    timeout=min(LISTENER_CONNECT_TIMEOUT_SECONDS, max(0.001, deadline - time.monotonic())))
                if self.listener_detected is False:
                    self.listener_closed = True
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(CLEANUP_POLL_INTERVAL_SECONDS, remaining))
        finally:
            if self.log is not None:
                self.log.close()
        self.shutdown_success = self.process_exited and self.listener_closed
        if not self.shutdown_success:
            detail = f"; prior_error={exc!r}" if exc is not None else ""
            raise BackendFailure(f"llama-server cleanup failed: {self.cleanup_diagnostics()}{detail}") from exc

    def cleanup_diagnostics(self) -> dict[str, Any]:
        return {"owned_pid": self.owned_pid, "process_poll_result": self.process.poll() if self.process else None,
                "process_exit_status": self.exit_status, "process_exited": self.process_exited,
                "host": self.config.real_runtime.host, "port": self.config.real_runtime.port,
                "listener_detected": self.listener_detected, "listener_closed": self.listener_closed,
                "listener_probe_error": self.listener_probe_error, "cleanup_attempts": self.cleanup_attempts}

    def provenance(self) -> dict[str, Any]:
        r = self.config.real_runtime
        return {"backend_instance_id": "local-llama-server-1", "backend_type": "llama_cpp",
                "runtime_binary": self.preflight_result.get("binary"), "runtime_arguments": self.args,
                "llama_cpp_version": self.version, "llama_cpp_git_revision": self.revision,
                "model_path": self.preflight_result.get("model_path"), "model_sha256": r.model_sha256,
                "model_file_size": Path(self.preflight_result["model_path"]).stat().st_size, "model_metadata": None,
                "thread_count": r.thread_count, "context_size": r.context_size, "batch_size": r.batch_size,
                "temperature": r.temperature, "seed": self.config.experiment.seed,
                "sampling_parameters": {"max_tokens": r.max_tokens, "temperature": r.temperature},
                "host_architecture": platform.machine(), "operating_system": platform.platform(),
                "logical_cpu_count": os.cpu_count(), "startup_result": "READY" if self.startup_ms is not None else "FAILED",
                "ready_after_ms": self.startup_ms, "shutdown_result": "STOPPED" if self.shutdown_success else "FAILED",
                "backend_exit_status": self.exit_status, "process_exit_status": self.exit_status,
                "process_exited": self.process_exited, "listener_closed": self.listener_closed,
                "shutdown_success": self.shutdown_success, "owned_pid": self.owned_pid,
                "listener_detected": self.listener_detected, "cleanup_attempts": self.cleanup_attempts,
                "request_count_by_role": self.counts,
                "log_reference": "llama-server.log"}


class LlamaCppBackend:
    def __init__(self, role: str, config: Config, session: LlamaCppSession) -> None:
        self.role, self.config, self.session = role, getattr(config, role), session

    def infer(self, item: Request) -> Response:
        if item.backend_role != self.role or item.intended_revision != self.config.revision:
            raise ValueError("request does not match backend")
        output, success, start, end, latency, _ = self.session.complete(item.payload)
        self.session.counts[self.role] += 1
        return Response(item.request_index, item.request_id, item.rollout_id, item.epoch,
                        item.intended_revision, self.config.revision, self.role, success, latency,
                        False, output, item.desired_rollout_state, item.observed_rollout_state,
                        item.transition_index, item.routing_state_index, "MEASURED", start, end)

    def infer_behavior(self, item: dict[str, Any]) -> tuple[str, bool, float, float, float, dict[str, Any]]:
        from ..behavior.real import prompt_for_item
        result = self.session.complete(prompt_for_item(item, self.config.prompt_variant))
        self.session.counts[self.role] += 1
        return result
