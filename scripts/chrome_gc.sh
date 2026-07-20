#!/bin/bash
# chrome_gc.sh — 清理运行超过 5 分钟的孤立 Chrome 进程
# 每 5 分钟由 cron 执行

LOG=/tmp/toolkit_logs/chrome_gc.log
THRESHOLD=300   # 秒，超过此时间的 Chrome 视为僵尸
MAX_CHROME=20   # 若总数超过此值强制清理最老的
mkdir -p /tmp/toolkit_logs

NOW=$(date +%s)
CHROME_PIDS=$(ps aux | grep -E "chromium|chrome-linux64" | grep -v grep | awk "{print \$2}")
TOTAL=$(echo "$CHROME_PIDS" | grep -c . || true)

echo "[$(date "+%H:%M:%S")] Chrome total=$TOTAL max=$MAX_CHROME" >> $LOG

KILLED=0
for pid in $CHROME_PIDS; do
    # 读取进程启动时间（/proc/PID/stat 第22列 = starttime ticks）
    STAT="/proc/$pid/stat"
    [ -f "$STAT" ] || continue
    START_TICKS=$(awk "{print \$22}" "$STAT" 2>/dev/null)
    [ -z "$START_TICKS" ] && continue
    BTIME=$(grep "^btime" /proc/stat | awk "{print \$2}")
    PROC_START=$(( BTIME + START_TICKS / 100 ))
    AGE=$(( NOW - PROC_START ))
    if [ $AGE -gt $THRESHOLD ]; then
        kill -9 $pid 2>/dev/null && {
            KILLED=$((KILLED+1))
            echo "[$(date "+%H:%M:%S")] killed pid=$pid age=${AGE}s" >> $LOG
        }
    fi
done

# 兜底：若超过 MAX_CHROME 条，杀掉最老的超出部分
REMAINING=$(ps aux | grep -E "chromium|chrome-linux64" | grep -v grep | awk "{print \$2}" | wc -l)
if [ $REMAINING -gt $MAX_CHROME ]; then
    OVER=$(( REMAINING - MAX_CHROME ))
    echo "[$(date "+%H:%M:%S")] still $REMAINING > $MAX_CHROME, force-kill oldest $OVER" >> $LOG
    ps aux --sort=etime | grep -E "chromium|chrome-linux64" | grep -v grep | tail -$OVER | awk "{print \$2}" | xargs -r kill -9 2>/dev/null
    KILLED=$((KILLED+OVER))
fi

echo "[$(date "+%H:%M:%S")] done killed=$KILLED remaining=$(ps aux | grep -E "chromium|chrome-linux64" | grep -v grep | wc -l)" >> $LOG
