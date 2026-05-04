import { Router } from "express";
import { exec } from "child_process";
import fs from "fs";
import path from "path";

const router = Router();
const SESSIONS_DIR = "/root/Toolkit/.ai-sessions";
const ensureDir = (d: string) => { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); };

/* ─── Shared helpers ─────────────────────────────────── */
const SAFE_ENV: NodeJS.ProcessEnv = {
  HOME: "/root",
  PATH: process.env.PATH ?? "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
  TERM: "xterm",
  LANG: "en_US.UTF-8",
  LC_ALL: "en_US.UTF-8",
};

const callClaude = (prompt: string, model = "default"): Promise<string> =>
  new Promise((resolve, reject) => {
    const modelFlag = model && model !== "default" ? ` --model ${model}` : "";
    exec(
      `echo "$CLAUDE_MSG" | /usr/bin/claude --print${modelFlag}`,
      { env: { ...SAFE_ENV, CLAUDE_MSG: prompt }, cwd: "/root/Toolkit", timeout: 180_000, maxBuffer: 20 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (stdout?.trim()) resolve(stdout.trim());
        else reject(err ?? new Error(stderr || "no output from claude"));
      }
    );
  });

const execCmd = (
  cmd: string,
  cwd = "/root/Toolkit"
): Promise<{ stdout: string; stderr: string; code: number }> =>
  new Promise((resolve) => {
    exec(cmd, { env: SAFE_ENV, cwd, timeout: 90_000, maxBuffer: 5 * 1024 * 1024 }, (err, stdout, stderr) => {
      resolve({ stdout: stdout ?? "", stderr: stderr ?? "", code: err?.code ?? 0 });
    });
  });

const readFileSafe = (filePath: string, maxBytes = 15000): string | null => {
  try {
    if (!fs.existsSync(filePath)) return null;
    const stat = fs.statSync(filePath);
    if (stat.size > 500 * 1024) return `[文件过大: ${filePath} (${stat.size} bytes)]`;
    return fs.readFileSync(filePath, "utf-8").slice(0, maxBytes);
  } catch { return null; }
};

// Parse action blocks from AI response
const parseActions = (text: string) => {
  const reads: string[] = [];
  const writes: Array<{ path: string; content: string }> = [];
  const bashes: string[] = [];

  let m;
  const readRe = /\[READ:\s*([^\]]+)\]/g;
  while ((m = readRe.exec(text)) !== null) reads.push(m[1].trim());

  const writeRe = /\[WRITE:\s*([^\]]+)\]\n?([\s\S]*?)\[\/WRITE\]/g;
  while ((m = writeRe.exec(text)) !== null) writes.push({ path: m[1].trim(), content: m[2] });

  const bashRe = /```(?:bash|sh|shell|cmd)?\n?([\s\S]*?)```/g;
  while ((m = bashRe.exec(text)) !== null) bashes.push(m[1].trim());

  return { reads, writes, bashes };
};

/* ─── VPS Agent System Prompt ─────────────────────────── */
const AGENT_SYS = `你是部署在 VPS（45.205.27.69）上的 AI 编程助手和运维助手，拥有完整服务器控制权。

【你能做的事】
- 读取任意文件：输出 [READ: /绝对路径]，系统会把内容附加回来
- 写入任意文件：输出 [WRITE: /绝对路径]\\n文件完整内容\\n[/WRITE]
- 执行 bash 命令：输出 \`\`\`bash\\n命令\\n\`\`\`，系统会执行并把结果附加回来
- 以上可以组合使用，系统会依次处理并把所有结果一起反馈给你

【服务架构】
工作目录：/root/Toolkit (pnpm monorepo)
- api-server (PM2 #62, port 8081, TypeScript Express) → /root/Toolkit/artifacts/api-server/src/
  - 路由入口：/root/Toolkit/artifacts/api-server/src/routes/index.ts
  - 路由目录：/root/Toolkit/artifacts/api-server/src/routes/
  - 构建命令：cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server
- frontend (PM2 #1, port 3000, React + Vite) → /root/Toolkit/artifacts/ai-toolkit/src/
  - 路由配置：/root/Toolkit/artifacts/ai-toolkit/src/App.tsx
  - 页面目录：/root/Toolkit/artifacts/ai-toolkit/src/pages/
  - Vite 自动 HMR（修改后前端自动热更新，不需要重启）

【新功能对接规则】
当用户要求新增功能时：
1. 先读取相关文件了解现有结构
2. 参照现有模式生成代码
3. 如果是新 API 路由：写入 routes/ 目录并更新 routes/index.ts
4. 如果是新前端页面：写入 pages/ 目录并更新 App.tsx 添加路由
5. 后端有改动时执行构建并重启
6. 验证改动是否生效（curl 测试/pm2 logs）

【完成信号】
任务完成时输出 [DONE] 并简要说明做了什么。
如果遇到问题无法继续，输出 [STUCK: 原因]。

【规则】
- 写文件时输出完整内容，不要省略
- 优先中文回答
- 每轮只做必要的操作，不要一次输出过多`;

