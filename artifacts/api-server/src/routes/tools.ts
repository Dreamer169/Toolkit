import { Router, type IRouter } from "express";
import { createHash, randomBytes, randomUUID } from "crypto";

const router: IRouter = Router();

// ── 人名数据库 ────────────────────────────────────────────
const FIRST_NAMES = [
  "James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
  "Christopher","Daniel","Matthew","Anthony","Mark","Donald","Steven","Paul","Andrew","Joshua",
  "Kenneth","Kevin","Brian","George","Timothy","Ronald","Edward","Jason","Jeffrey","Ryan",
  "Jacob","Gary","Nicholas","Eric","Jonathan","Stephen","Larry","Justin","Scott","Brandon",
  "Benjamin","Samuel","Raymond","Gregory","Frank","Alexander","Patrick","Jack","Dennis","Jerry",
  "Mary","Patricia","Jennifer","Linda","Barbara","Elizabeth","Susan","Jessica","Sarah","Karen",
  "Lisa","Nancy","Betty","Margaret","Sandra","Ashley","Dorothy","Kimberly","Emily","Donna",
  "Michelle","Carol","Amanda","Melissa","Deborah","Stephanie","Rebecca","Sharon","Laura","Cynthia",
  "Kathleen","Amy","Angela","Shirley","Anna","Brenda","Pamela","Emma","Nicole","Helen",
  "Samantha","Katherine","Christine","Debra","Rachel","Carolyn","Janet","Catherine","Maria","Heather",
  "Emma","Olivia","Noah","Liam","Ava","Sophia","Isabella","Mia","Charlotte","Amelia",
  "Lucas","Ethan","Mason","Logan","Aiden","Jackson","Sebastian","Oliver","Elijah","Owen",
];
const LAST_NAMES = [
  "Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
  "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
  "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
  "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores",
  "Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts",
  "Turner","Phillips","Evans","Edwards","Collins","Stewart","Morris","Morales","Murphy","Cook",
  "Rogers","Gutierrez","Ortiz","Morgan","Cooper","Peterson","Bailey","Reed","Kelly","Howard",
  "Ramos","Kim","Cox","Ward","Richardson","Watson","Brooks","Chavez","Wood","James",
  "Bennett","Gray","Mendoza","Ruiz","Hughes","Price","Alvarez","Castillo","Sanders","Patel",
  "Myers","Long","Ross","Foster","Jimenez","Powell","Jenkins","Perry","Russell","Sullivan",
  "Parker","Butler","Barnes","Fisher","Henderson","Coleman","Simmons","Patterson","Jordan","Reynolds",
  "Hamilton","Graham","Kim","Griffin","Wallace","Moreno","West","Cole","Hayes","Bryant",
  "Hacker","Dev","Code","Tech","Net","Web","Pro","Max","Ace","Fox",
];

function genHumanUsername(): { username: string; firstName: string; lastName: string; pattern: string } {
  const fn = FIRST_NAMES[Math.floor(Math.random() * FIRST_NAMES.length)];
  const ln = LAST_NAMES[Math.floor(Math.random() * LAST_NAMES.length)];
  const fn_lc = fn.toLowerCase();
  const ln_lc = ln.toLowerCase();
  const ri = (a: number, b: number) => Math.floor(Math.random() * (b - a + 1)) + a;
  const year2 = String(ri(70, 99));
  const year4 = String(ri(1980, 2001));
  const num2  = String(ri(10, 99));
  const num3  = String(ri(100, 999));
  const patterns = [
    // Common real-person patterns (highest success rate)
    () => ({ u: fn + ln, p: "FirstLast" }),
    () => ({ u: fn + ln + year2, p: "FirstLast+year" }),
    () => ({ u: fn_lc + "." + ln_lc, p: "first.last" }),
    () => ({ u: fn_lc + ln_lc + num2, p: "firstlast+num" }),
    () => ({ u: fn[0].toLowerCase() + ln_lc + num2, p: "initial+last+num" }),
    () => ({ u: fn[0].toLowerCase() + ln_lc + year2, p: "initial+last+year" }),
    () => ({ u: fn_lc + ln[0].toLowerCase() + num3, p: "first+initial+num" }),
    () => ({ u: fn_lc + "_" + ln_lc, p: "first_last" }),
    () => ({ u: fn_lc + "_" + ln_lc + num2, p: "first_last+num" }),
    () => ({ u: ln_lc + fn_lc + num2, p: "LastFirst+num" }),
    () => ({ u: fn + ln + num3, p: "FirstLast+num3" }),
    () => ({ u: fn_lc + year4, p: "first+year4" }),
    () => ({ u: fn[0].toLowerCase() + "." + ln_lc + num2, p: "i.last+num" }),
  ];
  const res = patterns[Math.floor(Math.random() * patterns.length)]();
  return { username: res.u, firstName: fn, lastName: ln, pattern: res.p };
}

