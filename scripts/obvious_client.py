#!/usr/bin/env python3
"""obvious_client.py — minimal headless client for obvious.ai's persistent
e2b sandbox via cookie auth.

obvious.ai gives every account a real Linux 6.1 / Debian 13 / Python 3.13 /
Chromium 147 sandbox. The web UI is a chat front-end on top of an agent that
issues `run-shell` tool calls inside that sandbox. By replaying the same
HTTP calls the browser makes, we can drive that sandbox from any host —
no Playwright, no headless browser.

Auth is cookie-based (better-auth lib). Export the relevant cookies from a
logged-in session into a JSON file (Playwright `storage_state` works) and
point this client at it.

Usage:
    # interactive prompt
    python3 obvious_client.py --thread th_xxx --project prj_xxx \\
        --cookies /root/obvious_state.json \\
        "uname -a; ls /home/user/work"

    # programmatic
    from obvious_client import ObviousClient
    c = ObviousClient.from_storage_state('/root/obvious_state.json',
                                        thread_id='th_xxx', project_id='prj_xxx')
    for ev in c.send('echo hi'):
        print(ev)

API endpoints (discovered 2026-04-30):
    POST  /prepare/api/v2/agent/chat/{threadId}     — send user message
    GET   /prepare/threads/{threadId}/messages       — full message history
    GET   /prepare/hydrate/project/{projectId}       — thread agentStatus
    GET   /prepare/modes                             — list of agent modes
    GET   /prepare/workspaces/{wks}/billing/status   — credits / tier
"""
from __future__ import annotations
import json, time, uuid, urllib.request, urllib.error, argparse, sys
from dataclasses import dataclass, field
from typing import Any, Iterator

API_BASE = "https://api.app.obvious.ai/prepare"


