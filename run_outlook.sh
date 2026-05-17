#!/bin/bash
# Outlook 注册工作流（循环版 - 通过 API job 系统，前端可见）
export DATABASE_URL="postgresql://postgres:postgres@localhost/toolkit"
export PLAYWRIGHT_BROWSERS_PATH="/data/cache/ms-playwright"
export PATCHRIGHT_BROWSERS_PATH="/data/cache/ms-playwright"
export XRAY_LOCATION_ASSET="/data/outlook-workflow/xray"

API_BASE="http://localhost:8081/api"
LOOP_SLEEP=5

# 收到停止信号时立即终止所有子进程并退出
_cleanup() {
  echo "[loop] 收到停止信号，正在退出..."
  kill 0
  exit 0
}
trap _cleanup SIGTERM SIGINT

echo "======================================="
echo "  Outlook 注册工作流（持续循环 via API）$(date)"
echo "======================================="

ROUND=0
while true; do
    ROUND=$((ROUND+1))
    echo ""
    echo "━━━ 第 $ROUND 轮  $(date) ━━━"

    RESPONSE=$(curl -s -X POST "$API_BASE/tools/outlook/full-workflow" \
        -H "Content-Type: application/json" \
        -d '{"count":1,"skip_imap":true,"headless":true}' 2>/dev/null)

    JOB_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(chr(106)+chr(111)+chr(98)+chr(73)+chr(100),chr(32)))" 2>/dev/null | tr -d " ")
    SUCCESS=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(chr(115)+chr(117)+chr(99)+chr(99)+chr(101)+chr(115)+chr(115),False))" 2>/dev/null)

    if [ -z "$JOB_ID" ] || [ "$SUCCESS" != "True" ]; then
        echo "[loop] API 启动失败: $RESPONSE"
        sleep $LOOP_SLEEP
        continue
    fi

    echo "[loop] job 已启动: $JOB_ID"

    SINCE=0
    while true; do
        sleep 4
        STATUS_RESP=$(curl -s "$API_BASE/tools/outlook/full-workflow/$JOB_ID?since=$SINCE" 2>/dev/null)
        STATUS=$(echo "$STATUS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(chr(115)+chr(116)+chr(97)+chr(116)+chr(117)+chr(115),chr(63)))" 2>/dev/null)
        NEXT_SINCE=$(echo "$STATUS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get(chr(110)+chr(101)+chr(120)+chr(116)+chr(83)+chr(105)+chr(110)+chr(99)+chr(101),0))" 2>/dev/null)

        echo "$STATUS_RESP" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for log in d.get(\"logs\",[]):
        print(log.get(\"message\",\"\"))
except: pass
" 2>/dev/null

        SINCE=${NEXT_SINCE:-$SINCE}

        if [ "$STATUS" = "done" ] || [ "$STATUS" = "failed" ] || [ "$STATUS" = "crashed" ]; then
            ACCS=$(echo "$STATUS_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
accs=d.get(\"accounts\",[])
print(f\"结果: {len(accs)}个账号\", \" \".join(a.get(\"email\",\"\") for a in accs))
" 2>/dev/null)
            echo "[loop] 第 $ROUND 轮结束 status=$STATUS $ACCS"
            break
        fi
    done

    echo "[loop] ${LOOP_SLEEP}s 后下一轮..."
    sleep $LOOP_SLEEP
done
