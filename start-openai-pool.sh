#!/bin/bash
# v1.0 — openai-pool 启动包装。
# pre-bind: release port 8000 held by previous instance before exec.
# Prevents EADDRINUSE restart loop when PM2 rapid-restarts.
_port="${PORT:-8000}"
_stale=$(ss -lntp 2>/dev/null | grep -oP "\\*:${_port}[^,]*pid=\\K[0-9]+" | head -1)
if [[ -z "$_stale" ]]; then
  _stale=$(ss -lntp 2>/dev/null | grep -oP "0\\.0\\.0\\.0:${_port}[^,]*pid=\\K[0-9]+" | head -1)
fi
if [[ -n "$_stale" ]]; then
  kill -9 "$_stale" 2>/dev/null && echo "[start-openai-pool] killed stale pid=$_stale on :${_port}"
else
  fuser -k "${_port}/tcp" 2>/dev/null && echo "[start-openai-pool] fuser cleared :${_port}" || true
fi
sleep 0.3

export PORT="${_port}"
export PYTHONPATH="/root/Toolkit/artifacts/openai-pool"
cd /root/Toolkit/artifacts/openai-pool
exec python3 -m openai_pool_orchestrator
