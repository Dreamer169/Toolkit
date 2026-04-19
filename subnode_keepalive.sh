#!/bin/bash
NODES=(
  "https://8805863e-ded2-4f32-a0fb-2ea4e79524ed-00-1tx5loj1rfizc.janeway.replit.dev/health"
)
INTERVAL=150
STARTUP_DELAY=30
sleep $STARTUP_DELAY
while true; do
  for NODE_URL in "${NODES[@]}"; do
    RESP=$(curl -sf --max-time 12 "$NODE_URL" 2>/dev/null)
    if [ -n "$RESP" ]; then
      echo "$(date +%H:%M:%S) [keepalive] OK"
    else
      echo "$(date +%H:%M:%S) [keepalive] DEAD $NODE_URL"
    fi
  done
  sleep $INTERVAL
done
