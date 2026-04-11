import { Router, type IRouter } from "express";
import { createHash, randomBytes, randomUUID } from "crypto";

const router: IRouter = Router();

// ── 工具函数 ──────────────────────────────────────────────
function newMachineId() {
  return createHash("sha256").update(randomBytes(32)).digest("hex");
}
function newUUID() { return randomUUID(); }
function newSqmId() { return `{${randomUUID().toUpperCase()}}`; }

const UA_POOL = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
];

const SCREEN_PROFILES = [
  { w: 1920, h: 1080, dpr: 1.0, innerW: 1920, innerH: 937 },
  { w: 2560, h: 1440, dpr: 1.0, innerW: 2560, innerH: 1297 },
  { w: 1366, h: 768,  dpr: 1.0, innerW: 1366, innerH: 625 },
  { w: 1440, h: 900,  dpr: 2.0, innerW: 1440, innerH: 757 },
  { w: 1512, h: 982,  dpr: 2.0, innerW: 1512, innerH: 839 },
  { w: 2880, h: 1800, dpr: 2.0, innerW: 1440, innerH: 837 },
  { w: 1280, h: 720,  dpr: 1.0, innerW: 1280, innerH: 577 },
  { w: 3840, h: 2160, dpr: 2.0, innerW: 1920, innerH: 1017 },
  { w: 1600, h: 900,  dpr: 1.25, innerW: 1280, innerH: 720 },
  { w: 2560, h: 1600, dpr: 2.0, innerW: 1280, innerH: 798 },
];

const TIMEZONES = [
  { tz: "America/New_York",    offset: -5, locale: "en-US" },
  { tz: "America/Chicago",     offset: -6, locale: "en-US" },
  { tz: "America/Los_Angeles", offset: -8, locale: "en-US" },
  { tz: "Europe/London",       offset: 0,  locale: "en-GB" },
  { tz: "Europe/Paris",        offset: 1,  locale: "fr-FR" },
  { tz: "Asia/Tokyo",          offset: 9,  locale: "ja-JP" },
  { tz: "Asia/Shanghai",       offset: 8,  locale: "zh-CN" },
  { tz: "Asia/Singapore",      offset: 8,  locale: "en-SG" },
  { tz: "Australia/Sydney",    offset: 10, locale: "en-AU" },
  { tz: "Europe/Berlin",       offset: 1,  locale: "de-DE" },
];

const WEBGL_PROFILES = [
  { vendor: "Google Inc. (NVIDIA)", renderer: "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)" },
  { vendor: "Google Inc. (AMD)",    renderer: "ANGLE (AMD, AMD Radeon RX 6800 XT Direct3D11 vs_5_0 ps_5_0, D3D11)" },
  { vendor: "Google Inc. (Intel)", renderer: "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)" },
  { vendor: "Apple Inc.",           renderer: "Apple M3 Pro" },
  { vendor: "Apple Inc.",           renderer: "Apple M2" },
  { vendor: "Google Inc. (NVIDIA)", renderer: "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)" },
  { vendor: "Google Inc. (AMD)",    renderer: "ANGLE (AMD, AMD Radeon RX 7900 XTX Direct3D11 vs_5_0 ps_5_0, D3D11)" },
  { vendor: "Mesa/X.org",           renderer: "Mesa Intel(R) UHD Graphics 620 (KBL GT2)" },
];

const FONT_SETS: Record<string, string[]> = {
  windows: ["Arial","Calibri","Cambria","Candara","Comic Sans MS","Consolas","Constantia","Corbel","Courier New","Georgia","Impact","Lucida Console","Palatino Linotype","Segoe UI","Tahoma","Times New Roman","Trebuchet MS","Verdana"],
  mac:     ["Arial","Helvetica Neue","Georgia","Courier New","Times New Roman","Gill Sans","Palatino","Optima","Futura","Baskerville","Menlo","Monaco","SF Pro Display"],
  linux:   ["Arial","Courier New","DejaVu Sans","DejaVu Serif","FreeMono","Liberation Mono","Liberation Sans","Times New Roman","Ubuntu","Noto Sans"],
};

function rand<T>(arr: T[]): T { return arr[Math.floor(Math.random() * arr.length)]; }
function randInt(min: number, max: number) { return Math.floor(Math.random() * (max - min + 1)) + min; }
function randHex(len: number) { return randomBytes(len).toString("hex").slice(0, len); }