/* ─── POST /api/claude-code/agent — Full server-side agentic loop ─── */
router.post("/claude-code/agent", (req, res) => {
  const { task, model = "default", initFiles = [], cwd: reqCwd = "/root/Toolkit" } = req.body as {
    task: string; model?: string; initFiles?: string[]; cwd?: string;
  };
  if (!task?.trim()) { res.status(400).json({ error: "task required" }); return; }

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (d: Record<string, unknown>) => {
    try { res.write(`data: ${JSON.stringify(d)}\n\n`); } catch (_) {}
  };

  const run = async () => {
    try {
      send({ type: "start" });
      const contextParts: string[] = [AGENT_SYS];

      // 1. Project structure scan
      send({ type: "status", text: "扫描项目结构…" });
      const { stdout: structure } = await execCmd(
        "find /root/Toolkit/artifacts -type f \\( -name '*.ts' -o -name '*.tsx' \\) | grep -v node_modules | grep -v dist | grep -v '.bak' | sort | head -80",
        reqCwd
      );
      contextParts.push(`[项目文件清单]\n${structure.trim()}`);

      // 2. Auto-read files mentioned in task
      const pathRe = /\/[^\s"'`<>()[\]]+\.[a-zA-Z]{1,10}/g;
      const mentionedPaths = [...new Set([...task.matchAll(pathRe)].map((m) => m[0]))];

      for (const p of mentionedPaths) {
        const content = readFileSafe(p);
        if (content !== null) {
          send({ type: "read_file", path: p, lines: content.split("\n").length });
          contextParts.push(`[文件: ${p}]\n\`\`\`\n${content}\n\`\`\``);
        }
      }

      // 3. Always read structural key files (if not already included)
      const structuralFiles = [
        "/root/Toolkit/artifacts/api-server/src/routes/index.ts",
        "/root/Toolkit/artifacts/ai-toolkit/src/App.tsx",
        ...initFiles,
      ];
      for (const f of structuralFiles) {
        if (!mentionedPaths.includes(f)) {
          const content = readFileSafe(f, 8000);
          if (content !== null) {
            send({ type: "read_file", path: f, lines: content.split("\n").length });
            contextParts.push(`[关键文件: ${f}]\n\`\`\`\n${content}\n\`\`\``);
          }
        }
      }

      contextParts.push(`\n[任务]\n${task}`);
      let context = contextParts.join("\n\n");

      // 4. Agentic loop
      let iteration = 0;
      const MAX_ITER = 10;

      while (iteration < MAX_ITER) {
        send({ type: "ai_thinking", iteration: iteration + 1, text: `AI 思考中 (第 ${iteration + 1} 轮)…` });

        let aiResponse: string;
        try {
          aiResponse = await callClaude(context, model);
        } catch (e) {
          send({ type: "error", text: `AI 调用失败: ${String(e)}` });
          break;
        }

        send({ type: "ai_response", text: aiResponse, iteration: iteration + 1 });

        // Check terminal signals
        if (aiResponse.includes("[DONE]")) {
          send({ type: "complete", text: aiResponse, iterations: iteration + 1 });
          break;
        }
        if (aiResponse.includes("[STUCK:")) {
          send({ type: "stuck", text: aiResponse, iterations: iteration + 1 });
          break;
        }

        const { reads, writes, bashes } = parseActions(aiResponse);

        // No actions = done
        if (reads.length === 0 && writes.length === 0 && bashes.length === 0) {
          send({ type: "complete", text: aiResponse, iterations: iteration + 1 });
          break;
        }

        const feedbackParts: string[] = [];

        // Execute READs
        for (const p of reads) {
          const content = readFileSafe(p);
          if (content !== null) {
            feedbackParts.push(`[文件内容: ${p}]\n\`\`\`\n${content}\n\`\`\``);
            send({ type: "read_file", path: p, lines: content.split("\n").length });
          } else {
            feedbackParts.push(`[文件不存在: ${p}]`);
            send({ type: "read_file", path: p, error: true });
          }
        }

        // Execute WRITEs
        for (const w of writes) {
          try {
            const dir = path.dirname(w.path);
            if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
            if (fs.existsSync(w.path)) fs.writeFileSync(w.path + ".bak", fs.readFileSync(w.path));
            fs.writeFileSync(w.path, w.content, "utf-8");
            const lines = w.content.split("\n").length;
            feedbackParts.push(`[写入成功: ${w.path}] (${lines} 行, ${w.content.length} 字节)`);
            send({ type: "write_file", path: w.path, lines, size: w.content.length });
          } catch (e) {
            feedbackParts.push(`[写入失败: ${w.path}] ${String(e)}`);
            send({ type: "write_error", path: w.path, error: String(e) });
          }
        }

        // Execute BASHes
        for (const cmd of bashes) {
          send({ type: "exec_start", cmd });
          const result = await execCmd(cmd, reqCwd);
          const out = result.stdout.slice(0, 1500);
          const err = result.stderr.slice(0, 600);
          feedbackParts.push(
            `$ ${cmd}\n${out}${err ? `\n[stderr] ${err}` : ""}\n[exit: ${result.code}]`
          );
          send({ type: "exec_done", cmd, stdout: out, stderr: err, code: result.code });
        }

        // Build next iteration context
        context = [
          context,
          `\n[第 ${iteration + 1} 轮 AI 回复]\n${aiResponse}`,
          `\n[执行结果]\n${feedbackParts.join("\n\n")}`,
          "\n请根据以上结果继续完成任务。完成后输出 [DONE]。",
        ].join("\n\n");

        iteration++;
      }

      if (iteration >= MAX_ITER) {
        send({ type: "complete", text: `已达最大迭代次数 (${MAX_ITER})`, iterations: MAX_ITER });
      }
    } catch (e) {
      send({ type: "error", text: String(e) });
    }
    try { res.end(); } catch (_) {}
  };

  void run();
});

/* ─── POST /api/claude-code/chat — SSE streaming (single turn) ─── */
router.post("/claude-code/chat", (req, res) => {
  const { message, model = "default", cwd: reqCwd } = req.body as {
    message: string; model?: string; cwd?: string;
  };
  if (!message?.trim()) { res.status(400).json({ error: "message required" }); return; }

  const cwd = reqCwd ?? "/root/Toolkit";
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (d: unknown) => { try { res.write(`data: ${JSON.stringify(d)}\n\n`); } catch (_) {} };
  send({ type: "start" });

  const modelFlag = model && model !== "default" ? ` --model ${model}` : "";
  const child = exec(
    `echo "$CLAUDE_MSG" | /usr/bin/claude --print${modelFlag}`,
    { env: { ...SAFE_ENV, CLAUDE_MSG: message }, cwd, timeout: 180_000, maxBuffer: 20 * 1024 * 1024 },
    (error, stdout, stderr) => {
      const hasOutput = !!stdout?.trim();
      if (stderr?.trim() && !stderr.includes("Loaded MCP") && !stderr.includes("logLevel")) {
        send({ type: "stderr", text: stderr.trim() });
      }
      if (hasOutput) {
        const text = stdout.trim();
        for (let i = 0; i < text.length; i += 80) {
          send({ type: "text", content: text.slice(i, i + 80) });
        }
      }
      if (error && !hasOutput) send({ type: "error", text: error.message.split("\n")[0] });
      send({ type: "done", code: hasOutput ? 0 : error ? 1 : 0 });
      try { res.end(); } catch (_) {}
    }
  );
  req.on("close", () => { try { child.kill("SIGTERM"); } catch (_) {} });
});

/* ─── POST /api/claude-code/exec — proxy to remote-exec:9999 ─── */
router.post("/claude-code/exec", async (req, res) => {
  const { cmd, cwd } = req.body as { cmd: string; cwd?: string };
  if (!cmd?.trim()) { res.status(400).json({ error: "cmd required" }); return; }
  try {
    const r = await fetch("http://127.0.0.1:9999/exec", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-token": "zencoder-exec-2026" },
      body: JSON.stringify({ cmd, cwd: cwd ?? "/root/Toolkit", timeout: 60000 }),
      signal: AbortSignal.timeout(65000),
    });
    res.json(await r.json());
  } catch (err) {
    res.status(500).json({ error: String(err) });
  }
});

