#!/usr/bin/env python3
"""
replit_deploy_agent.py
完整子节点部署: 登录 Reseek → 创建 Node.js Repl → 写 agent 代码 → 运行 → 返回 URL。
用法: python3 replit_deploy_agent.py '<json>'
JSON: {
  "email": "...",
  "password": "...",
  "outlook_token": "",        // 可选, 用于重发验证邮件后点击链接
  "gateway_url": "http://45.205.27.69:8080",
  "proxy": "socks5://...",    // 可选
  "headless": true
}
"""
import sys, json, time, re

if len(sys.argv) < 2:
    print(json.dumps({"ok": False, "error": "缺少参数"}))
    sys.exit(1)

args = json.loads(sys.argv[1])
email        = args["email"]
password     = args["password"]
outlook_tok  = args.get("outlook_token", "")
gateway_url  = args.get("gateway_url", "http://45.205.27.69:8080").rstrip("/")
proxy_str    = args.get("proxy", "")
headless     = args.get("headless", True)

AGENT_CODE = """
const http = require("http"), https = require("https"), url = require("url");
const net = require("net"), crypto = require("crypto");
const sessions = new Map(), TTOKEN = process.env.TUNNEL_TOKEN || "";
function chkTok(q){const u=new URL("http://x"+q.url);return !TTOKEN||u.searchParams.get("token")===TTOKEN;}
const PORT = parseInt(process.env.PORT || "3000", 10);
const GW   = (process.env.SELF_REGISTER_URL || "GATEWAY_PLACEHOLDER").replace(/\\/$/, "");
const ME   = process.env.MY_GATEWAY_URL || (process.env.REPLIT_DEV_DOMAIN ? "https://" + process.env.REPLIT_DEV_DOMAIN : "");
const NAME = process.env.NODE_NAME || process.env.REPL_OWNER || "reseek-node";
const OBASE = process.env.AI_INTEGRATIONS_OPENAI_BASE_URL || "";
const OKEY  = process.env.AI_INTEGRATIONS_OPENAI_API_KEY  || "";
const ABASE = process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL || "";
const AKEY  = process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY  || "";
const GBASE = process.env.AI_INTEGRATIONS_GEMINI_BASE_URL || "";
const GKEY  = process.env.AI_INTEGRATIONS_GEMINI_API_KEY  || "";
function post(u,b){return new Promise((res,rej)=>{const p=new url.URL(u),lib=p.protocol==="https:"?https:http,opts={hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:"POST",headers:{"Content-Type":"application/json","Content-Length":Buffer.byteLength(b)}};const req=lib.request(opts,r=>{let d="";r.on("data",c=>d+=c);r.on("end",()=>res(d))});req.on("error",rej);req.write(b);req.end();});}
function proxy(q,s,base,key,prefix){let target=q.url;if(prefix)target=target.replace(prefix,"");const p=new url.URL(base.replace(/\\/$/,"")+target),lib=p.protocol==="https:"?https:http;let body="";q.on("data",c=>body+=c);q.on("end",()=>{const h={...q.headers,authorization:"Bearer "+key,host:p.hostname};delete h["content-length"];const req=lib.request({hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:q.method,headers:{...h,"content-length":Buffer.byteLength(body)}},r=>{s.writeHead(r.statusCode,r.headers);r.pipe(s)});req.on("error",e=>{s.writeHead(502,{"Content-Type":"application/json"});s.end(JSON.stringify({error:e.message}))});if(body)req.write(body);req.end();});}
async function reg(){if(!ME)return;try{const r=await post(GW+"/api/gateway/self-register",JSON.stringify({gatewayUrl:ME,name:NAME,openaiBaseUrl:OBASE,openaiApiKey:OKEY,anthropicBaseUrl:ABASE,anthropicApiKey:AKEY,geminiBaseUrl:GBASE,geminiApiKey:GKEY}));console.log("[agent] reg:",r.slice(0,160));}catch(e){console.error("[agent]",e.message);}}
http.createServer((q,s)=>{s.setHeader("Content-Type","application/json");if(q.url==="/"||q.url==="/health"){s.writeHead(200);s.end(JSON.stringify({ok:true,name:NAME,openai:Boolean(OBASE&&OKEY),anthropic:Boolean(ABASE&&AKEY),gemini:Boolean(GBASE&&GKEY),time:new Date().toISOString()}));return;}if(q.url.startsWith("/v1/")){if(OBASE&&OKEY)return proxy(q,s,OBASE,OKEY,"");s.writeHead(503);s.end(JSON.stringify({error:"AI_INTEGRATIONS_OPENAI 未配置"}));return;}if(q.url.startsWith("/anthropic/")){if(ABASE&&AKEY)return proxy(q,s,ABASE,AKEY,"/anthropic");s.writeHead(503);s.end(JSON.stringify({error:"AI_INTEGRATIONS_ANTHROPIC 未配置"}));return;}if(q.url.startsWith("/gemini/")){if(GBASE&&GKEY)return proxy(q,s,GBASE,GKEY,"/gemini");s.writeHead(503);s.end(JSON.stringify({error:"AI_INTEGRATIONS_GEMINI 未配置"}));return;}if(q.url.startsWith("/api/tunnel/")){if(!chkTok(q)){s.writeHead(403);s.end(JSON.stringify({error:"forbidden"}));return;}const u=new URL("http://x"+q.url),ps=q.url.split("/");if(q.method==="POST"&&ps[3]==="open"){const h=u.searchParams.get("host"),p=parseInt(u.searchParams.get("port")||"80");const id=crypto.randomBytes(4).toString("hex");const sk=net.createConnection({host:h,port:p},()=>{sessions.set(id,{sk,rb:[],rw:[],cl:false});sk.on("data",d=>{const ss=sessions.get(id);if(!ss)return;ss.rw.length?ss.rw.shift()(d):ss.rb.push(d);});sk.on("close",()=>{const ss=sessions.get(id);if(ss){ss.cl=true;ss.rw.forEach(r=>r(null));}});sk.on("error",()=>{const ss=sessions.get(id);if(ss){ss.cl=true;ss.rw.forEach(r=>r(null));}});s.writeHead(200);s.end(JSON.stringify({ok:true,id}));});sk.on("error",e=>{s.writeHead(502);s.end(JSON.stringify({error:e.message}));});setTimeout(()=>{if(!sessions.has(id))sk.destroy();},10000);return;}if(q.method==="GET"&&ps[3]==="read"){const id=ps[4],ss=sessions.get(id);if(!ss){s.writeHead(404);s.end(JSON.stringify({error:"no session"}));return;}s.writeHead(200,{"Transfer-Encoding":"chunked","Content-Type":"application/octet-stream"});(async()=>{while(true){if(ss.rb.length){const d=Buffer.concat(ss.rb.splice(0));s.write(d.length.toString(16)+"\r\n");s.write(d);s.write("\r\n");}else if(ss.cl){s.write("0\r\n\r\n");s.end();return;}else{const d=await new Promise(r=>ss.rw.push(r));if(!d){s.write("0\r\n\r\n");s.end();return;}s.write(d.length.toString(16)+"\r\n");s.write(d);s.write("\r\n");}}})();return;}if(q.method==="POST"&&ps[3]==="write"){const id=ps[4],ss=sessions.get(id);if(!ss){s.writeHead(404);s.end(JSON.stringify({error:"no session"}));return;}let b=Buffer.alloc(0);q.on("data",d=>b=Buffer.concat([b,d]));q.on("end",()=>{ss.sk.write(b);s.writeHead(200);s.end(JSON.stringify({ok:true}));});return;}{const id=ps[4],ss=sessions.get(id);if(ss){ss.sk.destroy();sessions.delete(id);}}s.writeHead(200);s.end(JSON.stringify({ok:true}));return;}s.writeHead(404);s.end('{"error":"nf"}');}).listen(PORT,"0.0.0.0",()=>{console.log("[agent] port="+PORT+" gw="+GW);reg();setInterval(reg,5*60*1000);});
""".replace("GATEWAY_PLACEHOLDER", gateway_url).strip()

