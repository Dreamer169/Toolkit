#!/bin/bash
NODES=(
  "https://8805863e-ded2-4f32-a0fb-2ea4e79524ed-00-1tx5loj1rfizc.janeway.replit.dev/health"
  "https://91da9028-2e6e-4cb3-b2af-971214174ff2-00-1lkrg8pehtpqv.kirk.replit.dev/api/gateway/health"
  "https://c424491c-db06-452b-90ff-a772aa24c1fe-00-38dq9bvr44ayf.spock.replit.dev/api/gateway/health"
  "https://gh-cli-install--bandersonndz.replit.app/api/gateway/health"
  "https://957ee4dd-b94b-4405-8373-7a1bd9558eff-00-1kr7ptb11d9tn.spock.replit.dev/api/gateway/health"
  "https://070cdd29-102a-42d2-9c53-73d2fdc045eb-00-1fwt4nf2u3x4i.picard.replit.dev/api/gateway/health"
)
INTERVAL=150
STARTUP_DELAY=30
sleep $STARTUP_DELAY
while true; do
  for NODE_URL in "${NODES[@]}"; do
    RESP=$(curl -sf --max-time 12 -H "ngrok-skip-browser-warning: 1" "$NODE_URL" 2>/dev/null)
    if [ -n "$RESP" ]; then
      echo "$(date +%H:%M:%S) [keepalive] OK ${NODE_URL##*/}"
    else
      echo "$(date +%H:%M:%S) [keepalive] DEAD $NODE_URL"
    fi
  done
  sleep $INTERVAL
done
