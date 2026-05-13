import { logger } from "../lib/logger.js";
import { PersistenceManager } from "../lib/persistence-manager.js";
import { jobQueue } from "../lib/job-queue.js";
import { setLiveVerifyEnabled, getLiveVerifyStatus, incRegBusy, decRegBusy } from "../lib/live-verify-poller.js";
import { microsoftFetch, getMicrosoftProxyEnv, pickProxyForAccount, resolveAccountProxy } from "../lib/proxy-fetch.js";
import { Router, type IRouter } from "express";
import { createHash, randomBytes, randomUUID } from "crypto";
import { execute, query, queryOne } from "../db.js";
import { existsSync } from "fs";
import { execFile as _execFile, spawn } from "child_process";
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
        scope: FULL_GRAPH_SCOPE,
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
        scope: FULL_GRAPH_SCOPE,
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
  const SCOPE = FULL_GRAPH_SCOPE;
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
  const { accountIds, filter, mode } = req.body as {
    accountIds?: number[];
    filter?: "needs_oauth_manual";
    mode?: "device_code" | "auto_browser";   // auto_browser = ex-reauth-manual behaviour
  };

  // ── mode=auto_browser: spawn Python auto-complete (absorbs reauth-manual) ──
  if (mode === "auto_browser") {
    try {
      cleanOldBatchSessions();
      const { query: dbQAb, execute: dbEAb } = await import("../db.js");
      const tagFlt = filter === "needs_oauth_manual"
        ? `AND COALESCE(tags,'') LIKE '%needs_oauth_manual%'
           AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'
           AND status NOT IN ('suspended','needs_oauth_pending')`
        : "AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')";
      let abRows: { id: number; email: string; password: string }[];
      if (accountIds?.length) {
        abRows = await dbQAb<{ id: number; email: string; password: string }>(
          `SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE platform='outlook' AND id = ANY($1::int[]) AND password IS NOT NULL AND password != '' ${tagFlt}`,
          [accountIds]
        );
      } else {
        abRows = await dbQAb<{ id: number; email: string; password: string }>(
          `SELECT id, email, COALESCE(password,'') AS password FROM accounts WHERE platform='outlook' AND password IS NOT NULL AND password != '' ${tagFlt} ORDER BY updated_at ASC LIMIT 10`
        );
      }
      if (!abRows.length) { res.json({ success: false, error: "没有符合条件的账号（需有密码）" }); return; }
      if (filter === "needs_oauth_manual") {
        for (const r of abRows) {
          await dbEAb(
            "UPDATE accounts SET token=NULL, refresh_token=NULL, status='needs_oauth_pending', updated_at=NOW() WHERE id=$1",
            [r.id]
          );
        }
      }
      const abPayload = abRows.map(r => ({
        accountId: r.id, email: r.email, password: r.password,
        userCode: "", deviceCode: "",
        dbUrl: process.env.DATABASE_URL || "postgresql://postgres:postgres@localhost/toolkit",
        removeTag: filter === "needs_oauth_manual" ? "needs_oauth_manual" : undefined,
      }));
      const abLogPath = `/tmp/dc_auto_${Date.now()}.log`;
      const { spawn: spAb } = await import("child_process");
      const { openSync: osAb } = await import("fs");
      const abScript = new URL("../auto_device_code.py", import.meta.url).pathname;
      const abFd = osAb(abLogPath, "a");
      const abProc = spAb("python3", [abScript, JSON.stringify(abPayload), ""], {
        detached: true, stdio: ["ignore", abFd, abFd],
        env: { ...(process.env as Record<string,string>), PYTHONUNBUFFERED: "1" },
      });
      abProc.unref();
      res.json({ success: true, logFile: abLogPath, accounts: abPayload.map(x => ({ accountId: x.accountId, email: x.email })) });
      return;
    } catch (e: unknown) {
      res.status(500).json({ success: false, error: String(e) });
      return;
    }
  }
  try {
    cleanOldBatchSessions();
    const { query: dbQ } = await import("../db.js");

    // filter="needs_oauth_manual": 只查有此标签账号，用于批量重授权已标记账号
    const tagFilter = filter === "needs_oauth_manual"
      ? " AND COALESCE(tags,'') LIKE '%needs_oauth_manual%'"
      : "";
    let rows: { id: number; email: string }[];
    if (accountIds?.length) {
      rows = await dbQ<{ id: number; email: string }>(
        `SELECT id, email FROM accounts WHERE platform='outlook' AND id = ANY($1::int[]) AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')${tagFilter}`,
        [accountIds]
      );
    } else {
      rows = await dbQ<{ id: number; email: string }>(
        `SELECT id, email FROM accounts WHERE platform='outlook' AND (token IS NULL OR token='') AND (refresh_token IS NULL OR refresh_token='')${tagFilter}`
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


// ── IMAP IDLE 守护进程路由 ────────────────────────────────────────────────────
// imap_idle_daemon.py: 为有 token 的账号维持 IMAP IDLE 连接，实时捕获新邮件

let _idleDaemonPid: number | null = null;

router.post("/tools/outlook/imap-idle/start", async (req, res) => {
  try {
    if (_idleDaemonPid) {
      try { process.kill(_idleDaemonPid, 0); res.json({ success: true, already: true, pid: _idleDaemonPid }); return; } catch { _idleDaemonPid = null; }
    }
    const { spawn: spId } = await import("child_process");
    const { openSync: osId } = await import("fs");
    const daemonScript = new URL("../imap_idle_daemon.py", import.meta.url).pathname;
    const logFd = osId("/tmp/imap_idle_daemon.log", "a");
    const daemon = spId("python3", [daemonScript], {
      detached: true,
      stdio: ["ignore", logFd, logFd],
      env: { ...(process.env as Record<string,string>), PYTHONUNBUFFERED: "1" },
    });
    daemon.unref();
    _idleDaemonPid = daemon.pid ?? null;
    res.json({ success: true, pid: _idleDaemonPid });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.post("/tools/outlook/imap-idle/stop", async (req, res) => {
  try {
    if (!_idleDaemonPid) { res.json({ success: true, msg: "未运行" }); return; }
    try { process.kill(_idleDaemonPid, "SIGTERM"); } catch { /* already gone */ }
    const pid = _idleDaemonPid;
    _idleDaemonPid = null;
    res.json({ success: true, stoppedPid: pid });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/outlook/imap-idle/status", async (req, res) => {
  try {
    const { readFileSync } = await import("fs");
    let running = false;
    if (_idleDaemonPid) {
      try { process.kill(_idleDaemonPid, 0); running = true; } catch { _idleDaemonPid = null; }
    }
    let statusMap: Record<string, unknown> = {};
    try { statusMap = JSON.parse(readFileSync("/tmp/imap_idle_status.json", "utf8")); } catch { /* no file yet */ }
    res.json({ success: true, running, pid: _idleDaemonPid, accounts: statusMap });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

router.get("/tools/outlook/imap-idle/events", async (req, res) => {
  try {
    const { since, limit: limitStr } = req.query as { since?: string; limit?: string };
    const limitN = parseInt(limitStr || "50", 10) || 50;
    const { readFileSync } = await import("fs");
    let events: unknown[] = [];
    try { events = JSON.parse(readFileSync("/tmp/imap_idle_events.json", "utf8")); } catch { /* no file yet */ }
    if (since) {
      events = (events as Array<{ ts: string }>).filter(e => e.ts > since);
    }
    const recent = (events as unknown[]).slice(-limitN);
    res.json({ success: true, events: recent, total: events.length });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ── POST /tools/outlook/enable-imap ─────────────────────────────────────────
// 为单个 Outlook 账号开启 IMAP+POP（enable_imap_v5.py，异步 jobQueue 模式）
// Body: { account_id?: number, email?: string, password?: string, proxy?: string, headless?: boolean }
router.post('/tools/outlook/enable-imap', async (req, res) => {
  try {
    const {
      account_id,
      email,
      password = '',
      proxy = '',
      headless = true,
    } = req.body as {
      account_id?: number;
      email?: string;
      password?: string;
      proxy?: string;
      headless?: boolean;
    };

    if (!account_id && !email) {
      res.status(400).json({ success: false, error: '必须提供 account_id 或 email' });
      return;
    }

    const scriptPath = new URL('../enable_imap_v5.py', import.meta.url).pathname;
    const args: string[] = [];
    if (account_id) {
      args.push('--account-id', String(account_id));
    } else {
      args.push('--email', email ?? '', '--password', password ?? '');
    }
    if (proxy) args.push('--proxy', proxy);
    args.push('--headless', headless ? 'true' : 'false');

    const label = account_id ? String(account_id) : (email ?? 'x').split('@')[0];
    const jobId = `imap5_${label}_${Date.now()}`;
    const job = await jobQueue.create(jobId);

    const { spawn } = await import('child_process');
    const child = spawn('python3', [scriptPath, ...args], {
      env: { ...(process.env as Record<string, string>), PYTHONUNBUFFERED: '1', DISPLAY: ':99' },
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

    void job;
    res.json({ success: true, jobId, message: 'IMAP 开启任务已启动，请轮询 /api/tools/jobs/' + jobId });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── POST /tools/outlook/enable-imap/batch ────────────────────────────────────
// 批量开启 IMAP+POP（enable_imap_v5.py，异步 jobQueue，每账号独立任务）
router.post('/tools/outlook/enable-imap/batch', async (req, res) => {
  try {
    const { ids = [], proxy = '', headless = true, concurrency = 1 } = req.body as {
      ids?: number[];
      proxy?: string;
      headless?: boolean;
      concurrency?: number;
    };
    if (!ids.length) {
      res.status(400).json({ success: false, error: '必须提供 ids 数组' });
      return;
    }

    const scriptPath = new URL('../enable_imap_v5.py', import.meta.url).pathname;
    const { spawn } = await import('child_process');
    const jobIds: string[] = [];

    // 按 concurrency 分批并发（默认 1 = 串行，防止多浏览器抢代理）
    const batchSize = Math.max(1, Math.min(concurrency, 3));
    for (let i = 0; i < ids.length; i += batchSize) {
      const batch = ids.slice(i, i + batchSize);
      await Promise.all(batch.map(async (aid) => {
        const jobId = `imap5_${aid}_${Date.now()}`;
        jobIds.push(jobId);
        const job = await jobQueue.create(jobId);
        void job;
        const args = ['--account-id', String(aid), '--headless', headless ? 'true' : 'false'];
        if (proxy) args.push('--proxy', proxy);
        const child = spawn('python3', [scriptPath, ...args], {
          env: { ...(process.env as Record<string, string>), PYTHONUNBUFFERED: '1', DISPLAY: ':99' },
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
        // wait for this account to finish before next batch slot
        await new Promise<void>((resolve) => {
          child.on('close', () => resolve());
          setTimeout(resolve, 360_000);
        });
      }));
    }

    res.json({ success: true, jobIds, total: ids.length, message: '批量 IMAP 开启已启动' });
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
// ── 完整工作流 Step 2/2：Outlook 注册主入口 ───────────────────────────────────
// 执行: outlook_register.py → patchright 随机指纹 + CF IP 代理 → Microsoft 账号注册
//       注册成功后自动 OAuth → 获取 refresh_token → 写入 PostgreSQL accounts 表
// 下游: 注册完成的账号由 scripts/unitool_pipeline.py 消费，完成 unitool.ai 注册
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
    workers  = 1,                   // parallel sub-process count
  } = req.body as {
    count?: number; proxy?: string; proxies?: string; headless?: boolean; delay?: number;
    engine?: string; wait?: number; retries?: number; autoProxy?: boolean;
    proxyMode?: string; cfPort?: number; username?: string; password?: string; workers?: number;
  };

  const n   = Math.min(999, Math.max(1, Math.floor(Number(count) || 1)));
  const eng = ["patchright", "playwright"].includes(engine) ? engine : "patchright";
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
  // Auto-recommend workers when not explicitly set (workers==1 default)
  // Hard cap: 16 (memory permitting; each Chrome ~500MB, 16 workers = ~8GB)
  let resolvedWorkers = Math.min(16, Math.max(1, Math.floor(Number(workers) || 1)));
  if (resolvedWorkers === 1 && n >= 2) {
    // Auto mode: derive optimal worker count from count + proxy availability
    if (effectiveProxyMode === "cf") {
      // CF pool: auto 6 workers (xray静态端口24个，内存允许时可手动传更高)
      resolvedWorkers = Math.min(6, n);  // 静态端口池6个，每端口独占
    } else if (proxyList.length >= 2) {
      // Named proxy pool: 1 worker per proxy, no arbitrary cap
      resolvedWorkers = Math.min(16, n, proxyList.length);
    }
    // n==1 or single proxy: stay sequential (resolvedWorkers stays 1)
  }
  // ── 内存感知降档 ─────────────────────────────────────────────────────────
  // /proc/meminfo MemAvailable；每 Chrome 约 600MB；不足时自动降 workers 防 OOM
  let effectiveWorkers = resolvedWorkers;
  if (resolvedWorkers > 1) {
    try {
      const { readFileSync: _rfs } = await import("fs");
      const _memRaw   = _rfs("/proc/meminfo", "utf8");
      const _availMat = _memRaw.match(/MemAvailable:\s+(\d+)\s+kB/);
      const _availMB  = _availMat ? Math.floor(parseInt(_availMat[1]) / 1024) : 9999;
      const _memPerW  = 1000;  // Chrome实测~1GB，防OOM
      const _maxByMem = Math.max(1, Math.floor(_availMB / _memPerW));
      if (_maxByMem < resolvedWorkers) {
        job.logs.push({ type: "warn", message: `⚠ 内存感知降档: 空闲${_availMB}MB 不足以支撑${resolvedWorkers}workers(每个~${_memPerW}MB) → 自动降为${_maxByMem}` });
        effectiveWorkers = _maxByMem;
      } else {
        job.logs.push({ type: "log", message: `[mem] 空闲${_availMB}MB OK，${resolvedWorkers}workers需${resolvedWorkers * _memPerW}MB` });
      }
    } catch (_e) { /* /proc/meminfo 不可用时跳过 */ }
  }
  const numWorkers = effectiveWorkers;
  if (numWorkers > 1) {
    args.push("--workers", String(numWorkers));
    job.logs.push({ type: "log", message: `[parallel] auto-parallel: ${numWorkers} workers for ${n} accounts` });
  }
  const _spawnEnv: Record<string, string> = {
    ...process.env as Record<string, string>,
    PYTHONUNBUFFERED: "1",
    PLAYWRIGHT_BROWSERS_PATH: "/data/cache/ms-playwright",
    DISPLAY: process.env.DISPLAY || ":99",
  };
  incRegBusy();
  const child = spawn("python3", args, { env: _spawnEnv });
  jobQueue.setChild(jobId, child);

  // identityMap hoisted here so stdout AND close handlers share it
  const identityMap = new Map<string, {
    access_token: string; refresh_token: string;
    cookies_json: string; fingerprint_json: string;
    user_agent: string; exit_ip: string; proxy_port: number; proxy_formatted: string;
  }>();

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

      // 解析 INLINE_RESULT（每账号成功后立即输出，防OOM崩溃丢失）
      if (t.startsWith("-- INLINE_RESULT --")) {
        try {
          const jsonPart = t.slice("-- INLINE_RESULT --".length).trim();
          const r = JSON.parse(jsonPart) as Record<string, unknown>;
          if (r.success && r.email) {
            const em = String(r.email);
            if (!identityMap.has(em)) {
              identityMap.set(em, {
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
            const already = job.accounts.find(a => a.email === em);
            if (!already) job.accounts.push({ email: em, password: String(r.password ?? "") });
            // 立即持久化该账号（异步，不阻塞）
            (async () => {
              const idn = identityMap.get(em)!;
              try {
                const ar = await queryOne<{id:number}>(
                  `INSERT INTO accounts (platform, email, password, status, token, refresh_token,
                                          cookies_json, fingerprint_json, user_agent, exit_ip, proxy_port, proxy_formatted)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                   ON CONFLICT (platform, email) DO UPDATE SET
                     password=EXCLUDED.password, status=EXCLUDED.status,
                     token=COALESCE(NULLIF(EXCLUDED.token,''), accounts.token),
                     refresh_token=COALESCE(NULLIF(EXCLUDED.refresh_token,''), accounts.refresh_token),
                     cookies_json=COALESCE(NULLIF(EXCLUDED.cookies_json,''), accounts.cookies_json),
                     fingerprint_json=COALESCE(NULLIF(EXCLUDED.fingerprint_json,''), accounts.fingerprint_json),
                     user_agent=COALESCE(NULLIF(EXCLUDED.user_agent,''), accounts.user_agent),
                     exit_ip=COALESCE(NULLIF(EXCLUDED.exit_ip,''), accounts.exit_ip),
                     proxy_port=COALESCE(NULLIF(EXCLUDED.proxy_port,0), accounts.proxy_port),
                     proxy_formatted=COALESCE(NULLIF(EXCLUDED.proxy_formatted,''), accounts.proxy_formatted),
                     updated_at=NOW()
                   RETURNING id`,
                  ["outlook", em, String(r.password ?? ""), "active",
                   idn.access_token||null, idn.refresh_token||null,
                   idn.cookies_json||null, idn.fingerprint_json||null,
                   idn.user_agent||null, idn.exit_ip||null,
                   idn.proxy_port||null,
                   idn.proxy_formatted||(idn.proxy_port?`socks5://127.0.0.1:${idn.proxy_port}`:null)]
                );
                job.logs.push({ type: "success", message: `✅ [inline] 已入库: ${em} id=${ar?.id}` });
              } catch(e) {
                job.logs.push({ type: "warn", message: `⚠ [inline] 入库失败(${em}): ${e}` });
              }
            })();
          }
        } catch {}
        continue;
      }

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
    try {
    // 解析 JSON 结果块
    // v8.20: identityMap declared above (before stdout handler) for cross-handler access
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

    // v9.30 BUG-FIX: stdout 误捕防护 — 只持久化 JSON 确认成功的账号（identityMap 里有的）
    // 根因: 子进程打印 ✅ 行 → stdout handler 立即 push job.accounts →
    //       进程崩溃/超时导致 JSON 块未输出 → identityMap 为空 →
    //       okCount 仍 >0 → DB 写入"幽灵账号" status=active →
    //       auto_device_code.py OAuth → MS 报 not_found → 写 not_found tag
    const confirmedAccounts = job.accounts.filter(acc => identityMap.has(acc.email));
    const okCount = confirmedAccounts.length;

    // ── 持久化到数据库 + 立即 ROPC 自动授权 ────────────────────────────────
    if (okCount > 0) {
      await (async () => {
        const pendingOAuthRows: { id: number; email: string; password: string }[] = [];
        for (const acc of confirmedAccounts) {
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
                  // Pass deviceCode+dbUrl (token exchange inside Python via CF proxy).
                  // Pass cookiesJson so the browser loads the registration session and
                  // skips email/password, going straight to device-confirm → consent.
                  const _idn = identityMap.get(s.email);
                  return { accountId: s.accountId, email: s.email, password: row?.password || '', userCode: s.userCode,
                    deviceCode: s.deviceCode, dbUrl: process.env.DATABASE_URL || 'postgresql://postgres:postgres@localhost/toolkit',
                    cookiesJson: _idn?.cookies_json || '' };
                })
                .filter((x: { password: string }) => x.password);
              if (autoPayload.length > 0) {
                const { spawn: spawnAuto } = await import('child_process');
                const autoScript = new URL('../auto_device_code.py', import.meta.url).pathname;
                // Proxy selection: prefer the per-account CF port used during registration
                // (strict IP consistency); fall back to Python's _pick_cf_proxy() if dead.
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
                // v9.25 FIX: CF VLESS cannot reach Microsoft URLs; use residential SOCKS5
                const RESIDENTIAL_PORTS_FOR_AUTH = [10851, 10853, 10854, 10857, 10859];
                let _autoProxy = _aliveProxyPort > 0 ? `socks5://127.0.0.1:${_aliveProxyPort}` : "";
                let _proxyTag = _aliveProxyPort > 0 ? "per-account-alive" : "residential-fallback";
                if (!_autoProxy) {
                  for (const rp of RESIDENTIAL_PORTS_FOR_AUTH) {
                    if (await _probePort(rp)) { _autoProxy = `socks5://127.0.0.1:${rp}`; break; }
                  }
                }
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
                  // v9.02: persist auto-auth logs after process completes
                  PersistenceManager.save(job).catch(() => {});
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

    // AUTO-LINK: outlook/register -> unitool_pipeline.py
    // DISABLED: chain_v3 (PM2) handles unitool registration autonomously.
    // Enabling this would cause unitool_pipeline.py to conflict with chain_v3
    // by both picking up verify_pending accounts simultaneously.


    job.logs.push({
      type: "done",
      message: `注册任务完成 · 成功 ${okCount} 个 / 共 ${n} 个` + (okCount > 0 ? ` ✅` : ``),
    });
    await jobQueue.finish(jobId, code ?? -1, "done");
    } finally {
      decRegBusy();
    }
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
  if (jobId.startsWith("fw_"))     return { source: "tools", kind: "outlook_full_workflow", title: "Outlook 完整工作流" };
  if (jobId.startsWith("imap5_")) return { source: "tools", kind: "outlook_enable_imap", title: "Outlook IMAP开启" };
  if (jobId.startsWith("reg_")) return { source: "tools", kind: "outlook_register", title: "Outlook 注册" };
  if (jobId.startsWith("curhttp_")) return { source: "tools", kind: "cursor_http_register", title: "Cursor HTTP 注册" };
  if (jobId.startsWith("cur_")) return { source: "tools", kind: "cursor_register", title: "Cursor 注册" };
  if (jobId.startsWith("retoken_")) return { source: "tools", kind: "outlook_retoken", title: "Outlook Retoken" };
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

// ── 完整工作流 Step 1/2：身份 + 指纹 + Outlook 账号信息预生成 ──────────────────
// 调用链: workflow/prepare(Step1) → outlook/register(Step2) → unitool_pipeline.py(下游)
// 说明: 此接口只生成随机身份数据，不执行任何浏览器操作；
//       真正注册 Outlook 账号（patchright+指纹+CF代理+OAuth refresh_token 入库）在 Step2。
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
  AND NOT (host = '127.0.0.1' AND port BETWEEN 10820 AND 10860)
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
      WHEN (host='127.0.0.1' AND port BETWEEN 10820 AND 10860)     THEN 'local_socks5'
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
    if (pool === "local_socks5")   filter = `AND host='127.0.0.1' AND port BETWEEN 10820 AND 10860`;
    if (pool === "subnode_bridge") filter = `AND host='127.0.0.1' AND port BETWEEN 1089 AND 1199`;
    if (!filter) continue;

      const _sq = pool === "local_socks5" ? "status != 'banned'" : ELIGIBLE_SHARED_PROXY_SQL;
    try {
      const rows = await query<{ formatted: string }>(
        `SELECT formatted FROM proxies
         WHERE ${_sq} ${filter}
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
    const search = (req.query.search as string) || "";
    const statusQ = (req.query.status as string) || "";
    const limit = Math.min(parseInt(req.query.limit as string) || 300, 500);
    const offset = parseInt(req.query.offset as string) || 0;

    const conditions: string[] = ["platform='outlook'"];
    const params: unknown[] = [];

    if (search) {
      params.push(`%${search.toLowerCase()}%`);
      conditions.push(`LOWER(email) LIKE $${params.length}`);
    }
    if (statusQ === "active")    conditions.push("status='active'");
    else if (statusQ === "suspended") conditions.push("status='suspended'");
    else if (statusQ === "noauth")    conditions.push("(COALESCE(token,'')='' AND COALESCE(refresh_token,'')='')");
    else if (statusQ === "autofix")   conditions.push("status='needs_oauth' AND COALESCE(tags,'') NOT LIKE '%needs_oauth_manual%'");

    const where = conditions.join(" AND ");

    const countRes = await query<{ count: string }>(
      `SELECT COUNT(*) AS count FROM accounts WHERE ${where}`,
      params
    );
    const total = parseInt(countRes[0]?.count ?? "0");

    const rowParams = [...params, limit, offset];
    const rows = await query<{
      id: number; email: string; password: string | null; token: string | null; refresh_token: string | null; tags: string | null;
      status: string | null; notes: string | null; created_at: string;
    }>(
      `SELECT id, email, password, token, refresh_token, status, notes, tags, created_at
       FROM accounts
       WHERE ${where}
       ORDER BY
         CASE WHEN status='active' THEN 0 ELSE 1 END,
         CASE WHEN COALESCE(refresh_token,'') <> '' OR COALESCE(token,'') <> '' THEN 0 ELSE 1 END,
         updated_at DESC NULLS LAST,
         created_at DESC
       LIMIT $${rowParams.length - 1} OFFSET $${rowParams.length}`,
      rowParams
    );
    res.json({
      success: true,
      total,
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
// ── 统一 Graph API scope（所有 token refresh 均使用此常量）──────────────────
// 来源: outlook_retoken.py + imap_idle_daemon.py 对齐（参考 hrhcode/luoianun）
const FULL_GRAPH_SCOPE = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read https://graph.microsoft.com/IMAP.AccessAsUser.All https://graph.microsoft.com/SMTP.Send";
const ROPC_CID  = "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
const ROPC_SCO  = FULL_GRAPH_SCOPE;

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
            scope: FULL_GRAPH_SCOPE,
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
    const rows = await query<{ id: number; email: string; refresh_token: string | null; status: string | null; tags: string | null }>(
      "SELECT id, email, refresh_token, status, tags FROM accounts WHERE id=$1 AND platform=\'outlook\'", [accountId]
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
          scope: FULL_GRAPH_SCOPE,
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
            scope: FULL_GRAPH_SCOPE,
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

        // AADSTS70000 = service abuse mode: 账号本身被微软封禁，重授权无效，直接标 abuse_mode
        const isServiceAbuse = desc.includes("AADSTS70000") || desc.includes("service abuse");
        if (isServiceAbuse) {
          if (!dry_run) await dbE(
            "UPDATE accounts SET status='suspended', tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'')||',abuse_mode',',')) AS t WHERE trim(t)<>''),','),'')), updated_at=NOW() WHERE id=$1",
            [acc.id]
          );
          purged.push({ id: acc.id, email: acc.email, reason: "AADSTS70000:service_abuse_mode" });
          continue;
        }

        const tokenRevoked = err === "invalid_grant"
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
    // 封禁账号：先用旧 access_token 快速验 Graph，可能是被旧逻辑误打成 suspended
    if (acc.status === "suspended" && (acc.tags ?? "").includes("abuse_mode")) {
      const _abuseTok = acc.token ?? "";
      if (_abuseTok) {
        try {
          const _abuseProxy = await resolveAccountProxy(accountId);
          const _abuseChk = await fetchGraphMessages(_abuseTok, "inbox", 1, undefined, false, _abuseProxy);
          if (_abuseChk.ok) {
            // 旧 token 还能用：恢复 active（误打的 suspended），继续走正常流程
            await execute("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [accountId]);
            acc.status = "active"; // 更新内存里的 acc 供后续流程使用
          } else if (_abuseChk.status === 403) {
            // 403 确认真的封禁，短路
            res.json({ success: false, error: "账号已被微软封禁（API 403），无法读取邮件", via: "blocked" });
            return;
          }
          // 其他错误（401/网络等）说明 token 过期但账号不一定被封，继续走正常流程尝试刷新
        } catch {
          res.json({ success: false, error: "账号已被微软封禁（API封禁），无法读取邮件", via: "blocked" });
          return;
        }
      } else {
        // 无 token，无从验证，直接短路
        res.json({ success: false, error: "账号已被微软封禁（API封禁），无法读取邮件", via: "blocked" });
        return;
      }
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
          scope: FULL_GRAPH_SCOPE,
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
          // 要求 invalid_grant + abuse 关键词，与 auto-check 保持一致
          // AADSTS70000 = 微软 service abuse 封禁; "disabled"/"abuse" 关键词太宽会误判
          const _isAbuse = _errCode === "invalid_grant" &&
            (_errDesc.includes("AADSTS70000") || _errDesc.includes("service abuse"));
          if (_isAbuse) {
            // 只打 abuse_mode 标签，不改 status —— 等 Graph API 返回 403 才设 suspended
            await addAccountTags(accountId, ["abuse_mode"]);
          } else if (_errCode === "invalid_grant") {
            // 普通 token 失效：只打 token_invalid，status 留给 XOAUTH2 耗尽路径处理
            await addAccountTags(accountId, ["token_invalid"]);
          }
        } catch (_te) { /* tag 失败不中断主流程 */ }
        accessToken = acc.token ?? "";
      }
    }

    // ── 辅助：根据 Graph API 错误码更新账号状态（收信时顺手体检）────────────
    const updateStatusFromGraphError = async (httpStatus: number, errCode?: string): Promise<boolean> => {
      try {
        // Graph 403 with "AccessDenied" 可能仅是 IMAP 权限不足，不足以判定微软封禁
        // accountClosed 是账号关闭的明确信号；其余 403 需配合已有 abuse_mode 标签才升级 suspended
        const isAbuse = errCode === "accountClosed" ||
          (httpStatus === 403 && errCode !== "AccessDenied" && errCode !== undefined) ||
          (httpStatus === 403 && (acc.tags ?? "").includes("abuse_mode"));
        const isInvalid = httpStatus === 401 || errCode === "InvalidAuthenticationToken" || errCode === "AuthenticationError";
        if (isAbuse && acc.status !== "suspended") {
          await addAccountTags(accountId, ["abuse_mode"], "suspended");
          req.log?.info({ accountId, httpStatus, errCode }, "[fetch-messages] Graph 返回封禁错误，已更新状态为 suspended");
          return true;
        }
        if (isInvalid) {
          await addAccountTags(accountId, ["token_invalid"]);
          req.log?.info({ accountId, httpStatus, errCode }, "[fetch-messages] Graph 返回 token 失效，已打 token_invalid 标签");
          return true;
        }
      } catch (te) { req.log?.warn({ err: String(te) }, "[fetch-messages] 更新状态失败"); }
      return false;
    };

    // ── 辅助：成功收信后清除历史脏标签（token_invalid / needs_oauth_manual / abuse_mode）──
    // graphOk=true 时额外清 abuse_mode（Graph 200 = 微软未封号，旧误判可安全清除）
    const clearStaleTags = async (graphOk = false): Promise<void> => {
      try {
        const stripList = graphOk
          ? ["token_invalid", "needs_oauth_manual", "abuse_mode"]
          : ["token_invalid", "needs_oauth_manual"];
        const conditions = stripList.map(t => `AND trim(t)<>'${t}'`).join(" ");
        await execute(
          `UPDATE accounts SET tags=(SELECT NULLIF(array_to_string(ARRAY(
             SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,''),',')) AS t
             WHERE trim(t)<>'' ${conditions}
           ),','),'')) WHERE id=$1`,
          [accountId]
        );
      } catch { /* 清标签失败不中断主流程 */ }
    };

    // 有 accessToken → Graph API
    if (accessToken) {
      // "全部邮件"：直接走全局 /me/messages，跨所有文件夹（含垃圾邮件、归档等）
      if (isAllFolder) {
        const allRes = await fetchGraphMessages(accessToken, mailFolder, limit, search, true, acctProxy);
        if (allRes.ok) {
          // Graph 成功：若 status 仍是 suspended（被旧逻辑误打），恢复为 active
          if (acc.status === "suspended") {
            try { await execute("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [accountId]); } catch {}
          }
          await clearStaleTags(true);
          if (allRes.messages.length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
          res.json({ success: true, messages: allRes.messages, count: allRes.messages.length, email: acc.email, via: "graph_all" });
          return;
        }
        // allFolder Graph 失败 → 更新状态；allFolder 无 IMAP 对应，直接返回
        const allStatusUpdated = await updateStatusFromGraphError(allRes.status, allRes.error?.code);
        if (allStatusUpdated) {
          res.json({ success: false, error: "账号 OAuth 授权已失效，状态已自动更新", needsAuth: true, statusUpdated: true });
          return;
        }
      }
      const primary = await fetchGraphMessages(accessToken, mailFolder, limit, search, false, acctProxy);
      if (primary.ok) {
        // Graph 成功：若 status 仍是 suspended（被旧逻辑误打），恢复为 active
        if (acc.status === "suspended") {
          try { await execute("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [accountId]); } catch {}
        }
        await clearStaleTags(true);
        if (primary.messages.length > 0 || mailFolder !== "inbox") {
          if (primary.messages.length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
          res.json({ success: true, messages: primary.messages, count: primary.messages.length, email: acc.email, via: "graph" });
          return;
        }
        // 收件箱为空时再查整个邮箱。历史邮件可能已被微软规则、自动验证或用户操作移动到归档/垃圾/已删除，
        // 只查 mailFolders/inbox 会误显示为"空邮箱"。
        const globalResult = await fetchGraphMessages(accessToken, mailFolder, limit, search, true, acctProxy);
        if (globalResult.ok && globalResult.messages.length > 0) {
          await clearStaleTags(true);
          try { await addAccountTags(accountId, ["inbox_verified"]); } catch {}
          res.json({ success: true, messages: globalResult.messages, count: globalResult.messages.length, email: acc.email, via: "graph_all", folderFallback: true });
          return;
        }
        res.json({ success: true, messages: [], count: 0, email: acc.email, via: "graph", folderFallback: globalResult.ok });
        return;
      }
      // Graph API 失败（token 过期 / 封禁等）→ 先更新状态，再降级 IMAP
      await updateStatusFromGraphError(primary.status, primary.error?.code);
    }

    // ── IMAP 路径（降级）──────────────────────────────────────────────────
    // 优先：XOAUTH2 IMAP（如有 token，与 hrhcode 相同方式）
    // 备用：Basic Auth IMAP（密码，微软已封，仅用于显示实际错误信息）
    if (accessToken) {
      // Graph API 失败但有 token → 尝试 XOAUTH2 IMAP
      const xoauthResult = await fetchViaImap(acc.email, acc.password ?? "", mailFolder, limit, search ?? "", accessToken, acctProxy);
      if (xoauthResult.success) {
        // XOAUTH2 成功：若 status 仍是 suspended，恢复为 active
        if (acc.status === "suspended") {
          try { await execute("UPDATE accounts SET status='active', updated_at=NOW() WHERE id=$1", [accountId]); } catch {}
        }
        await clearStaleTags(false);
        if ((xoauthResult.messages as unknown[]).length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
        res.json({ success: true, messages: xoauthResult.messages, count: (xoauthResult.messages as unknown[]).length, email: acc.email, via: "imap_xoauth2" });
        return;
      }
      // XOAUTH2 失败 → 尝试 login.live.com/oauth20_token.srf 获取 IMAP-专属 token（luoianun 方案）
      if (acc.refresh_token) {
        try {
          const liveR = await microsoftFetch("https://login.live.com/oauth20_token.srf", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token",
              client_id: OAUTH_CLIENT_ID,
              refresh_token: acc.refresh_token,
              // login.live.com 使用 wl.imap scope（非 graph namespace）— luoianun fix
              scope: "wl.imap wl.offline_access",
            }).toString(),
          }, acctProxy);
          const liveTd = await liveR.json() as { access_token?: string; refresh_token?: string };
          if (liveTd.access_token) {
            const liveImapResult = await fetchViaImap(acc.email, acc.password ?? "", mailFolder, limit, search ?? "", liveTd.access_token, acctProxy);
            if (liveImapResult.success) {
              await clearStaleTags(false);
              if ((liveImapResult.messages as unknown[]).length > 0) { try { await addAccountTags(accountId, ["inbox_verified"]); } catch {} }
              if (liveTd.refresh_token) { try { await execute("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3", [liveTd.access_token, liveTd.refresh_token, accountId]); } catch {} }
              res.json({ success: true, messages: liveImapResult.messages, count: (liveImapResult.messages as unknown[]).length, email: acc.email, via: "imap_xoauth2_live" });
              return;
            }
          }
        } catch { /* live.com fallback 失败，继续走原逻辑 */ }
      }
      // XOAUTH2 两端点均失败 → 所有 OAuth 路径耗尽，补打 token_invalid（幂等）
      const _isSuspAbuse = acc.status === "suspended" && (acc.tags ?? "").includes("abuse_mode");
      if (!_isSuspAbuse) {
        try { await addAccountTags(accountId, ["token_invalid"]); } catch {}
        res.json({ success: false, error: "账号 OAuth 授权已失效，请重新授权后即可读取邮件", needsAuth: true, statusUpdated: true });
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
      await clearStaleTags(false);
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
      env: { ...process.env as Record<string,string>, PLAYWRIGHT_BROWSERS_PATH: "/data/cache/ms-playwright", DISPLAY: process.env.DISPLAY || ":99", PYTHONUNBUFFERED: "1" },
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
          scope: FULL_GRAPH_SCOPE,
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
          scope: FULL_GRAPH_SCOPE,
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
          scope: FULL_GRAPH_SCOPE,
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
          scope: FULL_GRAPH_SCOPE,
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
          scope: FULL_GRAPH_SCOPE,
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
              scope: FULL_GRAPH_SCOPE,
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
          // v9.27 SSL-fix: pre-extract verify URL in TS (microsoftFetch handles proxy/SSL correctly)
          // This avoids SSL EOF errors in click_verify_link.py's Python HTTP client
          let _preVerifyUrl = "";
          try {
            const _bodyResp = await microsoftFetch(
              `https://graph.microsoft.com/v1.0/me/messages/${msg.id}?$select=body`,
              { headers: { Authorization: `Bearer ${accessToken}` } }, acctProxy
            );
            if (_bodyResp.ok) {
              const _bd = await _bodyResp.json() as { body?: { content?: string } };
              const _html = (_bd.body?.content ?? "").replace(/&amp;/g, "&");
              const _m = _html.match(/https:\/\/replit\.com\/[^"<>\s]+/) ??
                          _html.match(/https:\/\/reseek\.com\/[^"<>\s]+/);
              if (_m) _preVerifyUrl = _m[0];
            }
          } catch (_ve) { /* fall through — let click_verify_link.py try itself */ }
          const scriptPath = path.resolve(__dirname, "../click_verify_link.py");
          const params = JSON.stringify({ token: accessToken, message_id: _preVerifyUrl ? "" : msg.id, verify_url: _preVerifyUrl });
          const { spawn } = await import("child_process");
          const clickResult = await new Promise<{ success: boolean; verify_url?: string; final_url?: string; title?: string; error?: string }>((resolve) => {
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
          // v9.27 Fix3+4: auto-tag replit_used after verify-link click
          // 'Success! You can now close this window' -> replit_used
          // 'already registered / already exists' -> also replit_used (consumed)
          const _vFinalUrl = (clickResult.final_url ?? "").toLowerCase();
          const _vTitle = (clickResult.title ?? "").toLowerCase();
          const _vIsSuccess = clickResult.success
            || _vTitle.includes("you can now close") || _vTitle.includes("success")
            || _vTitle.includes("verified") || _vTitle.includes("confirmed")
            || _vFinalUrl.includes("signup_success") || _vFinalUrl.includes("email-verified");
          const _vIsAlready = _vTitle.includes("already") || _vTitle.includes("registered")
            || _vFinalUrl.includes("already") || (clickResult.error ?? "").includes("already");
          if (_vIsSuccess || _vIsAlready) {
            const { execute: _tagEx } = await import("../db.js");
            await _tagEx(
              `UPDATE accounts SET tags = CASE WHEN COALESCE(tags,'')='' THEN 'replit_used'
               WHEN tags NOT LIKE '%replit_used%' THEN tags||',replit_used' ELSE tags END,
               updated_at=NOW() WHERE id=$1`,
              [acc.id]
            ).catch(() => {});
            req.log.info({ id: acc.id, email: acc.email, isSuccess: _vIsSuccess, isAlready: _vIsAlready }, "[auto-verify] replit_used tag applied");
          }
          results.push({
            accountId: acc.id, email: acc.email,
            status: clickResult.success ? "clicked" : (_vIsAlready ? "already_used" : "failed"),
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
          scope: FULL_GRAPH_SCOPE,
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
    jobQueue.subscribe('done', (doneJob) => { void (async () => {
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
    })(); });
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


// ── auto-check 核心逻辑 (v9.35: 分批处理全部账号，路由立即返回，4h 定时触发) ────────
let _autoCheckRunning = false;
let _autoCheckStopped = false;  // 用户手动停止标志
let _autoCheckLastStats: { total: number; checked: number; valid: number; needsAuth: number; banned: number; skipped: number; stoppedEarly?: boolean; finishedAt: string | null } = {
  total: 0, checked: 0, valid: 0, needsAuth: 0, banned: 0, skipped: 0, finishedAt: null,
};

async function runAutoCheck(): Promise<void> {
  if (_autoCheckRunning) { logger.info("[auto-check] 上次检测仍在运行，跳过"); return; }
  _autoCheckRunning = true;
  _autoCheckStopped = false;
  const SCOPE = FULL_GRAPH_SCOPE;
  const BATCH = 30;
  const addTag = async (id: number, tag: string, newStatus?: string) => {
    const statusPart = newStatus ? `, status='${newStatus}'` : "";
    await execute(
      `UPDATE accounts SET tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'')||','||$2,',')) AS t WHERE trim(t)<>''),','),'')), updated_at=NOW()${statusPart} WHERE id=$1`,
      [id, tag]
    );
  };
  let checked = 0, valid = 0, needsAuth = 0, banned = 0, skipped = 0;
  try {
    const accounts = await query<{
      id: number; email: string;
      token: string | null; refresh_token: string | null; tags: string | null; status: string;
    }>(
      // auto-repair: 除 active 账号外，同时纳入 suspended+token_invalid 且仍有 refresh_token 的账号
      // （accounts.ts inbox 路径在 refresh 失败时设了 status='suspended'，否则这些账号永远被跳过）
      `SELECT id,email,token,refresh_token,tags,status FROM accounts WHERE platform='outlook' AND ((COALESCE(tags,'') NOT LIKE '%abuse_mode%' AND status='active') OR (status='suspended' AND COALESCE(tags,'') LIKE '%token_invalid%' AND COALESCE(refresh_token,'') <> '')) ORDER BY updated_at ASC`
    );
    // 立即把 total 写入全局 stats，让 status 接口实时可见
    _autoCheckLastStats = { total: accounts.length, checked: 0, valid: 0, needsAuth: 0, banned: 0, skipped: 0, finishedAt: null };
    logger.info({ total: accounts.length }, "[auto-check] 开始检测全部 active 账号");
    for (let i = 0; i < accounts.length; i += BATCH) {
      if (_autoCheckStopped) { logger.info({ checked }, "[auto-check] 用户手动停止"); break; }
      const batch = accounts.slice(i, i + BATCH);
      for (const acc of batch) {
        if (_autoCheckStopped) break;
        checked++;
        if (!acc.refresh_token || acc.refresh_token.length < 20) {
          needsAuth++; await addTag(acc.id, "needs_oauth_manual");
          // 无 RT → healthcheck 每5分钟会自动 device code 补授权（无需额外 spawn）
          continue;
        }
        let accessToken = "";
        try {
          const acctProxy = await resolveAccountProxy(acc.id);
          const tr = await microsoftFetch(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
              body: new URLSearchParams({ grant_type: "refresh_token", client_id: OAUTH_CLIENT_ID,
                refresh_token: acc.refresh_token!, scope: SCOPE }).toString() },
            acctProxy
          );
          const td = await tr.json() as { access_token?: string; refresh_token?: string; error?: string; error_description?: string };
          if (!tr.ok || !td.access_token) {
            const errCode = td.error ?? "";
            const errDesc = td.error_description ?? "";
            // AADSTS70000 = 微软 service abuse 封禁
            // AADSTS70008 = refresh token 正常过期 (NOT abuse) → token_invalid
            const isAbuse = errCode === "invalid_grant" &&
              (errDesc.includes("AADSTS70000") || errDesc.includes("service abuse"));
            if (isAbuse) { banned++; await addTag(acc.id, "abuse_mode", "suspended"); }
            else {
              needsAuth++;
              // 普通 RT 过期：清空失效凭据 + 设 needs_oauth → healthcheck 5min 内自动 device code 重授权
              await execute(
                "UPDATE accounts SET token=NULL, refresh_token=NULL, status='needs_oauth', tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,'')||',token_invalid',',')) AS t WHERE trim(t)<>''),','),'')) WHERE id=$1",
                [acc.id]
              );
            }
            continue;
          }
          accessToken = td.access_token;
          // refresh_token 有效 → token 已刷新成功，同时恢复 status=active（修复 accounts.ts 路径将 token_invalid 账号挂起的问题）
          await execute(
            "UPDATE accounts SET token=$1, refresh_token=$2, status='active', updated_at=NOW() WHERE id=$3",
            [accessToken, td.refresh_token ?? acc.refresh_token, acc.id]
          );
        } catch { skipped++; continue; }
        try {
          const acctProxy2 = await resolveAccountProxy(acc.id);
          const gr = await microsoftFetch(
            "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=1&$select=id",
            { headers: { Authorization: `Bearer ${accessToken}` } },
            acctProxy2
          );
          const gd = await gr.json() as { error?: { code?: string } };
          if (gr.ok) {
            valid++;
            // clear stale bad tags + 恢复 status=active（Graph 收件箱已验通 → 账号完全健康）
            await execute(`UPDATE accounts SET status='active', tags=(SELECT NULLIF(array_to_string(ARRAY(SELECT DISTINCT trim(t) FROM unnest(string_to_array(COALESCE(tags,''),',')) AS t WHERE trim(t)<>'' AND trim(t)<>'token_invalid' AND trim(t)<>'needs_oauth_manual'),','),'')) WHERE id=$1`,[acc.id]);
            await addTag(acc.id, "inbox_verified");
          } else {
            const code = gd.error?.code ?? "";
            if (gr.status === 403 || code === "AccessDenied") { banned++; await addTag(acc.id, "abuse_mode", "suspended"); }
            else { needsAuth++; await addTag(acc.id, "token_invalid"); }
          }
        } catch { skipped++; }
        await new Promise(r => setTimeout(r, 200));
      }
      // 每批结束后实时刷新全局 stats，让 status 接口返回最新进度
      const total = _autoCheckLastStats.total;
      _autoCheckLastStats = { total, checked, valid, needsAuth, banned, skipped, stoppedEarly: _autoCheckStopped, finishedAt: null };
      logger.info({ batch: Math.floor(i / BATCH) + 1, batchSize: batch.length, checked, valid, needsAuth, banned, skipped, stopped: _autoCheckStopped }, "[auto-check] 批次完成");
    }
  } catch (e: unknown) {
    logger.error({ err: String(e) }, "[auto-check] 检测出错");
  } finally {
    _autoCheckRunning = false;
    _autoCheckLastStats = { total: _autoCheckLastStats.total, checked, valid, needsAuth, banned, skipped, stoppedEarly: _autoCheckStopped, finishedAt: new Date().toISOString() };
    logger.info(_autoCheckLastStats, "[auto-check] 全部完成");
  }
}

// 每 4 小时自动触发一次（启动 1 分钟后首次运行）
setTimeout(() => {
  runAutoCheck().catch(() => {});
  setInterval(() => { runAutoCheck().catch(() => {}); }, 4 * 60 * 60 * 1000);
}, 60_000);

// ── POST /tools/outlook/auto-check ──────────────────────────────────────────
router.post("/tools/outlook/auto-check", async (req, res) => {
  try {
    if (_autoCheckRunning) {
      res.json({ success: true, started: false, running: true, message: "检测已在后台运行中，请稍候", stats: _autoCheckLastStats });
      return;
    }
    const totalRow = await query<{ cnt: string }>(
      "SELECT COUNT(*)::text AS cnt FROM accounts WHERE platform='outlook' AND status='active' AND COALESCE(tags,'') NOT LIKE '%abuse_mode%'"
    );
    const total = parseInt(totalRow[0]?.cnt ?? "0", 10);
    runAutoCheck().catch(() => {});
    res.json({ success: true, started: true, running: false, total, message: `已在后台启动检测，共 ${total} 个账号，每批30个` });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
})

// ── GET /tools/outlook/auto-check/status ────────────────────────────────────
router.get("/tools/outlook/auto-check/status", (_req, res) => {
  res.json({
    success: true,
    running: _autoCheckRunning,
    stopped: _autoCheckStopped,
    stats: _autoCheckLastStats,
  });
});

// ── POST /tools/outlook/auto-check/stop ─────────────────────────────────────
router.post("/tools/outlook/auto-check/stop", (_req, res) => {
  if (!_autoCheckRunning) {
    res.json({ success: false, message: "当前没有正在运行的检测" });
    return;
  }
  _autoCheckStopped = true;
  logger.info("[auto-check] 收到停止请求");
  res.json({ success: true, message: "已发送停止信号，当前账号处理完后将停止" });
});


// ── GET /tools/accounts/auto-fix-status ─────────────────────────────────────
// 实时返回 needs_oauth_pending（补授权进行中）和 needs_oauth 非 manual（等待自动修复）账号列表
// 前端每 5s 轮询，覆盖本地账号状态展示，无需整页刷新
router.get("/tools/accounts/auto-fix-status", async (_req, res) => {
  try {
    const [pending, waiting] = await Promise.all([
      query<{ id: number; email: string }>(
        "SELECT id, email FROM accounts WHERE platform=outlook AND status=needs_oauth_pending ORDER BY updated_at DESC"
      ),
      query<{ id: number; email: string }>(
        "SELECT id, email FROM accounts WHERE platform=outlook AND status=needs_oauth AND COALESCE(tags,) NOT LIKE %needs_oauth_manual% ORDER BY updated_at DESC"
      ),
    ]);
    res.json({
      success: true,
      autoCheckRunning: _autoCheckRunning,
      autoCheckStats: _autoCheckLastStats,
      pending: pending.map(a => ({ id: a.id, email: a.email })),
      waiting: waiting.map(a => ({ id: a.id, email: a.email })),
      pendingIds: pending.map(a => a.id),
      waitingIds: waiting.map(a => a.id),
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// CSV 导入 / 导出  (MailPilot 参考实现)
// ─────────────────────────────────────────────────────────────────────────────

/** GET /tools/outlook/export-csv
 *  将 accounts 表 outlook 账号导出为 CSV，列：email,password,token,refresh_token,status,tags
 */
router.get("/tools/outlook/export-csv", async (_req, res) => {
  try {
    const { query } = await import("../db.js");
    const rows = await query<{
      email: string; password: string | null; token: string | null;
      refresh_token: string | null; status: string | null; tags: string | null;
    }>(
      `SELECT email, password, token, refresh_token, status, tags
       FROM accounts WHERE platform='outlook' ORDER BY created_at ASC`
    );

    const esc = (v: string | null) => {
      const s = v ?? "";
      return s.includes(",") || s.includes('"') || s.includes("\n")
        ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const header = "email,password,token,refresh_token,status,tags";
    const lines = rows.map(r =>
      [r.email, r.password, r.token, r.refresh_token, r.status, r.tags].map(esc).join(",")
    );
    const csv = [header, ...lines].join("\r\n");

    res.setHeader("Content-Type", "text/csv; charset=utf-8");
    res.setHeader("Content-Disposition", `attachment; filename="outlook_accounts_${new Date().toISOString().slice(0,10)}.csv"`);
    res.send(csv);
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

/** POST /tools/outlook/import-csv
 *  Body: { csv: string }
 *  解析 CSV（列：email[,password][,token][,refresh_token][,status][,tags]），
 *  UPSERT 到 accounts 表（已存在则更新 password/token/refresh_token/tags，不覆盖 status 为 active）。
 */
router.post("/tools/outlook/import-csv", async (req, res) => {
  const { csv } = req.body as { csv?: string };
  if (!csv || typeof csv !== "string") {
    res.status(400).json({ success: false, error: "csv 字段不能为空" });
    return;
  }
  try {
    const { execute, query: dbQ } = await import("../db.js");
    const lines = csv.replace(/\r/g, "").split("\n").filter(l => l.trim());
    if (!lines.length) { res.json({ success: false, error: "CSV 为空" }); return; }

    // 解析 header
    const parseCsvLine = (line: string): string[] => {
      const result: string[] = [];
      let cur = "", inQuote = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (ch === '"') {
          if (inQuote && line[i+1] === '"') { cur += '"'; i++; }
          else { inQuote = !inQuote; }
        } else if (ch === "," && !inQuote) { result.push(cur); cur = ""; }
        else { cur += ch; }
      }
      result.push(cur);
      return result;
    };

    const headers = parseCsvLine(lines[0]).map(h => h.trim().toLowerCase());
    const emailIdx   = headers.indexOf("email");
    const pwIdx      = headers.indexOf("password");
    const tokenIdx   = headers.indexOf("token");
    const rtIdx      = headers.indexOf("refresh_token");
    const statusIdx  = headers.indexOf("status");
    const tagsIdx    = headers.indexOf("tags");

    if (emailIdx === -1) { res.json({ success: false, error: "CSV 缺少 email 列" }); return; }

    const dataLines = lines.slice(1);
    let inserted = 0, updated = 0, skipped = 0;

    for (const line of dataLines) {
      if (!line.trim()) continue;
      const cols = parseCsvLine(line);
      const email = cols[emailIdx]?.trim().toLowerCase();
      if (!email || !email.includes("@")) { skipped++; continue; }

      const password     = pwIdx     >= 0 ? (cols[pwIdx]?.trim()     || null) : null;
      const token        = tokenIdx  >= 0 ? (cols[tokenIdx]?.trim()  || null) : null;
      const refreshToken = rtIdx     >= 0 ? (cols[rtIdx]?.trim()     || null) : null;
      const status       = statusIdx >= 0 ? (cols[statusIdx]?.trim() || "active") : "active";
      const tags         = tagsIdx   >= 0 ? (cols[tagsIdx]?.trim()   || null) : null;

      // 检查是否已存在
      const exist = await dbQ<{ id: number }>(
        "SELECT id FROM accounts WHERE platform='outlook' AND email=$1", [email]
      );

      if (exist.length) {
        // 更新：只覆盖非空字段
        await execute(
          `UPDATE accounts SET
             password      = COALESCE($1, password),
             token         = COALESCE($2, token),
             refresh_token = COALESCE($3, refresh_token),
             status        = CASE WHEN $4 != '' THEN $4 ELSE status END,
             tags          = COALESCE(NULLIF($5,''), tags),
             updated_at    = NOW()
           WHERE id=$6`,
          [password, token, refreshToken, status, tags, exist[0].id]
        );
        updated++;
      } else {
        await execute(
          `INSERT INTO accounts (platform, email, password, token, refresh_token, status, tags, created_at, updated_at)
           VALUES ('outlook', $1, $2, $3, $4, $5, $6, NOW(), NOW())`,
          [email, password, token, refreshToken, status || "active", tags]
        );
        inserted++;
      }
    }

    res.json({ success: true, total: dataLines.length, inserted, updated, skipped });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// 跨账号批量扫码视图  (MailPilot batch-get-mails + code_extractor)
// ─────────────────────────────────────────────────────────────────────────────

/** POST /tools/outlook/batch-scan-inbox
 *  同时从多个账号拉收件箱，提取验证码汇聚到一个视图。
 *  Body: { accountIds?: number[], limit?: number, search?: string, codeOnly?: boolean }
 *  codeOnly=true 时只返回含验证码的邮件。
 */
router.post("/tools/outlook/batch-scan-inbox", async (req, res) => {
  const { accountIds, limit = 5, search = "", codeOnly = false } = req.body as {
    accountIds?: number[]; limit?: number; search?: string; codeOnly?: boolean;
  };
  try {
    const { query: dbQ, execute: dbE } = await import("../db.js");

    let rows: { id: number; email: string; token: string | null; refresh_token: string | null; password: string | null; status: string | null; tags: string | null }[];
    if (accountIds?.length) {
      rows = await dbQ(
        `SELECT id, email, token, refresh_token, password, status, tags
         FROM accounts WHERE platform='outlook' AND id = ANY($1::int[]) AND status != 'suspended'`,
        [accountIds]
      );
    } else {
      rows = await dbQ(
        `SELECT id, email, token, refresh_token, password, status, tags
         FROM accounts WHERE platform='outlook' AND status != 'suspended'
         AND (token IS NOT NULL AND token != '' OR refresh_token IS NOT NULL AND refresh_token != '' OR password IS NOT NULL AND password != '')
         ORDER BY updated_at DESC LIMIT 30`
      );
    }

    if (!rows.length) { res.json({ success: true, results: [], total: 0 }); return; }

    // 验证码提取（与前端 extractCode 逻辑一致）
    const extractCode = (text: string): string => {
      const m6  = text.match(/\b(\d{6,8})\b/);
      const mAZ = text.match(/\b([A-Z0-9]{6,10})\b/);
      return m6 ? m6[1] : mAZ ? mAZ[1] : "";
    };

    // 单账号拉取（复用 fetch-messages-by-id 的 Graph token 刷新逻辑）
    const OAUTH_CID = process.env.OUTLOOK_CLIENT_ID || "9e5f94bc-e8a4-4e73-b8be-63364c29d753";
    const SCOPE_BASIC = FULL_GRAPH_SCOPE;  // fix: align with FULL_GRAPH_SCOPE

    async function scanOne(acc: typeof rows[0]): Promise<{
      accountId: number; email: string; messages: Array<{ id: string; subject: string; from: string; receivedAt: string; preview: string; code: string }>; error?: string
    }> {
      let accessToken = acc.token ?? "";

      // 尝试刷新 refresh_token
      if (acc.refresh_token) {
        try {
          const tr = await microsoftFetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              grant_type: "refresh_token", client_id: OAUTH_CID,
              refresh_token: acc.refresh_token, scope: SCOPE_BASIC,
            }).toString(),
          });
          const td = await tr.json() as { access_token?: string; refresh_token?: string };
          if (td.access_token) {
            accessToken = td.access_token;
            await dbE("UPDATE accounts SET token=$1, refresh_token=$2, updated_at=NOW() WHERE id=$3",
              [accessToken, td.refresh_token ?? acc.refresh_token, acc.id]);
          }
        } catch { /* ignore refresh error */ }
      }

      if (!accessToken) return { accountId: acc.id, email: acc.email, messages: [], error: "无 token" };

      const topN = Math.min(50, Math.max(1, limit));
      const url = search
        ? `https://graph.microsoft.com/v1.0/me/messages?$top=${topN}&$search="${encodeURIComponent(search)}"&$select=id,subject,from,receivedDateTime,bodyPreview&$orderby=receivedDateTime desc`
        : `https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages?$top=${topN}&$select=id,subject,from,receivedDateTime,bodyPreview&$orderby=receivedDateTime desc`;

      try {
        const gr = await microsoftFetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
        if (!gr.ok) {
          const ed = await gr.json() as { error?: { message?: string } };
          return { accountId: acc.id, email: acc.email, messages: [], error: ed.error?.message ?? `HTTP ${gr.status}` };
        }
        const gd = await gr.json() as { value?: Array<{ id: string; subject?: string; from?: { emailAddress?: { address?: string } }; receivedDateTime?: string; bodyPreview?: string }> };
        const msgs = (gd.value ?? []).map(m => {
          const preview = m.bodyPreview ?? "";
          const subject = m.subject ?? "";
          const code = extractCode(preview + " " + subject);
          return { id: m.id, subject, from: m.from?.emailAddress?.address ?? "", receivedAt: m.receivedDateTime ?? "", preview: preview.slice(0, 200), code };
        });
        const filtered = codeOnly ? msgs.filter(m => m.code) : msgs;
        return { accountId: acc.id, email: acc.email, messages: filtered };
      } catch (e: unknown) {
        return { accountId: acc.id, email: acc.email, messages: [], error: String(e).slice(0, 80) };
      }
    }

    // 并发 5 个账号同时扫（MailPilot 方案）
    const CONCURRENCY = 5;
    const results: Awaited<ReturnType<typeof scanOne>>[] = [];
    for (let i = 0; i < rows.length; i += CONCURRENCY) {
      const batch = rows.slice(i, i + CONCURRENCY);
      const settled = await Promise.allSettled(batch.map(scanOne));
      for (const r of settled) {
        results.push(r.status === "fulfilled" ? r.value : { accountId: 0, email: "?", messages: [], error: String((r as PromiseRejectedResult).reason) });
      }
    }

    const total = results.reduce((s, r) => s + r.messages.length, 0);
    const codesFound = results.reduce((s, r) => s + r.messages.filter(m => m.code).length, 0);

    res.json({ success: true, results, total, codesFound, scanned: rows.length });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── POST /tools/outlook/full-workflow ────────────────────────────────────────
// 完整 Outlook 工作流：CF IP 注册 + OAuth + IMAP+POP 开启（IP一致性保证）
// 注册阶段：CF VLESS tunnel（保证注册/OAuth同一IP）
// IMAP阶段：ISP 端口（enable_imap_v5内部_find_isp_proxy自动选，MS SPA渲染需要）
// Body: { count?, email?, password?, proxy?, headless?, skip_imap?, delay?, output? }
router.post('/tools/outlook/full-workflow', async (req, res) => {
  try {
    const {
      count = 1,
      email = '',
      password = '',
      proxy = '',
      headless = true,
      skip_imap = false,
      delay = 8,
      output = '',
    } = req.body as {
      count?: number;
      email?: string;
      password?: string;
      proxy?: string;
      headless?: boolean;
      skip_imap?: boolean;
      delay?: number;
      output?: string;
    };

    const scriptPath = new URL('../outlook_full_workflow.py', import.meta.url).pathname;
    const args: string[] = [
      '--count', String(Math.max(1, Math.min(count, 20))),
      '--headless', headless ? 'true' : 'false',
      '--delay', String(delay),
    ];
    if (email)     args.push('--email',    email);
    if (password)  args.push('--password', password);
    if (proxy)     args.push('--proxy',    proxy);
    if (skip_imap) args.push('--skip-imap');
    if (output)    args.push('--output',   output);

    const jobId = `fw_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    const job = await jobQueue.create(jobId);
    void job;

    const { spawn } = await import('child_process');
    const child = spawn('python3', [scriptPath, ...args], {
      env: {
        ...(process.env as Record<string, string>),
        PYTHONUNBUFFERED: '1',
        DISPLAY: ':99',
        DATABASE_URL: process.env.DATABASE_URL ?? 'postgresql://postgres:postgres@localhost/toolkit',
      },
      cwd: new URL('..', import.meta.url).pathname,
    });
    jobQueue.setChild(jobId, child);

    child.stdout?.on('data', (d: Buffer) => {
      d.toString().split('\n').filter(Boolean).forEach((line: string) => {
        jobQueue.pushLog(jobId, { type: 'log', message: line });
        // parse account from JSON summary line
        try {
          const parsed = JSON.parse(line) as Array<{ email?: string; password?: string; success?: boolean; imap_enabled?: boolean }>;
          if (Array.isArray(parsed)) {
            for (const r of parsed) {
              if (r.email && r.success) {
                jobQueue.pushAccount(jobId, { email: r.email, password: r.password ?? '' });
              }
            }
          }
        } catch { /* not JSON */ }
      });
    });
    child.stderr?.on('data', (d: Buffer) =>
      d.toString().split('\n').filter(Boolean).forEach((l: string) =>
        jobQueue.pushLog(jobId, { type: 'log', message: '[err] ' + l })));
    child.on('close', (code: number | null) =>
      jobQueue.finish(jobId, code ?? -1, code === 0 ? 'done' : 'failed'));

    res.json({
      success: true,
      jobId,
      message: `完整工作流已启动（count=${count}, skip_imap=${skip_imap}），轮询 /api/tools/jobs/${jobId}`,
    });
  } catch (e: unknown) {
    res.status(500).json({ success: false, error: String(e) });
  }
});

// ── GET /tools/outlook/full-workflow/:jobId ───────────────────────────────────
router.get('/tools/outlook/full-workflow/:jobId', async (req, res) => {
  const job = await jobQueue.get(req.params.jobId);
  if (!job) { res.status(404).json({ success: false, error: '任务不存在' }); return; }
  const since = Number(req.query.since ?? 0);
  res.json({
    success: true,
    jobId: job.jobId,
    status: job.status,
    accounts: job.accounts,
    logs: job.logs.slice(since),
    nextSince: job.logs.length,
    exitCode: job.exitCode,
  });
});

// ── DELETE /tools/outlook/full-workflow/:jobId ────────────────────────────────
router.delete('/tools/outlook/full-workflow/:jobId', (req, res) => {
  const stopped = jobQueue.stop(req.params.jobId);
  if (!stopped) { res.status(404).json({ success: false }); return; }
  res.json({ success: true });
});


// ── GET /tools/unitool/token-stats — 触发 unitool_token_stats.py 刷新缓存 ──
// v5.40: 此路由之前缺失，导致监控页"刷新 token 余额"按钮无效
router.get("/tools/unitool/token-stats", async (req, res) => {
  const forceRefresh = req.query.refresh === "1";
  try {
    const { readFileSync, existsSync } = await import("fs");
    const CACHE = "/tmp/unitool_token_cache.json";
    // 如果只是读缓存
    if (!forceRefresh) {
      if (existsSync(CACHE)) {
        const raw = JSON.parse(readFileSync(CACHE, "utf8"));
        const vals = Object.values(raw) as Array<{ regular?: number; bonus?: number; ts?: number }>;
        const total_regular = vals.reduce((s, v) => s + Math.max(0, v.regular ?? 0), 0);
        const total_bonus   = vals.reduce((s, v) => s + Math.max(0, v.bonus   ?? 0), 0);
        return res.json({ success: true, cached: true, accounts: vals.length, total_regular, total_bonus });
      }
      return res.json({ success: false, error: "no cache" });
    }
    // 后台触发刷新脚本（每次最多刷新 100 条避免超时）
    const { spawn } = await import("child_process");
    const child = spawn("python3", [
      "/data/Toolkit/scripts/unitool_token_stats.py",
      "--refresh", "--limit", "100"
    ], { detached: true, stdio: "ignore" });
    child.unref();
    res.json({ success: true, triggered: true, pid: child.pid });
  } catch (e) {
    res.status(500).json({ success: false, error: String(e) });
  }
});


// ── 邮件池健康检测：批量 IMAP 验证 active Outlook 账号 ─────────────────────────
const _EMAILS_DB = "/data/Toolkit/artifacts/api-server/data.db";

function _sqliteExecEmails(sql: string, params: (string | number | null)[] = []): Promise<void> {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { execFile: _ef } = (require as NodeRequire)("child_process") as typeof import("child_process");
  return new Promise((resolve) => {
    const script = "import sqlite3,json,sys\ndb=sqlite3.connect(sys.argv[1])\ndb.execute(sys.argv[2],json.loads(sys.argv[3]))\ndb.commit()\ndb.close()";
    _ef("python3", ["-c", script, _EMAILS_DB, sql, JSON.stringify(params)], { timeout: 8000 }, () => resolve());
  });
}

function _sqliteQueryEmails(sql: string, params: (string | number)[] = []): Promise<Record<string, unknown>[]> {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const { execFile: _ef } = (require as NodeRequire)("child_process") as typeof import("child_process");
  return new Promise((resolve) => {
    const script = "import sqlite3,json,sys\ndb=sqlite3.connect(sys.argv[1])\ndb.row_factory=sqlite3.Row\ncur=db.execute(sys.argv[2],json.loads(sys.argv[3]))\nrows=[dict(r) for r in cur.fetchall()]\ndb.close()\nprint(json.dumps(rows))";
    _ef("python3", ["-c", script, _EMAILS_DB, sql, JSON.stringify(params)], { timeout: 10000 }, (err, stdout) => {
      if (err) { console.error("[emails-pool-check] sqlite ERR:", String(err)); resolve([]); return; }
      try { resolve(JSON.parse(stdout.trim())); } catch { resolve([]); }
    });
  });
}

let _emailsPoolCheckRunning = false;
let _emailsPoolCheckStats: {
  total: number; checked: number; valid: number; suspended: number; other: number; finishedAt: string | null; running: boolean;
} = { total: 0, checked: 0, valid: 0, suspended: 0, other: 0, finishedAt: null, running: false };

async function _runEmailsPoolCheck(batchConcurrency = 8): Promise<void> {
  if (_emailsPoolCheckRunning) return;
  _emailsPoolCheckRunning = true;
  _emailsPoolCheckStats = { total: 0, checked: 0, valid: 0, suspended: 0, other: 0, finishedAt: null, running: true };
  try {
    const rows = await _sqliteQueryEmails(
      "SELECT id, email, password FROM emails WHERE platform='outlook' AND status='active' ORDER BY last_checked_at ASC"
    ) as Array<{ id: number; email: string; password: string }>;
    _emailsPoolCheckStats.total = rows.length;
    console.log("[emails-pool-check] 开始检测 active outlook total=", rows.length);
    for (let i = 0; i < rows.length; i += batchConcurrency) {
      const batch = rows.slice(i, i + batchConcurrency);
      await Promise.all(batch.map(async (acc) => {
        const now = new Date().toISOString();
        try {
          const result = await imapCheckLogin(acc.email, acc.password ?? "");
          _emailsPoolCheckStats.checked++;
          if (result.ok) {
            _emailsPoolCheckStats.valid++;
            await _sqliteExecEmails("UPDATE emails SET last_checked_at=? WHERE id=?", [now, acc.id]);
          } else {
            const errMsg = (result.error ?? "").toLowerCase();
            const isBanned = /invalid.credentials|authentication.failed|incorrect.password|suspended|banned|account.lock|access.denied|disabled/i.test(errMsg);
            if (isBanned) {
              _emailsPoolCheckStats.suspended++;
              await _sqliteExecEmails(
                "UPDATE emails SET status='suspended', ban_reason=?, last_checked_at=?, updated_at=? WHERE id=?",
                [(result.error ?? "imap_auth_failed").slice(0, 200), now, now, acc.id]
              );
            } else {
              _emailsPoolCheckStats.other++;
              await _sqliteExecEmails("UPDATE emails SET last_checked_at=? WHERE id=?", [now, acc.id]);
            }
          }
        } catch (e: unknown) {
          _emailsPoolCheckStats.other++;
          console.warn("[emails-pool-check] 单账号异常", acc.email, String(e));
        }
      }));
      if (i % (batchConcurrency * 10) === 0) {
        console.log("[emails-pool-check] 进度 batch=", Math.floor(i / batchConcurrency) + 1, _emailsPoolCheckStats);
      }
      await new Promise(r => setTimeout(r, 300));
    }
  } catch (e: unknown) {
    console.error("[emails-pool-check] 异常", String(e));
  } finally {
    _emailsPoolCheckRunning = false;
    _emailsPoolCheckStats.running = false;
    _emailsPoolCheckStats.finishedAt = new Date().toISOString();
    console.log("[emails-pool-check] 完成", _emailsPoolCheckStats);
  }
}

// POST /tools/outlook/emails-pool-check  — 启动后台检测
router.post("/tools/outlook/emails-pool-check", (_req, res) => {
  if (_emailsPoolCheckRunning) {
    res.json({ success: true, started: false, running: true, stats: _emailsPoolCheckStats });
    return;
  }
  _runEmailsPoolCheck().catch((e) => console.error("[emails-pool-check] uncaught", String(e)));
  res.json({ success: true, started: true, message: "邮件池 IMAP 健康检测已在后台启动" });
});

// GET /tools/outlook/emails-pool-check/status  — 查询进度
router.get("/tools/outlook/emails-pool-check/status", (_req, res) => {
  res.json({ success: true, stats: _emailsPoolCheckStats });
});




export default router;
