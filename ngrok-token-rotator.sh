#!/bin/bash
# Watch all ngrok pm2 services; rotate authtoken on auth/limit errors.
POOL_FILE=/root/.config/ngrok/token-pool.txt
STATE=/root/.config/ngrok/token-state.json
LOGDIR=/root/.pm2/logs

declare -A SVC=(
  [ngrok]=/root/.config/ngrok/ngrok.yml
  [ngrok-gateway]=/root/.config/ngrok-gateway/ngrok.yml
  [ngrok-apiserver]=/root/.config/ngrok-sub2api/ngrok.yml
)

ERR_PATTERNS='ERR_NGROK_(108|105|3200|4018|9009)|authentication failed|invalid credentials|account limited|The authtoken|simultaneous ngrok agent sessions|tunnel session failed'

mapfile -t POOL < "$POOL_FILE"
[ ${#POOL[@]} -eq 0 ] && { echo "empty pool"; exit 1; }

next_token() {
  local svc="$1"
  local cur next
  cur=$(jq -r --arg s "$svc" '.[$s] // -1' "$STATE" 2>/dev/null)
  [[ "$cur" =~ ^-?[0-9]+$ ]] || cur=-1
  next=$(( (cur + 1) % ${#POOL[@]} ))
  jq --arg s "$svc" --argjson i "$next" '.[$s]=$i' "$STATE" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
  echo "${POOL[$next]}"
}

rotate() {
  local svc="$1" cfg="$2"
  local tok
  tok=$(next_token "$svc")
  echo "[$(date '+%F %T')] [$svc] FAILURE detected, rotating authtoken -> ...${tok: -8}"
  mkdir -p "$(dirname "$cfg")"
  cat > "$cfg" <<CFG
version: "3"
agent:
    authtoken: $tok
CFG
  pm2 restart "$svc" >/dev/null 2>&1 || true
}

echo "[$(date '+%F %T')] ngrok-token-rotator started; pool=${#POOL[@]} services=${!SVC[@]}"
while true; do
  for svc in "${!SVC[@]}"; do
    cfg="${SVC[$svc]}"
    out="$LOGDIR/${svc}-out.log"
    err="$LOGDIR/${svc}-error.log"
    recent=$(tail -n 50 "$out" "$err" 2>/dev/null)
    if echo "$recent" | grep -qiE "$ERR_PATTERNS"; then
      rotate "$svc" "$cfg"
      sleep 5
      : > "$out" 2>/dev/null || true
      : > "$err" 2>/dev/null || true
    fi
  done
  sleep 30
done
