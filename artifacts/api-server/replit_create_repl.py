#!/usr/bin/env python3
"""
replit_create_repl.py — 使用 Reseek session cookie 创建包含 agent 代码的 Repl。
用法: python3 replit_create_repl.py '<json>'
JSON: { "cookie": "...", "repl_name": "my-agent", "gateway_url": "http://..." }
"""
import sys, json, urllib.request, urllib.error

if len(sys.argv) < 2:
    print(json.dumps({"ok": False, "error": "缺少参数"}))
    sys.exit(1)

args = json.loads(sys.argv[1])
cookie      = args.get("cookie", "")
repl_name   = args.get("repl_name", "reseek-agent")
gateway_url = args.get("gateway_url", "http://45.205.27.69:8080")

if not cookie:
    print(json.dumps({"ok": False, "error": "cookie 不能为空"}))
    sys.exit(1)

GRAPHQL_URL = "https://replit.com/graphql"
HEADERS = {
    "Content-Type": "application/json",
    "Cookie": cookie,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://replit.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

def gql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(GRAPHQL_URL, data=payload, headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": str(e)}

# ── Step 1: 获取当前用户信息 ──────────────────────────────────────────────────
print("[create_repl] 获取用户信息...", flush=True)
me_query = """
query { currentUser { id username } }
"""
me = gql(me_query)
username = me.get("data", {}).get("currentUser", {}).get("username", "")
if not username:
    print(json.dumps({"ok": False, "error": f"无法获取用户名: {json.dumps(me)[:200]}"}))
    sys.exit(0)
print(f"[create_repl] 用户: {username}", flush=True)

# ── Step 2: 创建 Repl ─────────────────────────────────────────────────────────
# agent 代码通过 files 直接注入
AGENT_CODE = r"""
const http = require("http");
const https = require("https");
const url = require("url");

const PORT = parseInt(process.env.PORT || "3000", 10);
const GATEWAY_URL = (process.env.SELF_REGISTER_URL || "GATEWAY_PLACEHOLDER").replace(/\/$/, "");
const MY_URL = process.env.MY_GATEWAY_URL || (process.env.REPLIT_DEV_DOMAIN ? `https://${process.env.REPLIT_DEV_DOMAIN}` : "");
const NODE_NAME = process.env.NODE_NAME || process.env.REPL_OWNER || "reseek-agent";

const AI_OPENAI_BASE = process.env.AI_INTEGRATIONS_OPENAI_BASE_URL || "";
const AI_OPENAI_KEY  = process.env.AI_INTEGRATIONS_OPENAI_API_KEY  || "";
const AI_ANTH_BASE   = process.env.AI_INTEGRATIONS_ANTHROPIC_BASE_URL || "";
const AI_ANTH_KEY    = process.env.AI_INTEGRATIONS_ANTHROPIC_API_KEY  || "";

function fetchJson(u, o) {
  return new Promise((res, rej) => {
    const p = new url.URL(u), lib = p.protocol==="https:" ? https : http;
    const req = lib.request({hostname:p.hostname,port:p.port||(p.protocol==="https:"?443:80),path:p.pathname+p.search,method:o.method||"GET",headers:o.headers||{}}, r=>{let d="";r.on("data",c=>{d+=c});r.on("end",()=>{try{res({status:r.statusCode,data:JSON.parse(d)})}catch{res({status:r.statusCode,data:{}})}})});
    req.on("error",rej);
    if(o.body)req.write(JSON.stringify(o.body));
    req.end();
  });
}

async function selfRegister() {
  if (!MY_URL) return;
  try {
    const r = await fetchJson(`${GATEWAY_URL}/api/gateway/self-register`, {method:"POST",headers:{"Content-Type":"application/json"},body:{gatewayUrl:MY_URL,name:NODE_NAME,openaiBaseUrl:AI_OPENAI_BASE,openaiApiKey:AI_OPENAI_KEY,anthropicBaseUrl:AI_ANTH_BASE,anthropicApiKey:AI_ANTH_KEY}});
    console.log("[agent] registered:", r.status, JSON.stringify(r.data).slice(0,80));
  } catch(e){console.error("[agent] register failed:", e.message);}
}

http.createServer((req,res)=>{
  res.setHeader("Content-Type","application/json");
  if(req.url==="/"||req.url==="/health"){res.writeHead(200);res.end(JSON.stringify({ok:true,name:NODE_NAME,time:new Date().toISOString()}));return;}
  res.writeHead(404);res.end('{"error":"not found"}');
}).listen(PORT,"0.0.0.0",()=>{
  console.log(`[agent] port=${PORT} gateway=${GATEWAY_URL}`);
  selfRegister();
  setInterval(selfRegister, 5*60*1000);
});
""".replace("GATEWAY_PLACEHOLDER", gateway_url)

PKG_JSON = json.dumps({"name":"reseek-agent","version":"1.0.0","main":"index.js","scripts":{"start":"node index.js"}})

create_query = """
mutation CreateRepl($input: CreateReplInput!) {
  createRepl(input: $input) {
    ... on Repl { id url slug }
    ... on UserError { message }
  }
}
"""
variables = {
    "input": {
        "title": repl_name,
        "isPrivate": True,
        "language": "nodejs",
    }
}
print("[create_repl] 创建 Repl...", flush=True)
create_result = gql(create_query, variables)
repl = create_result.get("data", {}).get("createRepl", {})
repl_id  = repl.get("id", "")
repl_url = repl.get("url", "")
if not repl_id:
    print(json.dumps({"ok": False, "error": f"创建 Repl 失败: {json.dumps(create_result)[:200]}"}))
    sys.exit(0)
print(f"[create_repl] Repl 已创建: {repl_url}", flush=True)

# ── Step 3: 写入 agent 文件 ───────────────────────────────────────────────────
write_query = """
mutation WriteFile($replId: String!, $path: String!, $content: String!) {
  replFile(replId: $replId, path: $path, content: $content) { path }
}
"""
for fname, content in [("index.js", AGENT_CODE), ("package.json", PKG_JSON)]:
    res = gql(write_query, {"replId": repl_id, "path": fname, "content": content})
    print(f"[create_repl] 写入 {fname}: {json.dumps(res).get('data', {})}", flush=True)

print(json.dumps({"ok": True, "repl_id": repl_id, "repl_url": repl_url, "username": username}))
