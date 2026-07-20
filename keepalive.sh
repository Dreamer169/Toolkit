#!/bin/bash
# 防止 Codespace 因空闲关闭 — 每 4 分钟发送一次活动信号
while true; do
  # 发送假活动防止idle timeout
  curl -sf http://localhost:8080/api/healthz > /dev/null 2>&1
  # 写入时间戳
  date > /tmp/keepalive_last_ping
  sleep 240
done