/* ─── POST /api/claude-code/file-read ─── */
router.post("/claude-code/file-read", (req, res) => {
  const { path: filePath } = req.body as { path: string };
  if (!filePath) { res.status(400).json({ error: "path required" }); return; }
  const content = readFileSafe(filePath);
  if (content === null) { res.status(404).json({ error: "file not found", path: filePath }); return; }
  res.json({ content, size: content.length, lines: content.split("\n").length });
});

/* ─── POST /api/claude-code/file-write ─── */
router.post("/claude-code/file-write", (req, res) => {
  const { path: filePath, content, createBackup } = req.body as {
    path: string; content: string; createBackup?: boolean;
  };
  if (!filePath || content === undefined) { res.status(400).json({ error: "path and content required" }); return; }
  try {
    const dir = path.dirname(filePath);
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    if (createBackup && fs.existsSync(filePath)) fs.writeFileSync(filePath + ".bak", fs.readFileSync(filePath));
    fs.writeFileSync(filePath, content, "utf-8");
    res.json({ ok: true, path: filePath, size: content.length });
  } catch (e) { res.status(500).json({ error: String(e) }); }
});

/* ─── POST /api/claude-code/dir-scan ─── */
router.post("/claude-code/dir-scan", (req, res) => {
  const { path: dirPath, depth = 2 } = req.body as { path: string; depth?: number };
  if (!dirPath) { res.status(400).json({ error: "path required" }); return; }
  const scan = (p: string, d: number): unknown => {
    if (d <= 0 || !fs.existsSync(p)) return null;
    const stat = fs.statSync(p);
    if (stat.isFile()) return { type: "file", name: path.basename(p), size: stat.size };
    const entries = fs.readdirSync(p)
      .filter((e) => !e.startsWith(".") && e !== "node_modules" && e !== "dist" && !e.endsWith(".bak"))
      .map((e) => scan(path.join(p, e), d - 1))
      .filter(Boolean);
    return { type: "dir", name: path.basename(p), children: entries };
  };
  res.json(scan(dirPath, depth));
});