function generateFingerprint() {
  const ua = rand(UA_POOL);
  const screen = rand(SCREEN_PROFILES);
  const tz = rand(TIMEZONES);
  const webgl = rand(WEBGL_PROFILES);
  const isMac = ua.includes("Macintosh") || ua.includes("Mac OS X");
  const isWin = ua.includes("Windows");
  const isMobile = ua.includes("iPhone") || ua.includes("Android");
  const fontSet = isMac ? "mac" : isWin ? "windows" : "linux";
  const canvasHash = randHex(16);
  const audioHash = (Math.random() * 0.0001 + 0.9999).toFixed(8);

  return {
    userAgent: ua,
    platform: isMobile ? (ua.includes("iPhone") ? "iPhone" : "Linux armv8l") : isMac ? "MacIntel" : "Win32",
    language: tz.locale,
    languages: [tz.locale, "en-US"],
    timezone: tz.tz,
    timezoneOffset: tz.offset * -60,
    screen: {
      width: screen.w, height: screen.h,
      availWidth: screen.w, availHeight: screen.h - 48,
      colorDepth: 24, pixelDepth: 24,
    },
    viewport: {
      innerWidth: screen.innerW, innerHeight: screen.innerH,
      outerWidth: screen.w, outerHeight: screen.h - 80,
    },
    devicePixelRatio: screen.dpr,
    webgl: webgl,
    canvas: { hash: canvasHash, winding: true },
    audio: { hash: audioHash, oscillator: (Math.random() * 0.001 + 0.124).toFixed(8) },
    fonts: FONT_SETS[fontSet],
    plugins: isMobile ? [] : [
      "PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer",
      "Microsoft Edge PDF Viewer", "WebKit built-in PDF",
    ].slice(0, randInt(0, 5)),
    doNotTrack: Math.random() > 0.7 ? "1" : null,
    cookieEnabled: true,
    hardwareConcurrency: rand([2, 4, 6, 8, 10, 12, 16, 20]),
    deviceMemory: rand([2, 4, 8, 16, 32]),
    maxTouchPoints: isMobile ? randInt(2, 5) : 0,
    connectionType: rand(["4g", "4g", "4g", "wifi", "wifi"]),
    generatedAt: new Date().toISOString(),
  };
}

const MAILTM_BASE = "https://api.mail.tm";

