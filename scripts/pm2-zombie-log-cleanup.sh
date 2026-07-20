#!/bin/bash
# 清零长期停写的僵尸 pm2 日志（进程已停，日志文件超过 2 天未更新且 > 5MB）
LOG_DIR="/data/pm2-logs"
THRESHOLD_DAYS=2
THRESHOLD_SIZE_MB=5
CLEANED=0

for f in "$LOG_DIR"/*.log; do
  [ -f "$f" ] || continue
  # 跳过轮转归档（带日期戳的）
  [[ "$f" == *"__20"* ]] && continue
  # 文件大小 > 阈值
  size_mb=$(du -m "$f" 2>/dev/null | cut -f1)
  [ "${size_mb:-0}" -lt "$THRESHOLD_SIZE_MB" ] && continue
  # 最后修改时间 > 阈值天数
  if find "$f" -mtime "+${THRESHOLD_DAYS}" | grep -q .; then
    echo "[$(date +%F\ %T)] 清零僵尸日志: $f (${size_mb}MB, 超 ${THRESHOLD_DAYS}d 未写入)"
    > "$f"
    CLEANED=$((CLEANED+1))
  fi
done

echo "[$(date +%F\ %T)] 完成，共清零 $CLEANED 个僵尸日志"
