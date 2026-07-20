/**
 * Notifier — 任务完成推送
 * 1. 始终通过 ntfy.sh 推送（无需任何账号）
 * 2. 始终写入本地 /tmp/toolkit_accounts.jsonl 备查
 * 3. 若 configs 表有手动配置的 smtp_config，则额外发 SMTP 邮件
 */
import nodemailer from 'nodemailer';
import { jobQueue } from './job-queue.js';
import { query } from '../db.js';

const TO          = 'dreamer707@duck.com';
const NTFY_TOPIC  = 'toolkit-accounts-dreamer707';
const NTFY_URL    = `https://ntfy.sh/${NTFY_TOPIC}`;
const ACCOUNTS_LOG = '/tmp/toolkit_accounts.jsonl';

interface SmtpConfig { host: string; port: number; secure: boolean; user: string; pass: string }

// ── 1. ntfy.sh 推送 ──────────────────────────────────────────────────────────
async function sendNtfy(
  title: string,
  accounts: Array<{ email: string; password: string; token?: string }>,
): Promise<void> {
  const lines = accounts.map(a =>
    `${a.email}  |  ${a.password ?? '-'}${a.token ? `  |  ${a.token.slice(0, 40)}...` : ''}`
  );
  const body = `共 ${accounts.length} 个账号:\n` + lines.join('\n');

  try {
    const res = await fetch(NTFY_URL, {
      method: 'POST',
      headers: {
        'Title':    encodeURIComponent(title),
        'Priority': 'high',
        'Tags':     'white_check_mark,robot',
        'Content-Type': 'text/plain; charset=utf-8',
      },
      body,
    });
    if (res.ok) {
      console.log(`[Notifier] ✅ ntfy.sh 推送成功 → https://ntfy.sh/${NTFY_TOPIC}`);
    } else {
      console.warn('[Notifier] ntfy.sh 返回:', res.status);
    }
  } catch (err) {
    console.error('[Notifier] ntfy.sh 推送失败:', err);
  }
}

// ── 2. 本地日志 ───────────────────────────────────────────────────────────────
async function writeLocalLog(
  accounts: Array<{ email: string; password: string; token?: string; username?: string }>,
  platform: string,
  jobId: string,
): Promise<void> {
  try {
    const { appendFileSync } = await import('fs');
    const entry = JSON.stringify({ ts: new Date().toISOString(), platform, jobId, accounts }) + '\n';
    appendFileSync(ACCOUNTS_LOG, entry, 'utf-8');
    console.log(`[Notifier] ✅ 账号已写入本地文件: ${ACCOUNTS_LOG}`);
  } catch (err) {
    console.error('[Notifier] 本地日志写入失败:', err);
  }
}

// ── 3. SMTP（仅当 configs 表有手动配置时才尝试）───────────────────────────────
async function resolveManualSmtp(): Promise<SmtpConfig | null> {
  try {
    const rows = await query<{ value: string }>(
      "SELECT value FROM configs WHERE key = 'smtp_config' LIMIT 1"
    );
    if (rows[0]) return JSON.parse(rows[0].value) as SmtpConfig;
  } catch {}
  return null;
}

async function sendSmtp(subject: string, html: string): Promise<void> {
  const cfg = await resolveManualSmtp();
  if (!cfg) return; // 没有手动配置就不尝试

  const transporter = nodemailer.createTransport({
    host: cfg.host, port: cfg.port, secure: cfg.secure,
    auth: { user: cfg.user, pass: cfg.pass },
    tls:  { rejectUnauthorized: false },
  });
  try {
    await transporter.sendMail({ from: `"AI Toolkit" <${cfg.user}>`, to: TO, subject, html });
    console.log(`[Notifier] ✅ SMTP 邮件发送成功 → ${TO}`);
  } catch (err) {
    console.error('[Notifier] ❌ SMTP 失败:', err);
  }
}

// ── HTML 模板 ─────────────────────────────────────────────────────────────────
function buildHtml(
  accounts: Array<{ email: string; password: string; token?: string; username?: string }>,
  platform: string,
  jobId: string,
): string {
  const hasToken = accounts.some(a => a.token);
  const rows = accounts.map(a =>
    `<tr>
      <td style="padding:6px 12px;border-bottom:1px solid #30363d">${a.email}</td>
      <td style="padding:6px 12px;border-bottom:1px solid #30363d;font-family:monospace">${a.password ?? '-'}</td>
      ${hasToken ? `<td style="padding:6px 12px;border-bottom:1px solid #30363d;font-family:monospace;word-break:break-all;max-width:320px">${a.token ?? '-'}</td>` : ''}
    </tr>`
  ).join('');

  return `<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="background:#0d1117;color:#e6edf3;font-family:-apple-system,sans-serif;margin:0;padding:24px">
  <div style="max-width:700px;margin:0 auto">
    <h2 style="color:#3fb950;margin-bottom:4px">✅ 注册任务完成</h2>
    <p style="color:#8b949e;margin-top:0">
      平台: <strong style="color:#e6edf3">${platform}</strong>
      &nbsp;|&nbsp;
      任务ID: <code style="background:#161b22;padding:2px 6px;border-radius:4px">${jobId}</code>
    </p>
    <p style="color:#8b949e">成功注册 <strong style="color:#3fb950;font-size:18px">${accounts.length}</strong> 个账号：</p>
    <table style="width:100%;border-collapse:collapse;background:#161b22;border-radius:8px;overflow:hidden">
      <thead>
        <tr style="background:#21262d">
          <th style="padding:8px 12px;text-align:left;color:#8b949e;font-weight:500">邮箱</th>
          <th style="padding:8px 12px;text-align:left;color:#8b949e;font-weight:500">密码</th>
          ${hasToken ? '<th style="padding:8px 12px;text-align:left;color:#8b949e;font-weight:500">Token</th>' : ''}
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="color:#484f58;font-size:12px;margin-top:16px">
      AI Toolkit · ${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}
    </p>
  </div>
</body></html>`;
}

// ── 主入口 ────────────────────────────────────────────────────────────────────
export function initNotifier(): void {
  jobQueue.subscribe('done', async (job) => {
    if (!job.accounts.length) return;

    const platform = job.jobId.startsWith('cur_') ? 'Cursor'
                   : job.jobId.startsWith('reg_') ? 'Outlook'
                   : '未知平台';

    const title = `[AI Toolkit] ${platform} 注册成功 ${job.accounts.length} 个账号`;

    // 并行执行：ntfy.sh + 本地日志 + (可选)SMTP
    await Promise.all([
      sendNtfy(title, job.accounts),
      writeLocalLog(job.accounts, platform, job.jobId),
      sendSmtp(title, buildHtml(job.accounts, platform, job.jobId)),
    ]);
  });

  console.log(`[Notifier] 推送已就绪 → ntfy.sh topic: ${NTFY_TOPIC} | 本地日志: ${ACCOUNTS_LOG}`);
  console.log(`[Notifier] 订阅地址: https://ntfy.sh/${NTFY_TOPIC}`);
}
