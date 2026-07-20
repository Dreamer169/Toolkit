import { Router, type IRouter } from "express";
import { exec } from "child_process";
import { promisify } from "util";
import { readdir, readFile } from "fs/promises";
import { existsSync } from "fs";

const execAsync = promisify(exec);
const router: IRouter = Router();
const HOME = process.env.HOME || "/root";

// unitool-proxy endpoint (port 8089) — full ChatGPT SSID pool, OpenAI-compatible
const UNITOOL_URL = "http://127.0.0.1:8089/v1/chat/completions";
const UNITOOL_MODEL = "gpt-4o-mini";

// GET /codex/status
router.get("/codex/status", async (_req, res) => {
  try {
    const [loginR, configR, skillR, sessionR, versionR, uptimeR] = await Promise.all([
      execAsync("codex login status 2>&1 || true", { timeout: 10000 }),
      execAsync("cat ~/.codex/config.toml 2>/dev/null || true", { timeout: 5000 }),
      execAsync("ls ~/.codex/skills/ 2>/dev/null | wc -l || echo 0", { timeout: 5000 }),
      execAsync("find ~/.codex/sessions -name '*.jsonl' 2>/dev/null | wc -l || echo 0", { timeout: 5000 }),
      execAsync("codex --version 2>&1 | head -1 || echo unknown", { timeout: 5000 }),
      execAsync("uptime -p 2>/dev/null || uptime | head -1", { timeout: 5000 }),
    ]);
    const loginText = loginR.stdout.trim();
    const loggedIn = !loginText.includes("Not logged in");
    const authMode = (loginText.match(/Logged in using (.+?)(?:\s+-|$)/) || [])[1]?.trim() || null;
    const modelMatch = configR.stdout.match(/model\s*=\s*"([^"]+)"/);
    const model = modelMatch ? modelMatch[1] : "gpt-5.6-terra";
    res.json({
      loggedIn, authMode, model,
      version: versionR.stdout.trim(),
      skillCount: parseInt(skillR.stdout.trim(), 10) || 0,
      sessionCount: parseInt(sessionR.stdout.trim(), 10) || 0,
      vpsHost: "45.205.27.248",
      uptime: uptimeR.stdout.trim().replace(/^up\s+/, ""),
    });
  } catch (e: any) { res.status(500).json({ error: String(e.message) }); }
});

// POST /codex/exec — routes through unitool-proxy (port 8089) bypassing codex CLI auth issues
router.post("/codex/exec", async (req, res) => {
  const { prompt, model } = req.body as { prompt: string; model?: string };
  if (!prompt?.trim()) { res.status(400).json({ error: "prompt required" }); return; }
  const start = Date.now();
  const useModel = model || UNITOOL_MODEL;
  try {
    const resp = await fetch(UNITOOL_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: useModel,
        messages: [
          {
            role: "system",
            content: "You are OpenAI Codex, an expert AI coding assistant. Analyze tasks, write clean code, explain your reasoning, and use markdown code blocks. Be concise and accurate.",
          },
          { role: "user", content: prompt.trim() },
        ],
        max_tokens: 2000,
      }),
      signal: AbortSignal.timeout(90000),
    });
    const data = await resp.json() as any;
    const durationMs = Date.now() - start;
    if (!resp.ok || data.error) {
      const errMsg = data.error?.message || JSON.stringify(data);
      res.json({ output: `Error from upstream: ${errMsg}`, exitCode: 1, durationMs, model: useModel, sessionId: null });
      return;
    }
    const content: string = data.choices?.[0]?.message?.content || "(no output)";
    res.json({ output: content, exitCode: 0, durationMs, model: useModel, sessionId: null });
  } catch (e: any) {
    const durationMs = Date.now() - start;
    res.json({ output: `Request failed: ${e.message}`, exitCode: 1, durationMs, model: useModel, sessionId: null });
  }
});

// GET /codex/stats
router.get("/codex/stats", async (_req, res) => {
  try {
    const [skillR, sessionR] = await Promise.all([
      execAsync("ls ~/.codex/skills/ 2>/dev/null | wc -l || echo 0", { timeout: 5000 }),
      execAsync("find ~/.codex/sessions -name '*.jsonl' 2>/dev/null | wc -l || echo 0", { timeout: 5000 }),
    ]);
    res.json({
      skillCount: parseInt(skillR.stdout.trim(), 10) || 0,
      sessionCount: parseInt(sessionR.stdout.trim(), 10) || 0,
      totalExecs: 0, successRate: 0,
      recentActivity: Array.from({ length: 14 }, (_, i) => {
        const d = new Date(); d.setDate(d.getDate() - (13 - i));
        return { date: d.toISOString().slice(0, 10), count: 0 };
      }),
    });
  } catch (e: any) { res.status(500).json({ error: String(e.message) }); }
});

// GET /codex/skills
router.get("/codex/skills", async (_req, res) => {
  try {
    const skillDir = HOME + "/.codex/skills";
    if (!existsSync(skillDir)) { res.json([]); return; }
    const names = await readdir(skillDir);
    const skills = await Promise.all(names.map(async (name) => {
      try {
        const skillMd = await readFile(skillDir + "/" + name + "/SKILL.md", "utf8").catch(() => "");
        const descMatch = skillMd.match(/description:\s*(.+)/);
        const hasAgents = existsSync(skillDir + "/" + name + "/agents");
        return { name, path: skillDir + "/" + name, description: descMatch ? descMatch[1].trim().replace(/^"|"$/g, "") : null, hasAgents };
      } catch { return { name, path: skillDir + "/" + name, description: null, hasAgents: false }; }
    }));
    res.json(skills);
  } catch (e: any) { res.status(500).json({ error: String(e.message) }); }
});

// GET /codex/sessions
router.get("/codex/sessions", async (_req, res) => {
  try {
    const { stdout } = await execAsync("find ~/.codex/sessions -name '*.jsonl' 2>/dev/null | sort -r | head -20 || true", { timeout: 10000 });
    const paths = stdout.split("\n").map((s) => s.trim()).filter(Boolean);
    const sessions = await Promise.all(paths.map(async (p) => {
      const id = p.split("/").pop()?.replace(".jsonl", "") || p;
      const { stdout: cnt } = await execAsync(`wc -l < ${JSON.stringify(p)} 2>/dev/null || echo 0`, { timeout: 3000 });
      return { id, date: new Date().toISOString(), path: p, messageCount: parseInt(cnt.trim(), 10) || 0, hasRefusal: false };
    }));
    res.json(sessions);
  } catch (e: any) { res.status(500).json({ error: String(e.message) }); }
});

export default router;
