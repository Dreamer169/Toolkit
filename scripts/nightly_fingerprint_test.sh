#!/usr/bin/env bash
# nightly_fingerprint_test.sh
# 每晚自动跑9平台指纹检测 → 保存结果 → gh api推送GitHub → 发邮件
# 位置: /root/Toolkit/scripts/nightly_fingerprint_test.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLKIT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TEST_SCRIPT="$TOOLKIT_DIR/browser-model/artifacts/api-server/test_9platforms_v2.mjs"
RESULTS_DIR="$TOOLKIT_DIR/docs/test-results"
ALERT_SCRIPT="$TOOLKIT_DIR/scripts/send_alert.py"
LOG_DIR="/tmp/toolkit_logs"
REPO="Dreamer169/Toolkit"
BRANCH="main"

mkdir -p "$RESULTS_DIR" "$LOG_DIR"

DATE=$(date +%Y-%m-%d)
TIME=$(date +%H:%M)
RESULT_FILE="$RESULTS_DIR/${DATE}.log"
RUN_LOG="$LOG_DIR/nightly_fp_test.log"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] $*" | tee -a "$RUN_LOG"; }

log "=== 夜间指纹检测开始 ==="

# ── 运行测试 (超时保护 30 分钟) ───────────────────────────────────────────────
cd "$TOOLKIT_DIR/browser-model/artifacts/api-server"
TMPLOG="$LOG_DIR/fp_run_${DATE}.tmp"
timeout 1800 node "$TEST_SCRIPT" > "$TMPLOG" 2>&1 || true
log "测试运行完毕"

# ── 写入本地结果文件 ──────────────────────────────────────────────────────────
{
    echo "# 9平台指纹检测 $DATE $TIME (host=$(hostname))"
    echo ""
    cat "$TMPLOG"
} > "$RESULT_FILE"
log "结果已写入 $RESULT_FILE"

# ── 解析 pass/fail ─────────────────────────────────────────────────────────────
PASSED=$(grep -oP '\d+(?=/\d+ PASSED)' "$TMPLOG" 2>/dev/null | tail -1 || echo "?")
TOTAL=$(grep  -oP '(?<=\d/)\d+(?= PASSED)' "$TMPLOG" 2>/dev/null | tail -1 || echo "9")
FAILED_LIST=$(grep "❌" "$TMPLOG" 2>/dev/null || echo "  (无失败项)")
log "检测结果: $PASSED/$TOTAL PASSED"

# ── 推送到 GitHub (gh api，直接上传文件) ─────────────────────────────────────
log "推送测试结果到 GitHub ..."
CONTENT_B64=$(base64 -w0 "$RESULT_FILE")
REMOTE_PATH="docs/test-results/${DATE}.log"
EXISTING_SHA=$(gh api "repos/$REPO/contents/$REMOTE_PATH" --jq '.sha' 2>/dev/null || echo "")

if [ -n "$EXISTING_SHA" ]; then
    gh api "repos/$REPO/contents/$REMOTE_PATH" \
        --method PUT \
        --field "message=test-results: $DATE $PASSED/$TOTAL PASSED" \
        --field "content=$CONTENT_B64" \
        --field "branch=$BRANCH" \
        --field "sha=$EXISTING_SHA" \
        --jq '.commit.sha' >> "$RUN_LOG" 2>&1 \
        && log "✅ GitHub 更新成功 (覆盖已有文件)" \
        || log "⚠️  GitHub 推送失败"
else
    gh api "repos/$REPO/contents/$REMOTE_PATH" \
        --method PUT \
        --field "message=test-results: $DATE $PASSED/$TOTAL PASSED" \
        --field "content=$CONTENT_B64" \
        --field "branch=$BRANCH" \
        --jq '.commit.sha' >> "$RUN_LOG" 2>&1 \
        && log "✅ GitHub 创建成功 (新文件)" \
        || log "⚠️  GitHub 推送失败"
fi

# ── 构建邮件 ──────────────────────────────────────────────────────────────────
if [ "$PASSED" = "$TOTAL" ] && [ "$TOTAL" != "?" ]; then
    SUBJECT="[Toolkit] 指纹检测 $DATE ✅ $PASSED/$TOTAL PASSED"
    STATUS_COLOR="#22c55e"
    STATUS_TEXT="全部通过"
else
    SUBJECT="[Toolkit] 指纹检测 $DATE ❌ $PASSED/$TOTAL — 需要关注"
    STATUS_COLOR="#ef4444"
    STATUS_TEXT="存在失败"
fi

BODY_PRETEXT=$(cat "$TMPLOG" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
FAILED_HTML=$(echo "$FAILED_LIST" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')

BODY_HTML="<html><body style='font-family:monospace;background:#0f0f0f;color:#e5e5e5;padding:20px;'>
<h2 style='color:#60a5fa;margin-bottom:8px;'>🔍 Toolkit 9平台指纹检测报告</h2>
<table style='border-collapse:collapse;margin-bottom:16px;'>
  <tr><td style='padding:4px 14px 4px 0;color:#9ca3af;'>日期</td><td><b>$DATE $TIME</b></td></tr>
  <tr><td style='padding:4px 14px 4px 0;color:#9ca3af;'>主机</td><td>$(hostname)</td></tr>
  <tr><td style='padding:4px 14px 4px 0;color:#9ca3af;'>结果</td>
      <td><b style='font-size:18px;color:$STATUS_COLOR;'>$PASSED/$TOTAL PASSED — $STATUS_TEXT</b></td></tr>
</table>
$([ "$PASSED" != "$TOTAL" ] && printf '<h3 style="color:#ef4444;">❌ 失败项:</h3><pre style="background:#1c1917;color:#fca5a5;padding:10px;border-left:3px solid #ef4444;border-radius:4px;">%s</pre>' "$FAILED_HTML" || echo '<p style="color:#22c55e;">✅ 所有平台均通过，无需处理。</p>')
<hr style='border:none;border-top:1px solid #374151;margin:16px 0;'>
<h3 style='color:#9ca3af;'>完整输出:</h3>
<pre style='background:#1e1e2e;color:#cdd6f4;padding:14px;border-radius:8px;overflow:auto;font-size:12px;line-height:1.6;'>$BODY_PRETEXT</pre>
<p style='color:#4b5563;font-size:11px;margin-top:20px;'>
  自动生成 by nightly_fingerprint_test.sh @ $(hostname) &mdash; $(ts)<br>
  源码: <a href='https://github.com/Dreamer169/Toolkit' style='color:#60a5fa;'>github.com/Dreamer169/Toolkit</a>
</p>
</body></html>"

# ── 发送邮件 ──────────────────────────────────────────────────────────────────
log "发送邮件 → rjrbaphnak@rommiui.com ..."
python3 "$ALERT_SCRIPT" "$SUBJECT" "$BODY_HTML" >> "$RUN_LOG" 2>&1 \
    && log "✅ 邮件发送成功" \
    || log "⚠️  邮件发送失败（测试记录已在 GitHub 保存）"

log "=== 夜间检测完成: $PASSED/$TOTAL ==="