function genStrongPassword(length?: number): string {
  const ri = (a: number, b: number) => Math.floor(Math.random() * (b - a + 1)) + a;
  const n = length ?? ri(12, 16);
  const lower = "abcdefghijklmnopqrstuvwxyz";
  const upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const digits = "0123456789";
  const specials = "!@#$%^&*";
  const all = lower + upper + digits + specials;
  while (true) {
    let pw = "";
    for (let i = 0; i < n; i++) pw += all[Math.floor(Math.random() * all.length)];
    if (/[a-z]/.test(pw) && /[A-Z]/.test(pw) && /\d/.test(pw) && /[!@#$%^&*]/.test(pw)) return pw;
  }
}

// ── 工具函数 ──────────────────────────────────────────────
function newMachineId() {
  return createHash("sha256").update(randomBytes(32)).digest("hex");
}
function newUUID() { return randomUUID(); }
function newSqmId() { return `{${randomUUID().toUpperCase()}}`; }

// ── 人名邮箱用户名生成 ─────────────────────────────────────
router.get("/tools/email/gen-username", (req, res) => {
  const count = Math.min(50, Math.max(1, Number(req.query.count) || 10));
  const results = Array.from({ length: count }, () => {
    const info = genHumanUsername();
    const password = genStrongPassword();
    return { ...info, password };
  });
  res.json({ success: true, count, usernames: results });
});

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

// ── 微软 OAuth2 / Graph API ───────────────────────────────
router.post("/tools/outlook/refresh-token", async (req, res) => {
  const { clientId, refreshToken, tenantId } = req.body as {
    clientId?: string; refreshToken?: string; tenantId?: string;
  };
  if (!clientId || !refreshToken) {
    res.status(400).json({ success: false, error: "clientId 和 refreshToken 不能为空" });
    return;
  }
  const tid = tenantId || "common";
  try {
    const r = await fetch(`https://login.microsoftonline.com/${tid}/oauth2/v2.0/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: clientId,
        refresh_token: refreshToken,
        scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
      }).toString(),
    });
    const data = await r.json() as {
      access_token?: string; refresh_token?: string; expires_in?: number;
      token_type?: string; error?: string; error_description?: string;
    };
    if (!r.ok || !data.access_token) {
      res.json({ success: false, error: data.error_description ?? data.error ?? "OAuth2 失败" });
      return;
    }
    res.json({
      success: true,
      accessToken: data.access_token,
      refreshToken: data.refresh_token ?? refreshToken,
      expiresIn: data.expires_in,
      tokenType: data.token_type,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/outlook/messages", async (req, res) => {
  const { accessToken, folder, top, search } = req.body as {
    accessToken?: string; folder?: string; top?: number; search?: string;
  };
  if (!accessToken) {
    res.status(400).json({ success: false, error: "accessToken 不能为空" });
    return;
  }
  const mailFolder = folder || "inbox";
  const limit = Math.min(50, Math.max(1, top ?? 20));
  let url = `https://graph.microsoft.com/v1.0/me/mailFolders/${mailFolder}/messages?$top=${limit}&$select=id,subject,from,receivedDateTime,bodyPreview,isRead&$orderby=receivedDateTime desc`;
  if (search) url += `&$search="${encodeURIComponent(search)}"`;
  try {
    const r = await fetch(url, {
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    });
    const data = await r.json() as {
      value?: Array<{
        id: string; subject: string;
        from: { emailAddress: { name: string; address: string } };
        receivedDateTime: string; bodyPreview: string; isRead: boolean;
      }>;
      error?: { message: string; code: string };
    };
    if (!r.ok) {
      res.json({ success: false, error: data.error?.message ?? "获取邮件失败" });
      return;
    }
    const messages = (data.value ?? []).map((m) => ({
      id: m.id,
      subject: m.subject || "(无主题)",
      from: m.from?.emailAddress?.address ?? "",
      fromName: m.from?.emailAddress?.name ?? "",
      receivedAt: m.receivedDateTime,
      preview: m.bodyPreview,
      isRead: m.isRead,
    }));
    res.json({ success: true, messages, count: messages.length });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/outlook/profile", async (req, res) => {
  const token = req.headers["x-access-token"] as string;
  if (!token) { res.status(400).json({ success: false, error: "缺少 x-access-token" }); return; }
  try {
    const r = await fetch("https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName,accountEnabled", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await r.json() as {
      id?: string; displayName?: string; mail?: string;
      userPrincipalName?: string; accountEnabled?: boolean;
      error?: { message: string };
    };
    if (!r.ok) { res.json({ success: false, error: data.error?.message ?? "获取用户信息失败" }); return; }
    res.json({ success: true, profile: data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── Outlook 注册：后台任务 + 轮询 ─────────────────────────
// 避免代理/浏览器 12s 断连问题，改为异步任务模式

interface RegJob {
  status: "running" | "done" | "stopped";
  logs: Array<{ type: string; message: string }>;
  accounts: Array<{ email: string; password: string }>;
  exitCode: number | null;
  startedAt: number;
  child?: ReturnType<import("child_process").ChildProcess["kill"] extends (...args: unknown[]) => unknown ? never : never>;
}

const regJobs = new Map<string, RegJob>();

// 清理超过 30 分钟的旧任务
function cleanOldJobs() {
  const cutoff = Date.now() - 30 * 60 * 1000;
  for (const [id, job] of regJobs.entries()) {
    if (job.startedAt < cutoff) regJobs.delete(id);
  }
}

// 启动注册任务，立即返回 jobId
router.post("/tools/outlook/register", async (req, res) => {
  const {
    count    = 1,
    proxy: proxyInput = "",
    headless = true,
    delay    = 5,
    engine   = "patchright",
    wait     = 11,
    retries  = 2,
    autoProxy = false,
  } = req.body as {
    count?: number; proxy?: string; headless?: boolean; delay?: number;
    engine?: string; wait?: number; retries?: number; autoProxy?: boolean;
  };

  // 如果没有提供代理，且 autoProxy=true，则从代理池自动选取
  let proxy = proxyInput;
  let autoProxyId: number | null = null;
  if (!proxy && autoProxy) {
    try {
      const { query: dbQuery } = await import("../db.js");
      const rows = await dbQuery<{ id: number; formatted: string }>(
        "SELECT id, formatted FROM proxies WHERE status != 'banned' ORDER BY used_count ASC, RANDOM() LIMIT 1"
      );
      if (rows[0]) {
        proxy = rows[0].formatted;
        autoProxyId = rows[0].id;
        const { execute: dbExec } = await import("../db.js");
        await dbExec("UPDATE proxies SET used_count = used_count + 1, last_used = NOW(), status = 'active' WHERE id = $1", [autoProxyId]);
      }
    } catch {}
  }

  const n   = Math.min(10, Math.max(1, count));
  const eng = ["patchright", "playwright"].includes(engine) ? engine : "patchright";
  const jobId = `reg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  const job: RegJob = {
    status: "running",
    logs: [],
    accounts: [],
    exitCode: null,
    startedAt: Date.now(),
  };
  const proxyDisplay = proxy ? proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") : "无代理";
  job.logs.push({ type: "start", message: `启动 ${eng} 注册 ${n} 个 Outlook 账号 (bot_protection_wait=${wait}s)${autoProxyId ? " [代理池自动选取]" : ""}...` });
  if (proxy) job.logs.push({ type: "log", message: `🌐 代理: ${proxyDisplay}` });
  regJobs.set(jobId, job);
  cleanOldJobs();

  // 立即响应 jobId（不等待注册完成）
  res.json({ success: true, jobId, message: "注册任务已启动" });

  // 后台异步执行
  const { spawn } = await import("child_process");
  const scriptPath = new URL("../outlook_register.py", import.meta.url).pathname;
  const args = [
    scriptPath,
    "--count",    String(n),
    "--headless", headless ? "true" : "false",
    "--delay",    String(delay),
    "--engine",   eng,
    "--wait",     String(wait),
    "--retries",  String(retries),
  ];
  if (proxy) args.push("--proxy", proxy);

  const child = spawn("python3", args, { env: { ...process.env, PYTHONUNBUFFERED: "1" } });

  let jsonBuf = "";
  let inJson  = false;

  child.stdout.on("data", (chunk: Buffer) => {
    const raw = chunk.toString();
    if (raw.includes("── JSON 结果 ──") || inJson) { inJson = true; jsonBuf += raw; }

    const lines = raw.split("\n").filter(Boolean);
    for (const line of lines) {
      const t = line.trim();
      // 过滤无意义行和 JSON 结果块
      if (!t) continue;
      if (t.startsWith("──") || t.startsWith("🚀")) continue;
      if (t.startsWith("[") || t.startsWith("{") || t === "]" || t === "}") continue;
      if (t.startsWith('"') && t.includes(":")) continue;  // JSON 字段行
      if (/^\s*"(email|username|password|success|error|elapsed|engine)"\s*:/.test(t)) continue;
      if (t === "── JSON 结果 ──") continue;

      let type = "log";
      if (t.includes("⚠"))                         type = "warn";
      else if (t.includes("❌"))                    type = "error";
      else if (t.includes("✅") && t.includes("|")) type = "success";  // 带账号信息的成功行
      else if (t === "✅ 成功: 0 / 1" || t.startsWith("✅ 成功:")) type = "done";

      job.logs.push({ type, message: t });

      // 解析成功账号行
      if (type === "success" && t.includes("@outlook.com")) {
        const emailM = t.match(/([\w.\-+]+@(?:outlook|hotmail|live)\.com)/);
        const passM  = t.match(/密码:\s*(\S+)/);
        if (emailM && passM) {
          const already = job.accounts.find(a => a.email === emailM[1]);
          if (!already) job.accounts.push({ email: emailM[1], password: passM[1] });
        }
      }
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const msg = chunk.toString().trim();
    if (msg && !msg.includes("DeprecationWarning") && !msg.includes("FutureWarning") && !msg.includes("UserWarning")) {
      // only push meaningful stderr
      const lines = msg.split("\n");
      for (const l of lines) {
        const lt = l.trim();
        if (lt && lt.length > 5) job.logs.push({ type: "log", message: `[sys] ${lt.slice(0, 200)}` });
      }
    }
  });

  child.on("close", (code) => {
    // 解析 JSON 结果块
    try {
      const jsonStart = jsonBuf.indexOf("[");
      if (jsonStart >= 0) {
        const cleaned = jsonBuf.slice(jsonStart).split("\n── JSON")[0].trim();
        const parsed = JSON.parse(cleaned) as Array<Record<string, unknown>>;
        for (const r of parsed) {
          if (r.success && r.email && r.password) {
            const already = job.accounts.find(a => a.email === r.email);
            if (!already) job.accounts.push({ email: String(r.email), password: String(r.password) });
          }
        }
      }
    } catch {}

    const okCount = job.accounts.length;
    job.logs.push({
      type: "done",
      message: `注册任务完成 · 成功 ${okCount} 个 / 共 ${n} 个` + (okCount > 0 ? ` ✅` : ` (需要住宅代理才能通过 CAPTCHA)`),
    });
    job.status   = "done";
    job.exitCode = code;
  });
});

// 查询任务状态（前端每 2s 轮询）
router.get("/tools/outlook/register/:jobId", (req, res) => {
  const job = regJobs.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ success: false, error: "任务不存在" });
    return;
  }

  const since   = Number(req.query.since ?? 0);   // 上次已读的日志索引
  const newLogs = job.logs.slice(since);

  res.json({
    success:  true,
    status:   job.status,
    accounts: job.accounts,
    logs:     newLogs,
    nextSince: job.logs.length,
    exitCode:  job.exitCode,
  });
});

// 列出所有任务（实时监控用）
router.get("/tools/jobs", (_req, res) => {
  const jobs = Array.from(regJobs.entries()).map(([id, job]) => ({
    id,
    status: job.status,
    startedAt: job.startedAt,
    logCount: job.logs.length,
    accountCount: job.accounts.length,
    exitCode: job.exitCode,
    lastLog: job.logs.at(-1) ?? null,
  }));
  // 最新的排前面
  jobs.sort((a, b) => b.startedAt - a.startedAt);
  res.json({ success: true, jobs });
});

// 停止任务
router.delete("/tools/outlook/register/:jobId", (req, res) => {
  const job = regJobs.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false }); return; }
  try { (job as unknown as { _child?: { kill: () => void } })._child?.kill(); } catch {}
  job.status = "stopped";
  job.logs.push({ type: "warn", message: "⚠ 用户停止了任务" });
  res.json({ success: true });
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

// ── 完整工作流：一键准备 ─────────────────────────────────
router.get("/tools/workflow/prepare", async (req, res) => {
  try {
    // 1. 生成随机身份
    let identity: Record<string, unknown> | null = null;
    try {
      const r = await fetch("https://randomuser.me/api/?nat=us&results=1&noinfo");
      if (r.ok) {
        const d = await r.json() as { results: RandomUserResult[] };
        const p = d.results[0];
        identity = {
          firstName: p.name.first, lastName: p.name.last,
          name: `${p.name.first} ${p.name.last}`, gender: p.gender,
          email: p.email, username: p.login.username, password: p.login.password,
          phone: p.phone,
          address: `${p.location.street.number} ${p.location.street.name}`,
          city: p.location.city, state: p.location.state,
          zip: String(p.location.postcode), country: "United States",
          birthday: new Date(p.dob.date).toISOString().split("T")[0],
          age: p.dob.age,
        };
      }
    } catch {}

    // 2. 生成浏览器指纹
    const fingerprint = generateFingerprint();

    // 3. 生成 Outlook 注册用用户名
    const FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Christopher","Daniel","Matthew","Anthony","Mark","Steven","Paul","Andrew","Joshua","Benjamin","Samuel","Emma","Olivia","Ava","Sophia","Isabella"];
    const LAST  = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Lee","Perez"];
    const fn = FIRST[Math.floor(Math.random() * FIRST.length)];
    const ln = LAST[Math.floor(Math.random() * LAST.length)];
    const y2 = String(Math.floor(Math.random() * 30) + 70);
    const n2 = String(Math.floor(Math.random() * 90) + 10);
    const patterns = [`${fn}${ln}`, `${fn}${ln}${y2}`, `${fn.toLowerCase()}.${ln.toLowerCase()}`, `${fn.toLowerCase()}${ln.toLowerCase()}${n2}`, `${fn[0].toLowerCase()}${ln.toLowerCase()}${y2}`];
    const outlookUsername = patterns[Math.floor(Math.random() * patterns.length)];
    const outlookEmail = `${outlookUsername}@outlook.com`;

    // 4. 随机强密码
    const chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*";
    let password = "";
    while (true) {
      password = Array.from({ length: 14 }, () => chars[Math.floor(Math.random() * chars.length)]).join("");
      if (/[a-z]/.test(password) && /[A-Z]/.test(password) && /[0-9]/.test(password) && /[!@#$%^&*]/.test(password)) break;
    }

    res.json({
      success: true,
      identity,
      fingerprint,
      outlook: { email: outlookEmail, username: outlookUsername, password },
    });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

export default router;
