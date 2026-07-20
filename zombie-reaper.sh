#!/bin/bash
# 僵尸进程自动清理守护脚本
# 1) 每60秒扫描 Z 状态真正僵尸进程并清理
# 2) 每60秒扫描运行超过2小时的卡死 outlook_register/patchright/chromium 进程并清理

mkdir -p /tmp/toolkit_logs
LOG=/tmp/toolkit_logs/zombie-reaper.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

log "僵尸清理守护启动"

while true; do
  # === 1. 清理 Z 状态真僵尸 ===
  ZOMBIES=$(ps aux | awk '$8=="Z" {print $2}')
  COUNT=$(echo "$ZOMBIES" | grep -c '[0-9]' 2>/dev/null || echo 0)

  if [ "$COUNT" -gt 0 ] 2>/dev/null; then
    log "发现 $COUNT 个Z状态僵尸进程，开始清理..."
    for zpid in $ZOMBIES; do
      _PPID=$(ps -o ppid= -p $zpid 2>/dev/null | tr -d ' ')
      [ -z "$_PPID" ] && continue
      kill -SIGCHLD $_PPID 2>/dev/null
    done

    sleep 2
    REMAINING=$(ps aux | awk '$8=="Z" {print $2}')
    RCOUNT=$(echo "$REMAINING" | grep -c '[0-9]' 2>/dev/null || echo 0)

    if [ "$RCOUNT" -gt 0 ] 2>/dev/null; then
      for zpid in $REMAINING; do
        _PPID=$(ps -o ppid= -p $zpid 2>/dev/null | tr -d ' ')
        [ -z "$_PPID" ] && continue
        GRANDPPID=$(ps -o ppid= -p $_PPID 2>/dev/null | tr -d ' ')
        PCMD=$(ps -o comm= -p $_PPID 2>/dev/null)
        if [ "$GRANDPPID" = "1" ]; then
          log "孤立父进程 $_PPID ($PCMD) ppid=1 → SIGKILL"
          kill -9 $_PPID 2>/dev/null
        fi
      done
    fi
  fi

  # === 2. 清理运行超过 7200 秒(2h)的卡死长进程 ===
  STUCK_PATTERNS="outlook_register|oxylabs_register|kiro_register|ip2free_register|webshare_register"
  while IFS= read -r line; do
    PID=$(echo "$line" | awk '{print $1}')
    ELAPSED=$(echo "$line" | awk '{print $2}')
    CMD=$(echo "$line" | awk '{print $3}')
    [ -z "$PID" ] && continue
    log "卡死长进程 PID=$PID elapsed=${ELAPSED}s ($CMD) → SIGKILL"
    kill -9 $PID 2>/dev/null
  done < <(ps -eo pid,etimes,cmd --no-headers | awk "\$2>7200 && \$3~/python/ && \$0~/(outlook_register|oxylabs_register|kiro_register|ip2free_register|webshare_register)/")

  # === 3. 清理运行超过 3600 秒(1h)的孤立 patchright/chromium 进程 ===
  while IFS= read -r line; do
    PID=$(echo "$line" | awk '{print $1}')
    ELAPSED=$(echo "$line" | awk '{print $2}')
    [ -z "$PID" ] && continue
    _PPID=$(ps -o ppid= -p $PID 2>/dev/null | tr -d ' ')
    # 只清理父进程是 init(1) 或不存在的孤立进程
    if [ "$_PPID" = "1" ] || [ -z "$_PPID" ]; then
      log "孤立浏览器进程 PID=$PID elapsed=${ELAPSED}s → SIGKILL"
      kill -9 $PID 2>/dev/null
    fi
  done < <(ps -eo pid,etimes,cmd --no-headers | awk "\$2>3600 && (\$0~/patchright\/driver\/node/ || \$0~/chrome-linux64\/chrome/)")

  sleep 60
done
