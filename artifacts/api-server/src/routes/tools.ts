import { jobQueue } from "../lib/job-queue.js";
import { setLiveVerifyEnabled, getLiveVerifyStatus } from "../lib/live-verify-poller.js";
import { microsoftFetch, getMicrosoftProxyEnv, pickProxyForAccount, resolveAccountProxy } from "../lib/proxy-fetch.js";
import { Router, type IRouter } from "express";
import { createHash, randomBytes, randomUUID } from "crypto";
import { execute, query, queryOne } from "../db.js";
import { existsSync } from "fs";
import { execFile as _execFile } from "child_process";
import { Socket } from "net";
import path from "path";

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

// ── Outlook OAuth Client IDs ───────────────────────────────────────────────
// 所有 token 由此 client_id 生成（Thunderbird），刷新时也必须用同一个
const OAUTH_CLIENT_ID   = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";

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
    } else if (platform === "grok") {
      try {
        const r = await fetch("https://api.x.ai/v1/models", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { data?: Array<{ id: string }>; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 Grok API Key";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "cursor") {
      try {
        const r = await fetch("https://www.cursor.com/api/usage", {
          headers: { Authorization: `Bearer ${key}` },
        });
        if (r.ok) {
          const data = await r.json() as Record<string, unknown>;
          valid = true;
          info = { status: "有效", usage: JSON.stringify(data).slice(0, 100) };
        } else {
          error = "无效的 Cursor Token";
        }
      } catch (e: unknown) {
        error = String(e);
      }
    } else if (platform === "deepseek") {
      try {
        const r = await fetch("https://api.deepseek.com/models", {
          headers: { Authorization: `Bearer ${key}` },
        });
        const data = await r.json() as { data?: Array<{ id: string }>; error?: { message: string } };
        if (r.ok && data.data) {
          valid = true;
          info = { modelCount: data.data.length, firstModel: data.data[0]?.id };
        } else {
          error = data.error?.message ?? "无效的 DeepSeek API Key";
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
    const limited = tokens.slice(0, 50);
    const results = await Promise.allSettled(
      limited.map(async (token) => {
        const trimmed = token.trim();
        if (!trimmed) return { token: trimmed, valid: false, error: "空值" };
        const preview = trimmed.slice(0, 16) + "...";
        try {
          let endpoint = "https://api.openai.com/v1/models";
          let headers: Record<string, string> = { Authorization: `Bearer ${trimmed}` };
          let checkFn: (data: Record<string, unknown>) => boolean = (d) => !!(d.data);

          if (platform === "claude") {
            endpoint = "https://api.anthropic.com/v1/models";
            headers = { "x-api-key": trimmed, "anthropic-version": "2023-06-01" };
            checkFn = (d) => !!(d.data);
          } else if (platform === "gemini") {
            endpoint = `https://generativelanguage.googleapis.com/v1/models?key=${trimmed}`;
            headers = {};
            checkFn = (d) => !!(d.models);
          } else if (platform === "grok") {
            endpoint = "https://api.x.ai/v1/models";
            headers = { Authorization: `Bearer ${trimmed}` };
            checkFn = (d) => !!(d.data);
          } else if (platform === "deepseek") {
            endpoint = "https://api.deepseek.com/models";
            headers = { Authorization: `Bearer ${trimmed}` };
            checkFn = (d) => !!(d.data);
          } else if (platform === "cursor") {
            endpoint = "https://www.cursor.com/api/usage";
            headers = { Authorization: `Bearer ${trimmed}` };
            checkFn = () => true;
          }

          const r = await fetch(endpoint, { headers });
          const data = await r.json() as Record<string, unknown> & { error?: { message: string } };
          if (r.ok && checkFn(data)) {
            return { token: preview, valid: true };
          }
          return {
            token: preview,
            valid: false,
            error: (data.error as { message?: string })?.message ?? "无效",
          };
        } catch (e: unknown) {
          return { token: preview, valid: false, error: String(e) };
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
  const count = Math.max(1, Math.floor(Number(req.query.count) || 1));
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
    const r = await microsoftFetch(`https://login.microsoftonline.com/${tid}/oauth2/v2.0/token`, {
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
  const { accessToken: suppliedAccessToken, accountId, folder, top, search } = req.body as {
    accessToken?: string; accountId?: number; folder?: string; top?: number; search?: string;
  };
  let accessToken = suppliedAccessToken || "";
  let resolvedAccountId: number | null = typeof accountId === "number" ? accountId : null;
  let accountEmail: string | null = null;
  const mailFolder = folder || "inbox";
  const limit = Math.min(50, Math.max(1, top ?? 20));
  try {
    if (!accessToken) {
      const rows = resolvedAccountId
        ? await query<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
            "SELECT id, email, token, refresh_token FROM accounts WHERE id=$1 AND platform='outlook'",
            [resolvedAccountId],
          )
        : await query<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
            "SELECT id, email, token, refresh_token FROM accounts WHERE platform='outlook' AND (COALESCE(token,'') <> '' OR COALESCE(refresh_token,'') <> '') ORDER BY updated_at DESC LIMIT 1",
          );
      if (!rows.length) {
        res.status(400).json({ success: false, error: resolvedAccountId ? "账号不存在或不是 Outlook 账号" : "找不到可用 Outlook token" });
        return;
      }
      const account = rows[0];
      resolvedAccountId = account.id;
      accountEmail = account.email;
      accessToken = account.token || "";
      const acctProxy = await resolveAccountProxy(resolvedAccountId);
      if (account.refresh_token) {
        const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "refresh_token",
            client_id: OAUTH_CLIENT_ID,
            refresh_token: account.refresh_token,
            scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
          }).toString(),
        }, acctProxy);
        const td = await tr.json() as { access_token?: string; refresh_token?: string; error?: string; error_description?: string };
        if (tr.ok && td.access_token) {
          accessToken = td.access_token;
          await execute(
            "UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
            [accessToken, td.refresh_token ?? account.refresh_token, account.id],
          );
        } else if (!accessToken) {
          res.status(400).json({ success: false, error: td.error_description ?? td.error ?? "刷新 Outlook token 失败" });
          return;
        }
      }
    }
    if (!accessToken) {
      res.status(400).json({ success: false, error: "accessToken 不能为空" });
      return;
    }
    let url = `https://graph.microsoft.com/v1.0/me/mailFolders/${mailFolder}/messages?$top=${limit}&$select=id,subject,from,receivedDateTime,bodyPreview,isRead&$orderby=receivedDateTime desc`;
    if (search) url += `&$search="${encodeURIComponent(search)}"`;
    const _msgsProxy = typeof acctProxy !== "undefined" ? acctProxy : undefined;
    const r = await microsoftFetch(url, {
      headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    }, _msgsProxy);
    const data = await r.json() as {
      value?: Array<{
        id: string; subject: string;
        from: { emailAddress: { name: string; address: string } };
        receivedDateTime: string; bodyPreview: string; isRead: boolean;
      }>;
      error?: { message: string; code: string };
    };
    if (!r.ok) {
      res.status(r.status).json({ success: false, error: data.error?.message ?? "获取邮件失败" });
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
    res.json({ success: true, accountId: resolvedAccountId, email: accountEmail, messages, count: messages.length });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});
router.get("/tools/outlook/profile", async (req, res) => {
  const token = req.headers["x-access-token"] as string;
  if (!token) { res.status(400).json({ success: false, error: "缺少 x-access-token" }); return; }
  try {
    const r = await microsoftFetch("https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName,accountEnabled", {
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

// ── 微软账号存在性检验（公开 GetCredentialType 接口）───────
router.post("/tools/outlook/check-account", async (req, res) => {
  const { email } = req.body as { email?: string };
  if (!email) { res.status(400).json({ success: false, error: "email 不能为空" }); return; }
  try {
    const r = await microsoftFetch("https://login.microsoftonline.com/common/GetCredentialType", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://login.microsoftonline.com",
      },
      body: JSON.stringify({
        username: email,
        isOtherIdpSupported: true,
        checkPhones: false,
        isRemoteNGCSupported: false,
        isCookieBannerShown: false,
        isFidoSupported: false,
        originalRequest: "",
        flowToken: "",
      }),
    });
    const data = await r.json() as { IfExistsResult?: number; ThrottleStatus?: number; Credentials?: unknown };
    // IfExistsResult: 0 = 存在, 1 = 不存在, 4 = 未知/需要验证, 5 = 重定向到其他 IdP
    const exists = data.IfExistsResult === 0 || data.IfExistsResult === 5;
    const throttled = data.ThrottleStatus === 1;
    res.json({ success: true, exists, ifExistsResult: data.IfExistsResult, throttled });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// 批量检验多个账号是否存在
router.post("/tools/outlook/check-accounts-batch", async (req, res) => {
  const { emails } = req.body as { emails?: string[] };
  if (!emails?.length) { res.status(400).json({ success: false, error: "emails 不能为空" }); return; }
  const results: Array<{ email: string; exists: boolean; ifExistsResult: number }> = [];
  for (const email of emails.slice(0, 20)) {
    try {
      const r = await microsoftFetch("https://login.microsoftonline.com/common/GetCredentialType", {
        method: "POST",
        headers: { "Content-Type": "application/json", "User-Agent": "Mozilla/5.0", "Origin": "https://login.microsoftonline.com" },
        body: JSON.stringify({ username: email, isOtherIdpSupported: true, checkPhones: false, isRemoteNGCSupported: false, isCookieBannerShown: false, isFidoSupported: false, originalRequest: "", flowToken: "" }),
      });
      const data = await r.json() as { IfExistsResult?: number };
      const exists = data.IfExistsResult === 0 || data.IfExistsResult === 5;
      results.push({ email, exists, ifExistsResult: data.IfExistsResult ?? -1 });
    } catch {
      results.push({ email, exists: false, ifExistsResult: -1 });
    }
    await new Promise(r => setTimeout(r, 300)); // 避免限流
  }
  res.json({ success: true, results });
});

// ── 微软设备码授权流程（Device Code Flow）──────────────────
// 用户不需要 Redirect URI，只需访问 aka.ms/devicelogin 输入短码
router.post("/tools/outlook/device-code", async (req, res) => {
  const { clientId, tenantId } = req.body as { clientId?: string; tenantId?: string };
  const cid = clientId || "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
  const tid = tenantId || "common";
  try {
    const r = await microsoftFetch(`https://login.microsoftonline.com/${tid}/oauth2/v2.0/devicecode`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: cid,
        scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
      }).toString(),
    });
    const data = await r.json() as {
      device_code?: string; user_code?: string; verification_uri?: string;
      expires_in?: number; interval?: number; message?: string;
      error?: string; error_description?: string;
    };
    if (!r.ok || !data.device_code) {
      res.json({ success: false, error: data.error_description ?? data.error ?? "获取设备码失败" });
      return;
    }
    res.json({
      success: true,
      deviceCode: data.device_code,
      userCode: data.user_code,
      verificationUri: data.verification_uri,
      expiresIn: data.expires_in ?? 900,
      interval: data.interval ?? 5,
      message: data.message,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/outlook/device-poll", async (req, res) => {
  const { deviceCode, clientId, tenantId } = req.body as {
    deviceCode?: string; clientId?: string; tenantId?: string;
  };
  if (!deviceCode) {
    res.status(400).json({ success: false, error: "deviceCode 不能为空" });
    return;
  }
  const cid = clientId || "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
  const tid = tenantId || "common";
  try {
    const r = await microsoftFetch(`https://login.microsoftonline.com/${tid}/oauth2/v2.0/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        grant_type: "urn:ietf:params:oauth:grant-type:device_code",
        client_id: cid,
        device_code: deviceCode,
      }).toString(),
    });
    const data = await r.json() as {
      access_token?: string; refresh_token?: string; expires_in?: number;
      token_type?: string; error?: string; error_description?: string;
    };
    if (data.error === "authorization_pending") {
      res.json({ success: false, pending: true, error: "等待用户授权" });
      return;
    }
    if (data.error === "slow_down") {
      res.json({ success: false, pending: true, slowDown: true, error: "请求太频繁，稍候" });
      return;
    }
    if (!r.ok || !data.access_token) {
      res.json({ success: false, error: data.error_description ?? data.error ?? "授权失败或已过期" });
      return;
    }
    res.json({
      success: true,
      accessToken: data.access_token,
      refreshToken: data.refresh_token ?? "",
      expiresIn: data.expires_in,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 批量设备码 OAuth 授权 ──────────────────────────────────────────────────
// 为所有无 token 的 Outlook 账号同时申请设备码，前端展示所有码，
// 用户逐个在浏览器授权后，后台自动轮询并将 refresh_token 存入数据库。

interface BatchOAuthSession {
  accountId: number;
  email: string;
  deviceCode: string;
  userCode: string;
  verificationUri: string;
  status: "pending" | "done" | "expired" | "error";
  accessToken?: string;
  refreshToken?: string;
  errorMsg?: string;
  createdAt: number;
}

const batchOAuthSessions = new Map<string, BatchOAuthSession[]>();

function cleanOldBatchSessions() {
  const cutoff = Date.now() - 20 * 60 * 1000; // 20 分钟
  for (const [k, sessions] of batchOAuthSessions) {
    if (sessions[0]?.createdAt < cutoff) batchOAuthSessions.delete(k);
  }
}

async function createBatchOAuthSessions(rows: { id: number; email: string }[]) {
  cleanOldBatchSessions();
  const CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
  const SCOPE = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access";
  const sessionList: BatchOAuthSession[] = [];
  await Promise.allSettled(rows.map(async (acc) => {
    try {
      const r = await microsoftFetch("https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ client_id: CLIENT_ID, scope: SCOPE }).toString(),
      });
      const d = await r.json() as {
        device_code?: string; user_code?: string; verification_uri?: string;
        error?: string; error_description?: string;
      };
      if (!d.device_code || !d.user_code) {
        sessionList.push({
          accountId: acc.id, email: acc.email,
          deviceCode: "", userCode: "", verificationUri: "",
          status: "error", errorMsg: d.error_description ?? d.error ?? "获取设备码失败",
          createdAt: Date.now(),
        });
      } else {
        sessionList.push({
          accountId: acc.id, email: acc.email,
          deviceCode: d.device_code, userCode: d.user_code,
          verificationUri: d.verification_uri ?? "https://microsoft.com/devicelogin",
          status: "pending", createdAt: Date.now(),
        });
      }
    } catch (e) {
      sessionList.push({
        accountId: acc.id, email: acc.email,
        deviceCode: "", userCode: "", verificationUri: "",
        status: "error", errorMsg: String(e), createdAt: Date.now(),
      });
    }
  }));
  const sessionId = `batch-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  batchOAuthSessions.set(sessionId, sessionList);
  return { sessionId, sessionList };
}

// POST /tools/outlook/batch-oauth/start
// 为没有 token 的账号批量申请设备码
router.post("/tools/outlook/batch-oauth/start", async (req, res) => {
  const { accountIds } = req.body as { accountIds?: number[] };
  try {
    cleanOldBatchSessions();
    const { query: dbQ } = await import("../db.js");

    // 查出所有没有 token 的 Outlook 账号（或指定 ID）
    // Bug fix: 当传入 accountIds 时同样过滤已有 token 的账号，避免重复发起设备码授权
    let rows: { id: number; email: string }[];
    if (accountIds?.length) {
      rows = await dbQ<{ id: number; email: string }>(
        "SELECT id, email FROM accounts WHERE platform='outlook' AND id = ANY($1::int[]) AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')",
        [accountIds]
      );
    } else {
      rows = await dbQ<{ id: number; email: string }>(
        "SELECT id, email FROM accounts WHERE platform='outlook' AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')"
      );
    }

    if (!rows.length) {
      res.json({ success: false, error: "没有需要授权的账号" });
      return;
    }

    const { sessionId, sessionList } = await createBatchOAuthSessions(rows);

    res.json({
      success: true,
      sessionId,
      accounts: sessionList.map(s => ({
        accountId: s.accountId,
        email: s.email,
        deviceCode: s.deviceCode,  // client-side polling uses this directly
        userCode: s.userCode,
        verificationUri: s.verificationUri,
        status: s.status,
        errorMsg: s.errorMsg,
      })),
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// POST /tools/outlook/batch-oauth/poll
// 轮询所有 pending 的设备码，发现授权完成后立即存入数据库
router.post("/tools/outlook/batch-oauth/poll", async (req, res) => {
  const { sessionId } = req.body as { sessionId?: string };
  if (!sessionId || !batchOAuthSessions.has(sessionId)) {
    res.status(404).json({ success: false, error: "会话不存在或已过期" });
    return;
  }
  const sessions = batchOAuthSessions.get(sessionId)!;
  const CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
  const { execute: dbE } = await import("../db.js");

  // 并发轮询所有 pending 的账号
  await Promise.allSettled(sessions.filter(s => s.status === "pending").map(async (s) => {
    try {
      const r = await microsoftFetch("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "urn:ietf:params:oauth:grant-type:device_code",
          client_id: CLIENT_ID,
          device_code: s.deviceCode,
        }).toString(),
      });
      const d = await r.json() as {
        access_token?: string; refresh_token?: string;
        error?: string; error_description?: string;
      };
      if (d.access_token) {
        s.status = "done";
        s.accessToken = d.access_token;
        s.refreshToken = d.refresh_token ?? "";
        // 立即存入数据库
        await dbE(
          `UPDATE accounts
             SET token=$1, refresh_token=$2, status='active', updated_at=NOW(),
                 tags = CASE
                   WHEN COALESCE(tags,'') LIKE '%needs_oauth_manual%'
                   THEN NULLIF(TRIM(BOTH ',' FROM
                          REGEXP_REPLACE(COALESCE(tags,''), '(^|,?)needs_oauth_manual(,|$)', ',', 'g')
                        ), ',')
                   ELSE tags END
           WHERE id=$3`,
          [d.access_token, d.refresh_token ?? "", s.accountId]
        );
      } else if (d.error === "expired_token" || d.error === "code_expired") {
        s.status = "expired";
        s.errorMsg = "设备码已过期（15分钟限制），请重新发起授权";
      } else if (d.error && d.error !== "authorization_pending" && d.error !== "slow_down") {
        s.status = "error";
        s.errorMsg = d.error_description ?? d.error;
      }
      // authorization_pending / slow_down → 继续等待，不修改 status
    } catch { /* 网络错误，下次继续轮询 */ }
  }));

  const pending = sessions.filter(s => s.status === "pending").length;
  const done    = sessions.filter(s => s.status === "done").length;
  const errors  = sessions.filter(s => s.status === "error" || s.status === "expired").length;

  res.json({
    success: true,
    sessionId,
    pending, done, errors,
    allFinished: pending === 0,
    accounts: sessions.map(s => ({
      accountId: s.accountId,
      email: s.email,
      userCode: s.userCode,
      status: s.status,
      errorMsg: s.errorMsg,
    })),
  });
});



// POST /tools/outlook/batch-oauth/auto-complete
// 对指定账号（或所有无 token 账号）自动用浏览器完成设备码授权
router.post("/tools/outlook/batch-oauth/auto-complete", async (req, res) => {
  const { accountIds } = req.body as { accountIds?: number[] };
  try {
    cleanOldBatchSessions();
    const { query: dbQAc } = await import("../db.js");
    let rows: { id: number; email: string; password: string }[];
    if (accountIds?.length) {
      rows = await dbQAc<{ id: number; email: string; password: string }>(
        "SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE platform='outlook' AND id = ANY($1::int[]) AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')",
        [accountIds]
      );
    } else {
      rows = await dbQAc<{ id: number; email: string; password: string }>(
        "SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE platform='outlook' AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')"
      );
    }
    if (!rows.length) { res.json({ success: false, error: "没有需要授权的账号" }); return; }

    // v8.97: 直接构建 payload（无预获取设备码），Python 端通过 CF proxy 自申请
    const autoPayload = rows.map(r => ({
      accountId: r.id,
      email: r.email,
      password: r.password,
      userCode: "",      // 空 → authorize_one 通过 CF proxy 自申请
      deviceCode: "",    // 空 → authorize_one 通过 CF proxy 自申请
      dbUrl: process.env.DATABASE_URL || "postgresql://postgres:postgres@localhost/toolkit",
    })).filter(x => x.password);

    if (!autoPayload.length) { res.json({ success: false, error: "无密码可用账号" }); return; }

    const sessionId = `ac-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const { spawn: spawnAc } = await import("child_process");
    const { openSync: oAc } = await import("fs");
    const acScript = new URL("../auto_device_code.py", import.meta.url).pathname;
    const acLogPath = `/tmp/dc_autocomplete_${Date.now()}.log`;
    const acLogFd = oAc(acLogPath, "a");
    const acProc = spawnAc(
      "python3", [acScript, JSON.stringify(autoPayload), ""],  // v8.99: 空串→Python _pick_cf_proxy()独立中继
      { detached: true, stdio: ["ignore", acLogFd, acLogFd], env: { ...(process.env as Record<string,string>), PYTHONUNBUFFERED: "1" } }
    );
    acProc.unref();
    res.json({ success: true, sessionId, logFile: acLogPath, accounts: autoPayload.map(x => ({ accountId: x.accountId, email: x.email })) });

    acProc.on("close", (exitCode) => {
      console.log("[auto-complete] exit=" + exitCode + " log=" + acLogPath);
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// GET /tools/outlook/batch-oauth/log?file=<path>&offset=<n>
// 增量读取 auto-complete / reauth-manual 日志文件
router.get("/tools/outlook/batch-oauth/log", async (req, res) => {
  const { file, offset: offsetStr } = req.query as { file?: string; offset?: string };
  if (!file || !file.startsWith("/tmp/dc_")) {
    res.status(400).json({ success: false, error: "invalid file path" });
    return;
  }
  const offset = parseInt(offsetStr || "0", 10) || 0;
  try {
    const { readFileSync, statSync } = await import("fs");
    let size = 0;
    try { size = statSync(file).size; } catch { res.json({ success: true, lines: [], nextOffset: 0, done: false }); return; }
    const buf = readFileSync(file, { encoding: "utf8", flag: "r" });
    const allLines = buf.split("\n").filter(l => l.trim().length > 0);
    const newLines = allLines.slice(offset);
    const done = buf.includes("RESULTS:") || buf.includes("[summary]");
    res.json({ success: true, lines: newLines, nextOffset: allLines.length, done, size });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// POST /tools/outlook/batch-oauth/reauth-manual
// 专门针对 needs_oauth_manual 账号重新发起 OAuth 授权（设备码 + 自动浏览器完成）
router.post("/tools/outlook/batch-oauth/reauth-manual", async (req, res) => {
  const { accountIds } = req.body as { accountIds?: number[] };
  try {
    cleanOldBatchSessions();
    const { query: dbQRm, execute: dbERm } = await import("../db.js");

    let rows: { id: number; email: string; password: string }[];
    const baseFilter = `platform='outlook'
       AND COALESCE(tags,'') LIKE '%needs_oauth_manual%'
       AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'
       AND status NOT IN ('suspended', 'needs_oauth_pending')
       AND password IS NOT NULL AND password != ''`;

    if (accountIds?.length) {
      rows = await dbQRm<{ id: number; email: string; password: string }>(
        `SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE ${baseFilter} AND id = ANY($1::int[])`,
        [accountIds]
      );
    } else {
      rows = await dbQRm<{ id: number; email: string; password: string }>(
        `SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE ${baseFilter} ORDER BY updated_at ASC LIMIT 10`
      );
    }

    if (!rows.length) {
      res.json({ success: false, error: "没有需要重授权的 needs_oauth_manual 账号" });
      return;
    }

    // v8.97: 清零 token + 设为 needs_oauth_pending，不在 Node.js 预申请设备码
    // Python 脚本会通过同一 CF proxy 自申请设备码（确保申请IP==授权IP==兑换IP）
    for (const r of rows) {
      await dbERm(
        "UPDATE accounts SET token=NULL, refresh_token=NULL, status='needs_oauth_pending', updated_at=NOW() WHERE id=$1",
        [r.id]
      );
    }

    // v8.97: 直接构建 payload（无预获取设备码），Python 端自行申请
    const autoPayload = rows.map(r => ({
      accountId: r.id,
      email: r.email,
      password: r.password,
      userCode: "",      // 空 → authorize_one 通过 CF proxy 自申请
      deviceCode: "",    // 空 → authorize_one 通过 CF proxy 自申请
      dbUrl: process.env.DATABASE_URL || "postgresql://postgres:postgres@localhost/toolkit",
      removeTag: "needs_oauth_manual",
    })).filter(x => x.password);

    if (!autoPayload.length) {
      res.json({ success: false, error: "无密码可用账号" });
      return;
    }

    const sessionId = `reauth-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const { spawn: spawnRm } = await import("child_process");
    const { openSync: oRm } = await import("fs");
    const rmScript = new URL("../auto_device_code.py", import.meta.url).pathname;
    const rmLogPath = `/tmp/dc_reauth_${Date.now()}.log`;
    const rmLogFd = oRm(rmLogPath, "a");
    const rmProc = spawnRm(
      "python3", [rmScript, JSON.stringify(autoPayload), ""],  // v8.99: 空串→Python _pick_cf_proxy()独立中继
      { detached: true, stdio: ["ignore", rmLogFd, rmLogFd], env: { ...(process.env as Record<string,string>), PYTHONUNBUFFERED: "1" } }
    );
    rmProc.unref();

    res.json({
      success: true, sessionId, logFile: rmLogPath,
      accounts: autoPayload.map(x => ({ accountId: x.accountId, email: x.email })),
    });

    rmProc.on("close", (exitCode) => {
      console.log("[reauth-manual] exit=" + exitCode + " log=" + rmLogPath);
    });
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

// regJobs 已替换为持久化 jobQueue

// 启动注册任务，立即返回 jobId
router.post("/tools/outlook/register", async (req, res) => {
  const {
    count    = 1,
    proxy: proxyInput = "",
    proxies: proxiesInput = "",   // 多代理轮换：逗号或换行分隔
    headless = true,
    delay    = 2,
    engine   = "patchright",
    wait     = 11,
    retries  = 2,
    autoProxy = false,
    proxyMode = "cf",             // "cf" = 使用 CF IP 池 + xray 中继
    cfPort    = 443,
    username  = "",               // v9.23: 预生成用户名
    password  = "",               // v9.23: 预生成密码
  } = req.body as {
    count?: number; proxy?: string; proxies?: string; headless?: boolean; delay?: number;
    engine?: string; wait?: number; retries?: number; autoProxy?: boolean;
    proxyMode?: string; cfPort?: number; username?: string; password?: string;
  };

  const n   = Math.min(999, Math.max(1, Math.floor(Number(count) || 1)));
  const eng = ["patchright", "playwright", "camoufox"].includes(engine) ? engine : "patchright";
  const jobId = `reg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;

  // 解析多代理列表（支持换行或逗号分隔）
  let proxyList: string[] = proxiesInput
    ? proxiesInput.split(/\n|,/).map((p: string) => p.trim()).filter(Boolean)
    : proxyInput ? [proxyInput] : [];

  let proxy = proxyList[0] || "";
  const preJobLogs: Array<{ type: string; message: string }> = [];
  let effectiveProxyMode = proxyList.length > 0 ? "" : (proxyMode === "cf" ? "cf" : "");

  if (!proxy && autoProxy && proxyMode === "shared") {
    try {
      // v8.95 BUG-FIX: pickSharedProxyPool 会返回 external CF IPs (http://IP:443)
        // 这些 CF IP 只能通过 xray VLESS relay 使用，不支持直接 HTTP CONNECT 隧道
        // → ERR_TUNNEL_CONNECTION_FAILED。改用 pickAdaptiveProxy("outlook") 正确优先
        // local_socks5 (xray SOCKS5 端口 10820-10845) 和 webshare HTTP 真实代理。
        const pickedRaw = await pickAdaptiveProxy("outlook", Math.min(10, n * 3));  // v9.00: 3x spare proxies for rate-limit rotation
        const picked = pickedRaw.map((p) => ({ formatted: p.formatted, source: p.pool }));
      if (picked.length > 0) {
        proxyList = picked.map((p) => p.formatted);
        proxy = proxyList[0] || "";
        effectiveProxyMode = "";
        const sourceCounts = picked.reduce<Record<string, number>>((acc, p) => {
          acc[p.source] = (acc[p.source] || 0) + 1;
          return acc;
        }, {});
        const sourceLabel = Object.entries(sourceCounts).map(([k, v]) => `${k}:${v}`).join(", ");
        preJobLogs.push({ type: "log", message: `🌐 共享代理池已选取 ${picked.length} 个节点（${sourceLabel}）` });
      } else {
        effectiveProxyMode = "cf";
        preJobLogs.push({ type: "warn", message: "⚠ 共享代理池无可用节点，自动退回 CF IP 池 + xray" });
      }
    } catch (e) {
      effectiveProxyMode = "cf";
      preJobLogs.push({ type: "warn", message: `⚠ 共享代理池读取失败，自动退回 CF IP 池 + xray: ${String(e).slice(0, 120)}` });
    }
  } else if (!proxy && autoProxy) {
    effectiveProxyMode = "cf";
  }

  const proxyDisplay = proxy ? proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") : "无代理";
  const job = await jobQueue.create(jobId);
  // 将代理池阶段收集的日志合并
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  for (const l of preJobLogs) job.logs.push(l as any);
  job.logs.push({ type: "start", message: `启动 ${eng} 注册 ${n} 个 Outlook 账号 (bot_protection_wait=${wait}s)${effectiveProxyMode === "cf" ? " [CF+xray代理池]" : proxy ? " [手动代理]" : ""}...` });
  if (proxy) job.logs.push({ type: "log", message: `🌐 代理: ${proxyDisplay}` });
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
  // 多代理支持：列表 > 2 个时传 --proxies（逗号分隔），否则传 --proxy
  if (proxyList.length > 1) {
    args.push("--proxies", proxyList.join(","));
    job.logs.push({ type: "log", message: `🌐 代理轮换池: ${proxyList.length} 个节点` });
  } else if (proxy) {
    args.push("--proxy", proxy);
  }
  if (effectiveProxyMode === "cf") {
    args.push("--proxy-mode", "cf", "--cf-port", String(cfPort));
    job.logs.push({ type: "log", message: `☁️ CF+xray 代理池：共享代理不可用时作为备用，每账号独占一个已测速 CF 节点` });
  }

  // v9.23 BUG-FIX: pass pre-generated username/password to Python (first account)
  if (username) {
    const cleanUser = (username as string).replace(/@outlook\.com$/i, "");
    args.push("--username", cleanUser);
    job.logs.push({ type: "log", message: "Pre-generated username: " + cleanUser + "@outlook.com" });
  }
  if (password) {
    args.push("--password", password as string);
  }
  const _spawnEnv: Record<string, string> = { ...process.env as Record<string, string>, PYTHONUNBUFFERED: "1" };
  if (!headless) _spawnEnv["DISPLAY"] = process.env.DISPLAY || ":99";
  const child = spawn("python3", args, { env: _spawnEnv });
  jobQueue.setChild(jobId, child);

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
      // 只过滤独立的 JSON 括号行，不要过滤 [captcha]、[relay]、[register] 这类前缀
      if (t === "[" || t === "{" || t === "]" || t === "}") continue;
      if (t.startsWith("{") || (t.startsWith("[{") && t.endsWith("}]"))) continue; // JSON object/array行
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

  child.on("close", async (code) => {
    // 解析 JSON 结果块
    // v8.20: 扩展为 identityMap, 同时收集 token + cookies + fingerprint + UA + IP + port
    const identityMap = new Map<string, {
      access_token: string; refresh_token: string;
      cookies_json: string; fingerprint_json: string;
      user_agent: string; exit_ip: string; proxy_port: number; proxy_formatted: string;
    }>();
    try {
      const jsonStart = jsonBuf.indexOf("[");
      if (jsonStart >= 0) {
        const cleaned = jsonBuf.slice(jsonStart).split("\n── JSON")[0].trim();
        const parsed = JSON.parse(cleaned) as Array<Record<string, unknown>>;
        for (const r of parsed) {
          if (r.success && r.email && r.password) {
            const already = job.accounts.find(a => a.email === r.email);
            if (!already) job.accounts.push({ email: String(r.email), password: String(r.password) });
            identityMap.set(String(r.email), {
              access_token:     String(r.access_token     ?? ""),
              refresh_token:    String(r.refresh_token    ?? ""),
              cookies_json:     String(r.cookies_json     ?? ""),
              fingerprint_json: String(r.fingerprint_json ?? ""),
              user_agent:       String(r.user_agent       ?? ""),
              exit_ip:          String(r.exit_ip          ?? ""),
              proxy_port:       Number(r.proxy_port       ?? 0),
              proxy_formatted:  String(r.proxy_formatted  ?? ""),
            });
          }
        }
      }
    } catch {}
    // 兼容: 老代码可能用 tokenMap, 提供别名
    const tokenMap = identityMap;

    const okCount = job.accounts.length;

    // ── 持久化到数据库 + 立即 ROPC 自动授权 ────────────────────────────────
    if (okCount > 0) {
      await (async () => {
        const pendingOAuthRows: { id: number; email: string; password: string }[] = [];
        for (const acc of job.accounts) {
          let accountRow: { id: number } | null = null;
          // 1. 保存到账号库（失败则跳过该账号）
          try {
            // v8.20: identity bundle 同步入库 (cookies/fp/UA/exit_ip/port + token/refresh_token)
            // 解决: 注册时浏览器指纹/cookies/IP 全丢, retoken 时新指纹访问被微软判 abuse
            const _idn = identityMap.get(acc.email);
            accountRow = await queryOne<{ id: number }>(
              `INSERT INTO accounts (platform, email, password, status, token, refresh_token,
                                       cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port, proxy_formatted)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
               ON CONFLICT (platform, email) DO UPDATE SET
                 password         = EXCLUDED.password,
                 status           = EXCLUDED.status,
                 token            = COALESCE(NULLIF(EXCLUDED.token,''), accounts.token),
                 refresh_token    = COALESCE(NULLIF(EXCLUDED.refresh_token,''), accounts.refresh_token),
                 cookies_json     = COALESCE(NULLIF(EXCLUDED.cookies_json,''), accounts.cookies_json),
                 fingerprint_json = COALESCE(NULLIF(EXCLUDED.fingerprint_json,''), accounts.fingerprint_json),
                 user_agent       = COALESCE(NULLIF(EXCLUDED.user_agent,''), accounts.user_agent),
                 exit_ip          = COALESCE(NULLIF(EXCLUDED.exit_ip,''), accounts.exit_ip),
                 proxy_port       = COALESCE(NULLIF(EXCLUDED.proxy_port, 0), accounts.proxy_port),
                 proxy_formatted  = COALESCE(NULLIF(EXCLUDED.proxy_formatted,''), accounts.proxy_formatted),
                 updated_at       = NOW()
               RETURNING id`,
              [
                "outlook", acc.email, acc.password, "active",
                _idn?.access_token    || null,
                _idn?.refresh_token   || null,
                _idn?.cookies_json     || null,
                _idn?.fingerprint_json || null,
                _idn?.user_agent       || null,
                _idn?.exit_ip          || null,
                _idn?.proxy_port       || null,
                // proxy_formatted: 注册时实际使用的完整代理URL（IP一致性锚点）
                // 优先用 proxy_formatted 字段，fallback 到 proxy_port 重建 socks5://127.0.0.1:PORT
                (_idn?.proxy_formatted
                  ? _idn.proxy_formatted
                  : (_idn?.proxy_port ? `socks5://127.0.0.1:${_idn.proxy_port}` : null)),
              ],
            );
          } catch (dbErr) {
            job.logs.push({ type: "warn", message: `⚠ 账号库保存失败(${acc.email}): ${dbErr}` });
            continue;
          }
          // ── 统一数据库同步（fire-and-forget，失败不阻断）──
          try {
            const _idn2 = identityMap.get(acc.email);
            const _tok2  = tokenMap.get(acc.email);
            const _syncPayload = JSON.stringify({
              action: "outlook",
              email:         acc.email,
              password:      (acc as { email: string; password?: string }).password || "",
              token:         _tok2?.access_token  || _idn2?.access_token  || null,
              refresh_token: _tok2?.refresh_token || _idn2?.refresh_token || null,
              status:        "active",
              proxy: _idn2?.proxy_formatted || (_idn2?.proxy_port ? `socks5://127.0.0.1:${_idn2.proxy_port}` : null),
              egress_ip:        _idn2?.exit_ip          || null,
              user_agent:       _idn2?.user_agent        || null,
              fingerprint_json: _idn2?.fingerprint_json  || null,
              cookies_json:     _idn2?.cookies_json       || null,
            });
            import("child_process").then(({ spawn: _spawn }) => {
              const _cp = _spawn("python3", ["/data/Toolkit/artifacts/api-server/sync_unified_db.py"]);
              _cp.stdin.write(_syncPayload);
              _cp.stdin.end();
            }).catch(() => {});
          } catch (_) {}
          // 2. 同步到邮箱库（独立 try，失败不阻断后续）
          try {
            await execute(
              `INSERT INTO temp_emails (address,password,provider,status,notes)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (address) DO UPDATE SET password=EXCLUDED.password,status=EXCLUDED.status,notes=EXCLUDED.notes`,
              [acc.email, acc.password, "outlook", "active", "Outlook 自动注册"],
            );
          } catch (emailErr) {
            job.logs.push({ type: "warn", message: `⚠ 邮箱库同步失败(${acc.email}): ${emailErr}` });
          }
          // 3. 保存到档案库 — v8.21: 真持久化 cookies+fingerprint (不再依赖 job.fingerprint=null fallback)
          //    archives 表 schema 早就有 cookies/fingerprint/identity_data jsonb 字段, 但历年来一直被
          //    archiveFingerprint=(job as any).fingerprint=null 写空, retoken 也从没读它. 现在
          //    archives.cookies/fingerprint 存注册时真实浏览器 storage_state + BrowserProfile dict,
          //    proxy_used 存实际 CF 出口 IP+port, retoken 可用作 accounts 表丢字段时的 fallback 数据源
          try {
            const _idnArc = identityMap.get(acc.email);
            const archiveProxy = _idnArc?.exit_ip
              ? `socks5://127.0.0.1:${_idnArc.proxy_port}#cf=${_idnArc.exit_ip}`
              : (proxyList.length > 0 ? proxyList[0] : (proxy || null));
            // fingerprint_json/cookies_json 是合法 JSON 字符串, 直接 cast 成 jsonb (PG 自动 cast text->jsonb 失败时报错被 try 截获)
            const fpJsonForArchive = _idnArc?.fingerprint_json && _idnArc.fingerprint_json.startsWith("{")
              ? _idnArc.fingerprint_json : null;
            const cookiesJsonForArchive = _idnArc?.cookies_json && _idnArc.cookies_json.startsWith("{")
              ? _idnArc.cookies_json : null;
            const idnDataForArchive = _idnArc?.user_agent
              ? JSON.stringify({ user_agent: _idnArc.user_agent, exit_ip: _idnArc.exit_ip, proxy_port: _idnArc.proxy_port })
              : null;
            await execute(
              `INSERT INTO archives (platform,email,password,token,refresh_token,proxy_used,identity_data,fingerprint,cookies,status,notes)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11)
               ON CONFLICT (platform,email) DO UPDATE SET
                 password      = EXCLUDED.password,
                 token         = COALESCE(NULLIF(EXCLUDED.token,''), archives.token),
                 refresh_token = COALESCE(NULLIF(EXCLUDED.refresh_token,''), archives.refresh_token),
                 proxy_used    = COALESCE(NULLIF(EXCLUDED.proxy_used,''), archives.proxy_used),
                 identity_data = COALESCE(EXCLUDED.identity_data, archives.identity_data),
                 fingerprint   = COALESCE(EXCLUDED.fingerprint, archives.fingerprint),
                 cookies       = COALESCE(EXCLUDED.cookies, archives.cookies),
                 status        = EXCLUDED.status,
                 updated_at    = NOW()`,
              [
                "outlook", acc.email, acc.password,
                _idnArc?.access_token  || null,
                _idnArc?.refresh_token || null,
                archiveProxy,
                idnDataForArchive,
                fpJsonForArchive,
                cookiesJsonForArchive,
                "active", "Outlook 自动注册",
              ]
            );
            job.logs.push({ type: "log", message: `[档案库] ${acc.email} 已保存 (cookies=${cookiesJsonForArchive?cookiesJsonForArchive.length:0}B fp=${fpJsonForArchive?fpJsonForArchive.length:0}B)` });
          } catch (archErr) {
            job.logs.push({ type: "warn", message: `⚠ 档案库保存失败(${acc.email}): ${archErr}` });
          }
          // 2. In-browser authorization_code flow token
          //    ROPC (grant_type=password) disabled for personal MS accounts
          try {
            const tok           = tokenMap.get(acc.email);
            const inlineAccess  = tok?.access_token  || undefined;
            const inlineRefresh = tok?.refresh_token || undefined;
            // Bug fix: inlineRefresh 存在时也应保存并跳过设备码（防止 refresh_token 被浏览器拦截但 access_token 未捕到时重复授权）
            if (inlineAccess || inlineRefresh) {
              // v8.80 Bug N: 注册成功 in-browser OAuth 路径必须显式 status='active', 否则邮件中心/健康检查
              // 基于 status 过滤会漏掉这些账号 (设备码 fallback path 已 active, 这里也补齐)
              // archives 表通过 PG trigger 自动同步, 无需在此处手写 UPDATE archives.
              await execute(
                "UPDATE accounts SET token=$1, refresh_token=$2, status='active', updated_at=NOW() WHERE email=$3 AND platform='outlook'",
                [inlineAccess ?? null, inlineRefresh ?? null, acc.email],
              );
              job.logs.push({ type: "success", message: `[key] ${acc.email} in-browser OAuth 授权成功` });
            } else if (accountRow?.id) {
              pendingOAuthRows.push({ id: accountRow.id, email: acc.email, password: (acc as {email:string;password?:string}).password || '' });
              job.logs.push({ type: "warn", message: `[warn] ${acc.email} 未内联到 token，正在自动申请设备码` });
            } else {
              job.logs.push({ type: "warn", message: `[warn] ${acc.email} 未内联到 token，需手动设备码授权` });
            }
          } catch (authErr) {
            job.logs.push({ type: "warn", message: `[err] ${acc.email} 保存 token 异常: ${authErr}` });
          }
        }
        if (pendingOAuthRows.length > 0) {
          try {
            const { sessionId, sessionList } = await createBatchOAuthSessions(pendingOAuthRows);
            job.logs.push({ type: "log", message: `🔐 已自动创建邮箱授权会话: ${sessionId}` });
            for (const s of sessionList) {
              if (s.status === "pending") {
                job.logs.push({ type: "warn", message: `🔐 ${s.email} 设备码: ${s.userCode} · 打开 ${s.verificationUri} 输入后会自动入库 token` });
              } else {
                job.logs.push({ type: "warn", message: `🔐 ${s.email} 设备码申请失败: ${s.errorMsg ?? "未知错误"}` });
              }
            }
            // 自动完成设备码授权：用浏览器登录账号并批准，无需人工干预
            try {
              const autoPayload = sessionList
                .filter((s: BatchOAuthSession) => s.status === "pending")
                .map((s: BatchOAuthSession) => {
                  const row = pendingOAuthRows.find(r => r.email === s.email);
                  // v8.96: pass deviceCode+dbUrl so Python exchanges token via same CF proxy
                  return { accountId: s.accountId, email: s.email, password: row?.password || '', userCode: s.userCode,
                    deviceCode: s.deviceCode, dbUrl: process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost/toolkit' };
                })
                .filter((x: { password: string }) => x.password);
              if (autoPayload.length > 0) {
                const { spawn: spawnAuto } = await import('child_process');
                const autoScript = new URL('../auto_device_code.py', import.meta.url).pathname;
                // v8.75 Bug C: 动态 xray 已被 kill → TCP probe 决定 fallback
                // 优先用 per-account port 保 IP 严格一致; dead 则用 Pool A 10820
                // (同 AS13335 CF Workers, MS 视为同 /16 不触发警告; 比 10809 全局口好得多)
                const _net = await import('net');
                const _probePort = (port: number, timeoutMs = 1500): Promise<boolean> => new Promise((resolve) => {
                  const sock = _net.createConnection({ host: '127.0.0.1', port, timeout: timeoutMs });
                  let done = false;
                  const finish = (ok: boolean) => { if (done) return; done = true; try { sock.destroy(); } catch {} resolve(ok); };
                  sock.once('connect', () => finish(true));
                  sock.once('error', () => finish(false));
                  sock.once('timeout', () => finish(false));
                });
                // 在所有 pending 账号 proxy_port 中找一个还活着的; 都没活就用 Pool A 10820
                const _pendingPorts = Array.from(new Set(
                  autoPayload.map(a => identityMap.get(a.email)?.proxy_port || 0).filter(p => p > 0)
                ));
                let _aliveProxyPort = 0;
                for (const _p of _pendingPorts) {
                  if (await _probePort(_p)) { _aliveProxyPort = _p; break; }
                }
                const _autoProxy = _aliveProxyPort > 0
                  ? `socks5://127.0.0.1:${_aliveProxyPort}`
                  : '';  // v8.99: 不再 fallback 到共享静态端口 → auto_device_code.py 自行 _pick_cf_proxy()
                const _proxyTag = _aliveProxyPort > 0 ? 'per-account-alive' : 'pool-A-fallback (dyn-xray dead)';
                job.logs.push({ type: 'log', message: `🌐 自动授权代理: ${_autoProxy} [${_proxyTag}]` });
                const autoProc = spawnAuto(
                  'python3', [autoScript, JSON.stringify(autoPayload), _autoProxy],
                  { stdio: ["ignore", "pipe", "pipe"], env: { ...(process.env as Record<string,string>), PYTHONUNBUFFERED: '1' } }
                );
                job.logs.push({ type: 'log', message: `🤖 自动完成 ${autoPayload.length} 个账号的设备码授权…` });
                // v8.79 Bug L: 解析 Python 的 RESULTS: 行 → 跳过 error/suspended (它们 poll 必 timeout)
                const _autoResults: Map<string, { status: string; msg: string }> = new Map();
                autoProc.stdout?.on('data', (d: Buffer) => {
                  for (const line of d.toString().split('\n').filter(Boolean)) {
                    job.logs.push({ type: 'log', message: `[auto-auth] ${line}` });
                    if (line.startsWith('RESULTS:')) {
                      try {
                        const arr = JSON.parse(line.slice(8)) as Array<{email:string;status:string;msg:string}>;
                        for (const r of arr) _autoResults.set(r.email, { status: r.status, msg: r.msg });
                      } catch { /* ignore parse */ }
                    }
                  }
                });
                // v9.01: stderr capture (stdio changed ignore->pipe)
                autoProc.stderr?.on('data', (d2: Buffer) => {
                  const errL = d2.toString().split('\n').filter(Boolean);
                  for (const el of errL) {
                    const et = el.trim();
                    if (et && et.length > 5 && !et.includes('DeprecationWarning') && !et.includes('FutureWarning'))
                      job.logs.push({ type: 'log', message: '[auto-auth-sys] ' + et.slice(0, 200) });
                  }
                });
                // v9.01: token exchange done inside Python via CF proxy
                autoProc.on('close', (code: number | null) => {
                  job.logs.push({ type: code === 0 ? 'success' : 'warn', message: '\u{1F916} \u81EA\u52A8\u6388\u6743\u5B8C\u6210 (code=' + code + ') \u2014 token \u5DF2\u7531 Python \u5728 CF proxy \u5185\u5151\u6362\u5165\u5E93' });
                });
              }
            } catch (autoErr) {
              job.logs.push({ type: 'warn', message: `⚠ 启动自动设备码完成失败: ${autoErr}` });
            }
          } catch (oauthErr) {
            job.logs.push({ type: "warn", message: `⚠ 自动申请设备码失败: ${oauthErr}` });
          }
        }
        job.logs.push({ type: "log", message: `📦 已保存账号库 + 邮箱库，并尝试授权 ${okCount} 个账号` });
      })();
    }

    job.logs.push({
      type: "done",
      message: `注册任务完成 · 成功 ${okCount} 个 / 共 ${n} 个` + (okCount > 0 ? ` ✅` : ``),
    });
    await jobQueue.finish(jobId, code ?? -1, "done");
  });
});

// 查询任务状态（前端每 2s 轮询）
router.get("/tools/outlook/register/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) {
    res.status(404).json({ success: false, error: "任务不存在" });
    return;
  }

  const since   = Number(req.query.since ?? 0);
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
function classifyToolJob(jobId: string) {
  if (jobId.startsWith("reg_")) return { source: "tools", kind: "outlook_register", title: "Outlook 注册" };
  if (jobId.startsWith("curhttp_")) return { source: "tools", kind: "cursor_http_register", title: "Cursor HTTP 注册" };
  if (jobId.startsWith("cur_")) return { source: "tools", kind: "cursor_register", title: "Cursor 注册" };
  if (jobId.startsWith("retoken_")) return { source: "tools", kind: "outlook_retoken", title: "Outlook Retoken" };
  if (jobId.startsWith("np_reg_")) return { source: "tools", kind: "novproxy_register", title: "NovProxy 注册" };
  if (jobId.startsWith("np_login_")) return { source: "tools", kind: "novproxy_login", title: "NovProxy 登录" };
  if (jobId.startsWith("ip2free_")) return { source: "tools", kind: "ip2free_register", title: "ip2free 注册" };
  return { source: "tools", kind: "tool_job", title: "工具任务" };
}

router.get("/tools/jobs", async (_req, res) => {
  const allJobs = await jobQueue.list();
  const jobs = allJobs.map(job => ({
    id: job.jobId,
    ...classifyToolJob(job.jobId),
    status: job.status,
    startedAt: job.startedAt,
    logCount: job.logs.length,
    accountCount: job.accounts.length,
    exitCode: job.exitCode,
    lastLog: job.logs.at(-1) ?? null,
  }));
  res.json({ success: true, jobs });
});

router.get("/tools/jobs/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "任务不存在" }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({
    success: true,
    jobId: job.jobId,
    ...classifyToolJob(job.jobId),
    status: job.status,
    accounts: job.accounts,
    logs: job.logs.slice(since),
    nextSince: job.logs.length,
    exitCode: job.exitCode,
  });
});

router.delete("/tools/jobs/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});

// 停止任务
router.delete("/tools/outlook/register/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});

// ── Cursor 账号自动注册 ────────────────────────────────────
router.post("/tools/cursor/register", async (req, res) => {
  const {
    count = 1,
    proxy: proxyInput = "",
    headless = true,
    autoProxy = false,
    cdpUrl = "",
    userDataDir = "",
  } = req.body as { count?: number; proxy?: string; headless?: boolean; autoProxy?: boolean; cdpUrl?: string; userDataDir?: string };

  let proxy = proxyInput;
  if (!proxy && autoProxy) {
    try {
      await syncLocalSubnodeBridgeProxies();
      const { query: dbQuery } = await import("../db.js");
      const rows = await dbQuery<{ id: number; formatted: string }>(
        `SELECT id, formatted FROM proxies WHERE ${ELIGIBLE_SHARED_PROXY_SQL} ORDER BY CASE WHEN ${SUBNODE_BRIDGE_SQL} THEN 0 WHEN host <> '127.0.0.1' THEN 1 ELSE 2 END, used_count ASC, RANDOM() LIMIT 1`
      );
      if (rows[0]) {
        proxy = rows[0].formatted;
        const { execute: dbExec } = await import("../db.js");
        await dbExec("UPDATE proxies SET used_count = used_count + 1, last_used = NOW(), status = 'active' WHERE id = $1", [rows[0].id]);
      }
    } catch {}
  }

  const n = Math.min(5, Math.max(1, count));
  const jobId = `cur_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const proxyDisplay = proxy ? proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") : "无代理";
  const job = await jobQueue.create(jobId);
  job.logs.push({ type: "start", message: `启动 Cursor 自动注册 ${n} 个账号...` });
  if (proxy) job.logs.push({ type: "log", message: `🌐 代理: ${proxyDisplay}` });
  if (cdpUrl) job.logs.push({ type: "log", message: "🆓 免费真实浏览器模式：使用外部 Chrome CDP" });
  if (userDataDir) job.logs.push({ type: "log", message: "🆓 免费持久 Profile 模式：复用 Chrome 用户数据目录" });

  // Cursor 注册默认只走免费真实浏览器/Profile 路径，不自动启用付费打码服务。

  res.json({ success: true, jobId, message: "Cursor 注册任务已启动" });

  const { spawn } = await import("child_process");
  const scriptPath = new URL("../cursor_register.py", import.meta.url).pathname;
  const args = [scriptPath, "--count", String(n), "--headless", headless ? "true" : "false"];
  if (proxy) args.push("--proxy", proxy);
  if (cdpUrl) args.push("--cdp-url", cdpUrl);
  if (userDataDir) args.push("--user-data-dir", userDataDir);

  const child = spawn("python3", args, { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
  jobQueue.setChild(jobId, child);

  child.stdout.on("data", (chunk: Buffer) => {
    for (const line of chunk.toString().split("\n")) {
      const s = line.trim();
      if (!s) continue;
      try {
        const ev = JSON.parse(s) as { type: string; message: string };
        if (ev.type === "accounts") {
          const accounts = JSON.parse(ev.message) as Array<{ email: string; password: string; name: string; token?: string }>;
          for (const acc of accounts) {
            const existing = (job.accounts as Array<{email:string}>).find(a => a.email === acc.email);
            if (existing) {
              // update token if we now have it
              if (acc.token) (existing as any).token = acc.token;
            } else {
              job.accounts.push({ email: acc.email, password: acc.password, username: acc.name, token: acc.token });
            }
            // Upsert into DB with token
            import("../db.js").then(({ execute: dbExec }) => {
              dbExec(
                `INSERT INTO accounts (platform, email, password, token, status, notes, created_at)
                 VALUES ('cursor', $1, $2, $3, 'active', 'Auto registered', NOW())
                 ON CONFLICT (platform, email) DO UPDATE
                   SET password = EXCLUDED.password,
                       token = COALESCE(EXCLUDED.token, accounts.token),
                       status = 'active'`,
                [acc.email, acc.password, acc.token ?? null]
              ).catch(() => {});
            }).catch(() => {});
          }
        } else {
          job.logs.push({ type: ev.type === "success" ? "success" : ev.type === "error" ? "error" : "log", message: ev.message });
          if (ev.type === "success") {
            // push to job.accounts immediately so notifier can see it
            (function() {
              const _m = ev.message.match(/[\w.+\-]+@[\w.\-]+/);
              const _pw = ev.message.match(/密码[：:]\s*(\S+)/);
              if (_m) {
                const _exists = (job.accounts as Array<{email:string}>).find(a => a.email === _m[0]);
                if (!_exists) job.accounts.push({ email: _m[0], password: _pw?.[1] ?? "" });
              }
            })();
            import("../db.js").then(({ execute: dbExec }) => {
              const m = ev.message.match(/\S+@\S+/);
              const pwm = ev.message.match(/密码:\s*(\S+)/);
              if (m) {
                dbExec(
                  `INSERT INTO accounts (platform, email, password, status, notes, created_at)
                   VALUES ('cursor', $1, $2, 'active', 'Auto registered', NOW())
                   ON CONFLICT (platform, email) DO UPDATE SET password = EXCLUDED.password, status = 'active'`,
                  [m[0], pwm?.[1] ?? ""]
                ).catch(() => {});
              }
            }).catch(() => {});
          }
        }
      } catch {
        if (s) job.logs.push({ type: "log", message: s });
      }
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const s = chunk.toString().trim();
    if (s && !s.includes("DeprecationWarning") && !s.includes("FutureWarning") && !s.includes("UserWarning")) {
      job.logs.push({ type: "error", message: s.slice(0, 300) });
    }
  });

  child.on("close", async (code) => {
    const ok = job.accounts.length;
    job.logs.push({ type: code === 0 ? "done" : "error", message: `任务结束  成功: ${ok} / ${n}` });
    await jobQueue.finish(jobId, code ?? -1, code === 0 ? "done" : "failed");
  });
});

router.get("/tools/cursor/register/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "任务不存在" }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({ success: true, status: job.status, accounts: job.accounts, logs: job.logs.slice(since), nextSince: job.logs.length, exitCode: job.exitCode });
});

router.delete("/tools/cursor/register/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});


// ── Cursor HTTP 注册（无浏览器，纯 HTTP 协议）────────────────────────────────
// POST /tools/cursor/register-http
router.post("/tools/cursor/register-http", async (req, res) => {
  const {
    email = "",
    password = "",
    proxy: proxyInput = "",
    useXray = false,
    skipStep1 = false,
    autoProxy = false,
    imapHost = "",
    imapUser = "",
    imapPass = "",
    outlookAccountId,
  } = req.body as {
    email?: string; password?: string; proxy?: string;
    useXray?: boolean; skipStep1?: boolean; autoProxy?: boolean;
    imapHost?: string; imapUser?: string; imapPass?: string;
    outlookAccountId?: number;
  };

  let proxy = proxyInput;
  if (useXray) {
    proxy = "socks5://127.0.0.1:10808";
  } else if (!proxy && autoProxy) {
    try {
      await syncLocalSubnodeBridgeProxies();
      const { query: dbQuery } = await import("../db.js");
      const rows = await dbQuery<{ formatted: string }>(
        `SELECT formatted FROM proxies WHERE ${ELIGIBLE_SHARED_PROXY_SQL} ORDER BY CASE WHEN ${SUBNODE_BRIDGE_SQL} THEN 0 WHEN host <> '127.0.0.1' THEN 1 ELSE 2 END, used_count ASC, RANDOM() LIMIT 1`
      );
      if (rows[0]) proxy = rows[0].formatted;
    } catch {}
  }

  let resolvedEmail = email;
  let resolvedImapUser = imapUser;
  let resolvedImapPass = imapPass;
  if (outlookAccountId) {
    try {
      const { query: dbQuery } = await import("../db.js");
      const rows = await dbQuery<{ email: string; password: string | null }>(
        "SELECT email, password FROM accounts WHERE id=$1 AND platform='outlook'",
        [outlookAccountId]
      );
      if (rows[0]) {
        resolvedEmail = resolvedEmail || rows[0].email;
        if (!resolvedImapUser) resolvedImapUser = rows[0].email;
        if (!resolvedImapPass) resolvedImapPass = rows[0].password ?? "";
      }
    } catch {}
  }

  const jobId = `curhttp_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const job = await jobQueue.create(jobId);
  const proxyDisplay = proxy ? proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") : "无代理";
  job.logs.push({ type: "start", message: `启动 Cursor HTTP 注册（纯 HTTP，无浏览器）...` });
  if (proxy) job.logs.push({ type: "log", message: `🌐 代理: ${proxyDisplay}` });
  if (skipStep1) job.logs.push({ type: "log", message: "⏭️  跳过 Step1（直接 Server Action）" });

  res.json({ success: true, jobId, message: "Cursor HTTP 注册任务已启动" });

  const { spawn } = await import("child_process");
  const scriptPath = new URL("../cursor_register_http.py", import.meta.url).pathname;
  const args = [scriptPath];
  if (resolvedEmail)   args.push("--email", resolvedEmail);
  if (password)        args.push("--password", password);
  if (proxy)           args.push("--proxy", proxy);
  if (useXray)         args.push("--use-xray");
  if (skipStep1)       args.push("--skip-step1");
  if (imapHost || resolvedImapUser) {
    args.push("--imap-host",  imapHost || "outlook.office365.com");
    args.push("--imap-user",  resolvedImapUser);
    args.push("--imap-pass",  resolvedImapPass);
  }

  const child = spawn("python3", args, { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
  jobQueue.setChild(jobId, child);

  child.stdout.on("data", (chunk: Buffer) => {
    for (const line of chunk.toString().split("\n")) {
      const s = line.trim();
      if (!s) continue;
      try {
        const ev = JSON.parse(s);
        if (ev.success === true && ev.token) {
          job.accounts.push({ email: ev.email, password: ev.password, token: ev.token });
          job.logs.push({ type: "success", message: `✅ 注册成功: ${ev.email} | token ${ev.token.length} chars` });
          import("../db.js").then(({ execute: dbExec }) => {
            dbExec(
              `INSERT INTO accounts (platform, email, password, token, status, notes, created_at)
               VALUES ('cursor', $1, $2, $3, 'active', 'HTTP注册', NOW())
               ON CONFLICT (platform, email) DO UPDATE
                 SET password = EXCLUDED.password, token = EXCLUDED.token, status = 'active'`,
              [ev.email, ev.password, ev.token]
            ).catch(() => {});
          }).catch(() => {});
          return;
        }
        if (ev.success === false && ev.error) {
          job.logs.push({ type: "error", message: `❌ ${ev.error}` });
          return;
        }
      } catch {}
      const isErr = /error|Error|failed|失败|异常/.test(s) && !/Step.*->/.test(s);
      job.logs.push({ type: isErr ? "error" : "log", message: s });
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const s = chunk.toString().trim();
    if (s && !s.includes("DeprecationWarning") && !s.includes("FutureWarning")) {
      job.logs.push({ type: "error", message: s.slice(0, 300) });
    }
  });

  child.on("close", async (code) => {
    const ok = (job.accounts as unknown[]).length;
    job.logs.push({ type: ok ? "done" : "error", message: `任务结束  成功: ${ok} 个` });
    await jobQueue.finish(jobId, code ?? -1, ok ? "done" : "failed");
  });
});

router.get("/tools/cursor/register-http/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "任务不存在" }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({ success: true, status: job.status, accounts: job.accounts, logs: job.logs.slice(since), nextSince: job.logs.length });
});

router.delete("/tools/cursor/register-http/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
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

const REMOTE_GATEWAY_BASE_URL = (process.env["REMOTE_GATEWAY_BASE_URL"] || "http://localhost:8080").replace(/\/$/, "");
const REMOTE_EXEC_BASE_URL = (process.env["REMOTE_EXEC_BASE_URL"] || "http://45.205.27.69:9999").replace(/\/$/, "");
const REMOTE_EXEC_TOKEN = process.env["REMOTE_EXEC_TOKEN"] || "zencoder-exec-2026";

async function fetchJsonWithTimeout(url: string, options: RequestInit = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...options, signal: controller.signal });
    const text = await r.text();
    let data: unknown = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    return { ok: r.ok, status: r.status, data };
  } finally {
    clearTimeout(timeout);
  }
}

router.get("/tools/gateway/status", async (_req, res) => {
  const gateway = await fetchJsonWithTimeout(`${REMOTE_GATEWAY_BASE_URL}/v1/models`, { method: "GET", headers: { "Authorization": "Bearer sk-06cf1c8b3d804a5abf90f71c36fe1b08" } }, 8000)
    .then((r) => ({ reachable: r.status < 500, status: r.status, baseUrl: REMOTE_GATEWAY_BASE_URL }))
    .catch((e: unknown) => ({ reachable: false, baseUrl: REMOTE_GATEWAY_BASE_URL, error: String(e) }));

  const exec = await fetchJsonWithTimeout(`${REMOTE_EXEC_BASE_URL}/health`, {
    method: "GET",
    headers: { "x-token": REMOTE_EXEC_TOKEN },
  }, 8000)
    .then((r) => ({ reachable: r.ok, status: r.status, baseUrl: REMOTE_EXEC_BASE_URL, data: r.data }))
    .catch((e: unknown) => ({ reachable: false, baseUrl: REMOTE_EXEC_BASE_URL, error: String(e) }));

  res.json({ success: Boolean(gateway.reachable), gateway, exec, replitPath: "/api/gateway" });
});

router.post("/tools/gateway/request", async (req, res) => {
  try {
    const { path: gatewayPath = "/", method = "GET", headers: extraHeaders = {}, body } = req.body as {
      path?: string; method?: string; headers?: Record<string, string>; body?: unknown;
    };
    const normalizedPath = String(gatewayPath).startsWith("/") ? String(gatewayPath) : `/${gatewayPath}`;
    if (/^https?:\/\//i.test(normalizedPath)) {
      res.status(400).json({ success: false, error: "path 只能是远程网关相对路径" });
      return;
    }

    const payload = typeof body === "string" ? body : body === undefined ? undefined : JSON.stringify(body);
    const r = await fetchJsonWithTimeout(`${REMOTE_GATEWAY_BASE_URL}${normalizedPath}`, {
      method,
      headers: { "Content-Type": "application/json", ...extraHeaders },
      body: method.toUpperCase() !== "GET" && method.toUpperCase() !== "HEAD" ? payload : undefined,
    }, 120000);
    res.status(r.ok ? 200 : 502).json({ success: r.ok, status: r.status, data: r.data, upstream: REMOTE_GATEWAY_BASE_URL });
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
  dob: { date: string; age: number };
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

// ── 通用代理请求（避免前端 CORS 问题）─────────────────────
router.post("/tools/proxy-request", async (req, res) => {
  try {
    const { url, method = "GET", headers: extraHeaders = {}, body } = req.body as {
      url?: string; method?: string; headers?: Record<string, string>; body?: string;
    };
    if (!url) { res.status(400).json({ success: false, error: "url 不能为空" }); return; }

    const allowed = [
      "45.205.27.69",
      "sub2api.com", "cpa.io", "cpaapi.io", "oaifree.com", "api.x.ai",
      "api.anthropic.com", "api.openai.com", "api.deepseek.com",
      "generativelanguage.googleapis.com",
    ];
    const host = new URL(url).hostname;
    if (!allowed.some((a) => host.endsWith(a))) {
      res.status(403).json({ success: false, error: `域名 ${host} 不在允许列表中` });
      return;
    }

    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", ...extraHeaders },
      body: method !== "GET" ? body : undefined,
    });
    let data: unknown;
    try { data = await r.json(); } catch { data = { raw: await r.text() }; }
    res.json({ success: r.ok, status: r.status, data });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── Webshare 代理池 ──────────────────────────────────────────────────────────
const WEBSHARE_API_KEY = process.env["WEBSHARE_API_KEY"] || "lx7r5124cubob5mfmofbdtjvdti5bqy2lxdg06ho";
const WEBSHARE_API_BASE = "https://proxy.webshare.io/api/v2";

type WebshareProxy = {
  id: string; username: string; password: string;
  proxy_address: string; port: number; valid: boolean;
  country_code: string; city_name: string; last_verification: string;
};

async function syncWebshareProxies(): Promise<{ synced: number; total: number; error?: string }> {
  const apiKey = WEBSHARE_API_KEY;
  if (!apiKey) return { synced: 0, total: 0, error: "WEBSHARE_API_KEY not configured" };
  try {
    const resp = await fetch(`${WEBSHARE_API_BASE}/proxy/list/?mode=direct&page=1&page_size=100`, {
      headers: { "Authorization": `Token ${apiKey}` },
      signal: AbortSignal.timeout(15_000),
    });
    if (!resp.ok) return { synced: 0, total: 0, error: `API ${resp.status}: ${(await resp.text()).slice(0, 100)}` };
    const data = await resp.json() as { count: number; results: WebshareProxy[] };
    let synced = 0;
    for (const p of data.results) {
      if (!p.valid) continue;
      const formatted = `http://${p.username}:${p.password}@${p.proxy_address}:${p.port}`;
      await execute(
        `INSERT INTO proxies (formatted, host, port, username, password, status, used_count, raw)
         VALUES ($1, $2, $3, $4, $5, 'idle', 0, $6)
         ON CONFLICT (formatted) DO UPDATE SET
           host     = EXCLUDED.host,
           port     = EXCLUDED.port,
           username = EXCLUDED.username,
           password = EXCLUDED.password,
           raw      = EXCLUDED.raw,
           status   = CASE WHEN proxies.status = 'banned' THEN 'idle' ELSE proxies.status END`,
        [formatted, p.proxy_address, p.port, p.username, p.password,
         JSON.stringify({ ws_id: p.id, country: p.country_code, city: p.city_name, last_check: p.last_verification })]
      );
      synced++;
    }
    return { synced, total: data.count };
  } catch (e) {
    return { synced: 0, total: 0, error: String(e) };
  }
}

// GET /tools/webshare/status — 查看 DB 中已同步的 Webshare 代理 + 实时 API 状态
router.get("/tools/webshare/status", async (_req, res) => {
  try {
    type PRow = { formatted: string; host: string; port: number; status: string; used_count: number; last_used: string | null; raw: string | null };
    const rows = await (query as (s: string) => Promise<PRow[]>)(
      `SELECT formatted, host, port, status, used_count, last_used, raw
       FROM proxies WHERE username = 'nnhginhn' ORDER BY used_count ASC`
    ).catch(() => [] as PRow[]);

    let live: { count: number; proxies: Array<{ ip: string; port: number; valid: boolean; country: string; city: string; last_check: string }> } | null = null;
    const apiKey = WEBSHARE_API_KEY;
    if (apiKey) {
      try {
        const r = await fetch(`${WEBSHARE_API_BASE}/proxy/list/?mode=direct&page=1&page_size=100`, {
          headers: { "Authorization": `Token ${apiKey}` },
          signal: AbortSignal.timeout(10_000),
        });
        if (r.ok) {
          const d = await r.json() as { count: number; results: WebshareProxy[] };
          live = { count: d.count, proxies: d.results.map((p) => ({ ip: p.proxy_address, port: p.port, valid: p.valid, country: p.country_code, city: p.city_name, last_check: p.last_verification })) };
        }
      } catch { /* silent */ }
    }

    res.json({
      success: true,
      db: {
        count: rows.length,
        proxies: rows.map((r) => {
          let meta: Record<string, unknown> = {};
          try { meta = JSON.parse(r.raw || "{}"); } catch { /* */ }
          return { proxy: r.formatted, host: r.host, port: r.port, status: r.status, used_count: r.used_count, last_used: r.last_used, ...meta };
        }),
      },
      live,
    });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// POST /tools/webshare/sync — 从 Webshare API 全量拉取并 upsert 进 proxies 表
router.post("/tools/webshare/sync", async (_req, res) => {
  const result = await syncWebshareProxies();
  if (result.error) {
    res.status(500).json({ success: false, ...result });
    return;
  }
  res.json({ success: true, ...result, message: `已同步 ${result.synced}/${result.total} 个 Webshare 代理到 proxies 表` });
});

// ── ip2free.com 注册 ──────────────────────────────────────────────────────────

// POST /tools/ip2free/register — 使用 Webshare 代理 + Outlook 邮箱在 ip2free.com 注册
router.post("/tools/ip2free/register", async (req, res) => {
  const {
    email           = "",
    outlookPassword = "",
    accessToken     = "",
    ip2freePassword = "",
    proxy: proxyInput = "",
    inviteCode      = "7pdC4VeeYw",
    headless        = true,
    autoProxy       = false,
  } = req.body as {
    email?: string; outlookPassword?: string; accessToken?: string;
    ip2freePassword?: string; proxy?: string; inviteCode?: string;
    headless?: boolean; autoProxy?: boolean;
  };

  if (!email) {
    res.status(400).json({ success: false, error: "email 是必填项" });
    return;
  }

  const jobId = `ip2free_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const preJobLogs: Array<{ type: string; message: string }> = [];

  // ── IP一致性: 优先查账号绑定代理 ───────────────────────────────────────
  // 从 accounts 表取该 Outlook 邮箱注册时绑定的代理（proxy_formatted > proxy_port 重建）
  // 这确保 ip2free 浏览器注册 + IMAP 验证码读取都使用与 Outlook 注册时相同的出口 IP
  let accountProxy = "";
  let outlookAccountId = 0;
  let outlookDbPassword = "";
  if (email) {
    try {
      const acctRow = await queryOne<{ id: number | null; password: string | null; proxy_formatted: string | null; proxy_port: number | null }>(
        "SELECT id, password, proxy_formatted, proxy_port FROM accounts WHERE platform='outlook' AND email=$1 LIMIT 1",
        [email]
      );
      if (acctRow?.id) outlookAccountId = acctRow.id;
      // 用 Outlook DB 密码作为 ip2free 注册密码（未手动指定时自动使用）
      if (acctRow?.password && !outlookDbPassword) outlookDbPassword = acctRow.password;
      if (acctRow?.proxy_formatted) {
        accountProxy = acctRow.proxy_formatted;
      } else if (acctRow?.proxy_port && acctRow.proxy_port > 0) {
        accountProxy = `socks5://127.0.0.1:${acctRow.proxy_port}`;
      }
      if (accountProxy) {
        req.log?.info?.({ accountProxy: accountProxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") },
          "ip2free: 使用账号绑定代理（IP一致性）");
      }
    } catch (e) {
      req.log?.warn?.({ err: e }, "ip2free: 账号绑定代理查询失败，降级到自适应选取");
    }
  }

  // ── 自适应多池代理选取 ────────────────────────────────────────────────────
  // 优先级: 账号绑定代理 > 手动指定 > DB 自适应选取（Pool-C → Pool-B → Pool-A）
  let proxyList: string[] = [];
  if (accountProxy) proxyList.push(accountProxy);         // IP一致性最高优先
  if (proxyInput && !proxyList.includes(proxyInput)) proxyList.push(proxyInput);

  if (autoProxy) {
    try {
      const picked = await pickAdaptiveProxy("ip2free", 5);
      const newProxies = picked
        .map((p) => p.formatted)
        .filter((f) => !proxyList.includes(f));
      proxyList.push(...newProxies);
      if (picked.length > 0) {
        const summary = picked.map((p) => `${p.pool}:${p.formatted.replace(/:([^:@]{4})[^:@]*@/, ":****@").slice(0, 40)}`).join(" | ");
        preJobLogs.push({ type: "log", message: `🌐 自适应代理池: ${picked.length} 个节点 | ${summary}` });
      } else {
        preJobLogs.push({ type: "warn", message: "⚠ 无可用代理，将以直连模式尝试" });
      }
    } catch (e) {
      preJobLogs.push({ type: "warn", message: `⚠ 自适应代理选取失败: ${String(e).slice(0, 100)}` });
    }
  }

  const primaryProxy    = proxyList[0] ?? "";
  const proxyDisplay    = primaryProxy ? primaryProxy.replace(/:([^:@]{4})[^:@]*@/, ":****@") : "无代理";
  const job = await jobQueue.create(jobId);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  for (const l of preJobLogs) job.logs.push(l as any);
  job.logs.push({ type: "start", message: `启动 ip2free 注册: ${email} [${proxyList.length} 个代理备选]` });

  res.json({ success: true, jobId, message: "ip2free 注册任务已启动" });

  const { spawn } = await import("child_process");
  const scriptPath = new URL("../ip2free_register.py", import.meta.url).pathname;
  const args: string[] = [
    scriptPath,
    "--email",       email,
    "--invite-code", inviteCode,
    "--headless",    headless ? "true" : "false",
  ];
  if (outlookPassword) args.push("--outlook-password", outlookPassword);
  if (accessToken)     args.push("--access-token",     accessToken);
  const finalIp2freePassword = ip2freePassword || outlookDbPassword;
  if (finalIp2freePassword) args.push("--ip2free-password", finalIp2freePassword);
  // 账号 DB id：Python 侧用于 Graph API 读取验证码（替代已死的 IMAP）
  if (outlookAccountId > 0) args.push("--account-id", String(outlookAccountId));
  if (accountProxy)    args.push("--account-proxy",    accountProxy);
  // 传多代理列表给 Python（ip2free_register.py ProxyChain 负责逐一重试）
  if (proxyList.length > 1) {
    args.push("--proxies", proxyList.join(","));
    args.push("--no-auto-proxy");   // Python 侧已有完整列表，无需再查 DB
    job.logs.push({ type: "log", message: `🔄 多代理重试模式: ${proxyList.length} 个备选` });
  } else if (proxyList.length === 1) {
    args.push("--proxy", proxyList[0]);
    args.push("--no-auto-proxy");
  }
  // autoProxy 且无手动代理：让 Python 自己查 DB（双保险）

  const child = spawn("python3", args, {
    env: { ...(process.env as Record<string, string>), PYTHONUNBUFFERED: "1" },
  });
  jobQueue.setChild(jobId, child);

  let jsonBuf = "";
  let inJson  = false;

  child.stdout.on("data", (chunk: Buffer) => {
    const raw = chunk.toString();
    if (raw.includes("── JSON 结果 ──") || inJson) { inJson = true; jsonBuf += raw; }
    for (const line of raw.split("\n").filter(Boolean)) {
      const t = line.trim();
      if (!t || t === "── JSON 结果 ──" || t === "[" || t === "]") continue;
      let type = "log";
      if (t.includes("⚠"))       type = "warn";
      else if (t.includes("❌")) type = "error";
      else if (t.includes("✅")) type = "success";
      job.logs.push({ type, message: t });
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const msg = chunk.toString().trim();
    if (msg && !msg.includes("DeprecationWarning")) {
      for (const l of msg.split("\n")) {
        const lt = l.trim();
        if (lt && lt.length > 5) job.logs.push({ type: "log", message: `[sys] ${lt.slice(0, 200)}` });
      }
    }
  });

  child.on("close", async (code) => {
    let regOk      = false;
    let ip2freePwd = "";
    try {
      const start = jsonBuf.indexOf("[");
      if (start >= 0) {
        const parsed = JSON.parse(jsonBuf.slice(start)) as Array<Record<string, unknown>>;
        if (parsed[0]?.success) {
          regOk      = true;
          ip2freePwd = String(parsed[0].ip2free_password || "");
          job.accounts.push({ email, password: ip2freePwd });
          // 持久化到 accounts 表
          try {
            await execute(
              `INSERT INTO accounts (platform, email, password, status, notes)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (platform, email) DO UPDATE SET
                 password=EXCLUDED.password, status=EXCLUDED.status,
                 notes=EXCLUDED.notes, updated_at=NOW()`,
              ["ip2free", email, ip2freePwd, "active", `ip2free注册 inv:${inviteCode}`]
            );
            job.logs.push({ type: "log", message: `📦 ip2free 账号已入库 (${email})` });
          } catch (dbErr) {
            job.logs.push({ type: "warn", message: `⚠ 数据库保存失败: ${dbErr}` });
          }
        }
      }
    } catch { /* ignore JSON parse error */ }

    job.logs.push({
      type:    "done",
      message: `ip2free 注册${regOk ? "成功 ✅" : "失败 ❌"} · ${email}`,
    });
    await jobQueue.finish(jobId, code ?? -1, "done");
  });
});

// GET /tools/ip2free/register/:jobId — 查询注册任务状态
router.get("/tools/ip2free/register/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "任务不存在" }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({
    success:   true,
    status:    job.status,
    accounts:  job.accounts,
    logs:      job.logs.slice(since),
    nextSince: job.logs.length,
    exitCode:  job.exitCode,
  });
});


// ── CF IP 代理池 ──────────────────────────────────────────────
const CF_POOL_SCRIPT = process.env["CF_POOL_SCRIPT"]
  || [
    path.resolve(process.cwd(), "artifacts/api-server/cf_pool_api.py"),
    "/workspaces/Toolkit/artifacts/api-server/cf_pool_api.py",
    "/home/runner/workspace/artifacts/api-server/cf_pool_api.py",
  ].find((candidate) => existsSync(candidate))
  || path.resolve(process.cwd(), "artifacts/api-server/cf_pool_api.py");
const CF_POOL_PYTHON = process.env["PYTHON_BIN"] || "python3";
const CF_POOL_REMOTE_API_BASE = (process.env["CF_POOL_REMOTE_API_BASE_URL"] || process.env["REMOTE_API_BASE_URL"] || "http://45.205.27.69:8080/api").replace(/\/$/, "");
const SUBNODE_BRIDGE_MIN_PORT = Number(process.env["SUBNODE_BRIDGE_MIN_PORT"] || 1089);
const SUBNODE_BRIDGE_MAX_PORT = Number(process.env["SUBNODE_BRIDGE_MAX_PORT"] || 1199);
const SUBNODE_BRIDGE_SQL = `
  (
    (host = '127.0.0.1' AND port BETWEEN ${SUBNODE_BRIDGE_MIN_PORT} AND ${SUBNODE_BRIDGE_MAX_PORT})
    OR formatted = 'socks5://127.0.0.1:1089'
    OR formatted ILIKE 'socks5://127.0.0.1:109%'
    OR formatted ILIKE 'socks5://127.0.0.1:11%'
  )
`;
let lastSubnodeBridgeSync = 0;

const SOCKS5_PROBE_HOST = "login.live.com";
const SOCKS5_PROBE_PORT = 443;
const SOCKS5_HANDSHAKE_TIMEOUT_MS = 600;
const SOCKS5_CONNECT_TIMEOUT_MS = 12000;

function testSocks5Connectivity(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new Socket();
    let step = 0;
    let done = false;
    let timer: ReturnType<typeof setTimeout>;

    const finish = (ok: boolean) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      socket.destroy();
      resolve(ok);
    };

    const armTimer = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(() => finish(false), ms);
    };

    socket.once("error", () => finish(false));
    socket.on("data", (buf) => {
      if (step === 0) {
        if (buf.length < 2 || buf[0] !== 0x05 || buf[1] !== 0x00) return finish(false);
        step = 1;
        const hostBuf = Buffer.from(SOCKS5_PROBE_HOST, "ascii");
        const req = Buffer.alloc(7 + hostBuf.length);
        req[0] = 0x05;
        req[1] = 0x01;
        req[2] = 0x00;
        req[3] = 0x03;
        req[4] = hostBuf.length;
        hostBuf.copy(req, 5);
        req.writeUInt16BE(SOCKS5_PROBE_PORT, 5 + hostBuf.length);
        armTimer(SOCKS5_CONNECT_TIMEOUT_MS);
        socket.write(req);
      } else if (step === 1) {
        finish(buf.length >= 2 && buf[0] === 0x05 && buf[1] === 0x00);
      }
    });

    armTimer(SOCKS5_HANDSHAKE_TIMEOUT_MS);
    socket.connect(port, "127.0.0.1", () => {
      socket.write(Buffer.from([0x05, 0x01, 0x00]));
    });
  });
}

/** 快速 TCP 连通性探测（不含 SOCKS5 握手），用于判断端口是否有服务在监听 */
function testPortListening(port: number, timeoutMs = 300): Promise<boolean> {
  return new Promise((resolve) => {
    const s = new Socket();
    let done = false;
    const finish = (ok: boolean) => { if (!done) { done = true; s.destroy(); resolve(ok); } };
    const t = setTimeout(() => finish(false), timeoutMs);
    s.once("connect", () => { clearTimeout(t); finish(true); });
    s.once("error",   () => { clearTimeout(t); finish(false); });
    s.connect(port, "127.0.0.1");
  });
}

async function syncLocalSubnodeBridgeProxies(force = false) {
  const now = Date.now();
  if (!force && now - lastSubnodeBridgeSync < 30000) return;
  lastSubnodeBridgeSync = now;

  const ports = Array.from(
    { length: SUBNODE_BRIDGE_MAX_PORT - SUBNODE_BRIDGE_MIN_PORT + 1 },
    (_, i) => SUBNODE_BRIDGE_MIN_PORT + i
  );

  // 阶段1：快速 TCP 连通探测（300ms），区分"无监听"和"有监听但 SOCKS5 未达标"
  const BATCH = 8;
  const tcpResults: { port: number; listening: boolean }[] = [];
  for (let i = 0; i < ports.length; i += BATCH) {
    const batch = ports.slice(i, i + BATCH);
    const r = await Promise.all(batch.map(async (port) => ({ port, listening: await testPortListening(port) })));
    tcpResults.push(...r);
  }

  const listeningPorts = tcpResults.filter((r) => r.listening).map((r) => r.port);
  const deadPorts      = tcpResults.filter((r) => !r.listening).map((r) => r.port);

  // 阶段2：仅对监听端口做 SOCKS5→login.live.com 握手
  const socks5Results: { port: number; ok: boolean }[] = [];
  for (let i = 0; i < listeningPorts.length; i += BATCH) {
    const batch = listeningPorts.slice(i, i + BATCH);
    const r = await Promise.all(batch.map(async (port) => ({ port, ok: await testSocks5Connectivity(port) })));
    socks5Results.push(...r);
  }

  const good     = socks5Results.filter((r) => r.ok).map((r) => r.port);
  const stubborn = socks5Results.filter((r) => !r.ok).map((r) => r.port);

  console.log(
    `[subnode-bridge] 探测完成: 可用=${good.length} 监听但SOCKS5不通=${stubborn.length} 死亡=${deadPorts.length} 端口:`,
    good.join(",") || "无"
  );

  for (const port of good) {
    await execute(
      `INSERT INTO proxies (formatted, host, port, status, used_count, last_used)
       VALUES ($1, '127.0.0.1', $2, 'idle', 0, NULL)
       ON CONFLICT (formatted) DO UPDATE SET
         host='127.0.0.1',
         port=$2,
         status=CASE WHEN proxies.status='banned' THEN 'idle' ELSE proxies.status END`,
      [`socks5://127.0.0.1:${port}`, port]
    );
  }

  // 仅 ban 真正死亡（无监听）的端口，stubborn 端口保持原状不 ban
  if (deadPorts.length > 0) {
    const deadFormatted = deadPorts.map((port) => `socks5://127.0.0.1:${port}`);
    await execute(
      `UPDATE proxies SET status='banned', last_used=NOW()
       WHERE formatted = ANY($1::text[]) AND status != 'banned'`,
      [deadFormatted]
    );
  }
}
async function forwardCfPoolRequest(endpoint: string, init?: RequestInit) {
  const r = await fetch(`${CF_POOL_REMOTE_API_BASE}${endpoint}`, init);
  const text = await r.text();
  let data: unknown;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }
  return { status: r.status, data };
}

const ELIGIBLE_SHARED_PROXY_SQL = `
  status != 'banned'
  AND NOT (host = '127.0.0.1' AND port BETWEEN 10820 AND 10845)
  AND NOT (formatted ILIKE 'socks5://127.0.0.1:1082%' OR formatted ILIKE 'socks5://127.0.0.1:1083%' OR formatted ILIKE 'socks5://127.0.0.1:1084%')
`;

const SHARED_PROXY_SOURCE_CASE = `
  CASE
    WHEN ${SUBNODE_BRIDGE_SQL} THEN 'subnode_bridge'
    WHEN host = '127.0.0.1' THEN 'local_proxy'
    ELSE 'external'
  END
`;

async function pickSharedProxyPool(limit: number, purpose: "generic" | "outlook" = "generic"): Promise<Array<{ id: number; formatted: string; source: string }>> {
  await syncLocalSubnodeBridgeProxies();
  const n = Math.min(50, Math.max(1, Math.floor(limit || 1)));
  const rows = await query<{ id: number; formatted: string; source: string }>(`
    SELECT id, formatted, ${SHARED_PROXY_SOURCE_CASE} AS source
    FROM proxies
    WHERE ${ELIGIBLE_SHARED_PROXY_SQL}
      AND ($2 = 'generic' OR (${SUBNODE_BRIDGE_SQL}) OR host != '127.0.0.1')
    ORDER BY
      CASE
        WHEN ${SUBNODE_BRIDGE_SQL} THEN 0
        WHEN host <> '127.0.0.1' THEN 1
        ELSE 2
      END,
      used_count ASC,
      RANDOM()
    LIMIT $1
  `, [n, purpose]);
  if (rows.length > 0) {
    await execute(
      "UPDATE proxies SET used_count = used_count + 1, last_used = NOW(), status = 'active' WHERE id = ANY($1::int[])",
      [rows.map((r) => r.id)]
    );
  }
  return rows;
}

/**
 * 自适应多池代理选取 — 按用途优先级从不同代理池选取代理列表。
 *
 * 池优先级（由 purpose 决定）:
 *   outlook   → local_socks5 → webshare_http → external
 *   ip2free   → webshare_http → local_socks5 → external
 *   cursor    → webshare_http → local_socks5 → external
 *   generic   → webshare_http → local_socks5
 *
 * 返回格式化 URL 列表（空列表表示无可用代理）。
 */
async function pickAdaptiveProxy(
  purpose: "outlook" | "ip2free" | "cursor" | "generic",
  count   = 3,
): Promise<Array<{ formatted: string; pool: string }>> {
  const poolOrder: Record<string, string[]> = {
    outlook:  ["local_socks5", "webshare_http"],
    ip2free:  ["webshare_http", "local_socks5"],
    cursor:   ["webshare_http", "local_socks5"],
    generic:  ["webshare_http", "local_socks5"],
  };

  const POOL_CASE = `
    CASE
      WHEN formatted ILIKE 'http://%@%'                             THEN 'webshare_http'
      WHEN (host='127.0.0.1' AND port BETWEEN 10820 AND 10845)     THEN 'local_socks5'
      WHEN (host='127.0.0.1' AND port BETWEEN 1089 AND 1199)       THEN 'subnode_bridge'
      ELSE 'other'
    END
  `;

  const order   = poolOrder[purpose] ?? poolOrder["generic"];
  const results: Array<{ formatted: string; pool: string }> = [];
  let   remain  = Math.min(10, Math.max(1, count));

  for (const pool of order) {
    if (remain <= 0) break;
    let filter = "";
    if (pool === "webshare_http")  filter = "AND formatted ILIKE 'http://%@%'";  // v8.98: exclude bare CF IPs (http://IP:443 no-auth) — they need xray relay, cannot do HTTP CONNECT
    if (pool === "local_socks5")   filter = `AND host='127.0.0.1' AND port BETWEEN 10820 AND 10845`;
    if (pool === "subnode_bridge") filter = `AND host='127.0.0.1' AND port BETWEEN 1089 AND 1199`;
    if (!filter) continue;

    try {
      const rows = await query<{ formatted: string }>(
        `SELECT formatted FROM proxies
         WHERE ${ELIGIBLE_SHARED_PROXY_SQL} ${filter}
         ORDER BY used_count ASC, RANDOM()
         LIMIT $1`,
        [remain]
      );
      for (const r of rows) results.push({ formatted: r.formatted, pool });
      remain -= rows.length;
    } catch { /* non-fatal */ }
  }

  if (results.length > 0) {
    await execute(
      "UPDATE proxies SET used_count=used_count+1, last_used=NOW() WHERE formatted=ANY($1::text[])",
      [results.map((r) => r.formatted)]
    ).catch(() => {});
  }
  return results;
}

function shouldForwardCfPool(error?: Error) {
  return Boolean(error?.message?.includes("ENOENT"));
}

router.get("/tools/cf-pool/status", async (_req, res) => {
  try {
    const { spawnSync } = await import("child_process");
    const r = spawnSync(CF_POOL_PYTHON, [CF_POOL_SCRIPT, "status"], {
      timeout: 10000, encoding: "utf8",
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    if (shouldForwardCfPool(r.error)) {
      const remote = await forwardCfPoolRequest("/tools/cf-pool/status");
      res.status(remote.status).json(remote.data);
      return;
    }
    if (r.stderr) console.error("[cf-pool]", r.stderr.slice(0, 200));
    if (r.error || r.status !== 0) {
      res.status(500).json({ success: false, error: r.error?.message || r.stderr || "cf_pool_api failed" });
      return;
    }
    const data = r.stdout ? JSON.parse(r.stdout) : {};
    res.json({ success: true, ...data });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/cf-pool/refresh", async (req, res) => {
  try {
    const { count = 60, target = 20, threads = 5, port = 443, maxLatency = 800 } = req.body as {
      count?: number; target?: number; threads?: number; port?: number; maxLatency?: number;
    };
    const { spawnSync } = await import("child_process");
    const r = spawnSync(CF_POOL_PYTHON, [
      CF_POOL_SCRIPT, "refresh",
      "--count", String(count),
      "--target", String(target),
      "--threads", String(threads),
      "--port", String(port),
      "--max-latency", String(maxLatency),
    ], { timeout: 45000, encoding: "utf8", env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    if (shouldForwardCfPool(r.error)) {
      const remote = await forwardCfPoolRequest("/tools/cf-pool/refresh", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body ?? {}),
      });
      res.status(remote.status).json(remote.data);
      return;
    }
    if (r.stderr) console.error("[cf-pool refresh]", r.stderr.slice(0, 400));
    if (r.error || r.status !== 0) {
      res.status(500).json({ success: false, error: r.error?.message || r.stderr || "cf_pool_api failed" });
      return;
    }
    const data = r.stdout ? JSON.parse(r.stdout) : {};
    res.json({ success: true, ...data });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── CF IP 池：封禁单个 IP ──────────────────────────────────────────────────
router.post("/tools/cf-pool/ban", async (req, res) => {
  const { ip } = req.body as { ip?: string };
  if (!ip) { res.status(400).json({ success: false, error: "ip 不能为空" }); return; }
  try {
    const { spawnSync } = await import("child_process");
    const r = spawnSync(CF_POOL_PYTHON, [CF_POOL_SCRIPT, "ban", "--ip", ip], {
      timeout: 8000, encoding: "utf8",
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });
    if (shouldForwardCfPool(r.error)) {
      const remote = await forwardCfPoolRequest("/tools/cf-pool/ban", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body ?? {}),
      });
      res.status(remote.status).json(remote.data);
      return;
    }
    if (r.error || r.status !== 0) {
      res.status(500).json({ success: false, error: r.error?.message || r.stderr || "ban failed" });
      return;
    }
    const data = r.stdout ? JSON.parse(r.stdout) : {};
    res.json({ success: true, ...data });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── CF IP 池：重测存活（手动触发 / 定时任务复用）─────────────────────────
router.post("/tools/cf-pool/retest", async (req, res) => {
  try {
    const { maxLatency = 800, threads = 8, port = 443 } = req.body as {
      maxLatency?: number; threads?: number; port?: number;
    };
    const { spawnSync } = await import("child_process");
    const r = spawnSync(CF_POOL_PYTHON, [
      CF_POOL_SCRIPT, "retest",
      "--max-latency", String(maxLatency),
      "--threads",     String(threads),
      "--port",        String(port),
    ], { timeout: 60000, encoding: "utf8", env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    if (shouldForwardCfPool(r.error)) {
      const remote = await forwardCfPoolRequest("/tools/cf-pool/retest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req.body ?? {}),
      });
      res.status(remote.status).json(remote.data);
      return;
    }
    if (r.error || r.status !== 0) {
      res.status(500).json({ success: false, error: r.error?.message || r.stderr || "retest failed" });
      return;
    }
    const data = r.stdout ? JSON.parse(r.stdout) : {};
    res.json({ success: true, ...data });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ── Outlook 账号列表（邮箱库专用）──────────────────────────────────────────
router.get("/tools/outlook/accounts", async (req, res) => {
  try {
    const { query } = await import("../db.js");
    const rows = await query<{
      id: number; email: string; password: string | null; token: string | null; refresh_token: string | null; tags: string | null;
      status: string | null; notes: string | null; created_at: string;
    }>(
      `SELECT id, email, password, token, refresh_token, status, notes, tags, created_at
       FROM accounts
       WHERE platform='outlook'
       ORDER BY
         CASE WHEN status='active' THEN 0 ELSE 1 END,
         CASE WHEN COALESCE(refresh_token,'') <> '' OR COALESCE(token,'') <> '' THEN 0 ELSE 1 END,
         updated_at DESC NULLS LAST,
         created_at DESC`,
      []
    );
    res.json({
      success: true,
      accounts: rows.map((row) => ({
        ...row,
        token: row.token ? "ok" : null,
        refresh_token: row.refresh_token ? "ok" : null,
      })),
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 保存 Outlook refresh_token ─────────────────────────────────────────────
router.post("/tools/outlook/save-token", async (req, res) => {
  const { email, token, refreshToken } = req.body as { email?: string; token?: string; refreshToken?: string };
  if (!email) { res.status(400).json({ success: false, error: "email 不能为空" }); return; }
  try {
    const { execute } = await import("../db.js");
    await execute(
      "UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE email=$3 AND platform='outlook'",
      [token || null, refreshToken || null, email]
    );
    res.json({ success: true });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 批量验证微软账号有效性（ROPC 错误码诊断）────────────────────────────────
// 错误码参考: https://learn.microsoft.com/en-us/azure/active-directory/develop/reference-aadsts-error-codes
const ROPC_CID  = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const ROPC_SCO  = "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access";

function ropcStatus(err?: string, desc?: string): string {
  if (!err) return "valid";
  if (err === "invalid_grant") {
    if (desc?.includes("AADSTS50034")) return "not_exist";
    if (desc?.includes("AADSTS50126")) return "wrong_password";
    if (desc?.includes("AADSTS50076") || desc?.includes("AADSTS50079")) return "need_mfa";
    if (desc?.includes("AADSTS53003"))  return "blocked_ca";
    if (desc?.includes("AADSTS90072")) return "wrong_tenant";
    return `invalid_grant`;
  }
  if (err === "authorization_pending") return "pending";
  return err;
}

// IMAP 登录测试（check_only=true，仅 login/logout，不拉邮件）
// 支持 access_token → XOAUTH2（imapclient）; 无 token → Basic Auth（imaplib）
async function imapCheckLogin(email: string, password: string, accessToken?: string): Promise<{ ok: boolean; error?: string; via?: string }> {
  const { spawn } = await import("child_process");
  const scriptPath = new URL("../outlook_imap.py", import.meta.url).pathname;
  return new Promise((resolve) => {
    const paramObj: Record<string, unknown> = { email, password, limit: 1, folder: "INBOX", search: "", check_only: true };
    if (accessToken) paramObj["access_token"] = accessToken;
    const params = JSON.stringify(paramObj);
    const child = spawn(process.env.PYTHON_BIN || "/usr/bin/python3", [scriptPath, params], { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.on("close", () => {
      try {
        const r = JSON.parse(out.trim()) as { success: boolean; error?: string; via?: string };
        resolve(r.success ? { ok: true, via: r.via } : { ok: false, error: r.error, via: r.via });
      } catch { resolve({ ok: false, error: `解析失败: ${out.slice(0, 100)}` }); }
    });
    child.on("error", (e) => resolve({ ok: false, error: e.message }));
    setTimeout(() => { child.kill(); resolve({ ok: false, error: "IMAP 超时" }); }, 20000);
  });
}

router.post("/tools/outlook/verify-accounts", async (req, res) => {
  const { ids } = req.body as { ids?: number[] };
  try {
    const { query: dbQ, execute: dbE } = await import("../db.js");
    const rows = await dbQ<{ id: number; email: string; password: string | null; token: string | null; refresh_token: string | null; tags: string | null; status: string }>(
      ids?.length
        ? `SELECT id, email, password, token, refresh_token, tags, status FROM accounts WHERE platform='outlook' AND id = ANY($1::int[])`
        : `SELECT id, email, password, token, refresh_token, tags, status FROM accounts WHERE platform='outlook'`,
      ids?.length ? [ids] : []
    );
    const results: Array<{ id: number; email: string; status: string; via?: string; error?: string }> = [];
    for (const acc of rows) {
      let accessToken = "";   // 不直接使用可能过期的 DB token
      const acctProxy = await resolveAccountProxy(acc.id);

      // 1. 有 refresh_token → 先用 /common/ 刷新（优先级最高）
      let refreshError = "";
      if (acc.refresh_token) {
        const r = await microsoftFetch(`https://login.microsoftonline.com/common/oauth2/v2.0/token`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "refresh_token",
            client_id: OAUTH_CLIENT_ID,
            refresh_token: acc.refresh_token,
            scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
          }).toString(),
        }, acctProxy);
        const td = await r.json() as { access_token?: string; refresh_token?: string; error?: string; error_description?: string };
        if (td.access_token) {
          accessToken = td.access_token;
          await dbE("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
            [accessToken, td.refresh_token ?? acc.refresh_token, acc.id]);
        } else {
          refreshError = td.error_description ?? td.error ?? "刷新失败(未知)";
        }
      } else {
        // 无 refresh_token → 退而使用 DB 里存的 token（可能过期，姑且一试）
        accessToken = acc.token ?? "";
      }

      // 2. 有 accessToken → Graph API 验证（/me 轻量接口）
      //    不走 IMAP，避免 BasicAuthBlocked 误报
      if (accessToken) {
        const gr = await microsoftFetch("https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName", {
          headers: { Authorization: `Bearer ${accessToken}` },
        }, acctProxy);
        if (gr.ok) {
          await dbE("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [acc.id]);
          results.push({ id: acc.id, email: acc.email, status: "valid", via: "graph" });
          continue;
        }
        // Graph 失败（token 无效）→ 报错，不回落 Basic Auth
        const ge = await gr.json() as { error?: { message?: string } };
        await dbE("UPDATE accounts SET status='error', updated_at=NOW() WHERE id=$1", [acc.id]);
        results.push({ id: acc.id, email: acc.email, status: "error", error: `Graph API 验证失败: ${ge?.error?.message ?? gr.status}` });
        continue;
      }

      // 3. 有 refresh_token 但刷新失败 → 直接报错，不走 Basic Auth
      if (acc.refresh_token && refreshError) {
        await dbE("UPDATE accounts SET status='error', updated_at=NOW() WHERE id=$1", [acc.id]);
        results.push({ id: acc.id, email: acc.email, status: "error", error: `OAuth token 刷新失败: ${refreshError.slice(0, 120)}` });
        continue;
      }

      // 4. 无 refresh_token 且无有效 token
      //    - 有 needs_oauth_manual 标签或 status=needs_oauth → 直接报 needs_oauth（勿走 IMAP，微软已封 Basic Auth）
      //    - 无密码 → 报 no_password
      //    - 有密码但无 OAuth → IMAP Basic Auth（仅极少数未迁移账号）
      const acTags = (acc as unknown as { tags?: string | null } & typeof acc).tags ?? "";
      if (acTags.includes("needs_oauth_manual") || acc.status === "needs_oauth") {
        results.push({ id: acc.id, email: acc.email, status: "needs_oauth", error: "账号需要设备码重新授权（needs_oauth_manual）" });
        continue;
      }
      if (!acc.password) {
        results.push({ id: acc.id, email: acc.email, status: "no_password", error: "数据库无密码且无 OAuth token" });
        continue;
      }
      const chk = await imapCheckLogin(acc.email, acc.password);
      if (chk.ok) {
        await dbE("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [acc.id]);
        results.push({ id: acc.id, email: acc.email, status: "valid", via: "basic_auth" });
      } else {
        const err = chk.error ?? "";
        let status = "error";
        if (/BasicAuthBlocked/i.test(err))                          status = "imap_disabled";
        else if (/AUTHENTICATIONFAILED|LOGIN failed|认证失败/i.test(err)) status = "wrong_password";
        else if (/禁用基础密码|basic auth blocked/i.test(err))      status = "imap_disabled";
        else if (/refused|拒绝|timed out|IMAP 超时/i.test(err))    status = "connection_error";
        await dbE("UPDATE accounts SET status=$1, updated_at=NOW() WHERE id=$2", [status, acc.id]);
        results.push({ id: acc.id, email: acc.email, status, error: err.slice(0, 160) });
      }
    }
    const valid    = results.filter(r => r.status === "valid").length;
    const pwErr    = results.filter(r => r.status === "wrong_password").length;
    const disabled = results.filter(r => r.status === "imap_disabled").length;
    res.json({ success: true, results, total: rows.length, valid, pwErr, imap_disabled: disabled });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── ROPC 一键自动授权（ROPC 已被微软封锁，改为引导设备码授权）────────────────
// 注意：微软已于2024年底对个人 Outlook/Hotmail 账号全面封锁 ROPC 密码授权
// 解决方案：使用设备码授权 /tools/outlook/device-code（无需密码，用户扫码即可）
router.post("/tools/outlook/auto-auth", async (req, res) => {
  const { accountId } = req.body as { accountId?: number };
  if (!accountId) { res.status(400).json({ success: false, error: "accountId 不能为空" }); return; }
  try {
    const { query } = await import("../db.js");
    const rows = await query<{ id: number; email: string; refresh_token: string | null }>(
      "SELECT id, email, refresh_token FROM accounts WHERE id=$1 AND platform=\'outlook\'", [accountId]
    );
    const acc = rows[0];
    if (!acc) { res.status(404).json({ success: false, error: "账号不存在" }); return; }
    // 封禁账号（suspended+abuse_mode）直接短路，不浪费时间试 token/Graph/IMAP
    if (acc.status === "suspended" && (acc.tags ?? "").includes("abuse_mode")) {
      res.json({ success: false, error: "账号已被微软封禁（API封禁），无法读取邮件", via: "blocked" });
      return;
    }

    // 已有 refresh_token → 直接刷新 access_token，无需用户操作
    const acctProxy = await resolveAccountProxy(accountId);
    if (acc.refresh_token) {
      const { execute } = await import("../db.js");
      const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          client_id: OAUTH_CLIENT_ID,
          refresh_token: acc.refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string; error_description?: string };
      if (td.access_token) {
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [td.access_token, td.refresh_token ?? acc.refresh_token, accountId]);
        res.json({ success: true, email: acc.email, via: "refresh_token" });
        return;
      }
      res.json({ success: false, needsDeviceFlow: true, error: `refresh_token 已失效：${td.error_description ?? "请重新设备码授权"}` });
      return;
    }

    // 无 refresh_token → 引导设备码授权
    res.json({
      success: false,
      needsDeviceFlow: true,
      error: "微软已封锁密码直连授权（AADSTS70003）。请点击「设备码」按钮，前往 microsoft.com/devicelogin 输入代码完成授权。",
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 批量一键授权（优先 refresh_token 刷新，无 refresh_token 账号提示设备码）────
router.post("/tools/outlook/auto-auth-all", async (req, res) => {
  try {
    const { query, execute } = await import("../db.js");
    const rows = await query<{
      id: number; email: string; password: string | null; refresh_token: string | null;
    }>(
      "SELECT id, email, password, refresh_token FROM accounts WHERE platform=\'outlook\' AND (token IS NULL OR token=\'\') ",
      []
    );
    if (rows.length === 0) {
      res.json({ success: true, results: [], msg: "没有需要授权的账号" });
      return;
    }
    const results: Array<{ id: number; email: string; ok: boolean; needsDeviceFlow?: boolean; error?: string }> = [];
    for (const acc of rows) {
      // 优先用 refresh_token 刷新
      const acctProxy = await resolveAccountProxy(acc.id);
      if (acc.refresh_token) {
        try {
          const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token",
              client_id: OAUTH_CLIENT_ID,
              refresh_token: acc.refresh_token,
              scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
            }).toString(),
          }, acctProxy);
          const td = await r.json() as { access_token?: string; refresh_token?: string; error_description?: string };
          if (td.access_token) {
            await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
              [td.access_token, td.refresh_token ?? acc.refresh_token, acc.id]);
            results.push({ id: acc.id, email: acc.email, ok: true });
            continue;
          }
          results.push({ id: acc.id, email: acc.email, ok: false, needsDeviceFlow: true, error: `refresh_token 已失效：${td.error_description ?? "需重新授权"}` });
        } catch (e) {
          results.push({ id: acc.id, email: acc.email, ok: false, error: String(e) });
        }
      } else {
        // 无 refresh_token：需要设备码授权
        results.push({ id: acc.id, email: acc.email, ok: false, needsDeviceFlow: true,
          error: "无 OAuth token，请使用设备码授权（点击账号旁「设备码」按钮）" });
      }
    }
    const ok = results.filter(r => r.ok).length;
    res.json({ success: true, results, total: rows.length, authorized: ok, failed: rows.length - ok });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 按账号ID拉取邮件（自动刷新token）──────────────────────────────────────
// 供邮件中心使用，前端只传账号ID，token管理完全在后端
const DEFAULT_CLIENT_ID = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";


function splitAccountTags(tags: string | null | undefined): string[] {
  return Array.from(new Set((tags ?? "").split(",").map(t => t.trim()).filter(Boolean)));
}

async function addAccountTags(accountId: number, tagsToAdd: string[], status?: string): Promise<void> {
  const { query, execute } = await import("../db.js");
  const rows = await query<{ tags: string | null }>("SELECT tags FROM accounts WHERE id=$1", [accountId]);
  const merged = Array.from(new Set([...splitAccountTags(rows[0]?.tags), ...tagsToAdd.map(t => t.trim()).filter(Boolean)])).join(",");
  if (status) {
    await execute("UPDATE accounts SET tags=$1, status=$2, updated_at=NOW() WHERE id=$3", [merged || null, status, accountId]);
  } else {
    await execute("UPDATE accounts SET tags=$1, updated_at=NOW() WHERE id=$2", [merged || null, accountId]);
  }
}

type GraphMailMessage = {
  id: string;
  subject?: string;
  from?: { emailAddress?: { name?: string; address?: string } };
  receivedDateTime?: string;
  bodyPreview?: string;
  isRead?: boolean;
  body?: { content?: string; contentType?: string };
  parentFolderId?: string;
};

function mapGraphMessages(value: GraphMailMessage[] | undefined) {
  return (value ?? []).map((m) => ({
    id: m.id,
    subject: m.subject || "(无主题)",
    from: m.from?.emailAddress?.address ?? "",
    fromName: m.from?.emailAddress?.name ?? "",
    receivedAt: m.receivedDateTime ?? new Date().toISOString(),
    preview: m.bodyPreview ?? "",
    body: m.body?.content ?? "",
    bodyType: m.body?.contentType ?? "text",
    isRead: !!m.isRead,
    folderId: m.parentFolderId ?? "",
  }));
}

async function fetchGraphMessages(accessToken: string, mailFolder: string, limit: number, search: string | undefined, global = false, proxy?: string) {
  const select = "id,subject,from,receivedDateTime,bodyPreview,isRead,body,parentFolderId";
  const q = (search ?? "").replace(/["\\]/g, " ").trim();
  let url = global
    ? `https://graph.microsoft.com/v1.0/me/messages?$top=${limit}&$select=${select}`
    : `https://graph.microsoft.com/v1.0/me/mailFolders/${mailFolder}/messages?$top=${limit}&$select=${select}`;
  if (q) {
    url += `&$search="${encodeURIComponent(q)}"`;
  } else {
    url += "&$orderby=receivedDateTime desc";
  }
  const r = await microsoftFetch(url, { headers: { Authorization: `Bearer ${accessToken}`, ConsistencyLevel: "eventual" } }, proxy);
  const data = await r.json() as { value?: GraphMailMessage[]; error?: { message?: string; code?: string } };
  return { ok: r.ok, status: r.status, error: data.error, messages: r.ok ? mapGraphMessages(data.value) : [] };
}

// ── IMAP 辅助：spawn python3 outlook_imap.py ─────────────────────────────
// 优先 XOAUTH2（access_token）→ Basic Auth 备用
async function fetchViaImap(
  email: string, password: string, folder: string, limit: number, search: string,
  accessToken?: string, proxy?: string
): Promise<{ success: boolean; messages?: unknown[]; error?: string; via?: string }> {
  const { spawn } = await import("child_process");
  const scriptPath = new URL("../outlook_imap.py", import.meta.url).pathname;

  // 文件夹名称映射
  const folderMap: Record<string, string> = {
    inbox: "INBOX", sentItems: "Sent", junkemail: "Junk",
    drafts: "Drafts", deleteditems: "Deleted Items", archive: "Archive",
  };
  const imapFolder = folderMap[folder] ?? "INBOX";

  return new Promise((resolve) => {
    const paramObj: Record<string, unknown> = {
      email, password, limit, folder: imapFolder, search: search || ""
    };
    if (accessToken) paramObj["access_token"] = accessToken;
    if (proxy) paramObj["proxy"] = proxy;
    const params = JSON.stringify(paramObj);
    const child = spawn(process.env.PYTHON_BIN || "/usr/bin/python3", [scriptPath, params], { env: { ...process.env, PYTHONUNBUFFERED: "1" } });
    let out = "";
    child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    child.on("close", () => {
      try {
        const raw = JSON.parse(out.trim()) as {
          success: boolean;
          messages?: Array<{
            subject: string; from: string; date: string;
            preview: string; urls: string[]; verify_urls: string[];
            is_read: boolean; body_html?: string; body_plain?: string;
          }>;
          error?: string;
        };
        if (!raw.success) { resolve({ success: false, error: raw.error ?? "IMAP 失败" }); return; }
        const messages = (raw.messages ?? []).map((m, i) => ({
          id: `imap-${i}-${Date.now()}`,
          subject: m.subject || "(无主题)",
          from: m.from?.replace(/^.*<(.+)>.*$/, "$1") ?? m.from ?? "",
          fromName: m.from?.replace(/^(.*?)\s*<.+>$/, "$1").trim() ?? "",
          receivedAt: m.date ? new Date(m.date).toISOString() : new Date().toISOString(),
          preview: m.preview,
          body: m.body_html || m.body_plain || m.preview,
          bodyType: m.body_html ? "html" : "text",
          isRead: m.is_read,
          verifyUrls: m.verify_urls,
        }));
        resolve({ success: true, messages, via: "imap" });
      } catch {
        resolve({ success: false, error: `IMAP 解析失败: ${out.slice(0, 200)}` });
      }
    });
    child.on("error", (e) => resolve({ success: false, error: `IMAP 进程启动失败: ${e.message}` }));
    setTimeout(() => { child.kill(); resolve({ success: false, error: "IMAP 超时（30s）" }); }, 30000);
  });
}


// ── 批量 ROPC 验证 + 自动删除风控账号 ─────────────────────────────────────────
// 删除条件：AADSTS50034(不存在) | AADSTS50126(密码错) | AADSTS53003(CA封禁)
// 保留条件：need_mfa | imap_disabled | connection_error（账号存在，只是访问受限）
router.post("/tools/outlook/purge-invalid", async (req, res) => {
  const { ids, dry_run: dryRunSnake, dryRun: dryRunCamel } = req.body as { ids?: number[]; dry_run?: boolean; dryRun?: boolean };
  const dry_run = dryRunSnake ?? dryRunCamel ?? false;
  try {
    const { query: dbQ, execute: dbE } = await import("../db.js");

    const rows = await dbQ<{ id: number; email: string; password: string | null; refresh_token: string | null; token: string | null; status: string | null }>(
      ids?.length
        ? "SELECT id, email, password, refresh_token, token, status FROM accounts WHERE platform='outlook' AND id = ANY($1::int[])"
        : "SELECT id, email, password, refresh_token, token, status FROM accounts WHERE platform='outlook'",
      ids?.length ? [ids] : []
    );

    const purged: Array<{ id: number; email: string; reason: string }> = [];
    const kept:   Array<{ id: number; email: string; reason: string }> = [];
    const valid:  Array<{ id: number; email: string }> = [];

    // ── 多重确认辅助：Graph /me ────────────────────────────────────────────
    const checkGraphToken = async (token: string): Promise<boolean> => {
      try {
        const gr = await microsoftFetch("https://graph.microsoft.com/v1.0/me?$select=mail", {
          headers: { Authorization: "Bearer " + token },
        });
        return gr.ok;
      } catch { return false; }
    };

    // ── 多重确认辅助：IMAP 密码验证 ──────────────────────────────────────
    const checkImapPassword = async (email: string, password: string): Promise<"wrong" | "ok" | "imap_disabled" | "unknown"> => {
      try {
        const result = await fetchViaImap(email, password, "INBOX", 1, "");
        if (result.success) return "ok";
        const errMsg = String((result as { error?: string }).error ?? "").toLowerCase();
        if (errMsg.includes("invalid credentials") || errMsg.includes("authentication failed") || errMsg.includes("incorrect password")) return "wrong";
        if (errMsg.includes("disabled") || errMsg.includes("not enabled") || errMsg.includes("access denied")) return "imap_disabled";
        return "unknown";
      } catch { return "unknown"; }
    };

    for (const acc of rows) {
      // ── 第一轮：有 refresh_token → 尝试刷新 ──────────────────────────────
      if (acc.refresh_token) {
        const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({
            grant_type: "refresh_token",
            client_id: OAUTH_CLIENT_ID,
            refresh_token: acc.refresh_token,
            scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
          }).toString(),
        });
        const td = await r.json() as { access_token?: string; refresh_token?: string; error?: string; error_description?: string };

        if (td.access_token) {
          if (!dry_run) {
            await dbE("UPDATE accounts SET token=$1, refresh_token=$2, status='active', updated_at=NOW() WHERE id=$3",
              [td.access_token, td.refresh_token ?? acc.refresh_token, acc.id]);
          }
          valid.push({ id: acc.id, email: acc.email });
          continue;
        }

        const err  = (td.error ?? "unknown").toLowerCase();
        const desc = td.error_description ?? "";
        const tokenRevoked = err === "invalid_grant"
          || desc.includes("AADSTS70000")
          || desc.includes("AADSTS70008")
          || desc.includes("AADSTS700082")
          || desc.includes("AADSTS50173");

        if (!tokenRevoked) {
          // 非吊销错误（网络超时等）→ 保留
          if (!dry_run) await dbE("UPDATE accounts SET status='error', updated_at=NOW() WHERE id=$1", [acc.id]);
          kept.push({ id: acc.id, email: acc.email, reason: "refresh_error:" + err });
          continue;
        }

        // ── 第二轮：refresh invalid_grant → 尝试现有 access_token ──────────
        if (acc.token) {
          const tokenOk = await checkGraphToken(acc.token);
          if (tokenOk) {
            if (!dry_run) await dbE("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [acc.id]);
            valid.push({ id: acc.id, email: acc.email });
            continue;
          }
        }

        // ── 第三轮：所有 token 失效 → IMAP 密码验证 ──────────────────────────
        if (acc.password) {
          const imapResult = await checkImapPassword(acc.email, acc.password);
          if (imapResult === "ok") {
            if (!dry_run) await dbE("UPDATE accounts SET status='needs_oauth', updated_at=NOW() WHERE id=$1", [acc.id]);
            kept.push({ id: acc.id, email: acc.email, reason: "password_ok_token_expired" });
            continue;
          }
          if (imapResult === "imap_disabled") {
            if (!dry_run) await dbE("UPDATE accounts SET status='needs_oauth', updated_at=NOW() WHERE id=$1", [acc.id]);
            kept.push({ id: acc.id, email: acc.email, reason: "imap_disabled_needs_oauth" });
            continue;
          }
          if (imapResult === "wrong") {
            // 三轮全部确认失败 → 删除
            if (!dry_run) await dbE("DELETE FROM accounts WHERE id=$1", [acc.id]);
            purged.push({ id: acc.id, email: acc.email, reason: "all_failed:token_revoked+wrong_password" });
            continue;
          }
          // IMAP 未知错误 → 保守保留
          if (!dry_run) await dbE("UPDATE accounts SET status='error', updated_at=NOW() WHERE id=$1", [acc.id]);
          kept.push({ id: acc.id, email: acc.email, reason: "imap_unknown_keep" });
          continue;
        }

        // 无密码 + refresh 吊销 → 无法恢复 → 删除
        if (!dry_run) await dbE("DELETE FROM accounts WHERE id=$1", [acc.id]);
        purged.push({ id: acc.id, email: acc.email, reason: "token_revoked_no_password" });
        continue;
      }

      // ── 无 refresh_token：检查现有 access_token ───────────────────────────
      if (acc.token) {
        const tokenOk = await checkGraphToken(acc.token);
        if (tokenOk) {
          valid.push({ id: acc.id, email: acc.email });
          continue;
        }
      }

      // ── 无任何 token：按密码/状态分类 ────────────────────────────────────
      if (acc.status === "wrong_password" && acc.password) {
        // 第二轮确认：IMAP 再验一次，防误判
        const imapResult = await checkImapPassword(acc.email, acc.password);
        if (imapResult === "ok" || imapResult === "imap_disabled") {
          if (!dry_run) await dbE("UPDATE accounts SET status='needs_oauth', updated_at=NOW() WHERE id=$1", [acc.id]);
          kept.push({ id: acc.id, email: acc.email, reason: "password_recheck_ok" });
          continue;
        }
        if (!dry_run) await dbE("DELETE FROM accounts WHERE id=$1", [acc.id]);
        purged.push({ id: acc.id, email: acc.email, reason: "confirmed_wrong_password" });
        continue;
      }

      if (acc.status === "wrong_password") {
        if (!dry_run) await dbE("DELETE FROM accounts WHERE id=$1", [acc.id]);
        purged.push({ id: acc.id, email: acc.email, reason: "wrong_password_no_creds" });
        continue;
      }

      if (!acc.password) {
        kept.push({ id: acc.id, email: acc.email, reason: "no_credentials" });
        continue;
      }

      // 有密码但无 token → needs_oauth（IMAP 被禁，等待 OAuth 重授权）
      kept.push({ id: acc.id, email: acc.email, reason: "needs_oauth" });
    }

    res.json({
      success: true,
      dry_run,
      total:   rows.length,
      valid:   valid.length,
      purged:  purged.length,
      kept:    kept.length,
      detail:  { valid, purged, kept },
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


router.post("/tools/outlook/fetch-messages-by-id", async (req, res) => {
  const { accountId, folder, top, search } = req.body as {
    accountId?: number; folder?: string; top?: number; search?: string;
  };
  if (!accountId) { res.status(400).json({ success: false, error: "accountId 不能为空" }); return; }

  try {
    const { query, execute } = await import("../db.js");
    const rows = await query<{
      id: number; email: string; password: string | null;
      token: string | null; refresh_token: string | null;
      status: string | null; tags: string | null;
    }>("SELECT id, email, password, token, refresh_token, status, tags FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]);
    const acc = rows[0];
    if (!acc) { res.status(404).json({ success: false, error: "账号不存在" }); return; }
    // 封禁账号直接短路，不浪费时间试 token/Graph/IMAP
    if (acc.status === "suspended" && (acc.tags ?? "").includes("abuse_mode")) {
      res.json({ success: false, error: "账号已被微软封禁（API封禁），无法读取邮件", via: "blocked" });
      return;
    }

    const mailFolder = folder || "inbox";
    const isAllFolder = mailFolder === "all";
    const limit = Math.min(250, Math.max(1, top ?? 50));

    let accessToken = acc.token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);

    // 有 refresh_token → 先用 /common/ 刷新（不直接信任 DB 里可能过期的 token）
    if (acc.refresh_token) {
      const r = await microsoftFetch(`https://login.microsoftonline.com/common/oauth2/v2.0/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          client_id: OAUTH_CLIENT_ID,
          refresh_token: acc.refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string; error_description?: string; error?: string };
      if (td.access_token) {
        accessToken = td.access_token;
        await execute(
          "UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [accessToken, td.refresh_token ?? acc.refresh_token, accountId]
        );
      } else {
        // refresh 失败 → 自动打标签，再降级 IMAP
        const _errCode = (td as { error?: string }).error ?? "";
        const _errDesc = (td as { error_description?: string }).error_description ?? "";
        try {
          const _isAbuse = _errDesc.includes("AADSTS70000") || _errDesc.includes("service abuse");
          if (_isAbuse) {
            await addAccountTags(accountId, ["abuse_mode"], "suspended");
          } else if (_errCode === "invalid_grant") {
            await addAccountTags(accountId, ["token_invalid"], "suspended");
          }
        } catch (_te) { /* tag 失败不中断主流程 */ }
        accessToken = acc.token ?? "";
      }
    }

    // 有 accessToken → Graph API
    if (accessToken) {
      // "全部邮件"：直接走全局 /me/messages，跨所有文件夹（含垃圾邮件、归档等）
      if (isAllFolder) {
        const allRes = await fetchGraphMessages(accessToken, mailFolder, limit, search, true, acctProxy);
        if (allRes.ok) {
          if (allRes.messages.length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
          res.json({ success: true, messages: allRes.messages, count: allRes.messages.length, email: acc.email, via: "graph_all" });
          return;
        }
      }
      const primary = await fetchGraphMessages(accessToken, mailFolder, limit, search, false, acctProxy);
      if (primary.ok) {
        if (primary.messages.length > 0 || mailFolder !== "inbox") {
          if (primary.messages.length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
          res.json({ success: true, messages: primary.messages, count: primary.messages.length, email: acc.email, via: "graph" });
          return;
        }
        // 收件箱为空时再查整个邮箱。历史邮件可能已被微软规则、自动验证或用户操作移动到归档/垃圾/已删除，
        // 只查 mailFolders/inbox 会误显示为“空邮箱”。
        const globalResult = await fetchGraphMessages(accessToken, mailFolder, limit, search, true, acctProxy);
        if (globalResult.ok && globalResult.messages.length > 0) {
          try { await addAccountTags(accountId, ["inbox_verified"]); } catch {}
          res.json({ success: true, messages: globalResult.messages, count: globalResult.messages.length, email: acc.email, via: "graph_all", folderFallback: true });
          return;
        }
        res.json({ success: true, messages: [], count: 0, email: acc.email, via: "graph", folderFallback: globalResult.ok });
        return;
      }
      // Graph API 失败（token 过期等）→ 降级 IMAP
    }

    // ── IMAP 路径（降级）──────────────────────────────────────────────────
    // 优先：XOAUTH2 IMAP（如有 token，与 hrhcode 相同方式）
    // 备用：Basic Auth IMAP（密码，微软已封，仅用于显示实际错误信息）
    if (accessToken) {
      // Graph API 失败但有 token → 尝试 XOAUTH2 IMAP
      const xoauthResult = await fetchViaImap(acc.email, acc.password ?? "", mailFolder, limit, search ?? "", accessToken, acctProxy);
      if (xoauthResult.success) {
        if ((xoauthResult.messages as unknown[]).length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
        res.json({ success: true, messages: xoauthResult.messages, count: (xoauthResult.messages as unknown[]).length, email: acc.email, via: "imap_xoauth2" });
        return;
      }
      // XOAUTH2 失败 → 判断是否为永久封禁账号（suspended+abuse_mode）
      // 永久封禁账号不提示重授权，正常显示错误；其他账号提示 token 失效需重授权
      const _isSuspAbuse = acc.status === "suspended" && (acc.tags ?? "").includes("abuse_mode");
      if (!_isSuspAbuse) {
        res.json({ success: false, error: "账号 OAuth 授权已失效，请重新授权后即可读取邮件", needsAuth: true });
        return;
      }
    }
    if (!acc.password) {
      res.json({ success: false, error: "账号无密码且无 OAuth token，无法读取邮件", needsAuth: true });
      return;
    }
    // 有搜索词但无 OAuth token → Basic Auth 已封，搜索不可用
    if (search && !accessToken) {
      res.json({ success: false, error: "搜索需要 OAuth 授权，请点击「获取授权」完成授权后即可搜索", needsAuth: true });
      return;
    }
    const imapResult = await fetchViaImap(acc.email, acc.password, mailFolder, limit, search ?? "", undefined, acctProxy);
    if (imapResult.success) {
      if ((imapResult.messages as unknown[]).length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
      res.json({ success: true, messages: imapResult.messages, count: (imapResult.messages as unknown[]).length, email: acc.email, via: "imap" });
    } else {
      res.json({ success: false, error: imapResult.error ?? "IMAP 失败", via: "imap" });
    }
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ---------------------------------------------------------------------------
// Auto-retoken: detached spawn + log file, api-server restart won't lose job
// ---------------------------------------------------------------------------

router.post("/tools/outlook/auto-retoken", async (req, res) => {
  try {
    const { allError, headless = true, ids } = req.body as {
      allError?: boolean; headless?: boolean; ids?: number[];
    };
    const { spawn } = await import("child_process");
    const path = await import("path");
    const fs   = await import("fs");

    const jobId   = `retoken_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const logPath = `/tmp/${jobId}.log`;
    const pidPath = `/tmp/${jobId}.pid`;
    const job     = await jobQueue.create(jobId);

    job.logs.push({ type: "log", message: `__meta__ logPath=${logPath}` });
    res.json({ success: true, jobId, message: "retoken 任务已启动" });

    const scriptPath = path.resolve(__dirname, "../outlook_retoken.py");
    const args: string[] = ["--headless", headless ? "true" : "false"];
    if (allError) args.push("--all-error");
    if (ids && ids.length > 0) args.push("--ids", ids.join(","));

    const logFd = fs.openSync(logPath, "w");

    // detached=true: child joins own process group, survives parent restart
    const child = spawn("python3", [scriptPath, ...args], {
      env: { ...process.env },
      stdio: "ignore",
      detached: true,
    });
    if (child.pid) fs.writeFileSync(pidPath, String(child.pid));
    child.unref();

    jobQueue.setChild(jobId, child);

    const pushLog = (type: "log" | "success" | "warn" | "error", msg: string) => {
      job.logs.push({ type, message: msg.slice(0, 500) });
      try { fs.writeSync(logFd, msg + "\n"); } catch {}
    };

    let buf = "";
    child.stdout?.on("data", (d: Buffer) => {
      buf += d.toString();
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      lines.forEach((l) => {
        const t = l.includes("\u2705") ? "success"
          : l.includes("\u274c") || l.includes("\u5931\u8d25") ? "error"
          : l.includes("\u26a0\ufe0f") ? "warn" : "log";
        if (l.trim()) pushLog(t, l);
      });
    });
    child.stderr?.on("data", (d: Buffer) => {
      d.toString().split("\n").filter(Boolean).forEach((l) => pushLog("warn", l));
    });
    child.on("close", async (code) => {
      if (buf.trim()) pushLog("log", buf);
      try { fs.closeSync(logFd); } catch {}
      try { fs.unlinkSync(pidPath); } catch {}
      await jobQueue.finish(jobId, code ?? -1, code === 0 ? "done" : "failed");
    });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/outlook/auto-retoken/:jobId", async (req, res) => {
  try {
    const job = await jobQueue.get(req.params.jobId);
    if (job) {
      res.json({ success: true, jobId: job.jobId, status: job.status, logs: job.logs, exitCode: job.exitCode });
      return;
    }
    // api-server restarted: recover from log file
    const fs      = await import("fs");
    const jobId   = req.params.jobId;
    const logPath = `/tmp/${jobId}.log`;
    const pidPath = `/tmp/${jobId}.pid`;
    if (!fs.existsSync(logPath)) {
      res.status(404).json({ success: false, error: "任务不存在" });
      return;
    }
    const content = fs.readFileSync(logPath, "utf-8");
    const logs = content.split("\n").filter(Boolean).map((l: string) => ({
      type: l.includes("\u2705") ? "success"
        : l.includes("\u274c") || l.includes("\u5931\u8d25") ? "error"
        : l.includes("\u26a0\ufe0f") ? "warn" : "log",
      message: l,
    }));
    let status = "done";
    if (fs.existsSync(pidPath)) {
      const pid = parseInt(fs.readFileSync(pidPath, "utf-8").trim(), 10);
      try { process.kill(pid, 0); status = "running"; } catch { status = "done"; }
    }
    res.json({ success: true, jobId, status, logs, exitCode: null, recovered: true });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.delete("/tools/outlook/auto-retoken/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  res.json({ success: stopped, message: stopped ? "已停止" : "任务不存在或已结束" });
});

// ─────────────────────────────────────────────────────────────────────────────
// Mark email as read / unread via Graph API
// ─────────────────────────────────────────────────────────────────────────────

router.patch("/tools/outlook/message/:accountId/:messageId/read", async (req, res) => {
  try {
    const { accountId: rawId, messageId } = req.params;
    const { isRead = true } = req.body as { isRead?: boolean };
    const accountId = parseInt(rawId, 10);
    const { query, execute } = await import("../db.js");

    const rows = await query<{ token: string | null; refresh_token: string | null; exit_ip: string | null }>(
      "SELECT token, refresh_token, exit_ip FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]
    );
    if (!rows[0]) { res.status(404).json({ success: false, error: "账号不存在" }); return; }

    let token = rows[0].token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);
    if (rows[0].refresh_token) {
      const r = await microsoftFetch(`https://login.microsoftonline.com/common/oauth2/v2.0/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
          refresh_token: rows[0].refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string };
      if (td.access_token) {
        token = td.access_token;
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [token, td.refresh_token ?? rows[0].refresh_token, accountId]);
      }
    }
    if (!token) { res.status(400).json({ success: false, error: "无可用 token" }); return; }

    const gr = await microsoftFetch(`https://graph.microsoft.com/v1.0/me/messages/${encodeURIComponent(messageId)}`, {
      method: "PATCH",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ isRead }),
    }, acctProxy);
    if (!gr.ok) {
      const err = await gr.json() as { error?: { message?: string } };
      res.status(gr.status).json({ success: false, error: err?.error?.message ?? "Graph API 失败" });
      return;
    }
    res.json({ success: true, messageId, isRead });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Move email to a folder via Graph API
// ─────────────────────────────────────────────────────────────────────────────

router.post("/tools/outlook/message/:accountId/:messageId/move", async (req, res) => {
  try {
    const { accountId: rawId, messageId } = req.params;
    const { destinationId } = req.body as { destinationId: string };
    const accountId = parseInt(rawId, 10);
    if (!destinationId) { res.status(400).json({ success: false, error: "destinationId 必填 (e.g. inbox, deleteditems, junkemail, archive)" }); return; }
    const { query, execute } = await import("../db.js");

    const rows = await query<{ token: string | null; refresh_token: string | null; exit_ip: string | null }>(
      "SELECT token, refresh_token, exit_ip FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]
    );
    if (!rows[0]) { res.status(404).json({ success: false, error: "账号不存在" }); return; }

    let token = rows[0].token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);
    if (rows[0].refresh_token) {
      const r = await microsoftFetch(`https://login.microsoftonline.com/common/oauth2/v2.0/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
          refresh_token: rows[0].refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string };
      if (td.access_token) {
        token = td.access_token;
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [token, td.refresh_token ?? rows[0].refresh_token, accountId]);
      }
    }
    if (!token) { res.status(400).json({ success: false, error: "无可用 token" }); return; }

    const gr = await microsoftFetch(`https://graph.microsoft.com/v1.0/me/messages/${messageId}/move`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ destinationId }),
    }, acctProxy);
    if (!gr.ok) {
      const err = await gr.json() as { error?: { message?: string } };
      res.status(gr.status).json({ success: false, error: err?.error?.message ?? "Graph API 失败" });
      return;
    }
    const moved = await gr.json() as { id: string };
    res.json({ success: true, newMessageId: moved.id, destinationId });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Delete email via Graph API
// ─────────────────────────────────────────────────────────────────────────────

router.delete("/tools/outlook/message/:accountId/:messageId", async (req, res) => {
  try {
    const { accountId: rawId, messageId } = req.params;
    const accountId = parseInt(rawId, 10);
    const { query, execute } = await import("../db.js");

    const rows = await query<{ token: string | null; refresh_token: string | null; exit_ip: string | null }>(
      "SELECT token, refresh_token, exit_ip FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]
    );
    if (!rows[0]) { res.status(404).json({ success: false, error: "账号不存在" }); return; }

    let token = rows[0].token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);
    if (rows[0].refresh_token) {
      const r = await microsoftFetch(`https://login.microsoftonline.com/common/oauth2/v2.0/token`, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
          refresh_token: rows[0].refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string };
      if (td.access_token) {
        token = td.access_token;
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [token, td.refresh_token ?? rows[0].refresh_token, accountId]);
      }
    }
    if (!token) { res.status(400).json({ success: false, error: "无可用 token" }); return; }

    const gr = await microsoftFetch(`https://graph.microsoft.com/v1.0/me/messages/${messageId}`, {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
    }, acctProxy);
    if (gr.status === 204) {
      res.json({ success: true, messageId, deleted: true });
    } else {
      const err = await gr.json() as { error?: { message?: string } };
      res.status(gr.status).json({ success: false, error: err?.error?.message ?? "Graph API 失败" });
    }
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});



// ─────────────────────────────────────────────────────────────────────────────
// Send email via Graph API /me/sendMail
// POST /tools/outlook/send-message  { accountId, to, subject, body?, bodyType? }
// ─────────────────────────────────────────────────────────────────────────────
router.post("/tools/outlook/send-message", async (req, res) => {
  const { accountId, to, subject, body = "", bodyType = "Text" } = req.body as {
    accountId: number; to: string | string[]; subject: string;
    body?: string; bodyType?: "Text" | "HTML";
  };
  if (!accountId || !to || !subject) {
    res.status(400).json({ success: false, error: "accountId / to / subject 必填" });
    return;
  }
  const recipients = (Array.isArray(to) ? to : [to])
    .map((addr: string) => ({ emailAddress: { address: addr.trim() } }));
  try {
    const { query, execute } = await import("../db.js");
    const rows = await query<{ token: string | null; refresh_token: string | null; exit_ip: string | null }>(
      "SELECT token, refresh_token, exit_ip FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]
    );
    if (!rows[0]) { res.status(404).json({ success: false, error: "账号不存在" }); return; }
    let token = rows[0].token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);
    if (rows[0].refresh_token) {
      const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
          refresh_token: rows[0].refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string };
      if (td.access_token) {
        token = td.access_token;
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [token, td.refresh_token ?? rows[0].refresh_token, accountId]);
      }
    }
    if (!token) { res.status(400).json({ success: false, error: "无可用 token，请先授权" }); return; }
    const gr = await microsoftFetch("https://graph.microsoft.com/v1.0/me/sendMail", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({
        message: {
          subject,
          body: { contentType: bodyType === "HTML" ? "HTML" : "Text", content: body },
          toRecipients: recipients,
        },
        saveToSentItems: true,
      }),
    }, acctProxy);
    if (gr.status === 202 || gr.status === 200 || gr.status === 204) {
      res.json({ success: true, to: recipients.map((r: { emailAddress: { address: string } }) => r.emailAddress.address) });
      return;
    }
    const err = await gr.json() as { error?: { message?: string; code?: string } };
    res.status(gr.status).json({ success: false, error: err?.error?.message ?? "Graph sendMail 失败", code: err?.error?.code });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 一键点击邮件中的验证链接（Graph API 拉取正文 + patchright 访问）──────────
router.post("/tools/outlook/click-verify-link", async (req, res) => {
  const { accountId, messageId, verifyUrl } = req.body as {
    accountId: number; messageId?: string; verifyUrl?: string;
  };
  if (!accountId) { res.status(400).json({ success: false, error: "缺少 accountId" }); return; }

  try {
    const { query: dbQ } = await import("../db.js");
    const rows = await dbQ<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
      "SELECT id, email, token, refresh_token FROM accounts WHERE id=$1 AND platform='outlook'",
      [accountId]
    );
    if (!rows.length) { res.status(404).json({ success: false, error: "账号不存在" }); return; }
    const acc = rows[0];

    // 刷新 token
    let accessToken = acc.token || "";
    const acctProxy = await resolveAccountProxy(acc.id);
    if (acc.refresh_token) {
      const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token",
          client_id: OAUTH_CLIENT_ID,
          refresh_token: acc.refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
        }).toString(),
      }, acctProxy);
      const td = await tr.json() as { access_token?: string; refresh_token?: string };
      if (tr.ok && td.access_token) {
        accessToken = td.access_token;
        const { execute: dbE } = await import("../db.js");
        await dbE(
          "UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [accessToken, td.refresh_token ?? acc.refresh_token, acc.id]
        );
      }
    }
    if (!accessToken) { res.status(400).json({ success: false, error: "无法获取 access token" }); return; }

    // 调用 Python 脚本（提取链接 + patchright 访问）
    const scriptPath = path.resolve(__dirname, "../click_verify_link.py");
    const params = JSON.stringify({ token: accessToken, message_id: messageId ?? "", verify_url: verifyUrl ?? "" });
    const { spawn } = await import("child_process");
    const result = await new Promise<{ success: boolean; verify_url?: string; final_url?: string; title?: string; error?: string }>((resolve) => {
      const child = spawn(process.env.PYTHON_BIN || "/usr/bin/python3", [scriptPath, params], { env: { ...process.env, ...getMicrosoftProxyEnv(), PYTHONUNBUFFERED: "1" } });
      let out = "";
      child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
      child.stderr.on("data", (d: Buffer) => { process.stderr.write(d); });
      child.on("close", () => {
        const lines = out.trim().split("\n");
        const last = lines.at(-1) ?? "";
        try { resolve(JSON.parse(last)); }
        catch { resolve({ success: false, error: last.slice(0, 200) }); }
      });
      child.on("error", (e) => resolve({ success: false, error: e.message }));
      setTimeout(() => { child.kill(); resolve({ success: false, error: "超时（45s）" }); }, 45000);
    });

    res.json({ ...result, email: acc.email });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── 批量扫描所有账号收件箱，自动点击待处理验证链接 ────────────────────────
router.post("/tools/outlook/auto-verify-emails", async (req, res) => {
  const { accountIds, subjectFilter = "verify" } = req.body as { accountIds?: number[]; subjectFilter?: string };
  try {
    const { query: dbQ } = await import("../db.js");
    const rows = await dbQ<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
      accountIds?.length
        ? "SELECT id, email, token, refresh_token FROM accounts WHERE platform='outlook' AND id = ANY($1::int[])"
        : "SELECT id, email, token, refresh_token FROM accounts WHERE platform='outlook' AND status='active' AND (token IS NOT NULL OR refresh_token IS NOT NULL) AND COALESCE(tags,'') NOT LIKE '%replit_used%' AND COALESCE(tags,'') NOT LIKE '%token_invalid%' AND COALESCE(tags,'') NOT LIKE '%inbox_error%' AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'",
      accountIds?.length ? [accountIds] : []
    );
    const results: Array<{ accountId: number; email: string; status: string; error?: string; verifyUrl?: string }> = [];

    for (const acc of rows) {
      try {
        let accessToken = acc.token || "";
        const acctProxy = await resolveAccountProxy(acc.id);
        if (acc.refresh_token) {
          const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token",
              client_id: OAUTH_CLIENT_ID,
              refresh_token: acc.refresh_token,
              scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read offline_access",
            }).toString(),
          }, acctProxy);
          const td = await tr.json() as { access_token?: string; refresh_token?: string };
          if (tr.ok && td.access_token) accessToken = td.access_token;
        }
        if (!accessToken) { results.push({ accountId: acc.id, email: acc.email, status: "skip", error: "无 token" }); continue; }

        // 搜索匹配主题的未读邮件
        const searchUrl = `https://graph.microsoft.com/v1.0/me/messages?$search="subject:${subjectFilter}"&$select=id,subject,isRead&$top=50`;
        const gr = await microsoftFetch(searchUrl, { headers: { Authorization: `Bearer ${accessToken}` } }, acctProxy);
        if (!gr.ok) { results.push({ accountId: acc.id, email: acc.email, status: "skip", error: "Graph API 失败" }); continue; }
        const gd = await gr.json() as { value?: Array<{ id: string; subject: string; isRead: boolean }> };
        const msgs = (gd.value ?? []).filter(m => m.subject.toLowerCase().includes(subjectFilter.toLowerCase()));
        if (!msgs.length) { results.push({ accountId: acc.id, email: acc.email, status: "none" }); continue; }

        // 对每封匹配邮件执行点击验证
        for (const msg of msgs) {
          const scriptPath = path.resolve(__dirname, "../click_verify_link.py");
          const params = JSON.stringify({ token: accessToken, message_id: msg.id, verify_url: "" });
          const { spawn } = await import("child_process");
          const clickResult = await new Promise<{ success: boolean; verify_url?: string; error?: string }>((resolve) => {
            const child = spawn(process.env.PYTHON_BIN || "/usr/bin/python3", [scriptPath, params], { env: { ...process.env, ...getMicrosoftProxyEnv(), PYTHONUNBUFFERED: "1" } });
            let out = "";
            child.stdout.on("data", (d: Buffer) => { out += d.toString(); });
            child.on("close", () => {
              const last = out.trim().split("\n").at(-1) ?? "";
              try { resolve(JSON.parse(last)); } catch { resolve({ success: false, error: last.slice(0, 100) }); }
            });
            child.on("error", (e) => resolve({ success: false, error: e.message }));
            setTimeout(() => { child.kill(); resolve({ success: false, error: "timeout" }); }, 45000);
          });
          results.push({
            accountId: acc.id, email: acc.email,
            status: clickResult.success ? "clicked" : "failed",
            error: clickResult.error,
            verifyUrl: clickResult.verify_url,
          });
        }
      } catch (e) {
        results.push({ accountId: acc.id, email: acc.email, status: "error", error: String(e) });
      }
    }

    const clicked = results.filter(r => r.status === "clicked").length;
    res.json({ success: true, total: rows.length, clicked, results });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ── 实时验证轮询控制 ─────────────────────────────────────────────────────

// ── 手动删除 Outlook 账号（从 DB 中彻底移除）─────────────────────────────
router.delete("/tools/outlook/account/:id", async (req, res) => {
  const accountId = parseInt(req.params["id"] ?? "", 10);
  if (!accountId) { res.status(400).json({ success: false, error: "无效 id" }); return; }
  try {
    const { execute } = await import("../db.js");
    await execute("DELETE FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

// ── 按主题关键词批量删除邮件（Graph API）────────────────────────────────────
router.post("/tools/outlook/account/:id/batch-delete-by-subject", async (req, res) => {
  const accountId = parseInt(req.params["id"] ?? "", 10);
  const { keyword } = req.body as { keyword?: string };
  if (!accountId || !keyword?.trim()) {
    res.status(400).json({ success: false, error: "accountId 和 keyword 不能为空" }); return;
  }
  try {
    const { query, execute } = await import("../db.js");
    const rows = await query<{ token: string | null; refresh_token: string | null }>(
      "SELECT token, refresh_token FROM accounts WHERE id=$1 AND platform='outlook'", [accountId]
    );
    if (!rows[0]) { res.status(404).json({ success: false, error: "账号不存在" }); return; }

    let token = rows[0].token ?? "";
    const acctProxy = await resolveAccountProxy(accountId);
    if (rows[0].refresh_token) {
      const r = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
          refresh_token: rows[0].refresh_token,
          scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite offline_access",
        }).toString(),
      }, acctProxy);
      const td = await r.json() as { access_token?: string; refresh_token?: string };
      if (td.access_token) {
        token = td.access_token;
        await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
          [token, td.refresh_token ?? rows[0].refresh_token, accountId]);
      }
    }
    if (!token) { res.status(400).json({ success: false, error: "无可用 token，请先授权" }); return; }

    // 搜索包含关键词的邮件（搜索所有文件夹）
    const kw = keyword.trim();
    const searchUrl = `https://graph.microsoft.com/v1.0/me/messages?$search="subject:${kw}"&$select=id,subject&$top=50`;
    const gr = await microsoftFetch(searchUrl, { headers: { Authorization: `Bearer ${token}` } }, acctProxy);
    if (!gr.ok) {
      const gd = await gr.json() as { error?: { message?: string } };
      res.status(400).json({ success: false, error: gd.error?.message ?? "Graph API 错误" }); return;
    }
    const gd = await gr.json() as { value?: Array<{ id: string; subject: string }> };
    const msgs = (gd.value ?? []).filter(m => m.subject.toLowerCase().includes(kw.toLowerCase()));

    let deleted = 0;
    for (const msg of msgs) {
      const dr = await microsoftFetch(`https://graph.microsoft.com/v1.0/me/messages/${msg.id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      }, acctProxy);
      if (dr.ok || dr.status === 204) deleted++;
    }
    res.json({ success: true, deleted, total: msgs.length });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
});

router.get("/tools/outlook/live-verify/status", (_req, res) => {
  res.json({ success: true, ...getLiveVerifyStatus() });
});

router.post("/tools/outlook/live-verify/toggle", (req, res) => {
  const { enabled } = req.body as { enabled: boolean };
  setLiveVerifyEnabled(!!enabled);
  res.json({ success: true, ...getLiveVerifyStatus() });
});


// ═══════════════════════════════════════════════════════════════════════════
// sub2api 子节点管理：同步 Outlook 账号到 sub2api upstream_accounts
// 连接使用 sub2api PostgreSQL DB（独立于 toolkit DB）
// ═══════════════════════════════════════════════════════════════════════════

const SUB2API_DB_CONFIG = {
  host: "127.0.0.1",
  port: 5432,
  user: "postgres",
  password: "postgres",
  database: "sub2api",
};
const OAUTH_CLIENT_ID_FOR_SUB2API = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";

async function getSub2apiPool() {
  const { Pool } = await import("pg");
  return new Pool({ ...SUB2API_DB_CONFIG, max: 5 });
}

// ── GET /tools/sub2api/list ────────────────────────────────────────────────
router.get("/tools/sub2api/list", async (_req, res) => {
  let pool: import("pg").Pool | null = null;
  try {
    pool = await getSub2apiPool();
    const result = await pool.query(
      `SELECT id, name, platform, type,
              LEFT(credentials::text, 80) AS creds_preview,
              status, concurrency, priority,
              last_used_at, created_at, error_message
       FROM accounts
       WHERE deleted_at IS NULL
       ORDER BY created_at DESC
       LIMIT 200`
    );
    res.json({ success: true, total: result.rows.length, accounts: result.rows });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  } finally {
    await pool?.end();
  }
});

// ── POST /tools/sub2api/sync ───────────────────────────────────────────────
// 把 toolkit accounts 表中有 refresh_token 的 Outlook 账号推送为 sub2api 子节点
router.post("/tools/sub2api/sync", async (req, res) => {
  const { dryRun = false, concurrency = 3, priority = 50 } = req.body as {
    dryRun?: boolean; concurrency?: number; priority?: number;
  };
  let sub2apiPool: import("pg").Pool | null = null;
  try {
    const { query: tkQ } = await import("../db.js");
    // 读取所有有 refresh_token 的 Outlook 账号
    const accounts = await tkQ<{ id: number; email: string; token: string | null; refresh_token: string | null }>(
      "SELECT id, email, token, refresh_token FROM accounts WHERE (platform='cursor' AND token IS NOT NULL AND token != '') OR (platform='outlook' AND refresh_token IS NOT NULL AND refresh_token != '') "
    );
    if (!accounts.length) {
      res.json({ success: true, pushed: 0, skipped: 0, message: "没有可用的 refresh_token 账号" });
      return;
    }

    if (dryRun) {
      res.json({ success: true, dryRun: true, total: accounts.length, accounts: accounts.map(a => a.email) });
      return;
    }

    sub2apiPool = await getSub2apiPool();

    let pushed = 0, skipped = 0;
    const details: { email: string; action: string; error?: string }[] = [];

    for (const acc of accounts) {
      try {
        // 检查是否已存在（按 name = email 匹配）
        const exists = await sub2apiPool.query(
          "SELECT id FROM accounts WHERE name=$1 AND deleted_at IS NULL LIMIT 1",
          [acc.email]
        );
        if (exists.rows.length > 0) {
          // 更新 refresh_token（token 可能轮换）
          await sub2apiPool.query(
            "UPDATE accounts SET credentials=credentials || $1::jsonb, updated_at=NOW() WHERE name=$2 AND deleted_at IS NULL",
            [JSON.stringify({ refresh_token: acc.refresh_token, client_id: OAUTH_CLIENT_ID_FOR_SUB2API }), acc.email]
          );
          skipped++;
          details.push({ email: acc.email, action: "updated" });
        } else {
          // 新增
          const insertRes = await sub2apiPool.query(
            `INSERT INTO accounts (name, platform, type, credentials, extra, concurrency, priority, status, schedulable, auto_pause_on_expired, rate_multiplier, created_at, updated_at)
             VALUES ($1, 'cursor', 'oauth', $2::jsonb, '{}', $3, $4, 'active', true, true, 1.0, NOW(), NOW())
             RETURNING id`,
            [
              acc.email,
              acc.token ? JSON.stringify({ session_token: acc.token }) : JSON.stringify({ refresh_token: acc.refresh_token, client_id: OAUTH_CLIENT_ID_FOR_SUB2API }),
              concurrency,
              priority,
            ]
          );
          // 自动关联到 cursor-default group
          const newId = insertRes.rows[0]?.id;
          if (newId) {
            await sub2apiPool.query(
              'INSERT INTO account_groups (account_id, group_id, priority, created_at) VALUES ($1, 8, 50, NOW()) ON CONFLICT DO NOTHING',
              [newId]
            );
          }
          pushed++;
          details.push({ email: acc.email, action: "inserted" });
        }
      } catch (e) {
        details.push({ email: acc.email, action: "error", error: String(e) });
      }
    }

    res.json({ success: true, total: accounts.length, pushed, updated: skipped, details });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  } finally {
    await sub2apiPool?.end();
  }
});

// ── DELETE /tools/sub2api/accounts/:id ────────────────────────────────────
router.delete("/tools/sub2api/accounts/:id", async (req, res) => {
  const id = parseInt(req.params.id, 10);
  if (isNaN(id)) { res.status(400).json({ success: false, error: "无效 id" }); return; }
  let pool: import("pg").Pool | null = null;
  try {
    pool = await getSub2apiPool();
    await pool.query("UPDATE accounts SET deleted_at=NOW() WHERE id=$1", [id]);
    res.json({ success: true, deleted: id });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  } finally {
    await pool?.end();
  }
});

// ── POST /tools/sub2api/enable/:id  /  /tools/sub2api/disable/:id ─────────
router.post("/tools/sub2api/enable/:id", async (req, res) => {
  const id = parseInt(req.params.id, 10);
  let pool: import("pg").Pool | null = null;
  try {
    pool = await getSub2apiPool();
    await pool.query("UPDATE accounts SET status='active', schedulable=true, error_message=NULL, updated_at=NOW() WHERE id=$1", [id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
  finally { await pool?.end(); }
});

router.post("/tools/sub2api/disable/:id", async (req, res) => {
  const id = parseInt(req.params.id, 10);
  let pool: import("pg").Pool | null = null;
  try {
    pool = await getSub2apiPool();
    await pool.query("UPDATE accounts SET status='disabled', schedulable=false, updated_at=NOW() WHERE id=$1", [id]);
    res.json({ success: true });
  } catch (e) { res.status(500).json({ success: false, error: String(e) }); }
  finally { await pool?.end(); }
});


// ── CF IP 池：定时存活检验（每 5 分钟重测一次，移除死链节点）──────────────
(async () => {
  const { spawnSync } = await import("child_process");
  const runRetest = () => {
    try {
      const r = spawnSync("python3", [
        CF_POOL_SCRIPT, "retest",
        "--max-latency", "800",
        "--threads",     "8",
        "--port",        "443",
      ], { timeout: 55000, encoding: "utf8", env: { ...process.env, PYTHONUNBUFFERED: "1" } });
      const result = r.stdout ? JSON.parse(r.stdout) : {};
      if (result.removed > 0 || result.kept !== undefined) {
        console.log(`[cf-pool retest] kept=${result.kept ?? "?"} removed=${result.removed ?? 0}`);
      }
    } catch (e) {
      // 静默：池为空时 retest 快速退出
    }
  };
  // 启动后 30s 首次运行，之后每 5 分钟
  setTimeout(() => {
    runRetest();
    setInterval(runRetest, 5 * 60 * 1000);
  }, 30_000);
})();


// ─────────────────────────────────────────────────────────────────────────────
// Obvious 沙箱控制 (直接bypass链路 — port-49999/Jupyter无认证代码执行)
//
// 技术原理 (2026-05发现):
//   obvious.ai每个Project自带一个e2b Linux沙箱.
//   该沙箱暴露两个公开无认证端点:
//     https://49999-{sandboxId}.e2b.app/execute  — POST执行代码 (FastAPI)
//     https://8888-{sandboxId}.e2b.app/api/kernels — Jupyter WebSocket
//   直接调用 = 完全绕过obvious AI安全过滤器, root权限执行任意代码.
//
// 账号目录结构 /root/obvious-accounts/{label}/:
//   storage_state.json  — 浏览器cookies (better-auth session token)
//   manifest.json       — projectId / threadId / sandboxId / execBase
//
// 核心脚本 (scripts/):
//   obvious_sandbox.py  — ObviousSandbox类 (from_account/execute/shell/add_ssh_key)
//   obvious_client.py   — obvious REST API客户端 (project/thread/wake)
//   obvious_executor.py — 注册执行器 (--register/--health/--exec)
//   obvious_pool.py     — 多账号池 (status/acquire/dispatch_batch)
//   obvious_provision.py — 全自动注册新obvious账号
// ─────────────────────────────────────────────────────────────────────────────

const PYTHON = process.env.PYTHON_BIN || 'python3';
const _OBV_WS = process.cwd().endsWith('/artifacts/api-server')
  ? path.resolve(process.cwd(), '../..')
  : process.cwd();
const OBVIOUS_SCRIPTS_DIR = path.resolve(_OBV_WS, 'scripts');
const OBVIOUS_ACC_DIR = '/root/obvious-accounts';

function _spawnObviousJob(
  jobId: string,
  args: string[],
  extraEnv: Record<string, string> = {}
): void {
  import('child_process').then(({ spawn }) => {
    const child = spawn(PYTHON, args, {
      env: { ...process.env as Record<string, string>, PYTHONUNBUFFERED: '1', ...extraEnv },
    });
    jobQueue.setChild(jobId, child);
    child.stdout?.on('data', (d: Buffer) =>
      d.toString().split('\n').filter(Boolean).forEach((l: string) =>
        jobQueue.pushLog(jobId, { type: 'log', message: l })));
    child.stderr?.on('data', (d: Buffer) =>
      d.toString().split('\n').filter(Boolean).forEach((l: string) =>
        jobQueue.pushLog(jobId, { type: 'log', message: '[err] ' + l })));
    child.on('close', (code: number | null) =>
      jobQueue.finish(jobId, code ?? -1, code === 0 ? 'done' : 'failed'));
  });
}

// GET /tools/obvious/accounts — 账号列表 (含sandboxId/health)
router.get('/tools/obvious/accounts', async (_req, res) => {
  try {
    const fs = await import('fs');
    const indexPath = OBVIOUS_ACC_DIR + '/index.json';
    const accounts = fs.existsSync(indexPath)
      ? JSON.parse(fs.readFileSync(indexPath, 'utf8')) : [];
    const withHealth = accounts.map((a: Record<string, unknown>) => {
      const healthPath = OBVIOUS_ACC_DIR + '/' + String(a.label) + '/health.json';
      let health: Record<string, unknown> = {};
      try { if (fs.existsSync(healthPath)) health = JSON.parse(fs.readFileSync(healthPath, 'utf8')); } catch (_) {}
      return { ...a, alive: health.alive, credits: health.credits,
               tier: health.tier, checkedAt: health.checkedAt };
    });
    res.json({ success: true, accounts: withHealth });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// GET /tools/obvious/status — 池状态
router.get('/tools/obvious/status', async (_req, res) => {
  try {
    const { execFileSync } = await import('child_process');
    const out = execFileSync(PYTHON, [
      path.join(OBVIOUS_SCRIPTS_DIR, 'obvious_pool.py'), 'status',
    ], { encoding: 'utf8', timeout: 30000, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
    res.json({ success: true, output: out });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// GET /tools/obvious/sandbox/health?account=eu-test1 — 沙箱健康检查
router.get('/tools/obvious/sandbox/health', async (req, res) => {
  const account = String(req.query.account || 'eu-test1');
  try {
    const { execFileSync } = await import('child_process');
    const out = execFileSync(PYTHON, [
      path.join(OBVIOUS_SCRIPTS_DIR, 'obvious_executor.py'),
      '--health', '--account', account, '--acc-dir', OBVIOUS_ACC_DIR,
    ], { encoding: 'utf8', timeout: 60000, env: { ...process.env, PYTHONUNBUFFERED: '1' } });
    try { res.json({ success: true, ...JSON.parse(out.trim().split('\n').at(-1) ?? '{}'), raw: out }); }
    catch (_) { res.json({ success: true, raw: out }); }
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// POST /tools/obvious/sandbox/exec — 直接在obvious沙箱执行代码 (port-49999 bypass)
// Body: { account?, code, language? }
router.post('/tools/obvious/sandbox/exec', async (req, res) => {
  const { account = 'eu-test1', code, language = 'python' } =
    req.body as { account?: string; code?: string; language?: string };
  if (!code) { res.status(400).json({ success: false, error: 'code required' }); return; }
  try {
    const created = await jobQueue.create('obvious_exec_' + Date.now());
    const wrapperCode = [
      'import sys, pathlib',
      'sys.path.insert(0, ' + JSON.stringify(OBVIOUS_SCRIPTS_DIR) + ')',
      'from obvious_sandbox import ObviousSandbox',
      'sb = ObviousSandbox.from_account(' + JSON.stringify(account) + ', acc_dir=pathlib.Path(' + JSON.stringify(OBVIOUS_ACC_DIR) + '))',
      'print("[sandbox]", repr(sb), file=sys.stderr)',
      'print(sb.execute(' + JSON.stringify(code) + ', language=' + JSON.stringify(language) + '), end="")',
    ].join('\n');
    _spawnObviousJob(created.jobId, ['-c', wrapperCode]);
    res.json({ success: true, jobId: created.jobId });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// POST /tools/obvious/sandbox/install — pip install packages into the obvious sandbox on demand
// Body: { account?, packages }   packages: string | string[]
// Returns: { success, jobId } — poll /api/tools/obvious/job/:jobId for stdout/status
router.post('/tools/obvious/sandbox/install', async (req, res) => {
  const { account = 'eu-test1', packages } =
    req.body as { account?: string; packages?: string | string[] };
  if (!packages || (Array.isArray(packages) && packages.length === 0)) {
    res.status(400).json({ success: false, error: 'packages required (string or string[])' }); return;
  }
  const pkgList = (Array.isArray(packages) ? packages : String(packages).split(/[\s,]+/))
    .map((p: string) => p.trim()).filter(Boolean);
  const invalid = pkgList.filter((p: string) => !/^[A-Za-z0-9._\-\[\]>=<!~^,]+$/.test(p));
  if (invalid.length > 0) {
    res.status(400).json({ success: false, error: 'invalid package name(s): ' + invalid.join(', ') }); return;
  }
  const safeList = pkgList.map((p: string) => JSON.stringify(p));
  try {
    const created = await jobQueue.create('obvious_pip_' + Date.now());
    const wrapperCode = `
import sys, pathlib
sys.path.insert(0, ${JSON.stringify(OBVIOUS_SCRIPTS_DIR)})
from obvious_sandbox import ObviousSandbox
sb = ObviousSandbox.from_account(${JSON.stringify(account)}, acc_dir=pathlib.Path(${JSON.stringify(OBVIOUS_ACC_DIR)}))
pkgs = [${safeList.join(', ')}]
print("[sandbox]", repr(sb), "installing:", pkgs, file=sys.stderr)
out = sb.shell("pip install --quiet " + " ".join(pkgs))
print(out)
pkg0 = pkgs[0].split(">=")[0].split("<=")[0].split("==")[0].split("!=")[0].split("[")[0].replace("-","_")
try:
    __import__(pkg0)
    print("[install] import OK:", pkg0)
except ImportError:
    print("[install] note: module name may differ from package name:", pkg0)
`.trim();
    _spawnObviousJob(created.jobId, ['-c', wrapperCode]);
    res.json({ success: true, jobId: created.jobId, packages: pkgList,
               message: 'pip install started — poll /api/tools/obvious/job/' + created.jobId });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// POST /tools/obvious/register — obvious沙箱内注册Replit账号
// Body: { email, account?, proxy? }
router.post('/tools/obvious/register', async (req, res) => {
  const { email, account = 'eu-test1', proxy } =
    req.body as { email?: string; account?: string; proxy?: string };
  if (!email) { res.status(400).json({ success: false, error: 'email required' }); return; }
  try {
    const created = await jobQueue.create('obvious_reg_' + Date.now());
    const args = [
      path.join(OBVIOUS_SCRIPTS_DIR, 'obvious_executor.py'),
      '--register', '--email', email, '--account', account, '--acc-dir', OBVIOUS_ACC_DIR,
    ];
    if (proxy) args.push('--proxy', proxy);
    _spawnObviousJob(created.jobId, args);
    res.json({ success: true, jobId: created.jobId, message: 'obvious注册任务已启动' });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// POST /tools/obvious/provision — 全自动注册新obvious账号
// Body: { label?, proxy? }
router.post('/tools/obvious/provision', async (req, res) => {
  const { label, proxy } = req.body as { label?: string; proxy?: string };
  const accLabel = label || ('auto-' + Date.now());
  try {
    const created = await jobQueue.create('obvious_prov_' + Date.now());
    const args = [
      path.join(OBVIOUS_SCRIPTS_DIR, 'obvious_pool.py'),
      '--dir', OBVIOUS_ACC_DIR, 'provision', '--label', accLabel,
    ];
    if (proxy) args.push('--proxy', proxy);
    _spawnObviousJob(created.jobId, args, { DISPLAY: ':99' });
    // 监听 obvious provision 完成事件，同步写入统一数据库
    const _provJobId = created.jobId;
    const _provLabel = accLabel;
    jobQueue.once('done', async (doneJob: { jobId: string; exitCode?: number }) => {
      if (doneJob.jobId !== _provJobId || doneJob.exitCode !== 0) return;
      try {
        const _fs = await import('fs');
        // 优先读 manifest.json（含 password/proxy/全字段），fallback 到 index.json
        const _manifestPath = OBVIOUS_ACC_DIR + '/' + _provLabel + '/manifest.json';
        const _idxPath = OBVIOUS_ACC_DIR + '/index.json';
        let _newAcc: Record<string, unknown> | null = null;
        if (_fs.existsSync(_manifestPath)) {
          _newAcc = JSON.parse(_fs.readFileSync(_manifestPath, 'utf8')) as Record<string, unknown>;
        } else if (_fs.existsSync(_idxPath)) {
          const _all = JSON.parse(_fs.readFileSync(_idxPath, 'utf8')) as Array<Record<string, unknown>>;
          _newAcc = _all.find(a => a.label === _provLabel) ?? null;
        }
        if (_newAcc) {
          const _cp = (await import('child_process')).spawn(
            'python3', ['/data/Toolkit/artifacts/api-server/sync_unified_db.py']
          );
          _cp.stdin.write(JSON.stringify({ action: 'obvious', ..._newAcc }));
          _cp.stdin.end();
        }
      } catch (_) {}
    });
    res.json({ success: true, jobId: created.jobId, label: accLabel, message: 'obvious账号注册任务已启动' });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// GET /tools/obvious/provision/:jobId — 查询provision任务
router.get('/tools/obvious/provision/:jobId', async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: 'job not found' }); return; }
  const provisioned = (job.logs || []).some(
    (l: { message?: string } | undefined | null) => typeof l?.message === 'string' && (l.message.includes('provisioned') || l.message.includes('✅')));
  res.json({ success: true, job, provisioned });
});

// GET /tools/obvious/job/:jobId — 通用任务状态查询
router.get('/tools/obvious/job/:jobId', async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: 'job not found' }); return; }
  res.json({ success: true, job });
});

// POST /tools/obvious/repair — 修复账号 null projectId/threadId/sandboxId
// Body: { label: string, headless?: boolean }
router.post("/tools/obvious/repair", async (req, res) => {
  const { label, headless } = req.body as { label: string; headless?: boolean };
  if (!label) { res.status(400).json({ success: false, error: "label is required" }); return; }
  try {
    const created = await jobQueue.create("obvious_repair_" + Date.now());
    const args = [
      path.join(OBVIOUS_SCRIPTS_DIR, "repair_account.py"),
      "--label", label,
    ];
    if (headless) args.push("--headless");
    _spawnObviousJob(created.jobId, args, { DISPLAY: ":99" });
    res.json({ success: true, jobId: created.jobId, label, message: "repair 任务已启动，poll /api/tools/obvious/job/" + created.jobId });
  } catch (e: unknown) { res.status(500).json({ success: false, error: String(e) }); }
});

// ═══ captcha-recognition 模块 (port 8765) ════════════════════════════════════
const CAPTCHA_API = `http://localhost:${process.env.CAPTCHA_API_PORT || "8765"}`;

// GET /tools/captcha/health — 模型状态
router.get("/tools/captcha/health", async (_req, res) => {
  try {
    const r = await fetch(`${CAPTCHA_API}/health`, { signal: AbortSignal.timeout(5_000) });
    const data = await r.json();
    res.json(data);
  } catch (e) { res.status(502).json({ ok: false, error: String(e) }); }
});

// POST /tools/captcha/recognize — 识别验证码图片
// Body: { base64: "<base64_png>" } | { image_path: "/abs/path" }
router.post("/tools/captcha/recognize", async (req, res) => {
  try {
    const r = await fetch(`${CAPTCHA_API}/recognize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(15_000),
    });
    const data = await r.json();
    res.status(r.ok ? 200 : 500).json(data);
  } catch (e) { res.status(502).json({ error: String(e) }); }
});

// GET /tools/captcha/train/status — 训练任务状态
router.get("/tools/captcha/train/status", async (_req, res) => {
  try {
    const r = await fetch(`${CAPTCHA_API}/train/status`, { signal: AbortSignal.timeout(5_000) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: String(e) }); }
});

// POST /tools/captcha/train/start — 启动训练
// Body: { skip_gen?: boolean }
router.post("/tools/captcha/train/start", async (req, res) => {
  try {
    const r = await fetch(`${CAPTCHA_API}/train/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(10_000),
    });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ error: String(e) }); }
});


// ═══ pydoll WAF bypass 模块 (port 8766) ══════════════════════════════════════
const PYDOLL_API = `http://localhost:${process.env.PYDOLL_PORT || "8766"}`;

// GET /tools/waf/healthz — 服务健康
router.get("/tools/waf/healthz", async (_req, res) => {
  try {
    const r = await fetch(`${PYDOLL_API}/healthz`, { signal: AbortSignal.timeout(5_000) });
    res.json(await r.json());
  } catch (e) { res.status(502).json({ ok: false, error: String(e) }); }
});

// POST /tools/waf/bypass — CF WAF 绕过
// Body: { url, headless?, screenshot?, timeout? }
router.post("/tools/waf/bypass", async (req, res) => {
  try {
    const r = await fetch(`${PYDOLL_API}/bypass`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(90_000),
    });
    const data = await r.json();
    res.status(r.ok ? 200 : 500).json(data);
  } catch (e) { res.status(502).json({ success: false, error: String(e) }); }
});

// POST /tools/waf/scrape — 隐蔽爬取 + 数据提取
// Body: { url, selectors, headless? }
router.post("/tools/waf/scrape", async (req, res) => {
  try {
    const r = await fetch(`${PYDOLL_API}/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
      signal: AbortSignal.timeout(90_000),
    });
    const data = await r.json();
    res.status(r.ok ? 200 : 500).json(data);
  } catch (e) { res.status(502).json({ success: false, error: String(e) }); }
});


// ═══ novproxy 自动化模块 ════════════════════════════════════════════════════
// 注册: pydoll + ddddocr (隐蔽绕 CF + 自动解图片验证码)
// 登录: 纯 HTTP 直调 api.novproxy.com (无浏览器，学自 Outlook 工作流思路)

// POST /tools/novproxy/register — 启动批量注册 job (支持代理轮换 → IP 一致性)
router.post("/tools/novproxy/register", async (req, res) => {
  const {
    accounts = [],
    delay    = 3,
    proxies: proxiesInput = "",   // 逗号/换行分隔代理列表 → 每账号轮换 IP
    proxy:   proxyInput   = "",
    autoProxy = false,
    graphTokenJson = "{}",
  } = req.body as { accounts?: [string,string][]; delay?: number; proxies?: string; proxy?: string; autoProxy?: boolean; graphTokenJson?: string };
  if (!accounts.length) { res.status(400).json({ success: false, error: "需要账号列表" }); return; }

  // 解析代理列表
  let proxyList: string[] = proxiesInput
    ? proxiesInput.split(/\n|,/).map((p: string) => p.trim()).filter(Boolean)
    : proxyInput ? [proxyInput] : [];

  const jobId = `np_reg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const job   = await jobQueue.create(jobId);
  const proxyNote = proxyList.length > 0 ? ` [${proxyList.length}个代理轮换]` : autoProxy ? " [自动CF代理]" : "";
  job.logs.push({ type: "start", message: `pydoll 注册任务启动: ${accounts.length} 个账号 (ddddocr 自动解验证码)${proxyNote}` });
  res.json({ success: true, jobId });

  // 如果 autoProxy，从 CF 代理池拉取
  if (!proxyList.length && autoProxy) {
    try {
      const poolR = await fetch(`http://localhost:${process.env.PORT || "8081"}/api/tools/cf-pool/status`);
      const pool = await poolR.json() as { pool?: {ip:string;latency:number}[]; available?: number };
      if (pool.pool && pool.pool.length > 0) {
        proxyList = pool.pool.slice(0, accounts.length).map((p) => `socks5://127.0.0.1:${p.ip.includes(':') ? p.ip.split(':')[1] : '10808'}`);
        job.logs.push({ type: "log", message: `☁️ CF代理池: 选取 ${proxyList.length} 个节点` });
      }
    } catch (e) {
      job.logs.push({ type: "warn", message: `⚠ CF代理池获取失败，使用直连: ${String(e).slice(0,80)}` });
    }
  }

  const { spawn } = await import("child_process");
  const scriptPath = "/root/Toolkit/scripts/novproxy_register_worker.py";
  const args = [scriptPath, "--accounts", JSON.stringify(accounts), "--delay", String(delay)];
  if (proxyList.length > 1) args.push("--proxies", proxyList.join(","));
  else if (proxyList.length === 1) args.push("--proxy", proxyList[0]);
  // Auto-load Outlook refresh_tokens from DB so register worker can read email verification codes
  let graphMap: Record<string, string> = {};
  try { graphMap = JSON.parse(graphTokenJson); } catch {}
  if (Object.keys(graphMap).length === 0) {
    try {
      const emailList = accounts.map((a) => a[0]);
      const rows = (await query(
        "SELECT email, token as rt FROM accounts WHERE platform='outlook' AND email = ANY($1::text[])",
        [emailList]
      ).catch(() => [])) as { email: string; rt: string }[];
      for (const row of rows) { if (row.rt) graphMap[row.email] = row.rt; }
      if (Object.keys(graphMap).length > 0)
        job.logs.push({ type: "log", message: `[Graph] Loaded ${Object.keys(graphMap).length} Outlook token(s) for email code reading` });
      else
        job.logs.push({ type: "warn", message: "[Graph] No Outlook tokens found in DB. Register will NOT fill mailbox-captcha -> secure_email will be empty -> free trial may not be granted." });
    } catch {}
  }
  args.push("--graph-token-json", JSON.stringify(graphMap));
  const child = spawn("python3", args, { env: { ...process.env as Record<string, string>, PYTHONUNBUFFERED: "1" } });
  jobQueue.setChild(jobId, child);

  child.stdout.on("data", (chunk: Buffer) => {
    for (const line of chunk.toString().split("\n")) {
      const t = line.trim();
      if (!t) continue;
      if (t.startsWith("[OK]")) {
        const parts = t.slice(5).split("|");
        if (parts.length >= 2) {
          const exitIp = parts[2]?.trim() || "";
          job.accounts.push({ email: parts[0].trim(), password: parts[1].trim(), username: exitIp });
          job.logs.push({ type: "success", message: `✅ 注册成功: ${parts[0].trim()}${exitIp ? " (IP:"+exitIp+")" : ""}` });
        }
      } else if (t.startsWith("[FAIL]")) {
        const parts = t.slice(7).split("|");
        job.logs.push({ type: "error", message: `❌ 失败: ${parts.join(" — ")}` });
      } else if (t.startsWith("[LOG]")) {
        const msg = t.slice(6).trim();
        if (msg) job.logs.push({ type: "log", message: msg });
      } else if (t.startsWith("[DONE]")) {
        job.logs.push({ type: "done", message: `🏁 注册完成 ${t.slice(7)}` });
      }
    }
  });
  child.stderr.on("data", (chunk: Buffer) => {
    for (const l of chunk.toString().split("\n")) {
      const lt = l.trim();
      if (lt && lt.length > 5 && !lt.includes("DeprecationWarning") && !lt.includes("UserWarning") && !lt.includes("show_ad"))
        job.logs.push({ type: "log", message: `[sys] ${lt.slice(0, 200)}` });
    }
  });
  child.on("close", (code: number) => {
    job.status = "done";
    job.logs.push({ type: "done", message: `进程退出 code=${code}，共成功 ${job.accounts.length} 个` });
  });
});

// GET /tools/novproxy/register/:jobId
router.get("/tools/novproxy/register/:jobId", async (req, res) => {
  const since = parseInt(String(req.query.since ?? "0"), 10);
  const job   = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  res.json({ success: true, status: job.status, logs: job.logs.slice(since), accounts: job.accounts, nextSince: job.logs.length });
});

// DELETE /tools/novproxy/register/:jobId
router.delete("/tools/novproxy/register/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});

// POST /tools/novproxy/login — 批量登录 job (纯 HTTP，零浏览器)
// 隐蔽思路：直调 API + 自动拉取代理凭据 + IP白名单 + 入库保存
router.post("/tools/novproxy/login", async (req, res) => {
  const { accounts = [], delay = 0.5, proxy = "" } = req.body as { accounts?: [string,string][]; delay?: number; proxy?: string };
  if (!accounts.length) { res.status(400).json({ success: false, error: "需要账号列表" }); return; }

  const jobId = `np_login_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const job   = await jobQueue.create(jobId);
  job.logs.push({ type: "start", message: `HTTP 直调登录: ${accounts.length} 个账号 (无浏览器) → 自动提取代理凭据 + 入库` });
  res.json({ success: true, jobId });

  const { spawn } = await import("child_process");
  const scriptPath = "/root/Toolkit/scripts/novproxy_login_worker.py";
  const spawnArgs = [scriptPath, "--accounts", JSON.stringify(accounts), "--delay", String(delay)];
  if (proxy) spawnArgs.push("--proxy", proxy);
  const child = spawn("python3", spawnArgs, { env: { ...process.env as Record<string, string>, PYTHONUNBUFFERED: "1" } });
  jobQueue.setChild(jobId, child);

  // 临时缓存：jobId → { proxy_user, proxy_pass, proxy_server, proxy_port }
  const proxyCache = new Map<string, { pu: string; pp: string; sv: string; pt: string }>();

  child.stdout.on("data", (chunk: Buffer) => {
    (async () => {
    for (const line of chunk.toString().split("\n")) {
      const t = line.trim();
      if (!t) continue;

      if (t.startsWith("[OK]")) {
        // email|password|token|access_key|proxy_user|proxy_pass|alltraffic
        const parts = t.slice(5).split("|").map((s: string) => s.trim());
        const [email, password, token, access_key, proxy_user, proxy_pass, alltraffic] = parts;
        if (!email || !token) continue;

        const pc = proxyCache.get(email) ?? { pu: proxy_user||"", pp: proxy_pass||"", sv:"us.novproxy.io", pt:"1000" };
        const proxyConnStr = pc.pu ? `${pc.pu}:${pc.pp}@${pc.sv}:${pc.pt}` : "";
        const httpProxyStr = pc.pu ? `http://${pc.pu}:${pc.pp}@${pc.sv}:${pc.pt}` : "";
        const socks5Str    = pc.pu ? `socks5h://${pc.pu}:${pc.pp}@${pc.sv}:${pc.pt}` : "";

        job.accounts.push({
          email, password, token,
          username: proxy_user || "",   // 存代理子账号用户名
        });
        job.logs.push({ type: "success", message: `✅ ${email} | token=${token.slice(0,16)}... | 代理=${proxyConnStr.slice(0,40)} | 流量=${alltraffic}MB` });

        // ── 入库保存（仿 Outlook 工作流入库模式）─────────────────────
        try {
          await execute(
            `INSERT INTO accounts (platform, email, password, token, username, notes, status, updated_at)
             VALUES ('novproxy', $1, $2, $3, $4, $5, 'active', NOW())
             ON CONFLICT (platform, email) DO UPDATE SET
               token      = EXCLUDED.token,
               username   = EXCLUDED.username,
               notes      = EXCLUDED.notes,
               status     = 'active',
               updated_at = NOW()`,
            [email, password, token, proxy_user || "", httpProxyStr],
          );
          job.logs.push({ type: "log", message: `💾 入库: ${email} (代理连接串已保存)` });
        } catch (dbErr) {
          job.logs.push({ type: "warn", message: `⚠ 入库失败 (${email}): ${String(dbErr).slice(0, 120)}` });
        }

      } else if (t.startsWith("[PROXY]")) {
        // proxy_user|proxy_pass|server|port|alltraffic|whitelist_ip
        const parts = t.slice(8).split("|").map((s: string) => s.trim());
        const [pu, pp, sv, pt, at, wl] = parts;
        if (pu) {
          job.logs.push({ type: "log", message: `🌐 代理凭据: ${pu}:${pp}@${sv}:${pt} | 流量=${at}MB${wl ? " | 白名单:"+wl : ""}` });
          job.logs.push({ type: "log", message: `📋 HTTP:   http://${pu}:${pp}@${sv}:${pt}` });
          job.logs.push({ type: "log", message: `📋 SOCKS5: socks5h://${pu}:${pp}@${sv}:${pt}` });
          if (at !== "0" && parseFloat(at) > 0) {
            job.logs.push({ type: "success", message: `✨ 可用流量: ${at}MB | 代理已就绪` });
          } else {
            job.logs.push({ type: "warn", message: `⚠ 流量余额 0MB — 需充值后方可使用代理` });
          }
        }
      } else if (t.startsWith("[FAIL]")) {
        const parts = t.slice(7).split("|");
        job.logs.push({ type: "error", message: `❌ 登录失败: ${parts.join(" — ")}` });
      } else if (t.startsWith("[LOG]")) {
        const msg = t.slice(6).trim();
        if (msg) job.logs.push({ type: "log", message: msg });
      } else if (t.startsWith("[DONE]")) {
        job.logs.push({ type: "done", message: `🏁 登录完成 ${t.slice(7)}` });
      }
    }
    })();
  });
  child.stderr.on("data", (chunk: Buffer) => {
    for (const l of chunk.toString().split("\n")) {
      const lt = l.trim();
      if (lt && lt.length > 5 && !lt.includes("DeprecationWarning") && !lt.includes("UserWarning"))
        job.logs.push({ type: "log", message: `[sys] ${lt.slice(0, 200)}` });
    }
  });
  child.on("close", (code: number) => {
    job.status = "done";
    job.logs.push({ type: "done", message: `进程退出 code=${code}，共成功 ${job.accounts.length} 个入库` });
  });
});

// GET /tools/novproxy/login/:jobId
router.get("/tools/novproxy/login/:jobId", async (req, res) => {
  const since = parseInt(String(req.query.since ?? "0"), 10);
  const job   = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "job not found" }); return; }
  res.json({ success: true, status: job.status, logs: job.logs.slice(since), accounts: job.accounts, nextSince: job.logs.length });
});

// DELETE /tools/novproxy/login/:jobId
router.delete("/tools/novproxy/login/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});


// ─── CDK 批量兑换（去重防重复）────────────────────────────────────────────────
// POST /tools/novproxy/redeem-cdk
// body: { codes: string[], email: string, token: string }
router.post("/tools/novproxy/redeem-cdk", async (req, res) => {
  const { codes, email, token } = req.body as { codes: string[]; email: string; token: string };
  if (!Array.isArray(codes) || codes.length === 0 || !token) {
    res.status(400).json({ success: false, error: "codes[], email, token 必填" }); return;
  }
  const cleanCodes = [...new Set(codes.map((c: string) => c.trim().toUpperCase()).filter(Boolean))];
  const results: { code: string; result: string; msg: string; skipped: boolean }[] = [];

  for (const cdk of cleanCodes) {
    // ① 去重：查 DB 是否已成功兑换过
    const existing = await query<{ result: string; msg: string }>(
      "SELECT result, msg FROM novproxy_cdks WHERE code = $1 LIMIT 1", [cdk]
    ).catch(() => [] as { result: string; msg: string }[]) ?? [];
    if (existing.length > 0) {
      const prev = existing[0];
      results.push({ code: cdk, result: prev.result, msg: `[已跳过] 本地记录: ${prev.result} — ${prev.msg}`, skipped: true });
      continue;
    }

    // ② 调用 novproxy CDK 兑换接口
    let apiResult = "error", apiMsg = "";
    try {
      const params = new URLSearchParams({ token, cdkey: cdk });
      const resp = await fetch("https://api.novproxy.com/v1/cdkeyUse", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "Origin": "https://dash.novproxy.com" },
        body: params.toString(),
        signal: AbortSignal.timeout(10000),
      });
      const data = await resp.json() as { code: number; msg: string; data?: { traffic?: number } };
      apiMsg = data.msg ?? "";
      if (data.code === 0) {
        apiResult = "success";
      } else if (apiMsg.toLowerCase().includes("has been used") || apiMsg.toLowerCase().includes("already")) {
        apiResult = "used";
      } else if (apiMsg.toLowerCase().includes("does not exist") || apiMsg.toLowerCase().includes("invalid")) {
        apiResult = "invalid";
      } else {
        apiResult = "error";
      }
    } catch (e) {
      apiMsg = String(e).slice(0, 120);
      apiResult = "error";
    }

    // ③ 写入 DB（幂等）
    await execute(
      `INSERT INTO novproxy_cdks (code, account_email, result, msg, attempted_at)
       VALUES ($1, $2, $3, $4, NOW())
       ON CONFLICT (code) DO UPDATE SET result=$3, msg=$4, attempted_at=NOW()`,
      [cdk, email || null, apiResult, apiMsg]
    ).catch(() => {});

    results.push({ code: cdk, result: apiResult, msg: apiMsg, skipped: false });
  }

  const succeeded = results.filter(r => r.result === "success").length;
  res.json({ success: true, total: cleanCodes.length, succeeded, results });
});

// GET /tools/novproxy/cdk-records — 查询已记录的 CDK 兑换历史
router.get("/tools/novproxy/cdk-records", async (_req, res) => {
  const rows = await query(
    "SELECT code, account_email, result, msg, attempted_at FROM novproxy_cdks ORDER BY attempted_at DESC LIMIT 200"
  ).catch(() => []);
  res.json({ success: true, records: rows });
});


// GET /tools/novproxy/status — 查询 DB 中已保存的 novproxy 账号及代理凭据状态
router.get("/tools/novproxy/status", async (_req, res) => {
  try {
    type NvRow = { email: string; password: string; token: string; username: string; notes: string; status: string; updated_at: string };
    const rows = await (query as (sql: string) => Promise<NvRow[]>)(
      `SELECT email, password, token, username, notes, status, updated_at
       FROM accounts WHERE platform = 'novproxy'
       ORDER BY updated_at DESC LIMIT 200`
    ).catch(() => [] as NvRow[]);
    const accounts = rows.map((r: NvRow) => ({
      email:         r.email,
      password:      r.password,
      proxy_user:    r.username  || "",
      proxy_conn:    r.notes     || "",
      token_preview: r.token ? r.token.slice(0, 20) + "…" : "",
      status:        r.status,
      updated_at:    r.updated_at,
    }));
    res.json({ success: true, total: accounts.length, accounts });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// POST /tools/novproxy/diagnose -- free-trial root-cause analysis
router.post("/tools/novproxy/diagnose", async (req, res) => {
  const { email, password, token: existingToken } = req.body as { email: string; password?: string; token?: string };
  if (!email) { res.status(400).json({ success: false, error: "email required" }); return; }

  const SCRIPT = "/root/Toolkit/scripts/novproxy_diagnose.py";
  const payload = existingToken
    ? JSON.stringify({ email, token: existingToken })
    : JSON.stringify({ email, pwd: password ?? "" });

  const pyResult = await new Promise<Record<string, unknown>>((resolve, reject) => {
    _execFile("python3", [SCRIPT, payload], { timeout: 25_000, maxBuffer: 1024 * 512 }, (err, stdout, stderr) => {
      if (err && !stdout.trim()) { reject(new Error(`python exit: ${stderr.slice(0, 200)}`)); return; }
      try { resolve(JSON.parse(stdout.trim()) as Record<string, unknown>); }
      catch { reject(new Error(`json parse failed: ${stdout.slice(0, 200)} | ${stderr.slice(0, 200)}`)); }
    });
  }).catch((e: unknown) => ({ error: String(e) }) as Record<string, unknown>);

  if (pyResult["error"]) {
    const errMsg = String(pyResult["error"]);
    if (errMsg.includes("login_failed")) {
      res.json({ success: false, error: "Login failed -- no token", detail: pyResult["signin"] }); return;
    }
    res.status(500).json({ success: false, error: errMsg }); return;
  }

  try {
    const memberR    = (pyResult["member"]      ?? {}) as Record<string, unknown>;
    const trafficR   = (pyResult["trafficInfo"] ?? {}) as Record<string, unknown>;
    const priceR     = (pyResult["priceList"]   ?? {}) as Record<string, unknown>;

    const member      = (memberR?.data ?? {})  as Record<string, unknown>;
    const trafficInfo = (trafficR?.data ?? {}) as Record<string, unknown>;
    const priceData   = (priceR?.data  ?? {})  as { trafficList?: unknown[] };

    const issues: { level: string; code: string; title: string; desc: string; fix: string }[] = [];

    if (!member.secure_email) {
      issues.push({ level: "error", code: "no_email_verify",
        title: "Email not verified (secure_email empty)",
        desc: "Account was created via /v1/signup without email verification. The mailbox-captcha field was never filled. secure_email is empty.",
        fix: "Re-register and fill the email verification code (mailbox-captcha field); or contact novproxy support to manually activate." });
    }
    if (trafficInfo.flow_open === "off") {
      issues.push({ level: "error", code: "flow_off",
        title: "Proxy service is OFF (flow_open: off)",
        desc: "Traffic proxy service is disabled. No requests can be routed through this account.",
        fix: "Complete email verification -- service may activate automatically. Or top up traffic balance." });
    }
    const alltraffic = parseFloat(String(trafficInfo.alltraffic ?? 0)) || 0;
    if (alltraffic <= 0) {
      issues.push({ level: "error", code: "no_traffic",
        title: "Traffic balance: 0 MB",
        desc: "No usable traffic. Proxy cannot route any requests.",
        fix: "Top up a traffic package ($0.5/GB minimum) or redeem a valid CDK code." });
    }
    const isOutlook = /\@(outlook|hotmail|live)\.com$/i.test(email);
    if (isOutlook) {
      issues.push({ level: "warn", code: "email_not_deliverable",
        title: "novproxy emails do NOT reach Outlook",
        desc: "Confirmed: /v1/mailCode returns Success but ZERO emails arrive at Outlook (Inbox + JunkEmail + all folders checked = 0 novproxy emails). Cannot get verification code.",
        fix: "Register novproxy accounts with Gmail. Gmail successfully receives novproxy verification emails." });
    }
    const hasFreeTier = (priceData.trafficList?.length ?? 0) > 0;
    if (!hasFreeTier) {
      issues.push({ level: "warn", code: "no_free_tier",
        title: "priceList is empty (no free tier product)",
        desc: "/v2/priceList returns empty arrays. No free trial product is attached to this account in the system.",
        fix: "Free 500MB is granted after email-verified registration. Contact novproxy support or top up manually." });
    }

    let diagnoseMsg: string;
    if (alltraffic > 0) {
      diagnoseMsg = `Account has ${alltraffic}MB traffic. Proxy is usable.`;
    } else if (isOutlook && !member.secure_email) {
      diagnoseMsg = [
        "ROOT CAUSE (5 layers confirmed):",
        "1. Register worker used /v1/signup (no email verification) -> secure_email is empty",
        "2. mailbox-captcha field was never filled (worker did not request nor read email code)",
        "3. novproxy emails DO NOT reach Outlook (confirmed: /v1/mailCode says Success, 0 emails in all folders)",
        "4. flow_open: off + alltraffic: 0 (proxy service not activated)",
        "5. priceList: [] (no free-tier product attached to account)",
        "",
        "SOLUTION: Register novproxy accounts with Gmail (receives verification emails).",
        "This fixes the email delivery blocker. The updated register worker",
        "now clicks Get code, waits for email, fills mailbox-captcha automatically.",
        "For existing account: contact novproxy support to manually activate or top up."
      ].join("\n");
    } else if (!member.secure_email) {
      diagnoseMsg = "Email not verified (secure_email empty). Re-register with email verification completed.";
    } else {
      diagnoseMsg = "Email verified but no free traffic granted. Contact novproxy support or top up balance.";
    }

    res.json({
      success: true, email,
      member: { secure_email: member.secure_email ?? "", disable: member.disable, can_set_pwd: member.can_set_pwd },
      trafficInfo: { flow_open: trafficInfo.flow_open, alltraffic: trafficInfo.alltraffic, flow_num: trafficInfo.flow_num },
      issues,
      freeTrial: { available: alltraffic > 0, diagnoseMsg },
      priceListEmpty: !hasFreeTier,
    });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});



// ── SMS Center ─────────────────────────────────────────────────────────────
// GET /api/tools/sms/numbers?country=all|us|gb|ca|de|th|my|ph
router.get('/tools/sms/numbers', async (req, res) => {
  const { country = 'all' } = req.query as { country?: string };
  const SCRIPT = '/root/Toolkit/scripts/sms_center_fetch.py';
  const result = await new Promise<unknown>((resolve, reject) => {
    _execFile('python3', [SCRIPT, '--action', 'numbers', '--country', country],
      { timeout: 30_000, maxBuffer: 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && !stdout.trim()) { reject(new Error(stderr.slice(0, 300))); return; }
        try { resolve(JSON.parse(stdout.trim())); }
        catch { reject(new Error('json_parse: ' + stdout.slice(0, 200))); }
      });
  }).catch((e: unknown) => ({ error: String(e) }));
  res.json(result);
});

// POST /api/tools/sms/messages  body: { phoneId: number }
router.post('/tools/sms/messages', async (req, res) => {
  const { phoneId } = req.body as { phoneId?: number };
  if (!phoneId) { res.status(400).json({ error: 'phoneId required' }); return; }
  const SCRIPT = '/root/Toolkit/scripts/sms_center_fetch.py';
  const result = await new Promise<unknown>((resolve, reject) => {
    _execFile('python3', [SCRIPT, '--action', 'messages', '--phone-id', String(phoneId)],
      { timeout: 90_000, maxBuffer: 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && !stdout.trim()) { reject(new Error(stderr.slice(0, 300))); return; }
        try { resolve(JSON.parse(stdout.trim())); }
        catch { reject(new Error('json_parse: ' + stdout.slice(0, 200))); }
      });
  }).catch((e: unknown) => ({ error: String(e), messages: [] }));
  res.json(result);
});


// ── SMS Center: smsreceivefree.xyz ─────────────────────────────────────────
// GET /api/tools/smsrf/numbers   — scrape live number list from homepage feed
router.get('/tools/smsrf/numbers', async (req, res) => {
  const SCRIPT = '/root/Toolkit/scripts/smsreceivefree_fetch.py';
  const result = await new Promise<unknown>((resolve, reject) => {
    _execFile('python3', [SCRIPT, '--action', 'numbers'],
      { timeout: 90_000, maxBuffer: 2 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && !stdout.trim()) { reject(new Error(stderr.slice(0, 400))); return; }
        try { resolve(JSON.parse(stdout.trim())); }
        catch { reject(new Error('json_parse: ' + stdout.slice(0, 200))); }
      });
  }).catch((e: unknown) => ({ error: String(e) }));
  res.json(result);
});

// POST /api/tools/smsrf/messages  body: { phone: string, force?: boolean }
router.post('/tools/smsrf/messages', async (req, res) => {
  const { phone, force } = req.body as { phone?: string; force?: boolean };
  if (!phone) { res.status(400).json({ error: 'phone required' }); return; }
  const SCRIPT = '/root/Toolkit/scripts/smsreceivefree_fetch.py';
  const pyArgs = ['python3', SCRIPT, '--action', 'messages', '--phone', phone];
  if (force) pyArgs.push('--force');
  const result = await new Promise<unknown>((resolve, reject) => {
    _execFile(pyArgs[0], pyArgs.slice(1),
      { timeout: 120_000, maxBuffer: 2 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && !stdout.trim()) { reject(new Error(stderr.slice(0, 400))); return; }
        try { resolve(JSON.parse(stdout.trim())); }
        catch { reject(new Error('json_parse: ' + stdout.slice(0, 200))); }
      });
  }).catch((e: unknown) => ({ error: String(e), messages: [] }));
  res.json(result);
});


// ═══════════════════════════════════════════════════════════════════════════
// 文字点选验证码识别 — Text_select_captcha (FastAPI, port 8767)
// Source: https://github.com/MgArcher/Text_select_captcha
// POST /tools/text-captcha/identify  { dataType: 1|2, imageSource: url|base64, imageID? }
// POST /tools/text-captcha/show      { dataType: 1|2, imageSource: url|base64 }  → jpeg
// GET  /tools/text-captcha/health
// ═══════════════════════════════════════════════════════════════════════════
const TEXT_CAPTCHA_BASE = "http://127.0.0.1:8767";

async function proxyTextCaptcha(path: string, init?: RequestInit): Promise<Response> {
  return fetch(`${TEXT_CAPTCHA_BASE}${path}`, init);
}

router.get("/tools/text-captcha/health", async (_req, res) => {
  try {
    const r = await proxyTextCaptcha("/");
    const d = await r.json() as unknown;
    res.json({ ok: r.ok, status: r.status, data: d });
  } catch (e) {
    res.status(503).json({ ok: false, error: String(e) });
  }
});

router.post("/tools/text-captcha/identify", async (req, res) => {
  try {
    const r = await proxyTextCaptcha("/api/v1/identify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    const d = await r.json() as unknown;
    res.status(r.status).json(d);
  } catch (e) {
    res.status(503).json({ code: 503, msg: String(e), data: null });
  }
});

router.post("/tools/text-captcha/show", async (req, res) => {
  try {
    const r = await proxyTextCaptcha("/api/v1/show", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    const buf = await r.arrayBuffer();
    res.status(r.status).set("Content-Type", "image/jpeg").send(Buffer.from(buf));
  } catch (e) {
    res.status(503).json({ error: String(e) });
  }
});






// ═══════════════════════════════════════════════════════════════════════════
// Webshare.io 注册路由
// ═══════════════════════════════════════════════════════════════════════════

// POST /tools/webshare/register
router.post('/tools/webshare/register', async (req, res) => {
  const {
    email, password,
    proxy = '',
    headless = true,
    capsolverKey = '',
  } = req.body as { email?: string; password?: string; proxy?: string; headless?: boolean; capsolverKey?: string };

  if (!email || !password) {
    res.status(400).json({ success: false, error: 'email 和 password 不能为空' });
    return;
  }

  const jobId = `ws_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const job = await jobQueue.create(jobId);
  job.logs.push({ type: 'start', message: `启动 Webshare 注册 — ${email}` });
  if (capsolverKey) {
    job.logs.push({ type: 'log', message: '[Capsolver] 已提供 API Key，使用自动 CAPTCHA 解决方案' });
  } else {
    job.logs.push({ type: 'log', message: '🆓 免费模式: Xvfb + 服务器直连 IP + 音频 CAPTCHA (不需任何付费服务)' });
  }
  if (proxy) job.logs.push({ type: 'log', message: `代理: ${proxy.replace(/:([^:@]{4})[^:@]*@/, ':****@')}` });
  res.json({ success: true, jobId, message: 'Webshare 注册任务已启动' });

  const { spawn } = await import('child_process');
  const scriptPath = new URL('../webshare_register.py', import.meta.url).pathname;
  const args = [scriptPath, '--email', email, '--password', password, '--headless', headless ? 'true' : 'false'];
  if (proxy) args.push('--proxy', proxy);
  if (capsolverKey) args.push('--capsolver-key', capsolverKey);

  const spawnEnv: Record<string, string> = { ...(process.env as Record<string, string>), PYTHONUNBUFFERED: '1' };
  if (capsolverKey) spawnEnv['CAPSOLVER_KEY'] = capsolverKey;

  const child = spawn('python3', args, { env: spawnEnv });
  jobQueue.setChild(jobId, child);

  let jsonBuf = '';
  let inJson = false;

  child.stdout.on('data', (chunk: Buffer) => {
    const raw = chunk.toString();
    if (raw.includes('── 结果 ──') || raw.includes('── JSON 结果 ──') || inJson) { inJson = true; jsonBuf += raw; }
    for (const line of raw.split('\n')) {
      const t = line.trim();
      if (!t || t === '{' || t === '}' || t === '[' || t === ']') continue;
      if (t.startsWith('──')) continue;
      if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[{') && t.endsWith('}]'))) continue;
      const lvl = (t.includes('✅') || t.includes('成功')) ? 'ok'
                : (t.includes('❌') || t.includes('失败') || t.includes('错误')) ? 'error'
                : (t.includes('⚠️') || t.includes('⏳')) ? 'warn' : 'log';
      job.logs.push({ type: lvl, message: t });
    }
  });

  child.stderr.on('data', (chunk: Buffer) => {
    const t = chunk.toString().trim();
    if (t && !t.includes('DeprecationWarning') && !t.includes('asyncio') && !t.includes('FutureWarning')) {
      job.logs.push({ type: 'warn', message: `[stderr] ${t.slice(0, 200)}` });
    }
  });

  child.on('close', async (code: number | null) => {
    let wsResult: Record<string, unknown> | null = null;
    const jsonStart = jsonBuf.indexOf('{');
    if (jsonStart >= 0) {
      try { wsResult = JSON.parse(jsonBuf.slice(jsonStart).trim()); } catch {}
    }
    if (wsResult) {
      if (wsResult['success']) {
        job.logs.push({ type: 'ok', message: `✅ Webshare 注册成功: ${wsResult['email']}` });
        if (wsResult['api_key']) job.logs.push({ type: 'ok', message: `🔑 API Key: ${String(wsResult['api_key']).slice(0, 20)}...` });
        job.accounts.push({ email: String(wsResult['email'] ?? ''), password: String(wsResult['password'] ?? '') });
      } else {
        job.logs.push({ type: 'error', message: `❌ 失败: ${wsResult['error']}` });
      }
    } else if (code !== 0) {
      job.logs.push({ type: 'error', message: `❌ 进程异常退出: ${code}` });
    }
    (job as any)._wsResult = wsResult;
    await jobQueue.finish(jobId, code ?? -1, (wsResult?.['success'] || code === 0) ? 'done' : 'failed');
  });
});

// GET /tools/webshare/register/:jobId
router.get('/tools/webshare/register/:jobId', async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: '任务不存在' }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({
    success: true,
    status: job.status,
    logs: job.logs.slice(since),
    nextSince: job.logs.length,
    result: (job as any)._wsResult ?? null,
    exitCode: job.exitCode,
  });
});

// DELETE /tools/webshare/register/:jobId
router.delete('/tools/webshare/register/:jobId', (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  res.json({ success: !!stopped });
});



// ═══════════════════════════════════════════════════════════════════════════
// Oxylabs.io 注册路由
// ═══════════════════════════════════════════════════════════════════════════

// POST /tools/oxylabs/register
router.post("/tools/oxylabs/register", async (req, res) => {
  const {
    email, password,
    first_name = "", last_name = "", company = "", phone = "",
    proxy = "",
    headless = true,
    capsolverKey = "",
    cfClearance = "",
  } = req.body as { email?: string; password?: string; first_name?: string; last_name?: string; company?: string; phone?: string; proxy?: string; headless?: boolean; capsolverKey?: string; cfClearance?: string };

  if (!email || !password) {
    res.status(400).json({ success: false, error: "email 和 password 不能为空" });
    return;
  }

  const jobId = `oxy_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const job = await jobQueue.create(jobId);
  job.logs.push({ type: "start", message: `启动 Oxylabs 注册 — ${email}` });
  if (capsolverKey) job.logs.push({ type: "log", message: "[CapSolver] API Key 已提供，启用 CF Managed Challenge 自动解决" });
  if (cfClearance)  job.logs.push({ type: "log", message: `[CF] 手动 cf_clearance 已提供 (len=${cfClearance.length})，跳过 CF 挑战` });
  if (proxy) job.logs.push({ type: "log", message: `代理: ${proxy.replace(/:([^:@]{4})[^:@]*@/, ":****@")}` });
  res.json({ success: true, jobId, message: "Oxylabs 注册任务已启动" });

  const { spawn } = await import("child_process");
  const scriptPath = new URL("../oxylabs_register.py", import.meta.url).pathname;
  const args = [scriptPath, "--email", email, "--password", password, "--headless", headless ? "true" : "false"];
  if (proxy)        args.push("--proxy",        proxy);
  if (first_name)   args.push("--first",        first_name);
  if (last_name)    args.push("--last",         last_name);
  if (company)      args.push("--company",      company);
  if (phone)        args.push("--phone",        phone);
  if (capsolverKey) args.push("--capsolver-key", capsolverKey);
  if (cfClearance)  args.push("--cf-clearance",   cfClearance);

  const spawnEnv: Record<string, string> = { ...(process.env as Record<string, string>), PYTHONUNBUFFERED: "1" };
  if (capsolverKey) spawnEnv["CAPSOLVER_API_KEY"] = capsolverKey;

  const child = spawn("python3", args, { env: spawnEnv });
  jobQueue.setChild(jobId, child);

  let jsonBuf = "";
  let inJson = false;

  child.stdout.on("data", (chunk: Buffer) => {
    const raw = chunk.toString();
    if (raw.includes("── JSON 结果 ──") || inJson) { inJson = true; jsonBuf += raw; }
    for (const line of raw.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("──") || (t.startsWith("{") && t.endsWith("}"))) continue;
      const lvl = (t.includes("✅") || t.includes("成功")) ? "ok"
                : (t.includes("❌") || t.includes("失败") || t.includes("错误")) ? "error"
                : (t.includes("⚠") || t.includes("⏳")) ? "warn" : "log";
      job.logs.push({ type: lvl, message: t });
    }
  });

  child.stderr.on("data", (chunk: Buffer) => {
    const t = chunk.toString().trim();
    if (t && !t.includes("DeprecationWarning") && !t.includes("asyncio") && !t.includes("FutureWarning")) {
      job.logs.push({ type: "warn", message: `[stderr] ${t.slice(0, 200)}` });
    }
  });

  child.on("close", async (code: number | null) => {
    let oxyResult: Record<string, unknown> | null = null;
    const jsonStart = jsonBuf.indexOf("{");
    if (jsonStart >= 0) {
      try { oxyResult = JSON.parse(jsonBuf.slice(jsonStart).trim()); } catch {}
    }
    if (oxyResult) {
      if (oxyResult["success"]) {
        job.logs.push({ type: "ok", message: `✅ Oxylabs 注册成功: ${oxyResult["email"]}` });
        if (oxyResult["username"]) job.logs.push({ type: "ok", message: `👤 用户名: ${oxyResult["username"]}` });
        job.accounts.push({ email: String(oxyResult["email"] ?? ""), password: String(oxyResult["password"] ?? "") });
      } else {
        job.logs.push({ type: "error", message: `❌ 失败: ${oxyResult["error"]}` });
      }
    } else if (code !== 0) {
      job.logs.push({ type: "error", message: `❌ 进程异常退出: ${code}` });
    }
    (job as any)._oxyResult = oxyResult;
    await jobQueue.finish(jobId, code ?? -1, (oxyResult?.["success"] || code === 0) ? "done" : "failed");
  });
});

// GET /tools/oxylabs/register/:jobId
router.get("/tools/oxylabs/register/:jobId", async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: "任务不存在" }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({
    success: true,
    status: job.status,
    logs: job.logs.slice(since),
    nextSince: job.logs.length,
    result: (job as any)._oxyResult ?? null,
    exitCode: job.exitCode,
  });
});

// DELETE /tools/oxylabs/register/:jobId
router.delete("/tools/oxylabs/register/:jobId", (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  res.json({ success: !!stopped });
});


// ── 自动检测账号 token 健康状态 ───────────────────────────────────────────
// 安全策略：只用 refresh_token 换 token（标准 OAuth，不触发封号）
// 不做 IMAP 登录，不高频，每账号间隔 1.2 秒，跳过已知封禁账号
router.post("/tools/outlook/auto-check", async (req: Request, res: Response) => {
  const body = req.body as Record<string, unknown>;
  const limit = Math.min(50, Math.max(1, Number(body.limit ?? 30)));
  try {
    const { query: dbQ, execute: dbEx } = await import("../db.js");

    // 存量标签规范化：所有 suspended 账号统一补 abuse_mode（幂等，修复历史脏数据）
    await dbEx(
      `UPDATE accounts
         SET tags       = CASE WHEN COALESCE(tags,'') = '' THEN 'abuse_mode' ELSE tags || ',abuse_mode' END,
             updated_at = NOW()
       WHERE platform='outlook'
         AND status='suspended'
         AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'`
    );

    // 候选账号：只查 active 状态（suspended/error/needs_oauth 全部排除）
    const candidates = await dbQ<{
      id: number; email: string; refresh_token: string;
      tags: string | null; status: string | null;
    }>(
      `SELECT id, email, refresh_token, tags, status FROM accounts
       WHERE platform='outlook'
         AND status = 'active'
         AND refresh_token IS NOT NULL AND refresh_token <> ''
       ORDER BY updated_at ASC NULLS FIRST
       LIMIT $1`,
      [limit]
    );

    let valid = 0, needsAuth = 0, banned = 0, skipped = 0;
    const results: Array<{ id: number; email: string; result: string }> = [];

    for (const acc of candidates) {
      await new Promise<void>(r => setTimeout(r, 1200));
      try {
        const r = await microsoftFetch(
          "https://login.microsoftonline.com/common/oauth2/v2.0/token",
          {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token",
              client_id: OAUTH_CLIENT_ID,
              refresh_token: acc.refresh_token,
              scope: "https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
            }).toString(),
          }
        );
        const td = await r.json() as { access_token?: string; refresh_token?: string; error?: string; error_description?: string };

        if (td.access_token) {
          // 二次验证：调 Graph /me 确认账号未被 API 封禁
          // suspended 账号仍可换 token，但 Graph 会返回 401/403
          const gr = await microsoftFetch("https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName", {
            headers: { Authorization: `Bearer ${td.access_token}` },
          });
          const gd = await gr.json() as { error?: { code?: string } };
          const graphBanned =
            gr.status === 401 || gr.status === 403 ||
            gd.error?.code === "InvalidAuthenticationToken" ||
            gd.error?.code === "AccessDenied" ||
            gd.error?.code === "Authorization_RequestDenied";

          if (graphBanned) {
            // token 能换但 Graph 拒绝 → API 封禁（addAccountTags 自动加 abuse_mode）
            await addAccountTags(acc.id, [], "suspended");
            banned++;
            results.push({ id: acc.id, email: acc.email, result: "banned" });
          } else {
            // token 有效且 Graph 正常 → 更新 token，清除 token_invalid 标签
            await dbEx(
              "UPDATE accounts SET token=$1, refresh_token=$2, status='active', updated_at=NOW() WHERE id=$3",
              [td.access_token, td.refresh_token ?? acc.refresh_token, acc.id]
            );
            const curTags = splitAccountTags(acc.tags);
            if (curTags.includes("token_invalid")) {
              const cleaned = curTags.filter(t => t !== "token_invalid").join(",") || null;
              await dbEx("UPDATE accounts SET tags=$1 WHERE id=$2", [cleaned, acc.id]);
            }
            valid++;
            results.push({ id: acc.id, email: acc.email, result: "valid" });
          }
        } else {
          const errCode = td.error ?? "";
          const errDesc = td.error_description ?? "";
          if (errDesc.includes("AADSTS70000") || errDesc.includes("service abuse") || errDesc.includes("blocked") || errDesc.includes("AADSTS530032")) {
            // token 换取层面被拒 → 封禁（addAccountTags 自动加 abuse_mode）
            await addAccountTags(acc.id, [], "suspended");
            banned++;
            results.push({ id: acc.id, email: acc.email, result: "banned" });
          } else if (errCode === "invalid_grant" || errDesc.includes("AADSTS70008") || errDesc.includes("AADSTS50078") || errDesc.includes("AADSTS700082") || errDesc.includes("AADSTS135010")) {
            // refresh_token 过期 → 需重新授权，降级为 needs_oauth
            await addAccountTags(acc.id, ["token_invalid"], "needs_oauth");
            needsAuth++;
            results.push({ id: acc.id, email: acc.email, result: "needsAuth" });
          } else {
            skipped++;
            results.push({ id: acc.id, email: acc.email, result: "skipped" });
          }
        }
      } catch {
        skipped++;
        results.push({ id: acc.id, email: acc.email, result: "error" });
      }
    }

    res.json({ success: true, checked: candidates.length, valid, needsAuth, banned, skipped });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ── Outlook 截图查看器 API (v9.24) ────────────────────────────────────────────
// GET /api/tools/outlook/screenshots — 列出所有 fail/ok/err 截图及分类
router.get("/tools/outlook/screenshots", async (_req, res) => {
  const { readdir, stat: fsStat } = await import("fs/promises");
  try {
    const files = await readdir("/tmp");
    const pngs = files.filter(f =>
      (f.startsWith("outlook_fail_") || f.startsWith("outlook_ok_") || f.startsWith("outlook_err_")) &&
      f.endsWith(".png")
    );
    const screenshots = await Promise.all(pngs.map(async f => {
      const s = await fsStat();
      const typeRaw = f.startsWith("outlook_ok_") ? "ok" : f.startsWith("outlook_err_") ? "err" : "fail";
      const email = f.replace(/^outlook_(fail|ok|err)_/, "").replace(/\.png$/, "") + "@outlook.com";
      // size 分级: <50KB = activity-block (MS 快速拒绝页), >=50KB = captcha/form (真实页面)
      const errorType = typeRaw === "fail" ? (s.size < 50_000 ? "activity-block" : "captcha") : null;
      return { name: f, email, type: typeRaw, size: s.size, mtime: s.mtimeMs, errorType };
    }));
    screenshots.sort((a, b) => b.mtime - a.mtime);
    res.json({ success: true, screenshots });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// GET /api/tools/outlook/screenshot-img/:name — 返回截图原始 PNG
router.get("/tools/outlook/screenshot-img/:name", async (req, res) => {
  const { createReadStream, existsSync } = await import("fs");
  const name = req.params.name.replace(/[^a-zA-Z0-9._-]/g, "");
  if (!/^outlook_(fail|ok|err)_[\w.]+\.png$/.test(name)) {
    res.status(400).json({ success: false, error: "invalid name" }); return;
  }
  const fp = "/tmp/" + name;
  if (!existsSync(fp)) { res.status(404).end(); return; }
  res.setHeader("Content-Type", "image/png");
  res.setHeader("Cache-Control", "max-age=1800");
  createReadStream(fp).pipe(res as any);
});

export default router;