async function mailtmFetch(path: string, options: RequestInit = {}) {
  const res = await fetch(`${MAILTM_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string> ?? {}),
    },
  });
  const text = await res.text();
  try {
    return { ok: res.ok, status: res.status, data: JSON.parse(text) };
  } catch {
    return { ok: res.ok, status: res.status, data: text };
  }
}

router.get("/tools/email/domains", async (req, res) => {
  try {
    const result = await mailtmFetch("/domains");
    const domains = result.data?.["hydra:member"] ?? [];
    res.json({ success: true, domains: domains.map((d: { domain: string }) => d.domain) });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/email/create", async (req, res) => {
  try {
    const { address, password } = req.body as { address?: string; password?: string };
    if (!address || !password) {
      res.status(400).json({ success: false, error: "address 和 password 不能为空" });
      return;
    }
    const result = await mailtmFetch("/accounts", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    if (!result.ok) {
      res.json({ success: false, error: result.data?.detail ?? result.data ?? "创建失败" });
      return;
    }
    const tokenResult = await mailtmFetch("/token", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    res.json({
      success: true,
      account: { address, id: result.data.id },
      token: tokenResult.data?.token,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/email/token", async (req, res) => {
  try {
    const { address, password } = req.body as { address?: string; password?: string };
    if (!address || !password) {
      res.status(400).json({ success: false, error: "address 和 password 不能为空" });
      return;
    }
    const result = await mailtmFetch("/token", {
      method: "POST",
      body: JSON.stringify({ address, password }),
    });
    if (!result.ok) {
      res.json({ success: false, error: result.data?.detail ?? "登录失败" });
      return;
    }
    res.json({ success: true, token: result.data.token });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/email/messages", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    if (!token) {
      res.status(400).json({ success: false, error: "缺少 x-mail-token 请求头" });
      return;
    }
    const result = await mailtmFetch("/messages", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!result.ok) {
      res.json({ success: false, error: "获取邮件失败，Token 可能已过期" });
      return;
    }
    const messages = (result.data?.["hydra:member"] ?? []).map((m: {
      id: string;
      from: { address: string; name: string };
      subject: string;
      intro: string;
      createdAt: string;
      seen: boolean;
    }) => ({
      id: m.id,
      from: m.from,
      subject: m.subject,
      intro: m.intro,
      createdAt: m.createdAt,
      seen: m.seen,
    }));
    res.json({ success: true, messages, total: result.data?.["hydra:totalItems"] ?? 0 });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/email/messages/:id", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    const { id } = req.params as { id: string };
    if (!token) {
      res.status(400).json({ success: false, error: "缺少 x-mail-token" });
      return;
    }
    const result = await mailtmFetch(`/messages/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!result.ok) {
      res.json({ success: false, error: "获取邮件详情失败" });
      return;
    }
    res.json({ success: true, message: result.data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.delete("/tools/email/account", async (req, res) => {
  try {
    const token = req.headers["x-mail-token"] as string;
    const { accountId } = req.body as { accountId?: string };
    if (!token || !accountId) {
      res.status(400).json({ success: false, error: "缺少参数" });
      return;
    }
    await mailtmFetch(`/accounts/${accountId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    });
    res.json({ success: true });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/key-check", async (req, res) => {
  try {
    const { platform, key } = req.body as { platform?: string; key?: string };
    if (!platform || !key) {
      res.status(400).json({ success: false, error: "platform 和 key 不能为空" });
      return;
    }

    let valid = false;
    let info: Record<string, unknown> = {};
    let error = "";

    if (platform === "openai") {
      try {
        const r = await fetch("https://api.openai.com/v1/models", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { data?: Array<{ id: string }> ; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "claude") {
      try {
        const r = await fetch("https://api.anthropic.com/v1/models", {
          headers: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
          },
        });
        const data = await r.json() as { data?: Array<{ id: string }>; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "gemini") {
      try {
        const r = await fetch(
          `https://generativelanguage.googleapis.com/v1/models?key=${key}`
        );
        const data = await r.json() as { models?: Array<{ name: string }>; error?: { message: string } };
        if (r.ok && data.models) {
          valid = true;
          info = { modelCount: data.models.length, firstModel: data.models[0]?.name };
        } else {
          error = data.error?.message ?? "无效的 Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "openai-token") {
      try {
        const r = await fetch("https://api.openai.com/v1/me", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { email?: string; name?: string; error?: { message: string } };
        if (r.ok && data.email) {
          valid = true;
          info = { email: data.email, name: data.name };
        } else {
          error = data.error?.message ?? "无效的 Token";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else {
      res.status(400).json({ success: false, error: "不支持的平台" });
      return;
    }

    res.json({ success: true, valid, info, error });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/token-batch-check", async (req, res) => {
  try {
    const { tokens, platform } = req.body as { tokens?: string[]; platform?: string };
    if (!tokens || !Array.isArray(tokens) || tokens.length === 0) {
      res.status(400).json({ success: false, error: "tokens 不能为空" });
      return;
    }
    const limited = tokens.slice(0, 20);
    const results = await Promise.allSettled(
      limited.map(async (token) => {
        const trimmed = token.trim();
        if (!trimmed) return { token: trimmed, valid: false, error: "空值" };
        try {
          const endpoint =
            platform === "claude"
              ? "https://api.anthropic.com/v1/models"
              : "https://api.openai.com/v1/models";
          const headers: Record<string, string> =
            platform === "claude"
              ? { "x-api-key": trimmed, "anthropic-version": "2023-06-01" }
              : { Authorization: `Bearer ${trimmed}` };
          const r = await fetch(endpoint, { headers });
          const data = await r.json() as { data?: unknown[]; models?: unknown[]; error?: { message: string } };
          if (r.ok && (data.data || data.models)) {
            return { token: trimmed.slice(0, 12) + "...", valid: true };
          }
          return {
            token: trimmed.slice(0, 12) + "...",
            valid: false,
            error: data.error?.message ?? "无效",
          };
        } catch (e: unknown) {
          return { token: trimmed.slice(0, 12) + "...", valid: false, error: String(e) };
        }
      })
    );
    const output = results.map((r) =>
      r.status === "fulfilled" ? r.value : { valid: false, error: "请求失败" }
    );
    res.json({
      success: true,
      results: output,
      summary: {
        total: output.length,
        valid: output.filter((r) => r.valid).length,
        invalid: output.filter((r) => !r.valid).length,
      },
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 机器ID重置 ────────────────────────────────────────────
router.get("/tools/machine-id/generate", (_req, res) => {
  const machineId    = newMachineId();
  const macMachineId = newMachineId();
  const devDeviceId  = newUUID();
  const sqmId        = newSqmId();

  const paths = {
    windows: `%APPDATA%\\Cursor\\User\\globalStorage\\storage.json`,
    mac:     `~/Library/Application Support/Cursor/User/globalStorage/storage.json`,
    linux:   `~/.config/Cursor/User/globalStorage/storage.json`,
  };

  const winScript = `@echo off
:: Cursor 机器ID重置脚本 (Windows) - 由 AI Account Toolkit 生成
taskkill /F /IM cursor.exe 2>nul
set "FILE=%APPDATA%\\Cursor\\User\\globalStorage\\storage.json"
if exist "%FILE%" copy "%FILE%" "%FILE%.backup" >nul
echo 正在写入新机器ID...
powershell -Command "$j = Get-Content '%FILE%' -Raw | ConvertFrom-Json; $j.'telemetry.machineId'='${machineId}'; $j.'telemetry.macMachineId'='${macMachineId}'; $j.'telemetry.devDeviceId'='${devDeviceId}'; $j.'telemetry.sqmId'='${sqmId}'; $j | ConvertTo-Json -Depth 10 | Set-Content '%FILE%'"
echo 完成！请重新启动 Cursor。
pause`;

  const macScript = `#!/bin/bash
# Cursor 机器ID重置脚本 (macOS) - 由 AI Account Toolkit 生成
pkill -f "Cursor" 2>/dev/null
FILE="$HOME/Library/Application Support/Cursor/User/globalStorage/storage.json"
[ -f "$FILE" ] && cp "$FILE" "$FILE.backup"
python3 - <<'EOF'
import json, os
f = os.path.expanduser("~/Library/Application Support/Cursor/User/globalStorage/storage.json")
with open(f) as fp: data = json.load(fp)
data["telemetry.machineId"]    = "${machineId}"
data["telemetry.macMachineId"] = "${macMachineId}"
data["telemetry.devDeviceId"]  = "${devDeviceId}"
data["telemetry.sqmId"]        = "${sqmId}"
with open(f, "w") as fp: json.dump(data, fp, indent=2)
print("完成！请重新启动 Cursor。")
EOF`;

  const linuxScript = `#!/bin/bash
# Cursor 机器ID重置脚本 (Linux) - 由 AI Account Toolkit 生成
pkill -f "cursor" 2>/dev/null
FILE="$HOME/.config/Cursor/User/globalStorage/storage.json"
[ -f "$FILE" ] && cp "$FILE" "$FILE.backup"
python3 - <<'EOF'
import json, os
f = os.path.expanduser("~/.config/Cursor/User/globalStorage/storage.json")
with open(f) as fp: data = json.load(fp)
data["telemetry.machineId"]    = "${machineId}"
data["telemetry.macMachineId"] = "${macMachineId}"
data["telemetry.devDeviceId"]  = "${devDeviceId}"
data["telemetry.sqmId"]        = "${sqmId}"
with open(f, "w") as fp: json.dump(data, fp, indent=2)
print("完成！请重新启动 Cursor。")
EOF`;

  res.json({
    success: true,
    ids: { machineId, macMachineId, devDeviceId, sqmId },
    paths,
    scripts: { windows: winScript, mac: macScript, linux: linuxScript },
    json_patch: {
      "telemetry.machineId":    machineId,
      "telemetry.macMachineId": macMachineId,
      "telemetry.devDeviceId":  devDeviceId,
      "telemetry.sqmId":        sqmId,
    },
  });
});

router.get("/tools/machine-id/script/:os", (req, res) => {
  const os = (req.params as { os: string }).os;
  const machineId    = newMachineId();
  const macMachineId = newMachineId();
  const devDeviceId  = newUUID();
  const sqmId        = newSqmId();

  let script = "";
  let filename = "";
  let contentType = "text/plain";

  if (os === "windows") {
    filename = "cursor_reset.bat";
    contentType = "application/octet-stream";
    script = `@echo off\r\ntaskkill /F /IM cursor.exe 2>nul\r\nset "FILE=%APPDATA%\\Cursor\\User\\globalStorage\\storage.json"\r\nif exist "%FILE%" copy "%FILE%" "%FILE%.backup" >nul\r\npowershell -Command "$j = Get-Content '%FILE%' -Raw | ConvertFrom-Json; $j.'telemetry.machineId'='${machineId}'; $j.'telemetry.macMachineId'='${macMachineId}'; $j.'telemetry.devDeviceId'='${devDeviceId}'; $j.'telemetry.sqmId'='${sqmId}'; $j | ConvertTo-Json -Depth 10 | Set-Content '%FILE%'"\r\necho 完成！请重新启动 Cursor。\r\npause\r\n`;
  } else if (os === "mac" || os === "linux") {
    filename = os === "mac" ? "cursor_reset_mac.sh" : "cursor_reset_linux.sh";
    contentType = "application/octet-stream";
    const filePath = os === "mac"
      ? `~/Library/Application Support/Cursor/User/globalStorage/storage.json`
      : `~/.config/Cursor/User/globalStorage/storage.json`;
    script = `#!/bin/bash\npkill -f "Cursor" 2>/dev/null\nFILE="${filePath}"\n[ -f "$FILE" ] && cp "$FILE" "$FILE.backup"\npython3 -c "\nimport json, os\nf = os.path.expanduser('${filePath}')\nwith open(f) as fp: data = json.load(fp)\ndata['telemetry.machineId']='${machineId}'\ndata['telemetry.macMachineId']='${macMachineId}'\ndata['telemetry.devDeviceId']='${devDeviceId}'\ndata['telemetry.sqmId']='${sqmId}'\nwith open(f,'w') as fp: json.dump(data, fp, indent=2)\nprint('完成！请重新启动 Cursor。')\n"\n`;
  } else {
    res.status(400).json({ success: false, error: "os 必须是 windows / mac / linux" });
    return;
  }

  res.setHeader("Content-Disposition", `attachment; filename="${filename}"`);
  res.setHeader("Content-Type", contentType);
  res.send(script);
});

// ── 浏览器指纹 ────────────────────────────────────────────
router.get("/tools/fingerprint/generate", (req, res) => {
  const count = Math.min(10, Math.max(1, Number(req.query.count) || 1));
  const profiles = Array.from({ length: count }, generateFingerprint);
  res.json({ success: true, count, profiles });
});

router.get("/tools/ip-check", async (req, res) => {
  try {
    const r = await fetch("https://ipapi.co/json/");
    const data = await r.json() as Record<string, unknown>;
    res.json({ success: true, info: data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/proxy-check", async (req, res) => {
  const { proxy } = req.body as { proxy?: string };
  if (!proxy) {
    res.status(400).json({ success: false, error: "proxy 不能为空" });
    return;
  }
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    const r = await fetch("https://ipapi.co/json/", {
      signal: controller.signal,
    });
    clearTimeout(timeout);
    const data = await r.json() as Record<string, unknown>;
    res.json({ success: true, info: data, note: "当前环境无法直接测试外部代理，显示的是服务器本身 IP。如需测试代理请在本地环境运行" });
  } catch (e: unknown) {
    res.json({ success: false, error: `连接失败: ${String(e)}` });
  }
});

interface RandomUserResult {
  gender: string;
  name: { first: string; last: string };
  location: {
    street: { number: number; name: string };
    city: string;
    state: string;
    postcode: number | string;
    country: string;
  };
  email: string;
  login: { username: string; password: string };
  phone: string;
  dob: { date: string };
}

router.get("/tools/info-generate", async (req, res) => {
  const count = Math.min(20, Math.max(1, Number(req.query.count) || 1));
  try {
    const r = await fetch(
      `https://randomuser.me/api/?nat=us&results=${count}&noinfo`
    );
    const d = await r.json() as { results: RandomUserResult[] };
    const data = d.results.map((p: RandomUserResult) => ({
      firstName: p.name.first,
      lastName: p.name.last,
      name: `${p.name.first} ${p.name.last}`,
      gender: p.gender,
      email: p.email,
      username: p.login.username,
      password: p.login.password,
      phone: p.phone,
      address: `${p.location.street.number} ${p.location.street.name}`,
      city: p.location.city,
      state: p.location.state,
      zip: String(p.location.postcode),
      country: "United States",
      dob: new Date(p.dob.date).toLocaleDateString("en-US"),
    }));
    res.json({ success: true, data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

export default router;