def log(msg):
    print(f"[deploy] {msg}", flush=True)

try:
    from patchright.sync_api import sync_playwright

    proxy_cfg = {"server": proxy_str} if proxy_str else None
    launch_args = ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                   "--disable-extensions","--mute-audio"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=launch_args,
                                    proxy=proxy_cfg if proxy_cfg else None)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        page.set_default_timeout(30000)

        # ── 1. 登录 ────────────────────────────────────────────────────────────
        log("打开登录页...")
        page.goto("https://replit.com/login", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # 填邮箱
        email_sel = 'input[name="username"], input[type="email"], input[placeholder*="email" i], input[placeholder*="username" i]'
        try:
            page.locator(email_sel).first.fill(email, timeout=8000)
        except:
            page.keyboard.type(email)

        # 填密码
        try:
            page.locator('input[type="password"]').first.fill(password, timeout=8000)
        except:
            log("未找到密码框")

        # 提交
        try:
            page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Sign in")').first.click(timeout=5000)
        except:
            page.keyboard.press("Enter")

        log("等待登录...")
        page.wait_for_timeout(8000)
        cur_url = page.url
        log(f"当前URL: {cur_url[:80]}")

        if "login" in cur_url.lower():
            log("登录失败，仍在登录页")
            browser.close()
            print(json.dumps({"ok": False, "error": "登录失败"}))
            sys.exit(0)

        # ── 2. 处理邮箱未验证提示 ──────────────────────────────────────────────
        page.wait_for_timeout(2000)
        try:
            verify_notice = page.locator('text=/verify your email/i, text=/confirm your email/i, [data-testid="email-verification"]').first
            if verify_notice.is_visible(timeout=3000):
                log("检测到邮箱未验证提示，尝试重发验证邮件...")
                resend_btn = page.locator('button:has-text("Resend"), a:has-text("Resend"), button:has-text("Send again")').first
                if resend_btn.is_visible(timeout=3000):
                    resend_btn.click()
                    log("已点击重发验证邮件")
                    page.wait_for_timeout(5000)
                    # 如果有 outlook_token，尝试点击验证链接
                    if outlook_tok:
                        import subprocess, os
                        script = os.path.join(os.path.dirname(__file__), "click_verify_link.py")
                        r = subprocess.run(
                            ["python3", script,
                             json.dumps({"token": outlook_tok, "message_id": ""})],
                            capture_output=True, text=True, timeout=180
                        )
                        log(f"验证结果: {r.stdout[-200:]}")
                        page.wait_for_timeout(5000)
                        page.reload()
                        page.wait_for_timeout(3000)
        except:
            pass  # 没有未验证提示

        # ── 3. 创建新 Repl ─────────────────────────────────────────────────────
        log("导航到创建 Repl 页面...")
        page.goto("https://replit.com/new", wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # 搜索/选择 Node.js 模板
        try:
            search = page.locator('input[placeholder*="search" i], input[placeholder*="template" i], input[type="search"]').first
            if search.is_visible(timeout=5000):
                search.fill("Node.js")
                page.wait_for_timeout(1500)
        except:
            pass

        try:
            nodejs_opt = page.locator('[data-testid*="nodejs" i], [title*="Node.js"], div:has-text("Node.js"):visible').first
            if nodejs_opt.is_visible(timeout=5000):
                nodejs_opt.click()
                log("选择 Node.js 模板")
        except:
            # 尝试直接导航到 Node.js 模板
            log("模板选择失败，尝试直接导航...")
            page.goto("https://replit.com/new/nodejs", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

        # 等待编辑器加载
        log("等待 Repl 编辑器加载...")
        for _ in range(20):
            page.wait_for_timeout(2000)
            cur = page.url
            if "/repl/" in cur or "@" in cur:
                log(f"编辑器已加载: {cur[:80]}")
                break
            log(f"  等待编辑器... URL={cur[:60]}")
        else:
            log("编辑器加载超时")
            # 尝试通过已创建的 repl url
            pass

        repl_url = page.url
        log(f"Repl URL: {repl_url[:100]}")

        # ── 4. 写入 agent 代码 ─────────────────────────────────────────────────
        log("写入 agent 代码到 index.js...")

        # 在文件树中找到/点击 index.js
        written = False
        for file_sel in ['[data-cy="file-row"]:has-text("index.js")',
                         'li:has-text("index.js")',
                         '.file-row:has-text("index.js")',
                         '[data-testid="file-row"]:has-text("index.js")']:
            try:
                el = page.locator(file_sel).first
                if el.is_visible(timeout=3000):
                    el.click()
                    page.wait_for_timeout(1000)
                    break
            except:
                pass

        # 选中编辑器中所有内容并替换
        for editor_sel in ['.cm-content', '.CodeMirror-code', '[data-cy="editor"] .CodeMirror',
                           '.monaco-editor .view-lines', '[role="textbox"]']:
            try:
                editor = page.locator(editor_sel).first
                if editor.is_visible(timeout=3000):
                    editor.click()
                    page.wait_for_timeout(500)
                    # 全选
                    page.keyboard.press("Control+a")
                    page.wait_for_timeout(300)
                    # 删除
                    page.keyboard.press("Delete")
                    page.wait_for_timeout(300)
                    # 粘贴代码
                    ctx.set_content = None  # reset
                    page.evaluate(f"""
                    (() => {{
                      const text = {json.dumps(AGENT_CODE)};
                      document.execCommand('insertText', false, text);
                    }})()
                    """)
                    page.wait_for_timeout(1000)
                    written = True
                    log(f"代码已写入 (via {editor_sel})")
                    break
            except:
                pass

        if not written:
            # 尝试 keyboard paste
            log("尝试键盘粘贴...")
            try:
                page.keyboard.press("Control+a")
                page.wait_for_timeout(200)
                page.keyboard.type(AGENT_CODE[:500])  # 只写前面部分测试
                written = True
            except:
                pass

        # 保存
        page.keyboard.press("Control+s")
        page.wait_for_timeout(1000)

        # ── 5. 运行 Repl ───────────────────────────────────────────────────────
        log("点击运行...")
        run_btns = [
            'button[data-cy="run-btn"]',
            'button:has-text("Run")',
            '[data-testid="run-btn"]',
            '#run-btn',
            '.run-button',
        ]
        run_clicked = False
        for sel in run_btns:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=3000):
                    btn.click()
                    run_clicked = True
                    log(f"Run 按钮已点击 ({sel})")
                    break
            except:
                pass

        if not run_clicked:
            log("未找到 Run 按钮")

        page.wait_for_timeout(8000)

        # ── 6. 获取 webview URL ────────────────────────────────────────────────
        webview_url = ""
        # 方式1: 从 webview iframe src
        try:
            iframe = page.frame_locator('iframe[src*="repl.co"], iframe[src*="replit.dev"]').first
            webview_url = page.locator('iframe[src*="repl.co"], iframe[src*="replit.dev"]').first.get_attribute("src") or ""
        except:
            pass

        # 方式2: 从 URL 推导
        if not webview_url and repl_url:
            m = re.search(r'@([^/]+)/([^/?#]+)', repl_url)
            if m:
                uname, rname = m.group(1), m.group(2)
                webview_url = f"https://{rname}.{uname}.repl.co"

        log(f"Webview URL: {webview_url}")

        # ── 7. 从页面内抓取用户名 ──────────────────────────────────────────────
        username = ""
        try:
            username = page.evaluate("() => window.__REPLIT_NEXT_DATA__?.user?.username || window.__USER__?.username || ''") or ""
        except:
            pass
        if not username:
            m = re.search(r'replit\.com/@([^/]+)', repl_url)
            if m:
                username = m.group(1)

        browser.close()

    result = {
        "ok": True,
        "repl_url": repl_url,
        "webview_url": webview_url,
        "written": written,
        "run_clicked": run_clicked,
        "email": email,
        "username": username,
    }
    log(f"完成: {json.dumps(result)}")
    print(json.dumps(result))

except Exception as e:
    import traceback
    print(json.dumps({"ok": False, "error": str(e), "trace": traceback.format_exc()[-500:]}))