@dataclass
class ObviousClient:
    cookie_header: str
    thread_id: str
    project_id: str
    mode: str = "auto"   # auto | fast | deep | analyst | skill-builder
    timezone: str = "UTC"
    poll_interval: float = 3.0
    poll_timeout: float = 240.0
    user_agent: str = "Mozilla/5.0 obvious-client/0.1"

    @classmethod
    def from_storage_state(cls, path: str, thread_id: str, project_id: str,
                           mode: str = "auto") -> "ObviousClient":
        s = json.load(open(path))
        cookies = "; ".join(
            f"{c['name']}={c['value']}" for c in s["cookies"]
            if c["domain"].endswith("obvious.ai")
        )
        return cls(cookie_header=cookies, thread_id=thread_id,
                   project_id=project_id, mode=mode)

    # ---- low-level HTTP -------------------------------------------------

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        h = {
            "Cookie": self.cookie_header,
            "Accept": "application/json",
            "User-Agent": self.user_agent,
            "Origin": "https://app.obvious.ai",
            "Referer": "https://app.obvious.ai/",
        }
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def _request(self, method: str, path: str, body: dict | None = None,
                 timeout: float = 30.0) -> tuple[int, str]:
        url = f"{API_BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(json_body=body is not None),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    # ---- high-level API -------------------------------------------------

    def list_modes(self) -> list[dict]:
        s, b = self._request("GET", "/modes")
        if s != 200:
            raise RuntimeError(f"modes: {s} {b[:200]}")
        return json.loads(b)["modes"]

    def billing_status(self, workspace_id: str) -> dict:
        s, b = self._request("GET", f"/workspaces/{workspace_id}/billing/status")
        if s != 200:
            raise RuntimeError(f"billing: {s} {b[:200]}")
        return json.loads(b)

    def get_messages(self) -> list[dict]:
        s, b = self._request("GET", f"/threads/{self.thread_id}/messages")
        if s != 200:
            raise RuntimeError(f"messages: {s} {b[:200]}")
        return json.loads(b)["messages"]

    def get_agent_status(self) -> str | None:
        import json as _json
        s, b = self._request("GET", f"/hydrate/project/{self.project_id}")
        if s != 200:
            return None
        # hydrate returns newline-separated JSON objects (SSE-like stream)
        for line in b.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = _json.loads(line)
                data = obj.get("data") or {}
                status = data.get("agentStatus")
                if status:
                    return status
            except Exception:
                pass
        # fallback string scan
        idx = b.find('"agentStatus"')
        if idx < 0:
            return None
        chunk = b[idx:idx + 80]
        for sentinel in ("running", "completed", "idle", "error", "failed"):
            if f'"' + sentinel + '"' in chunk:
                return sentinel
        return None

    def get_sandbox_paused(self) -> bool | None:
        """Check if the e2b sandbox is paused via project metadata API."""
        import json as _json
        s, b = self._request("GET", f"/projects/{self.project_id}")
        if s != 200:
            return None
        try:
            j = _json.loads(b)
            return j.get("metadata", {}).get("sandbox", {}).get("isPaused")
        except Exception:
            return None

    def wake_sandbox(self, ping_timeout: float = 90.0) -> bool:
        """Wake paused sandbox by sending a ping message and waiting for completion."""
        import time as _time
        paused = self.get_sandbox_paused()
        if paused is False:
            return True
        try:
            baseline = len(self.get_messages())
            body = {
                "message": "ping",
                "messageId": __import__("uuid").uuid4().hex,
                "projectId": self.project_id,
                "fileIds": [],
                "modeId": self.mode,
                "timezone": self.timezone,
            }
            s, _ = self._request("POST", f"/api/v2/agent/chat/{self.thread_id}", body)
            if s != 200:
                return False
            deadline = _time.time() + ping_timeout
            while _time.time() < deadline:
                _time.sleep(3.0)
                status = self.get_agent_status()
                if status in ("completed", "idle"):
                    return True
                if status in ("error", "failed"):
                    return False
        except Exception:
            pass
        return False

    # ---- chat workflow --------------------------------------------------

    def send(self, prompt: str, file_ids: list[str] | None = None) -> dict:
        """Send a user message; return {executionId, baseline_msg_count}."""
        baseline = len(self.get_messages())
        body = {
            "message": prompt,
            "messageId": str(uuid.uuid4()),
            "projectId": self.project_id,
            "fileIds": file_ids or [],
            "modeId": self.mode,
            "timezone": self.timezone,
        }
        s, b = self._request("POST", f"/api/v2/agent/chat/{self.thread_id}", body)
        if s != 200:
            raise RuntimeError(f"chat POST: {s} {b[:300]}")
        return {"baseline": baseline, **json.loads(b)}

    def wait(self, baseline: int) -> list[dict]:
        """Poll until agentStatus == completed, return new messages."""
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            status = self.get_agent_status()
            if status in ("completed", "idle", "error", "failed"):
                msgs = self.get_messages()
                return msgs[baseline:]
        raise TimeoutError(f"agent did not complete within {self.poll_timeout}s")

    def ask(self, prompt: str) -> list[dict]:
        """Convenience: send + wait, return new messages."""
        r = self.send(prompt)
        return self.wait(r["baseline"])

    @staticmethod
    def extract_text(messages: list[dict]) -> str:
        out = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("type") == "text":
                for c in m.get("content") or []:
                    if c.get("type") == "text" and c.get("text"):
                        out.append(c["text"])
        return "\n\n".join(out)

    @staticmethod
    def extract_shell_results(messages: list[dict]) -> list[dict]:
        """Pull every run-shell tool call + result into a flat list."""
        calls: dict[str, dict] = {}
        for m in messages:
            for c in m.get("content") or []:
                if c.get("type") == "tool-call" and c.get("toolName") == "run-shell":
                    cid = c.get("toolCallId")
                    if cid:
                        calls[cid] = {"command": (c.get("input") or {}).get("command", ""),
                                      "stdout": "", "stderr": "", "exitCode": None}
                elif c.get("type") == "tool-result":
                    cid = c.get("toolCallId")
                    out = c.get("output") or c.get("result") or {}
                    # error-text: run-shell unavailable (fast mode or paused sandbox)
                    if out.get("type") == "error-text":
                        if cid in calls:
                            calls[cid]["stderr"] = str(out.get("value", "tool unavailable"))
                            calls[cid]["exitCode"] = -2
                        continue
                    val_wrap = out.get("value") or {}
                    val = (val_wrap.get("data") if isinstance(val_wrap, dict) else {}) or {}
                    if cid in calls:
                        calls[cid]["stdout"] = val.get("stdout", "")
                        calls[cid]["stderr"] = val.get("stderr", "")
                        calls[cid]["exitCode"] = val.get("exitCode")
                        calls[cid]["sandboxId"] = val.get("sandboxId")
                        calls[cid]["durationMs"] = val.get("durationMs")
        return list(calls.values())


def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="obvious.ai headless chat client")
    ap.add_argument("--cookies", required=True, help="Playwright storage_state JSON path")
    ap.add_argument("--thread", required=True, help="threadId (th_xxx)")
    ap.add_argument("--project", required=True, help="projectId (prj_xxx)")
    ap.add_argument("--mode", default="auto", choices=["auto","fast","deep","analyst","skill-builder"])
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--json", action="store_true", help="emit raw new messages as JSON")
    ap.add_argument("prompt", help="message to send")
    args = ap.parse_args(argv)

    c = ObviousClient.from_storage_state(args.cookies, args.thread, args.project, mode=args.mode)
    c.poll_timeout = args.timeout

    new = c.ask(args.prompt)
    if args.json:
        print(json.dumps(new, indent=2))
        return 0

    text = c.extract_text(new)
    shells = c.extract_shell_results(new)
    if shells:
        print("=== shell tool calls ===")
        for s in shells:
            print(f"$ {s['command']}")
            if s["stdout"]: print(s["stdout"].rstrip())
            if s["stderr"]: print(f"[stderr] {s['stderr'].rstrip()}", file=sys.stderr)
            print(f"[exit {s['exitCode']} sandbox={s.get('sandboxId')} {s.get('durationMs')}ms]")
            print()
    if text:
        print("=== assistant ===")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
