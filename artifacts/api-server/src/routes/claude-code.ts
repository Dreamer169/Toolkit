import { Router } from "express";
  import { exec, spawn } from "child_process";
  import fs from "fs";
  import path from "path";

  const router = Router();
  const SESSIONS_DIR = "/root/Toolkit/.ai-sessions";
  const HISTORY_FILE  = "/root/Toolkit/.ai-sessions/exec-history.json";
  const REPO_DIR      = "/root/Toolkit";
  const GH_REPO       = "Dreamer169/Toolkit";
  const GH_TOKEN      = process.env.GH_TOKEN || "ghp_PLACEHOLDER";

  const ensureDir = (d: string) => { if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true }); };

  /* ─── ENV ─────────────────────────────────────── */
  const FULL_ENV: NodeJS.ProcessEnv = {
    ...process.env,
    HOME: "/root", USER: "root",
    PATH: "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:/root/.local/bin",
    TERM: "xterm-256color", LANG: "en_US.UTF-8", LC_ALL: "en_US.UTF-8",
    PYTHONUNBUFFERED: "1", DEBIAN_FRONTEND: "noninteractive",
    GIT_AUTHOR_NAME: "AI Agent", GIT_AUTHOR_EMAIL: "ai@toolkit.local",
    GIT_COMMITTER_NAME: "AI Agent", GIT_COMMITTER_EMAIL: "ai@toolkit.local",
  };

  /* Claude Code API env (real model, no permission restrictions) */
  const CLAUDE_ENV: NodeJS.ProcessEnv = {
    ...FULL_ENV,
    GH_TOKEN: process.env.GH_TOKEN ?? "",
    GITHUB_TOKEN: process.env.GH_TOKEN ?? "",
    ANTHROPIC_BASE_URL: "https://api.xiaomimimo.com/anthropic",
    ANTHROPIC_AUTH_TOKEN: "sk-sszfdmshqaziz2d7dvl2nggaf2gum5kbs881qajf0fzavxyw",
    ANTHROPIC_MODEL: "mimo-v2.5-pro",
    ANTHROPIC_DEFAULT_SONNET_MODEL: "mimo-v2.5-pro",
    ANTHROPIC_DEFAULT_OPUS_MODEL:   "mimo-v2.5-pro",
    ANTHROPIC_DEFAULT_HAIKU_MODEL:  "mimo-v2.5-pro",
  };

  /* ─── Execution history ───────────────────────── */
  interface HistoryEntry {
    id: string; ts: number; cmd: string; cwd: string;
    stdout: string; stderr: string; code: number; duration: number;
    autoInstalled?: string; source?: string;
  }
  function loadHistory(): HistoryEntry[] {
    try { ensureDir(SESSIONS_DIR); if (!fs.existsSync(HISTORY_FILE)) return []; return JSON.parse(fs.readFileSync(HISTORY_FILE, "utf-8")); } catch { return []; }
  }
  function appendHistory(e: HistoryEntry) {
    const h = loadHistory(); h.push(e); ensureDir(SESSIONS_DIR);
    fs.writeFileSync(HISTORY_FILE, JSON.stringify(h.slice(-200), null, 2));
  }

  /* ─── Core exec ───────────────────────────────── */
  async function execCmd(cmd: string, cwd = REPO_DIR): Promise<{ stdout: string; stderr: string; code: number; duration: number }> {
    const t0 = Date.now();
    return new Promise((resolve) => {
      exec(cmd, { env: FULL_ENV, cwd, timeout: 120_000, maxBuffer: 10 * 1024 * 1024 }, (err, stdout, stderr) => {
        resolve({ stdout: stdout ?? "", stderr: stderr ?? "", code: err?.code ?? 0, duration: Date.now() - t0 });
      });
    });
  }

  function detectMissingDep(stderr: string): string | null {
    const py = stderr.match(/No module named ['"]?([a-zA-Z0-9_\-]+)['"]?/);
    if (py) return `pip3 install ${py[1]}`;
    const npm = stderr.match(/Cannot find module ['"]([^'"./][^'"]*)['"]/);
    if (npm) return `cd ${REPO_DIR} && pnpm add ${npm[1]}`;
    const cnf = stderr.match(/bash: ([^:]+): command not found/) || stderr.match(/([^:]+): not found/);
    if (cnf) { const m = cnf[1].trim(); if (m==="pnpm") return "npm install -g pnpm"; if (m==="pm2") return "npm install -g pm2"; return `apt-get install -y ${m}`; }
    return null;
  }

  async function execWithAutoInstall(cmd: string, cwd = REPO_DIR): Promise<{ stdout: string; stderr: string; code: number; autoInstalled?: string }> {
    const r = await execCmd(cmd, cwd);
    appendHistory({ id: Date.now().toString(36), ts: Date.now(), cmd, cwd, stdout: r.stdout.slice(0,2000), stderr: r.stderr.slice(0,500), code: r.code, duration: r.duration, source: "agent" });
    if (r.code !== 0 && r.stderr) {
      const ic = detectMissingDep(r.stderr);
      if (ic) { const ir = await execCmd(ic, cwd); if (ir.code === 0) { const retry = await execCmd(cmd, cwd); return { ...retry, autoInstalled: ic }; } }
    }
    return r;
  }

  const readFileSafe = (p: string, max = 20000): string | null => {
    try { if (!fs.existsSync(p)) return null; const s = fs.statSync(p); if (s.size>500*1024) return `[too large: ${p}]`; return fs.readFileSync(p,"utf-8").slice(0,max); } catch { return null; }
  };

  /* ─── Agent system prompt ─────────────────────── */
  const AGENT_SYS = `你是部署在 VPS（45.205.27.69）上的 AI 全权运维和编程助手，拥有完整 root 级服务器控制权。
  你通过 Bash 工具直接执行命令，可以做任何事，没有任何限制。

  【服务架构】
  工作目录：/root/Toolkit (pnpm monorepo)
  - api-server: PM2 #62, port 8081 (TypeScript Express)
    构建：cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server
  - frontend: PM2 #1, port 3000 (React+Vite 热更新，不需重启)
  - xray: PM2 #4 (VLESS代理, SOCKS5 port 10808, 每账号独立端口 10820-10845)
  - CF IP池: 171 节点 (GET http://localhost:8081/api/tools/cf-pool/status)

  【最重要：Outlook 账号注册】
  注册 N 个 Outlook 账号：
  \`\`\`bash
  curl -s -X POST http://localhost:8081/api/tools/outlook/register \\
    -H "Content-Type: application/json" \\
    -d '{"count":N,"engine":"patchright","headless":true,"wait":11,"retries":2,"proxyMode":"cf"}'
  \`\`\`
  获得 jobId 后，等待并查询进度：
  \`\`\`bash
  for i in \$(seq 1 24); do
    STATUS=\$(curl -s http://localhost:8081/api/tools/outlook/register/JOB_ID | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('status','?'),d.get('accountCount',0),'accounts')" 2>/dev/null)
    echo "[\$i] \$STATUS"
    if echo "\$STATUS" | grep -q "done"; then break; fi
    sleep 10
  done
  curl -s http://localhost:8081/api/tools/outlook/register/JOB_ID | python3 -c "
  import sys,json; d=json.load(sys.stdin)
  print('Status:',d.get('status'))
  print('Accounts:',d.get('accountCount',0))
  msg=d.get('lastLog',{}).get('message',''); print('Result:',msg)
  "
  \`\`\`

  【其他常用任务】
  构建重启 api-server：cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server
  安装 Python 包：pip3 install 包名
  安装系统包：apt-get install -y 包名
  pnpm 包：pnpm --filter @workspace/api-server add 包名
  PM2 管理：pm2 list | pm2 restart X | pm2 logs X --lines 50 --nostream
  Git 提交：cd /root/Toolkit && git add -A && git commit -m "msg" && git push https://\${GH_TOKEN}@github.com/REPO.git HEAD:main

  【规则】
  - 用户说"注册/生成 N 个 Outlook" → 立即执行 curl 注册命令，然后等待查询结果
  - 如果命令失败，分析原因并自动安装缺少的依赖
  - 做完所有事情后，总结结果`;

  /* ─── parse claude stream-json event → our SSE format ─── */
  function getToolIcon(name: string): string {
    const m: Record<string,string> = {Bash:"bash",Read:"read",Write:"write",Edit:"edit",MultiEdit:"edit",Glob:"glob",Grep:"grep",LS:"ls",TodoRead:"todo",TodoWrite:"todo",WebFetch:"web",WebSearch:"web"};
    return m[name] ?? "tool";
  }
  function getToolLabel(name: string, inp: Record<string,unknown>): string {
    if (name==="Bash") return (inp.command as string ?? inp.description as string ?? "").slice(0,100);
    if (name==="Read") return "read " + String(inp.file_path ?? inp.path ?? "");
    if (name==="Write") return "write " + String(inp.file_path ?? "");
    if (name==="Edit"||name==="MultiEdit") return "edit " + String(inp.file_path ?? "");
    if (name==="Glob") return "glob " + String(inp.pattern ?? "") + " in " + String(inp.path ?? ".");
    if (name==="Grep") return "grep " + String(inp.pattern ?? "") + " " + String(inp.path ?? "");
    if (name==="LS") return "ls " + String(inp.path ?? ".");
    if (name==="TodoRead") return "read todo list";
    if (name==="TodoWrite") return "update todo list";
    if (name==="WebFetch") return "fetch " + String(inp.url ?? "");
    if (name==="WebSearch") return "search: " + String(inp.query ?? "");
    return name;
  }
  function mapClaudeEvent(raw: Record<string,unknown>): Record<string,unknown> | null {
    const t = raw.type as string;
    if (t === "system") return { type: "status", text: "Claude Agent 初始化…" };
    if (t === "assistant") {
      const msg = raw.message as { content?: Array<{type:string;text?:string;name?:string;input?:{command?:string;description?:string};id?:string}> };
      const parts = msg?.content ?? [];
      const texts = parts.filter(c=>c.type==="text").map(c=>c.text??"").join("");
      const toolUses = parts.filter(c=>c.type==="tool_use");
      if (toolUses.length > 0) {
        const tu = toolUses[0];
        const inp = (tu.input ?? {}) as Record<string,unknown>;
        const toolName = String(tu.name ?? "Bash");
        return { type: "exec_start", tool: getToolIcon(toolName), toolName, cmd: getToolLabel(toolName, inp), toolId: tu.id, ai_text: texts, filePath: String(inp.file_path ?? inp.path ?? "") || null };
      }
      if (texts) return { type: "ai_response", text: texts };
      return null;
    }
    if (t === "user") {
      const msg = raw.message as { content?: Array<{type:string;tool_use_id?:string;content?:string|Array<{text?:string}>}> };
      const tr = (msg?.content ?? []).find(c=>c.type==="tool_result");
      if (tr) {
        const out = typeof tr.content==="string" ? tr.content : (Array.isArray(tr.content) ? tr.content.map((x:{text?:string})=>x.text??"").join("") : "");
        return { type: "exec_done", toolId: tr.tool_use_id, stdout: out.slice(0,3000), code: 0 };
      }
      return null;
    }
    if (t === "result") {
      const sub = raw.subtype as string;
      if (sub === "success") return { type: "complete", text: raw.result as string };
      return { type: "error", text: String(raw.result ?? raw.error ?? "unknown error") };
    }
    return null;
  }

  /* ─── POST /api/claude-code/agent — REAL claude -p with Bash tool ─── */
  router.post("/claude-code/agent", (req, res) => {
    const { task, model: _model = "default", cwd: reqCwd = REPO_DIR } = req.body as {
      task: string; model?: string; cwd?: string;
    };
    if (!task?.trim()) { res.status(400).json({ error: "task required" }); return; }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    let completeSent = false;
      const send = (d: Record<string,unknown>) => { if (d.type === "complete") completeSent = true; try { res.write(`data: ${JSON.stringify(d)}\n\n`); } catch (_) {} };
    send({ type: "start" });

    const fullPrompt = `${AGENT_SYS}\n\n[用户任务]\n${task}`;

    // Write prompt to tmpfile and pipe to claude (avoids all stdin issues)
    const tmpPromptFile = `/tmp/ct_${Date.now()}_${Math.random().toString(36).slice(2,6)}.txt`;
    fs.writeFileSync(tmpPromptFile, fullPrompt, "utf-8");
    const cShellCmd = `cat '${tmpPromptFile}' | /usr/bin/claude --allowedTools 'Bash' 'Read' 'Write' 'Edit' 'MultiEdit' 'Glob' 'Grep' 'LS' 'TodoRead' 'TodoWrite' 'WebFetch' 'WebSearch' --permission-mode acceptEdits --output-format stream-json --verbose; rm -f '${tmpPromptFile}'`;
    const child = spawn("bash", ["-c", cShellCmd], { env: CLAUDE_ENV, cwd: reqCwd });

    let buf = "";
    child.stdout.on("data", (data: Buffer) => {
      buf += data.toString();
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const raw = JSON.parse(trimmed) as Record<string,unknown>;
          const mapped = mapClaudeEvent(raw);
          if (mapped) send(mapped);
        } catch {}
      }
    });

    child.stderr.on("data", (data: Buffer) => {
      const text = data.toString().trim();
      if (text && !text.includes("Loaded MCP") && !text.includes("logLevel")) {
        send({ type: "status", text: text.slice(0, 200) });
      }
    });

    child.on("close", (exitCode, signal) => {
      if (!completeSent && (exitCode !== 0 || signal)) send({ type: "error", text: `Claude 退出异常: code=${exitCode}` });
      try { res.end(); } catch (_) {}
    });

    req.on("close", () => { try { child.kill("SIGTERM"); } catch (_) {} });
  });

  /* ─── POST /api/claude-code/chat — single-turn with real claude ─── */
  router.post("/claude-code/chat", (req, res) => {
    const { message, model: _m = "default", cwd: reqCwd } = req.body as { message: string; model?: string; cwd?: string };
    if (!message?.trim()) { res.status(400).json({ error: "message required" }); return; }
    const cwd = reqCwd ?? REPO_DIR;

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const send = (d: unknown) => { try { res.write(`data: ${JSON.stringify(d)}\n\n`); } catch (_) {} };
    send({ type: "start" });

    const child = exec(
      `/usr/bin/claude -p "$CLAUDE_MSG" --output-format text`,
      { env: { ...CLAUDE_ENV, CLAUDE_MSG: message }, cwd, timeout: 180_000, maxBuffer: 20*1024*1024 },
      (error, stdout, stderr) => {
        const hasOutput = !!stdout?.trim();
        if (stderr?.trim() && !stderr.includes("Loaded MCP")) send({ type: "stderr", text: stderr.trim() });
        if (hasOutput) { const text = stdout.trim(); for (let i=0; i<text.length; i+=80) send({ type: "text", content: text.slice(i,i+80) }); }
        if (error && !hasOutput) send({ type: "error", text: error.message.split("\n")[0] });
        send({ type: "done", code: hasOutput ? 0 : (error ? 1 : 0) });
        try { res.end(); } catch (_) {}
      }
    );
    req.on("close", () => { try { child.kill("SIGTERM"); } catch (_) {} });
  });

  /* ─── GET /api/claude-code/server-metrics ─── */
  router.get("/claude-code/server-metrics", async (_req, res) => {
    try {
      const [pm2R, diskR, cpuR, cfR, gitR] = await Promise.all([
        execCmd("pm2 jlist 2>/dev/null | python3 -c \"import sys,json;procs=json.load(sys.stdin);print(json.dumps([{'id':p['pm_id'],'name':p['name'],'status':p['pm2_env']['status'],'cpu':p['monit']['cpu'],'mem':round(p['monit']['memory']/1024/1024,1),'restarts':p['pm2_env']['restart_time']} for p in procs]))\""),
        execCmd("df -h / /data 2>/dev/null | tail -n+2 | awk '{print $1,$2,$3,$4,$5}'"),
        execCmd("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1; free -m | grep Mem | awk '{printf \"%s %s\", $2, $3}'"),
        execCmd("curl -s http://localhost:8081/api/tools/cf-pool/status 2>/dev/null | python3 -c \"import sys,json;d=json.load(sys.stdin);print(json.dumps({'available':d.get('available',0),'used_total':d.get('used_total',0),'banned_total':d.get('banned_total',0)}))\""),
        execCmd("git --no-optional-locks status --short 2>/dev/null | head -8; echo '---'; git --no-optional-locks log --oneline -3 2>/dev/null"),
      ]);
      let procs: unknown[] = []; try { procs = JSON.parse(pm2R.stdout.trim()); } catch {}
      const cl = cpuR.stdout.trim().split("\n");
      const cpu = parseFloat(cl[0]??"")||0;
      const [mt,mu] = (cl[1]??"").split(" ").map(Number);
      const disks = diskR.stdout.trim().split("\n").map(l=>{ const p=l.split(" "); return {fs:p[0],size:p[1],used:p[2],avail:p[3],pct:p[4]}; }).filter(d=>d.fs);
      let cf={available:0,used_total:0,banned_total:0}; try{cf=JSON.parse(cfR.stdout.trim());}catch{}
      res.json({ cpu, mem:{total:mt,used:mu,pct:mt?Math.round(mu/mt*100):0}, disk:disks, processes:procs, cfPool:cf, git:gitR.stdout.trim(), ts:Date.now() });
    } catch(e) { res.status(500).json({error:String(e)}); }
  });

  /* ─── POST /api/claude-code/git-ops ─── */
  router.post("/claude-code/git-ops", async (req, res) => {
    const { action, message } = req.body as { action:string; message?:string };
    try {
      if (action==="status") { const r=await execCmd("git --no-optional-locks status --porcelain 2>/dev/null; echo '---'; git --no-optional-locks log --oneline -5 2>/dev/null",REPO_DIR); res.json({ok:true,output:r.stdout}); return; }
      if (action==="diff") { const r=await execCmd("git --no-optional-locks diff --stat HEAD 2>/dev/null; git --no-optional-locks diff HEAD 2>/dev/null|head -80",REPO_DIR); res.json({ok:true,output:r.stdout.slice(0,4000)}); return; }
      if (action==="log") { const r=await execCmd("git --no-optional-locks log --oneline -20 2>/dev/null",REPO_DIR); res.json({ok:true,output:r.stdout}); return; }
      if (action==="pull") { const r=await execCmd("git stash 2>/dev/null; git pull origin main --no-rebase 2>&1|tail -8",REPO_DIR); res.json({ok:r.code===0,output:r.stdout+r.stderr}); return; }
      if (action==="commit"||action==="commit-push") {
        const msg=message??`AI Agent: auto-commit ${new Date().toISOString()}`;
        await execCmd("git add -A",REPO_DIR);
        const st=await execCmd("git --no-optional-locks status --porcelain",REPO_DIR);
        if(!st.stdout.trim()){res.json({ok:true,output:"无需提交 (无变更)",sha:null});return;}
        const cr=await execCmd(`GIT_AUTHOR_NAME="AI Agent" GIT_AUTHOR_EMAIL="ai@toolkit.local" GIT_COMMITTER_NAME="AI Agent" GIT_COMMITTER_EMAIL="ai@toolkit.local" git commit -m "${msg.replace(/"/g,'\\"')}"`,REPO_DIR);
        if(cr.code!==0){res.status(500).json({ok:false,output:cr.stderr+cr.stdout});return;}
        const gh_token=GH_TOKEN;
        const pr=await execCmd(`git push https://${gh_token}@github.com/${GH_REPO}.git HEAD:main 2>&1`,REPO_DIR);
        const sha=(await execCmd("git rev-parse --short HEAD",REPO_DIR)).stdout.trim();
        res.json({ok:pr.code===0,sha,output:pr.stdout+pr.stderr}); return;
      }
      res.status(400).json({error:"unknown action"});
    } catch(e){res.status(500).json({error:String(e)});}
  });

  /* ─── POST /api/claude-code/pm2-action ─── */
  router.post("/claude-code/pm2-action", async (req, res) => {
    const { action, name, lines } = req.body as {action:string;name?:string;lines?:number};
    const cmds: Record<string,string> = {
      list:"pm2 jlist",
      restart:`pm2 restart ${name??"api-server"}`,
      stop:`pm2 stop ${name??"api-server"}`,
      start:`pm2 start ${name??"api-server"}`,
      logs:`pm2 logs ${name??"api-server"} --lines ${lines??50} --nostream`,
      flush:`pm2 flush ${name??"all"}`,
      "build-api":"cd /root/Toolkit && pnpm --filter @workspace/api-server run build && pm2 restart api-server",
    };
    const cmd=cmds[action]; if(!cmd){res.status(400).json({error:`unknown: ${action}`});return;}
    const r=await execWithAutoInstall(cmd);
    res.json({ok:r.code===0,stdout:r.stdout.slice(0,5000),stderr:r.stderr.slice(0,1000),code:r.code});
  });

  /* ─── POST /api/claude-code/install-dep ─── */
  router.post("/claude-code/install-dep", (req, res) => {
    const { type, pkg, cwd } = req.body as {type:string;pkg:string;cwd?:string};
    const cmds: Record<string,string> = {
      pip:`pip3 install ${pkg}`, npm:`npm install -g ${pkg}`,
      pnpm:`cd ${cwd??REPO_DIR} && pnpm add ${pkg}`,
      apt:`DEBIAN_FRONTEND=noninteractive apt-get install -y ${pkg}`,
      auto:`DEBIAN_FRONTEND=noninteractive apt-get install -y ${pkg} 2>/dev/null || pip3 install ${pkg} 2>/dev/null || npm install -g ${pkg}`,
    };
    const cmd=cmds[type]??cmds.auto;
    res.setHeader("Content-Type","text/event-stream"); res.setHeader("Cache-Control","no-cache"); res.flushHeaders();
    const send=(d:unknown)=>{try{res.write(`data: ${JSON.stringify(d)}\n\n`);}catch(_){}};
    send({type:"start",cmd});
    const child=spawn("bash",["-c",cmd],{env:FULL_ENV,cwd:REPO_DIR});
    child.stdout.on("data",(d:Buffer)=>send({type:"stdout",text:d.toString()}));
    child.stderr.on("data",(d:Buffer)=>send({type:"stderr",text:d.toString()}));
    child.on("close",(code)=>{send({type:"done",code});try{res.end();}catch(_){}});
    req.on("close",()=>{try{child.kill();}catch(_){}});
  });

  /* ─── POST /api/claude-code/exec-stream ─── */
  router.post("/claude-code/exec-stream", (req, res) => {
    const { cmd, cwd } = req.body as {cmd:string;cwd?:string};
    if (!cmd?.trim()){res.status(400).json({error:"cmd required"});return;}
    res.setHeader("Content-Type","text/event-stream"); res.setHeader("Cache-Control","no-cache"); res.setHeader("Connection","keep-alive"); res.flushHeaders();
    const send=(d:unknown)=>{try{res.write(`data: ${JSON.stringify(d)}\n\n`);}catch(_){}};
    send({type:"start",cmd});
    const t0=Date.now(); let stdout="",stderr="";
    const child=spawn("bash",["-c",cmd],{env:FULL_ENV,cwd:cwd??REPO_DIR});
    child.stdout.on("data",(d:Buffer)=>{const t=d.toString();stdout+=t;send({type:"stdout",text:t});});
    child.stderr.on("data",(d:Buffer)=>{const t=d.toString();stderr+=t;send({type:"stderr",text:t});});
    child.on("close",(code)=>{
      appendHistory({id:Date.now().toString(36),ts:Date.now(),cmd,cwd:cwd??REPO_DIR,stdout:stdout.slice(0,2000),stderr:stderr.slice(0,500),code:code??0,duration:Date.now()-t0,source:"exec-stream"});
      send({type:"done",code,duration:Date.now()-t0});try{res.end();}catch(_){};
    });
    req.on("close",()=>{try{child.kill();}catch(_){}});
  });

  /* ─── GET /api/claude-code/outlook-jobs — Outlook job list ─── */
  router.get("/claude-code/outlook-jobs", async (_req, res) => {
    try {
      const r = await execCmd("curl -s http://localhost:8081/api/tools/jobs 2>/dev/null");
      const jobs = JSON.parse(r.stdout.trim());
      const list = Array.isArray(jobs) ? jobs : (jobs.jobs ?? []);
      const outlookJobs = list.filter((j: Record<string,unknown>) => String(j.id ?? "").includes("reg_") || j.kind === "outlook_register").slice(0, 20);
      res.json(outlookJobs);
    } catch (e) { res.status(500).json({ error: String(e) }); }
  });

  /* ─── GET /api/claude-code/outlook-jobs/:jobId/stream — SSE job monitor ─── */
  router.get("/claude-code/outlook-jobs/:jobId/stream", async (req, res) => {
    const { jobId } = req.params;
    res.setHeader("Content-Type","text/event-stream"); res.setHeader("Cache-Control","no-cache"); res.setHeader("Connection","keep-alive"); res.flushHeaders();
    const send=(d:unknown)=>{try{res.write(`data: ${JSON.stringify(d)}\n\n`);}catch(_){}};

    let done=false; let polls=0;
    const poll=async()=>{
      try {
        const r=await execCmd(`curl -s http://localhost:8081/api/tools/jobs/${jobId} 2>/dev/null`);
        const job=JSON.parse(r.stdout.trim()) as Record<string,unknown>;
        send({type:"job_update",job});
        if(job.status==="done"||job.status==="error"||polls>60) done=true;
      } catch(e){send({type:"error",text:String(e)});done=true;}
      polls++;
      if(done){try{res.end();}catch(_){}}
      else setTimeout(poll,5000);
    };
    await poll();
    req.on("close",()=>{done=true;});
  });

  /* ─── POST /api/claude-code/outlook-register — Direct register + stream ─── */
  router.post("/claude-code/outlook-register", async (req, res) => {
    const { count=1, engine="patchright", proxyMode="cf", headless=true, wait=11, retries=2 } = req.body as {count?:number;engine?:string;proxyMode?:string;headless?:boolean;wait?:number;retries?:number};
    res.setHeader("Content-Type","text/event-stream"); res.setHeader("Cache-Control","no-cache"); res.setHeader("Connection","keep-alive"); res.flushHeaders();
    const send=(d:unknown)=>{try{res.write(`data: ${JSON.stringify(d)}\n\n`);}catch(_){}};

    try {
      send({type:"status",text:`正在启动注册任务：${count} 个 Outlook 账号…`});
      const r=await execCmd(`curl -s -X POST http://localhost:8081/api/tools/outlook/register -H "Content-Type: application/json" -d '{"count":${count},"engine":"${engine}","headless":${headless},"wait":${wait},"retries":${retries},"proxyMode":"${proxyMode}"}' 2>/dev/null`);
      const resp=JSON.parse(r.stdout.trim()) as Record<string,unknown>;
      const jobId=resp.jobId as string;
      if(!jobId){send({type:"error",text:"注册启动失败: "+JSON.stringify(resp)});res.end();return;}
      send({type:"started",jobId,message:`任务已启动 (jobId: ${jobId})，开始监控进度…`});

      let done=false; let polls=0; let lastStatus="";
      const poll=async()=>{
        try {
          const jr=await execCmd(`curl -s http://localhost:8081/api/tools/jobs/${jobId} 2>/dev/null`);
          const job=JSON.parse(jr.stdout.trim()) as Record<string,unknown>;
          const st=job.status as string;
          const cnt=job.accountCount as number??0;
          const lastLog=(job.lastLog as Record<string,unknown>)?.message as string??"";
          if(st!==lastStatus||lastLog) send({type:"progress",status:st,accountCount:cnt,lastLog,jobId});
          lastStatus=st;
          if(st==="done"||st==="error"||polls>72){
            done=true;
            // Get full results
            const fr=await execCmd(`curl -s http://localhost:8081/api/tools/outlook/register/${jobId} 2>/dev/null`);
            let accounts:unknown[]=[];
            try { const fd=JSON.parse(fr.stdout.trim()) as Record<string,unknown>; accounts=(fd.accounts as unknown[]??[]); } catch {}
            send({type:"complete",status:st,accountCount:cnt,accounts,jobId,lastLog});
          }
        } catch(e){send({type:"error",text:String(e)});done=true;}
        polls++;
        if(done){try{res.end();}catch(_){}}
        else setTimeout(poll,5000);
      };
      await poll();
    } catch(e){send({type:"error",text:String(e)});try{res.end();}catch(_){}}
    req.on("close",()=>{});
  });

  /* ─── History & Sessions ─── */
  router.get("/claude-code/history",(_req,res)=>{res.json(loadHistory().slice(-100).reverse());});
  router.delete("/claude-code/history",(_req,res)=>{ensureDir(SESSIONS_DIR);fs.writeFileSync(HISTORY_FILE,"[]");res.json({ok:true});});

  router.post("/claude-code/file-read",(req,res)=>{
    const {path:p}=req.body as {path:string}; if(!p){res.status(400).json({error:"path required"});return;}
    const c=readFileSafe(p); if(c===null){res.status(404).json({error:"not found"});return;}
    res.json({content:c,size:c.length,lines:c.split("\n").length});
  });

  router.post("/claude-code/file-write",(req,res)=>{
    const {path:p,content:c,createBackup:bk}=req.body as {path:string;content:string;createBackup?:boolean};
    if(!p||c===undefined){res.status(400).json({error:"path and content required"});return;}
    try{
      const dir=path.dirname(p); if(!fs.existsSync(dir))fs.mkdirSync(dir,{recursive:true});
      if(bk&&fs.existsSync(p))fs.writeFileSync(p+".bak",fs.readFileSync(p));
      fs.writeFileSync(p,c,"utf-8"); res.json({ok:true,path:p,size:c.length});
    }catch(e){res.status(500).json({error:String(e)});}
  });

  router.post("/claude-code/exec",async(req,res)=>{
    const {cmd,cwd}=req.body as {cmd:string;cwd?:string};
    if(!cmd?.trim()){res.status(400).json({error:"cmd required"});return;}
    try {
      const r=await fetch("http://127.0.0.1:9999/exec",{method:"POST",headers:{"Content-Type":"application/json","x-token":"zencoder-exec-2026"},body:JSON.stringify({cmd,cwd:cwd??REPO_DIR,timeout:60000}),signal:AbortSignal.timeout(65000)});
      res.json(await r.json());
    } catch {
      const result=await execWithAutoInstall(cmd,cwd??REPO_DIR);
      res.json({stdout:result.stdout,stderr:result.stderr,code:result.code,success:result.code===0});
    }
  });

  router.get("/claude-code/sessions",(_req,res)=>{
    ensureDir(SESSIONS_DIR);
    try{const s=fs.readdirSync(SESSIONS_DIR).filter(f=>f.endsWith(".json")&&f!=="exec-history.json").map(f=>{try{const r=JSON.parse(fs.readFileSync(path.join(SESSIONS_DIR,f),"utf-8"));return{id:r.id,title:r.title??"未命名",created_at:r.created_at??0,updated_at:r.updated_at??0,msgCount:(r.messages??[]).length,model:r.model??"default"};}catch{return null;}}).filter(Boolean).sort((a:any,b:any)=>b.updated_at-a.updated_at);res.json(s);}catch(e){res.status(500).json({error:String(e)});}
  });

  router.post("/claude-code/sessions",(req,res)=>{
    ensureDir(SESSIONS_DIR);
    const{id,title,messages,model}=req.body;
    if(!id||!messages){res.status(400).json({error:"id and messages required"});return;}
    const file=path.join(SESSIONS_DIR,`${id}.json`);
    const ex=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,"utf-8")):{};
    const now=Date.now();
    fs.writeFileSync(file,JSON.stringify({...ex,id,title:title??ex.title??"新会话",messages,model:model??"default",created_at:ex.created_at??now,updated_at:now},null,2));
    res.json({ok:true,id});
  });

  router.get("/claude-code/sessions/:id",(req,res)=>{
    const f=path.join(SESSIONS_DIR,`${req.params.id}.json`);
    if(!fs.existsSync(f)){res.status(404).json({error:"not found"});return;}
    res.json(JSON.parse(fs.readFileSync(f,"utf-8")));
  });

  router.delete("/claude-code/sessions/:id",(req,res)=>{
    const f=path.join(SESSIONS_DIR,`${req.params.id}.json`);
    if(fs.existsSync(f))fs.unlinkSync(f);
    res.json({ok:true});
  });

  router.patch("/claude-code/sessions/:id",(req,res)=>{
    const f=path.join(SESSIONS_DIR,`${req.params.id}.json`);
    if(!fs.existsSync(f)){res.status(404).json({error:"not found"});return;}
    const d=JSON.parse(fs.readFileSync(f,"utf-8"));
    if(req.body.title)d.title=req.body.title;
    d.updated_at=Date.now();
    fs.writeFileSync(f,JSON.stringify(d,null,2));
    res.json({ok:true});
  });


  /* ─── POST /api/claude-code/converse — UNIFIED multi-turn with Bash ─── */
  router.post("/claude-code/converse", (req, res) => {
    const { sessionId, history = [], message, cwd: reqCwd = REPO_DIR } = req.body as {
      sessionId?: string; history?: Array<{role:string;content:string;events?:unknown[]}>; message: string; cwd?: string;
    };
    if (!message?.trim()) { res.status(400).json({ error: "message required" }); return; }

    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const send = (d: Record<string,unknown>) => { try { res.write(`data: ${JSON.stringify(d)}\n\n`); } catch (_) {} };
    send({ type: "start" });

    // Build conversation history context
    const histCtx = history.slice(-8).map(m => {
      const role = m.role === "user" ? "用户" : "助手";
      const evSummary = (m.events ?? []).filter((e: unknown) => {
        const ev = e as Record<string,unknown>;
        return ev.type === "exec_done" && ev.stdout;
      }).map((e: unknown) => {
        const ev = e as Record<string,unknown>;
        return `[执行: ${(ev.cmd as string ?? "").slice(0,60)}]\n输出: ${(ev.stdout as string ?? "").slice(0,300)}`;
      }).join("\n");
      return `${role}: ${m.content}${evSummary ? "\n"+evSummary : ""}`;
    }).join("\n\n");

    const fullPrompt = `${AGENT_SYS}

${histCtx ? `[对话历史]\n${histCtx}\n\n` : ""}[当前消息]
用户: ${message}

请直接响应并在需要时使用 Bash 工具执行操作。`;

    const tmpFile = `/tmp/cv_${Date.now()}_${Math.random().toString(36).slice(2,6)}.txt`;
    fs.writeFileSync(tmpFile, fullPrompt, "utf-8");
    const shellCmd = `cat '${tmpFile}' | /usr/bin/claude --allowedTools 'Bash' 'Read' 'Write' 'Edit' 'MultiEdit' 'Glob' 'Grep' 'LS' 'TodoRead' 'TodoWrite' 'WebFetch' 'WebSearch' --permission-mode acceptEdits --output-format stream-json --verbose; rm -f '${tmpFile}'`;
    const child = spawn("bash", ["-c", shellCmd], { env: CLAUDE_ENV, cwd: reqCwd });

    let buf = "";
    let completeSent = false;
    child.stdout.on("data", (data: Buffer) => {
      buf += data.toString();
      const lines = buf.split("\n");
      buf = lines.pop() ?? "";
      for (const line of lines) {
        const t = line.trim();
        if (!t) continue;
        try {
          const raw = JSON.parse(t) as Record<string,unknown>;
          const mapped = mapClaudeEvent(raw);
          if (mapped) {
            if (mapped.type === "complete") completeSent = true;
            send(mapped);
          }
        } catch {}
      }
    });
    child.stderr.on("data", (data: Buffer) => {
      const t = data.toString().trim();
      if (t && !t.includes("Loaded MCP") && !t.includes("logLevel") && !t.includes("stream-json")) {
        send({ type: "status", text: t.slice(0, 150) });
      }
    });
    child.on("close", (code, signal) => {
      if (!completeSent) send({ type: "complete", text: "" });
      if (code !== 0 && signal) send({ type: "error", text: `退出异常: code=${code}` });
      // Auto-save session
      if (sessionId) {
        try {
          ensureDir(SESSIONS_DIR);
          const f = path.join(SESSIONS_DIR, `${sessionId}.json`);
          const ex = fs.existsSync(f) ? JSON.parse(fs.readFileSync(f,"utf-8")) : {};
          const now = Date.now();
          const msgs = [...(ex.messages ?? []), { role:"user", content:message, ts: now }];
          fs.writeFileSync(f, JSON.stringify({ ...ex, id:sessionId, updated_at:now, created_at:ex.created_at??now, messages: msgs }, null, 2));
        } catch {}
      }
      try { res.end(); } catch (_) {}
    });
    req.on("close", () => { try { child.kill("SIGTERM"); } catch (_) {} });
  });

  export default router;
  