/* ─── Session CRUD ─── */
router.get("/claude-code/sessions", (req, res) => {
  ensureDir(SESSIONS_DIR);
  try {
    const sessions = fs.readdirSync(SESSIONS_DIR)
      .filter((f) => f.endsWith(".json"))
      .map((f) => {
        try {
          const raw = JSON.parse(fs.readFileSync(path.join(SESSIONS_DIR, f), "utf-8"));
          return { id: raw.id, title: raw.title ?? "未命名", created_at: raw.created_at ?? 0, updated_at: raw.updated_at ?? 0, msgCount: (raw.messages ?? []).length, model: raw.model ?? "default" };
        } catch { return null; }
      })
      .filter(Boolean)
      .sort((a: any, b: any) => b.updated_at - a.updated_at);
    res.json(sessions);
  } catch (e) { res.status(500).json({ error: String(e) }); }
});

router.post("/claude-code/sessions", (req, res) => {
  ensureDir(SESSIONS_DIR);
  const { id, title, messages, model } = req.body;
  if (!id || !messages) { res.status(400).json({ error: "id and messages required" }); return; }
  const file = path.join(SESSIONS_DIR, `${id}.json`);
  const existing = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, "utf-8")) : {};
  const now = Date.now();
  fs.writeFileSync(file, JSON.stringify({ ...existing, id, title: title ?? existing.title ?? "新会话", messages, model: model ?? "default", created_at: existing.created_at ?? now, updated_at: now }, null, 2));
  res.json({ ok: true, id });
});

router.get("/claude-code/sessions/:id", (req, res) => {
  const file = path.join(SESSIONS_DIR, `${req.params.id}.json`);
  if (!fs.existsSync(file)) { res.status(404).json({ error: "not found" }); return; }
  res.json(JSON.parse(fs.readFileSync(file, "utf-8")));
});

router.delete("/claude-code/sessions/:id", (req, res) => {
  const file = path.join(SESSIONS_DIR, `${req.params.id}.json`);
  if (fs.existsSync(file)) fs.unlinkSync(file);
  res.json({ ok: true });
});

router.patch("/claude-code/sessions/:id", (req, res) => {
  const file = path.join(SESSIONS_DIR, `${req.params.id}.json`);
  if (!fs.existsSync(file)) { res.status(404).json({ error: "not found" }); return; }
  const data = JSON.parse(fs.readFileSync(file, "utf-8"));
  if (req.body.title) data.title = req.body.title;
  data.updated_at = Date.now();
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
  res.json({ ok: true });
});

export default router;
