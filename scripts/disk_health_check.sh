#!/bin/bash
# disk_health_check.sh — 校验关键磁盘布局
# Exit code:
#   0 = healthy
#   1 = warning (degraded but OK to continue)
#   2 = critical (should block registration tasks)
#
# 检查项:
#   1. /root/.cache/ms-playwright 是符号链接且指向 /data
#   2. 该符号链接目标存在且非空
#   3. pnpm store-dir 在 /data 上
#   4. 根盘 (/) 可用空间 > 5G (warn) / > 1G (critical)
#   5. /data 已挂载且可写

PREFIX='[DISK-CHECK]'
WARN=0
CRIT=0

# 1 + 2: ms-playwright symlink
PW_LINK=/root/.cache/ms-playwright
if [ ! -L "$PW_LINK" ]; then
    echo "$PREFIX CRITICAL: $PW_LINK 不是符号链接（可能已退化为实目录，根盘随时撑爆）"
    CRIT=1
else
    PW_TARGET=$(readlink -f "$PW_LINK")
    case "$PW_TARGET" in
        /data/*)
            if [ ! -d "$PW_TARGET" ] || [ -z "$(ls -A "$PW_TARGET" 2>/dev/null)" ]; then
                echo "$PREFIX CRITICAL: 软链目标 $PW_TARGET 不存在或为空（chromium 启动会失败）"
                CRIT=1
            else
                CHROMIUM=$(ls -d "$PW_TARGET"/chromium-* 2>/dev/null | head -1)
                if [ -z "$CHROMIUM" ]; then
                    echo "$PREFIX WARN: 软链目标 $PW_TARGET 无 chromium-* 子目录"
                    WARN=1
                else
                    echo "$PREFIX OK ms-playwright: → $PW_TARGET ($(basename "$CHROMIUM"))"
                fi
            fi
            ;;
        *)
            echo "$PREFIX WARN: 软链目标 $PW_TARGET 不在 /data，根盘可能被占用"
            WARN=1
            ;;
    esac
fi

# 3: pnpm store-dir
PNPM_STORE=$(pnpm config get store-dir 2>/dev/null)
case "$PNPM_STORE" in
    /data/*)
        echo "$PREFIX OK pnpm store: $PNPM_STORE"
        ;;
    undefined|"")
        echo "$PREFIX WARN: pnpm store-dir 未设置（默认会落到根盘 ~/.local/share/pnpm/store）"
        WARN=1
        ;;
    *)
        echo "$PREFIX WARN: pnpm store-dir = $PNPM_STORE，不在 /data"
        WARN=1
        ;;
esac

# 4: 根盘可用空间
ROOT_AVAIL_KB=$(df -P / | awk 'NR==2 {print $4}')
ROOT_AVAIL_GB=$((ROOT_AVAIL_KB / 1024 / 1024))
ROOT_USE=$(df -P / | awk 'NR==2 {print $5}')
if [ "$ROOT_AVAIL_GB" -lt 1 ]; then
    echo "$PREFIX CRITICAL: 根盘可用仅 ${ROOT_AVAIL_GB}G ($ROOT_USE used) — 注册任务可能因写入失败崩溃"
    CRIT=1
elif [ "$ROOT_AVAIL_GB" -lt 5 ]; then
    echo "$PREFIX WARN: 根盘可用 ${ROOT_AVAIL_GB}G ($ROOT_USE used) — 建议清理"
    WARN=1
else
    echo "$PREFIX OK 根盘: ${ROOT_AVAIL_GB}G 可用 ($ROOT_USE used)"
fi

# 5: /data 挂载
if ! mountpoint -q /data 2>/dev/null; then
    echo "$PREFIX CRITICAL: /data 未挂载（软链全部失效）"
    CRIT=1
else
    DATA_AVAIL_KB=$(df -P /data | awk 'NR==2 {print $4}')
    DATA_AVAIL_GB=$((DATA_AVAIL_KB / 1024 / 1024))
    DATA_USE=$(df -P /data | awk 'NR==2 {print $5}')
    if [ ! -w /data ]; then
        echo "$PREFIX CRITICAL: /data 不可写"
        CRIT=1
    elif [ "$DATA_AVAIL_GB" -lt 2 ]; then
        echo "$PREFIX WARN: /data 可用 ${DATA_AVAIL_GB}G ($DATA_USE used)"
        WARN=1
    else
        echo "$PREFIX OK /data: ${DATA_AVAIL_GB}G 可用 ($DATA_USE used)"
    fi
fi

if [ $CRIT -eq 1 ]; then exit 2; fi
if [ $WARN -eq 1 ]; then exit 1; fi
exit 0
