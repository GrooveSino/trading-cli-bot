from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from trading_gateway.support.redaction import redact_mapping


class ManagedEventLog:
    def __init__(self, session_id: str | None = None, log_file: Path | None = None, state_file: Path | None = None) -> None:
        self.session_id = session_id
        self.log_file = log_file
        self.state_file = state_file
        self.events: list[dict[str, Any]] = []
        if self.log_file:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            self.log_file.touch(exist_ok=True)
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **data: Any) -> None:
        row = {"ts": time.time(), "event": event}
        if self.session_id:
            row["session_id"] = self.session_id
        row.update(data)
        row = redact_mapping(row)
        self.events.append(row)
        if self.log_file:
            with self.log_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
        if self.state_file:
            payload = {"session_id": self.session_id, "updated_at": row["ts"], "last_event": row, "events": self.events[-20:]}
            self.state_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_managed_session(session_dir: str | Path, session_id: str) -> dict[str, Any]:
    state_file = Path(session_dir) / f"{session_id}.json"
    if not state_file.exists():
        raise ValueError(f"managed session not found: {session_id}")
    return json.loads(state_file.read_text(encoding="utf-8"))


def stop_managed_session(session_dir: str | Path, session_id: str) -> dict[str, Any]:
    state_file = Path(session_dir) / f"{session_id}.json"
    stop_file = Path(session_dir) / f"{session_id}.stop"
    if not state_file.exists():
        raise ValueError(f"managed session not found: {session_id}")
    stop_file.write_text(str(time.time()), encoding="utf-8")
    return {"session_id": session_id, "stop_file": str(stop_file), "status": "stop_requested"}


def detach_managed_risk_command(args: list[str], session_dir: str | Path, *, stdout_log_name: str | None = None) -> dict[str, Any]:
    root = Path.cwd()
    session_root = Path(session_dir)
    if not session_root.is_absolute():
        session_root = root / session_root
    session_root.mkdir(parents=True, exist_ok=True)
    session_id = uuid4().hex[:12]
    log_file = session_root / f"{session_id}.ndjson"
    state_file = session_root / f"{session_id}.json"
    stdout_file = session_root / (stdout_log_name or f"{session_id}.stdout.log")
    ManagedEventLog(session_id, log_file, state_file).emit("starting", mode="detached", argv=args)
    child_args = [arg for arg in args if arg != "--detach"]
    child_args.extend(["--managed-session-id", session_id, "--managed-log-file", str(log_file), "--managed-state-file", str(state_file)])
    with stdout_file.open("a", encoding="utf-8") as stdout:
        proc = subprocess.Popen([sys.executable, str(Path("cli") / "tbot.py"), *child_args], cwd=root, stdout=stdout, stderr=subprocess.STDOUT, start_new_session=True)
    return {
        "status": "detached",
        "session_id": session_id,
        "pid": proc.pid,
        "log_file": str(log_file),
        "state_file": str(state_file),
        "stdout_file": str(stdout_file),
    }

