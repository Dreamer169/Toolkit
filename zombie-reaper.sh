#!/bin/bash
# 僵尸进程自动清理守护脚本
# 每60秒扫描一次，对僵尸父进程发SIGCHLD，若无效则检查父进程是否孤立(ppid=1)再SIGKILL

mkdir -p /tmp/toolkit_logs
LOG=/tmp/toolkit_logs/zombie-reaper.log

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $LOG; }

log "僵尸清理守护启动"

while true; do
  ZOMBIES=$(ps aux | awk '$8=="Z" {print $2}')
  COUNT=$(echo "$ZOMBIES" | grep -c [0-9] 2>/dev/null || echo 0)

  if [ "$COUNT" -gt 0 ]; then
    log "发现 $COUNT 个僵尸进程，开始清理..."

    for zpid in $ZOMBIES; do
      PPID=$(ps -o ppid= -p $zpid 2>/dev/null | tr -d  )
      [ -z "$PPID" ] && continue

      # 先发SIGCHLD让父进程回收
      kill -SIGCHLD $PPID 2>/dev/null
    done

    sleep 2

    # 检查是否仍有僵尸
    REMAINING=$(ps aux | awk '$8=="Z" {print $2}')
    RCOUNT=$(echo "$REMAINING" | grep -c [0-9] 2>/dev/null || echo 0)

    if [ "$RCOUNT" -gt 0 ]; then
      for zpid in $REMAINING; do
        PPID=$(ps -o ppid= -p $zpid 2>/dev/null | tr -d  )
        [ -z "$PPID" ] && continue
        GRANDPPID=$(ps -o ppid= -p $PPID 2>/dev/null | tr -d  )
        PCMD=$(ps -o comm= -p $PPID 2>/dev/null)

        # 只SIGKILL孤立父进程(ppid=1)
        if [ "$GRANDPPID" = "1" ]; then
          log "孤立父进程 $PPID ($PCMD) ppid=1 → SIGKILL"
          kill -9 $PPID 2>/dev/null
        else
          log "父进程 $PPID ($PCMD) 有活跃祖父 $GRANDPPID，跳过"
        fi
      done
      sleep 1
      FINAL=$(ps aux | awk '$8=="Z"' | wc -l)
      log "清理后剩余僵尸: $FINAL"
    else
      log "SIGCHLD 已清除所有僵尸"
    fi
  fi

  sleep 60
done
