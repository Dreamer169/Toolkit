#!/bin/bash
# 批量开号 — 无间隔顺序注册，完成后 webhook 通知
PORTS=(10825 10826 10827 10828 10831 10832)
LABELS=(acc-2 acc-3 acc-4 acc-5 acc-6 acc-7)
export DISPLAY=:99

total="${#PORTS[@]}"
echo "[batch] start $(date -u)  remaining=$total"

for ((i=0; i<total; i++)); do
  port="${PORTS[$i]}"
  label="${LABELS[$i]}"

  ip=$(curl -s --max-time 6 --socks5-hostname 127.0.0.1:$port https://api.ipify.org 2>/dev/null)
  if [ -z "$ip" ]; then
    echo "[batch $(date -u +%H:%M:%S)] port=$port DEAD — skip"
    continue
  fi
  echo "[batch $(date -u +%H:%M:%S)] === $label port=$port ip=$ip ==="

  python3 /root/Toolkit/scripts/obvious_provision.py \
    --proxy socks5://127.0.0.1:$port \
    --label "$label" \
    --check-ip \
    --headless

  echo "[batch $(date -u +%H:%M:%S)] $label done exit=$?"
done

echo "[batch $(date -u +%H:%M:%S)] ALL DONE — sending webhook"
curl -sf -X POST http://localhost:8081/api/tools/obvious/pool/refresh \
  -H 'Content-Type: application/json' \
  -d '{"source":"batch_provision"}' && echo 'webhook OK' || echo 'webhook failed (ok if endpoint not exist yet)'
