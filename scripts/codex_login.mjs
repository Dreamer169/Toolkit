/**
 * codex_login.mjs — xvfb + headed Chrome 登录 ChatGPT，拿 codex token
 * 运行方式: xvfb-run -a node /data/Toolkit/scripts/codex_login.mjs
 */
import pkg from "/root/otherchannel-pools/airops/node_modules/playwright/index.js";
const { chromium } = pkg;
import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const EMAIL    = process.argv[2] || "enriquetaidzik515218@outlook.com";
const PASSWORD = process.argv[3] || "dokows263493";
const AUTH_PATH = path.join(os.homedir(), ".codex", "auth.json");
const CHROME   = "/data/cache/ms-playwright/chromium-1228/chrome-linux64/chrome";
const TIMEOUT  = 60_000;

let captured = null;

async function main() {
  // 有头模式 — xvfb 提供虚拟显示器，Cloudflare 无法区分真实浏览器
  const browser = await chromium.launch({
    executablePath: CHROME,
    headless: false,          // ← 关键：有头模式
    args: [
      "--no-sandbox",
      "--disable-setuid-sandbox",
      "--disable-dev-shm-usage",
      "--window-size=1280,800",
    ],
  });

  const ctx = await browser.newContext({
    userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    viewport: { width: 1280, height: 800 },
    locale: "en-US",
  });
  const page = await ctx.newPage();

  // 拦截 auth.openai.com/oauth/token 响应
  page.on("response", async (resp) => {
    if (captured) return;
    const url = resp.url();
    if (url.includes("auth.openai.com") && url.includes("token")) {
      try {
        const body = await resp.json().catch(() => null);
        if (body?.access_token && body?.refresh_token) {
          captured = body;
          console.error("[TOKEN] captured from:", url);
        }
      } catch {}
    }
  });

  // Step 1: 打开 authorize URL（codex client_id）
  console.error("[1] Opening authorize URL...");
  const params = new URLSearchParams({
    client_id: "app_EMoamEEZ73f0CkXaXp7hrann",
    redirect_uri: "http://localhost:1455/auth/callback",
    scope: "openid profile email offline_access",
    response_type: "code",
    response_mode: "query",
    state: "codex_login_" + Date.now(),
    nonce: Math.random().toString(36).slice(2),
    code_challenge: "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    code_challenge_method: "S256",
    prompt: "login",
  });
  await page.goto(`https://auth.openai.com/authorize?${params}`, {
    waitUntil: "domcontentloaded", timeout: TIMEOUT,
  });

  // 截图确认页面加载
  await page.screenshot({ path: "/tmp/codex_step1.png" });
  console.error("[1] Screenshot saved: /tmp/codex_step1.png");
  await page.waitForTimeout(2000);

  // Step 2: 输入邮箱
  console.error("[2] Entering email...");
  try {
    const emailSel = 'input[type="email"], input[name="username"], input[id*="email"], input[autocomplete*="email"]';
    await page.waitForSelector(emailSel, { timeout: 20000 });
    await page.fill(emailSel, EMAIL);
    await page.screenshot({ path: "/tmp/codex_step2.png" });
    await page.waitForTimeout(500);
    // 点 Continue
    const btn = page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Next")').first();
    await btn.click();
    await page.waitForTimeout(2000);
  } catch (e) {
    await page.screenshot({ path: "/tmp/codex_step2_err.png" });
    console.error("[2] Error:", e.message);
  }

  // Step 3: 输入密码
  console.error("[3] Entering password...");
  try {
    await page.waitForSelector('input[type="password"]', { timeout: 20000 });
    await page.fill('input[type="password"]', PASSWORD);
    await page.screenshot({ path: "/tmp/codex_step3.png" });
    await page.waitForTimeout(500);
    const btn = page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Sign in")').first();
    await btn.click();
  } catch (e) {
    await page.screenshot({ path: "/tmp/codex_step3_err.png" });
    console.error("[3] Error:", e.message);
  }

  // Step 4: 等待登录完成 + token 被拦截
  console.error("[4] Waiting for token...");
  const deadline = Date.now() + 60000;
  while (!captured && Date.now() < deadline) {
    await page.waitForTimeout(1500);
    const url = page.url();
    console.error("[4] URL:", url.slice(0, 80));
    await page.screenshot({ path: "/tmp/codex_step4.png" });

    // 登录成功后落到 localhost 1455（会连接失败但 URL 含 code）
    if (url.includes("localhost:1455") || url.includes("code=")) {
      console.error("[4] Got callback URL:", url.slice(0, 120));
      // 手动 exchange code
      const urlObj = new URL(url.replace("http://localhost:1455", "http://localhost:1455"));
      const code = urlObj.searchParams.get("code");
      if (code) {
        console.error("[4] Exchanging code:", code.slice(0, 20), "...");
        // 在页面内用 fetch 换 token（走 browser context）
        try {
          const result = await page.evaluate(async ([c]) => {
            const r = await fetch("https://auth.openai.com/oauth/token", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                client_id: "app_EMoamEEZ73f0CkXaXp7hrann",
                grant_type: "authorization_code",
                code: c,
                redirect_uri: "http://localhost:1455/auth/callback",
                code_verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
              }),
            });
            return { status: r.status, body: await r.json().catch(() => null) };
          }, [code]);
          console.error("[4] Token exchange:", result.status, JSON.stringify(result.body || {}).slice(0, 200));
          if (result.body?.access_token) captured = result.body;
        } catch (e) { console.error("[4] exchange error:", e.message); }
      }
      break;
    }

    if (url.includes("chatgpt.com") && !url.includes("auth/login") && !url.includes("auth/authorize")) {
      console.error("[4] Reached chatgpt.com, fetching session...");
      try {
        const sess = await page.evaluate(async () => {
          const r = await fetch("/api/auth/session", { credentials: "include" });
          return r.json();
        });
        console.error("[4] session:", JSON.stringify(sess || {}).slice(0, 300));
        if (sess?.accessToken) captured = { access_token: sess.accessToken, source: "chatgpt_session" };
      } catch (e) { console.error("[4] session error:", e.message); }
      break;
    }
  }

  await page.screenshot({ path: "/tmp/codex_final.png" });
  await browser.close();

  if (!captured?.access_token) {
    console.error("[FAIL] No token captured. Check screenshots in /tmp/codex_step*.png");
    process.exit(1);
  }

  const authData = {
    auth_mode: "chatgptAuthTokens",
    access_token: captured.access_token,
    refresh_token: captured.refresh_token || "",
    id_token: captured.id_token || "",
    client_id: "app_EMoamEEZ73f0CkXaXp7hrann",
  };
  fs.mkdirSync(path.dirname(AUTH_PATH), { recursive: true });
  fs.writeFileSync(AUTH_PATH, JSON.stringify(authData, null, 2) + "\n");
  console.error("[OK] Written to", AUTH_PATH);
  console.log(JSON.stringify({ ok: true, email: EMAIL, hasRefreshToken: !!authData.refresh_token }));
}

main().catch(e => { console.error("[ERROR]", e.message); process.exit(1); });
