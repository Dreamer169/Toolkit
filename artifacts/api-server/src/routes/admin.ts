import { Router } from "express";

const router = Router();

router.get("/admin", (_req, res) => {
  res.setHeader("Content-Type", "text/html; charset=utf-8");
  res.send(ADMIN_HTML);
});

const ADMIN_HTML = /* html */`<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gateway Portal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1e;color:#cbd5e1;font-family:'Inter',system-ui,sans-serif;font-size:13px;min-height:100vh}
::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:#0a0f1e}::-webkit-scrollbar-thumb{background:#1e293b;border-radius:3px}
.topbar{background:#0f172a;border-bottom:1px solid #1e293b;padding:12px 24px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.topbar-logo{width:32px;height:32px;border-radius:8px;background:linear-gradient(135deg,#6366f1,#a78bfa);display:flex;align-items:center;justify-content:center;font-weight:700;color:#fff;font-size:13px;flex-shrink:0}
.topbar-title{font-weight:700;font-size:15px;color:#a78bfa;letter-spacing:.3px}
.topbar-sub{font-size:11px;color:#64748b;margin-top:1px}
.topbar-right{margin-left:auto;display:flex;align-items:center;gap:12px}
.status-pill{display:flex;align-items:center;gap:6px;background:#052e1680;border:1px solid #064e2380;border-radius:99px;padding:4px 12px;font-size:11px;color:#34d399}
.status-dot{width:6px;height:6px;border-radius:50%;background:#34d399;display:inline-block}
.status-dot.bad{background:#f87171}
.countdown{font-size:11px;color:#475569}
.refresh-btn{background:#1e293b;border:1px solid #334155;color:#94a3b8;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px;transition:all .2s}
.refresh-btn:hover{background:#334155;color:#e2e8f0}
.main{max-width:1280px;margin:0 auto;padding:24px}
.err-banner{background:#3f0c0c40;border:1px solid #7f1d1d80;border-radius:8px;padding:12px 16px;color:#f87171;margin-bottom:20px;display:none}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:24px}
.pill{background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:10px 18px;min-width:90px}
.pill .val{font-size:22px;font-weight:700;line-height:1.2}
.pill .lbl{font-size:11px;color:#64748b;margin-top:2px}
.card{background:#0f172a;border:1px solid #1e293b;border-radius:12px;padding:20px;margin-bottom:16px}
.sec-hdr{font-size:11px;font-weight:600;color:#64748b;letter-spacing:.8px;text-transform:uppercase;margin-bottom:12px}
.url-box{display:flex;align-items:center;gap:12px}
.url-code{flex:1;background:#0d1117;border:1px solid #1e293b;border-radius:6px;padding:8px 12px;font-size:12px;color:#a78bfa;font-family:Menlo,monospace;word-break:break-all}
.copy-btn{background:#1e293b;border:1px solid #334155;color:#94a3b8;border-radius:6px;padding:4px 12px;cursor:pointer;font-size:11px;transition:all .2s;flex-shrink:0}
.copy-btn:hover{background:#334155;color:#e2e8f0}
.copy-btn.ok{background:#052e1680;border-color:#064e2380;color:#34d399}
.url-hint{margin-top:10px;font-size:11px;color:#64748b}
.url-hint code{color:#94a3b8}
.ep-list{display:flex;flex-direction:column;gap:8px}
.ep-row{display:flex;align-items:center;gap:10px;padding:8px 10px;background:#0d1526;border-radius:8px}
.method{font-weight:700;font-size:10px;padding:2px 7px;border-radius:4px;min-width:36px;text-align:center}
.method.GET{color:#34d399;background:#34d39918}.method.POST{color:#60a5fa;background:#60a5fa18}.method.DELETE{color:#f87171;background:#f8717118}
.ep-path{font-size:11px;color:#a78bfa;font-family:Menlo,monospace;flex:1}
.ep-desc{font-size:11px;color:#64748b;flex:2}
.auth-badge{font-size:10px;color:#fbbf24;background:#451a0380;padding:1px 6px;border-radius:4px}
.auth-row{background:#0d1526;border-radius:8px;padding:8px 12px;margin-bottom:6px}
.auth-lbl{font-size:10px;color:#64748b;margin-bottom:3px}
.auth-val{font-size:11px;color:#94a3b8;font-family:Menlo,monospace}
.key-input{width:100%;background:#0d1117;border:1px solid #334155;border-radius:6px;padding:7px 12px;color:#e2e8f0;font-size:12px;outline:none;margin-bottom:12px}
.key-input:focus{border-color:#6366f1}
.tabs{display:flex;gap:2px;border-bottom:1px solid #1e293b;margin-bottom:16px}
.tab{padding:8px 16px;font-size:12px;font-weight:500;color:#64748b;cursor:pointer;border:none;background:transparent;border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .2s}
.tab:hover{color:#94a3b8}.tab.active{color:#a78bfa;border-bottom-color:#a78bfa}
.tab-panel{display:none}.tab-panel.active{display:block}
.provider-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.pcard{background:#111827;border:1px solid #1e293b;border-radius:10px;overflow:hidden}
.pcard-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #1e293b;background:#0f172a}
.pcard-icon{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.pcard-title{font-weight:600;font-size:13px;color:#e2e8f0}
.pcard-sub{font-size:11px;color:#64748b}
.pcard-count{margin-left:auto;font-size:11px}
.node-row{display:flex;align-items:center;gap:8px;padding:7px 14px;border-bottom:1px solid #0d1526}
.node-row:last-child{border-bottom:none}
.node-name{flex:1;min-width:0;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.node-url{font-size:10px;color:#475569;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px}
.node-meta{display:flex;flex-direction:column;align-items:flex-end;gap:2px;flex-shrink:0}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:99px;font-size:10px;font-weight:600;white-space:nowrap}
.badge::before{content:'';width:5px;height:5px;border-radius:50%;flex-shrink:0}
.badge.ready{background:#052e1680;color:#34d399;border:1px solid #064e2380}.badge.ready::before{background:#34d399}
.badge.down{background:#3f0c0c80;color:#f87171;border:1px solid #7f1d1d80}.badge.down::before{background:#f87171}
.badge.credit-exhausted{background:#431c0080;color:#fb923c;border:1px solid #7c2d1280}.badge.credit-exhausted::before{background:#fb923c}
.badge.disabled{background:#1e293b80;color:#64748b;border:1px solid #33415580}.badge.disabled::before{background:#64748b}
.lat{font-size:11px;color:#60a5fa}.sf{font-size:10px;color:#64748b}
.matrix-wrap{overflow-x:auto;border:1px solid #1e293b;border-radius:10px}
.matrix-tbl{border-collapse:collapse;width:100%;white-space:nowrap}
.matrix-tbl th,.matrix-tbl td{border:1px solid #1a2744;padding:0}
.matrix-tbl thead th.model-col{writing-mode:vertical-rl;transform:rotate(180deg);padding:8px 4px;font-size:10px;color:#94a3b8;font-weight:500;max-width:32px;height:110px;cursor:default;vertical-align:bottom}
.matrix-tbl thead th.node-col{text-align:left;padding:8px 12px;font-size:11px;color:#64748b;min-width:220px;position:sticky;left:0;z-index:20;background:#0d1526}
.matrix-tbl tbody td.node-cell{position:sticky;left:0;z-index:5;background:#0a1526;padding:5px 10px;min-width:220px;border-right:1px solid #1e293b}
.matrix-tbl tbody td.check-cell{text-align:center;padding:4px;width:34px;font-size:13px}
.check-cell.yes{color:#34d399}.check-cell.no{color:#1a2744}
.node-tbl-wrap{overflow-x:auto;border:1px solid #1e293b;border-radius:10px}
.node-tbl{border-collapse:collapse;width:100%}
.node-tbl th{background:#0d1526;padding:8px 12px;text-align:left;font-size:11px;color:#64748b;font-weight:600;letter-spacing:.5px;white-space:nowrap}
.node-tbl td{padding:7px 12px;border-top:1px solid #0d1526;font-size:12px;white-space:nowrap}
.node-tbl tr:hover td{background:#0d1a30}
.err-cell{max-width:200px;overflow:hidden;text-overflow:ellipsis;color:#f87171;font-size:11px}
.model-groups{display:flex;flex-direction:column;gap:12px}
.mgroup{background:#111827;border:1px solid #1e293b;border-radius:10px;overflow:hidden}
.mgroup-hdr{padding:8px 14px;border-bottom:1px solid #1e293b;background:#0f172a;font-size:12px;color:#94a3b8;font-weight:600;letter-spacing:.5px;display:flex;align-items:center;gap:8px}
.mgroup-count{font-size:10px;color:#475569;font-weight:400}
.mgroup-body{display:flex;flex-wrap:wrap;gap:6px;padding:12px}
.model-chip{display:flex;align-items:center;gap:6px;background:#0d1526;border:1px solid #1e293b;border-radius:6px;padding:4px 10px}
.model-id{font-size:11px;color:#a78bfa;font-family:Menlo,monospace}
.codeblock{position:relative;margin-bottom:8px}
.codeblock pre{background:#0d1117;border:1px solid #1e293b;border-radius:8px;padding:14px;font-size:11px;color:#e2e8f0;overflow-x:auto;line-height:1.6;font-family:Menlo,monospace}
.codeblock .cb-copy{position:absolute;top:8px;right:8px}
.guide-item{margin-bottom:20px}
.guide-name{font-weight:600;font-size:13px;color:#e2e8f0;margin-bottom:8px}
.guide-steps{padding-left:18px;display:flex;flex-direction:column;gap:4px;margin-bottom:8px}
.guide-steps li{font-size:12px;color:#94a3b8}
.loading{text-align:center;padding:40px;color:#475569}
.empty{text-align:center;padding:30px;color:#475569;font-size:12px}
</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-logo">GW</div>
  <div>
    <div class="topbar-title">⚡ Gateway Portal</div>
    <div class="topbar-sub">友节点轮询池管理界面</div>
  </div>
  <div class="topbar-right">
    <div class="status-pill" id="status-pill">
      <span class="status-dot" id="status-dot"></span>
      <span id="status-txt">加载中…</span>
    </div>
    <span class="countdown" id="cd"></span>
    <button class="refresh-btn" onclick="load()">↻ 刷新</button>
  </div>
</div>

<div class="main">
  <div class="err-banner" id="err"></div>

  <!-- Summary -->
  <div class="summary" id="summary"><div class="loading">加载中…</div></div>

  <!-- Base URL -->
  <div class="card">
    <div class="sec-hdr">OpenAI 兼容接入地址</div>
    <div class="url-box">
      <div class="url-code" id="base-url-display">—</div>
      <button class="copy-btn" onclick="copyText(document.getElementById('base-url-display').textContent,this)">复制</button>
    </div>
    <div class="url-hint">在任何 OpenAI 兼容客户端中将 <code>base_url</code> 设为此地址，即可通过网关路由请求。</div>
  </div>

  <!-- Endpoints -->
  <div class="card">
    <div class="sec-hdr">API 端点</div>
    <div class="ep-list" id="ep-list"></div>
  </div>

  <!-- Auth -->
  <div class="card">
    <div class="sec-hdr">认证方式</div>
    <input class="key-input" id="api-key-input" placeholder="输入 API Key（用于生成示例命令）" oninput="onKeyInput()">
    <div id="auth-rows"></div>
  </div>

  <!-- Node Tabs -->
  <div class="card">
    <div class="tabs">
      <button class="tab active" onclick="switchTab('providers',this)">Provider 分组</button>
      <button class="tab" onclick="switchTab('matrix',this)">能力矩阵</button>
      <button class="tab" onclick="switchTab('nodes',this)">节点详情</button>
    </div>
    <div id="tab-providers" class="tab-panel active"><div class="loading">加载中…</div></div>
    <div id="tab-matrix" class="tab-panel"><div class="loading">加载中…</div></div>
    <div id="tab-nodes" class="tab-panel"><div class="loading">加载中…</div></div>
  </div>

  <!-- Models -->
  <div class="card">
    <div class="sec-hdr">可用模型 <span id="model-count" style="color:#475569;font-weight:400"></span></div>
    <div class="model-groups" id="model-groups"><div class="loading">加载中…</div></div>
  </div>

  <!-- Quick Test -->
  <div class="card">
    <div class="sec-hdr">快速测试</div>
    <div style="font-size:11px;color:#64748b;margin-bottom:6px">查询可用模型</div>
    <div class="codeblock"><pre id="curl-models"></pre><div class="cb-copy"><button class="copy-btn" onclick="copyText(document.getElementById('curl-models').textContent,this)">复制</button></div></div>
    <div style="font-size:11px;color:#64748b;margin:14px 0 6px">发起 Chat 补全请求</div>
    <div class="codeblock"><pre id="curl-chat"></pre><div class="cb-copy"><button class="copy-btn" onclick="copyText(document.getElementById('curl-chat').textContent,this)">复制</button></div></div>
  </div>

  <!-- Client Guide -->
  <div class="card">
    <div class="sec-hdr">客户端接入指南</div>
    <div id="client-guide"></div>
  </div>

  <div style="text-align:center;color:#334155;font-size:11px;padding:16px 0">
    Gateway Portal · 数据来源：<a href="/api/gateway/health" style="color:#6366f1" target="_blank">Health API</a>
  </div>
</div>

<script>
var _data=null,_models=[],_cdVal=30,_cdTimer=null,_apiKey='';

var BASE_ORIGIN=window.location.origin;
var V1_BASE=BASE_ORIGIN+'/api/gateway/v1';
var HEALTH_URL='/api/gateway/health';
var MODELS_URL='/api/gateway/v1/models';

var ENDPOINTS=[
  {method:'GET',path:'/api/gateway/health',desc:'节点健康状态总览',auth:false},
  {method:'GET',path:'/api/gateway/v1/models',desc:'可用模型列表（OpenAI 格式）',auth:false},
  {method:'POST',path:'/api/gateway/v1/chat/completions',desc:'Chat 补全（OpenAI 兼容）',auth:true},
  {method:'POST',path:'/api/gateway/v1/responses',desc:'Responses API（OpenAI 兼容）',auth:true},
  {method:'GET',path:'/api/gateway/nodes/status',desc:'节点状态实时快照',auth:false},
  {method:'DELETE',path:'/api/gateway/nodes/:id',desc:'移除节点',auth:false},
];

var PROVIDER_META={
  'remote-sub2api':{label:'Remote Sub2API',color:'#7c3aed',sub:'内部池路由'},
  'reseek-openai':{label:'Reseek OpenAI',color:'#0ea5e9',sub:'Replit 集成'},
  'reseek-anthropic':{label:'Reseek Anthropic',color:'#f59e0b',sub:'Replit 集成'},
  'reseek-gemini':{label:'Reseek Gemini',color:'#10b981',sub:'Replit 集成'},
  'friend-openai':{label:'Friend OpenAI',color:'#6366f1',sub:'自注册友节点'},
};

function h(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function copyText(text,btn){
  navigator.clipboard.writeText(text.trim()).then(function(){
    var orig=btn.textContent;btn.textContent='已复制';btn.classList.add('ok');
    setTimeout(function(){btn.textContent=orig;btn.classList.remove('ok');},1500);
  });
}

function statusBadge(st){
  var labels={ready:'就绪',down:'故障','credit-exhausted':'额度耗尽',disabled:'已禁用'};
  return '<span class="badge '+h(st)+'">'+(labels[st]||h(st))+'</span>';
}
function fmtLat(ms){
  if(ms==null)return '<span style="color:#475569">—</span>';
  var c=ms<1000?'#34d399':ms<3000?'#fbbf24':'#f87171';
  return '<span style="color:'+c+'">'+ms+'ms</span>';
}

function switchTab(id,el){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  el.classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
}

/* ─── Summary ─── */
function renderSummary(nodes){
  var c={total:0,ready:0,down:0,credit:0,disabled:0},tl=0,lc=0;
  nodes.forEach(function(n){
    c.total++;
    if(n.status==='ready')c.ready++;
    else if(n.status==='down')c.down++;
    else if(n.status==='credit-exhausted')c.credit++;
    else if(n.status==='disabled')c.disabled++;
    if(n.lastLatencyMs!=null){tl+=n.lastLatencyMs;lc++;}
  });
  var al=lc?Math.round(tl/lc):null;
  var pills=[
    {lbl:'总节点',val:c.total,color:'#a78bfa'},
    {lbl:'就绪',val:c.ready,color:'#34d399'},
    {lbl:'故障',val:c.down,color:'#f87171'},
    {lbl:'额度耗尽',val:c.credit,color:'#fb923c'},
    {lbl:'已禁用',val:c.disabled,color:'#475569'},
  ];
  var html='';
  pills.forEach(function(p){
    html+='<div class="pill"><div class="val" style="color:'+p.color+'">'+p.val+'</div><div class="lbl">'+p.lbl+'</div></div>';
  });
  if(al!=null){
    var lc2=al<1000?'#34d399':al<3000?'#fbbf24':'#f87171';
    html+='<div class="pill"><div class="val" style="color:'+lc2+'">'+al+'ms</div><div class="lbl">平均延迟</div></div>';
  }
  document.getElementById('summary').innerHTML=html;
  /* status pill */
  var dot=document.getElementById('status-dot');
  var txt=document.getElementById('status-txt');
  if(c.ready>0){dot.className='status-dot';txt.textContent=c.ready+' / '+c.total+' 就绪';}
  else{dot.className='status-dot bad';txt.textContent='0 / '+c.total+' 就绪';}
}

/* ─── Endpoints ─── */
function renderEndpoints(){
  var html='';
  ENDPOINTS.forEach(function(ep){
    html+='<div class="ep-row">'
      +'<span class="method '+ep.method+'">'+ep.method+'</span>'
      +'<span class="ep-path">'+h(ep.path)+'</span>'
      +'<span class="ep-desc">'+h(ep.desc)+'</span>'
      +(ep.auth?'<span class="auth-badge">需认证</span>':'')
      +'<button class="copy-btn" onclick="copyText(\''+h(BASE_ORIGIN+ep.path)+'\',this)">复制</button>'
      +'</div>';
  });
  document.getElementById('ep-list').innerHTML=html;
}

/* ─── Auth ─── */
function renderAuth(){
  var k=_apiKey||'YOUR_API_KEY';
  var rows=[
    ['Authorization Header（推荐）','Authorization: Bearer '+k],
    ['x-goog-api-key（Gemini 客户端）','x-goog-api-key: '+k],
    ['URL 参数','?key='+k],
  ];
  var html='';
  rows.forEach(function(r){
    html+='<div class="auth-row"><div class="auth-lbl">'+r[0]+'</div><div class="auth-val">'+h(r[1])+'</div></div>';
  });
  document.getElementById('auth-rows').innerHTML=html;
}

function onKeyInput(){
  _apiKey=document.getElementById('api-key-input').value;
  renderAuth();
  renderCurls();
}

/* ─── Providers ─── */
function renderProviders(nodes){
  var groups={};
  nodes.forEach(function(n){if(!groups[n.type])groups[n.type]=[];groups[n.type].push(n);});
  var order=['remote-sub2api','reseek-openai','reseek-anthropic','reseek-gemini','friend-openai'];
  var types=[];
  order.forEach(function(t){if(groups[t])types.push(t);});
  Object.keys(groups).forEach(function(t){if(types.indexOf(t)<0)types.push(t);});
  var html='<div class="provider-grid">';
  types.forEach(function(type){
    var ns=groups[type];
    var meta=PROVIDER_META[type]||{label:type,color:'#475569',sub:''};
    var ready=ns.filter(function(n){return n.status==='ready';}).length;
    var down=ns.filter(function(n){return n.status==='down';}).length;
    html+='<div class="pcard"><div class="pcard-hdr">'
      +'<div class="pcard-icon" style="background:'+meta.color+'22;border:1px solid '+meta.color+'44">⚙</div>'
      +'<div><div class="pcard-title">'+h(meta.label)+'</div><div class="pcard-sub">'+h(meta.sub)+'</div></div>'
      +'<div class="pcard-count"><span style="color:#34d399">'+ready+'↑</span>'
      +(down?' <span style="color:#f87171">'+down+'↓</span>':'')
      +' <span style="color:#64748b">/ '+ns.length+'</span></div></div>';
    ns.forEach(function(n){
      html+='<div class="node-row"><div style="flex:1;min-width:0">'
        +'<div class="node-name">'+h(n.name)+'</div>'
        +(!n.type.startsWith('reseek-')&&n.type!=='remote-sub2api'?'<div class="node-url">'+h(n.baseUrl)+'</div>':'')
        +'</div><div class="node-meta">'+statusBadge(n.status)
        +(n.lastLatencyMs!=null?'<span class="lat">'+n.lastLatencyMs+'ms</span>':'')
        +'<span class="sf">'+(n.successes||0)+'✓ '+(n.failures||0)+'✗</span>'
        +'</div></div>';
    });
    html+='</div>';
  });
  document.getElementById('tab-providers').innerHTML=html+'</div>';
}

/* ─── Matrix ─── */
function renderMatrix(nodes){
  var friendNodes=nodes.filter(function(n){return n.type==='friend-openai'&&Array.isArray(n.models)&&n.models.length>0;});
  if(!friendNodes.length){document.getElementById('tab-matrix').innerHTML='<div class="empty">暂无友节点模型能力数据。友节点自注册时会上报 models[] 能力列表。</div>';return;}
  var seen={};
  friendNodes.forEach(function(n){n.models.forEach(function(m){seen[m]=1;});});
  var allModels=Object.keys(seen).sort();
  var html='<div class="matrix-wrap"><table class="matrix-tbl"><thead><tr><th class="node-col">节点 / 模型 →</th>';
  allModels.forEach(function(m){html+='<th class="model-col" title="'+h(m)+'">'+h(m.replace(/-20\d{6}$/,'').replace('claude-','cl-').replace('gemini-','gem-').replace('gpt-',''))+'</th>';});
  html+='</tr></thead><tbody>';
  friendNodes.forEach(function(n){
    var mset={};n.models.forEach(function(m){mset[m]=1;});
    var has=allModels.filter(function(m){return mset[m];}).length;
    html+='<tr><td class="node-cell"><div style="display:flex;align-items:center;gap:6px">'+statusBadge(n.status)
      +'<div><div style="font-size:12px;font-weight:500">'+h(n.name)+'</div>'
      +'<div style="font-size:10px;color:#64748b">'+has+'/'+allModels.length+(n.lastLatencyMs!=null?' · '+n.lastLatencyMs+'ms':'')+'</div>'
      +'</div></div></td>';
    allModels.forEach(function(m){
      if(mset[m]){html+='<td class="check-cell yes">✓</td>';}
      else{html+='<td class="check-cell no">·</td>';}
    });
    html+='</tr>';
  });
  document.getElementById('tab-matrix').innerHTML=html+'</tbody></table></div>';
}

/* ─── Node Table ─── */
function renderNodeTable(nodes){
  var html='<div class="node-tbl-wrap"><table class="node-tbl"><thead><tr>'
    +'<th>名称</th><th>类型</th><th>来源</th><th>状态</th><th>延迟</th><th>成功/失败</th><th>型号数</th><th>最后错误</th>'
    +'</tr></thead><tbody>';
  nodes.forEach(function(n){
    html+='<tr><td><div style="font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis" title="'+h(n.baseUrl)+'">'+h(n.name)+'</div>'
      +(!n.type.startsWith('reseek-')&&n.type!=='remote-sub2api'?'<div style="font-size:10px;color:#475569;max-width:180px;overflow:hidden;text-overflow:ellipsis">'+h(n.baseUrl)+'</div>':'')
      +'</td>'
      +'<td style="color:#94a3b8;font-size:11px">'+h(n.type)+'</td>'
      +'<td style="color:#64748b">'+h(n.source)+'</td>'
      +'<td>'+statusBadge(n.status)+'</td>'
      +'<td>'+fmtLat(n.lastLatencyMs)+'</td>'
      +'<td><span style="color:#34d399">'+(n.successes||0)+'✓</span> <span style="color:#f87171">'+(n.failures||0)+'✗</span></td>'
      +'<td style="color:#94a3b8;text-align:center">'+(n.models&&n.models.length?n.models.length:'—')+'</td>'
      +'<td class="err-cell" title="'+h(n.lastError||'')+'">'+h((n.lastError||'').slice(0,60)||'—')+'</td>'
      +'</tr>';
  });
  document.getElementById('tab-nodes').innerHTML=html+'</tbody></table></div>';
}

/* ─── Models ─── */
function renderModels(models){
  var groups={};
  models.forEach(function(m){
    var k=m.gateway_node||m.owned_by||'other';
    if(!groups[k])groups[k]=[];
    groups[k].push(m);
  });
  document.getElementById('model-count').textContent='('+models.length+')';
  var html='';
  Object.entries(groups).forEach(function(e){
    var gname=e[0],items=e[1];
    html+='<div class="mgroup"><div class="mgroup-hdr">'+h(gname)+' <span class="mgroup-count">'+items.length+' 个模型</span></div><div class="mgroup-body">';
    items.forEach(function(m){
      html+='<div class="model-chip"><span class="model-id">'+h(m.id)+'</span>'
        +'<button class="copy-btn" style="padding:2px 8px;font-size:10px" onclick="copyText(\''+h(m.id)+'\',this)">复制</button></div>';
    });
    html+='</div></div>';
  });
  document.getElementById('model-groups').innerHTML=html||'<div class="empty">暂无模型数据</div>';
}

/* ─── Curls ─── */
function renderCurls(){
  var k=_apiKey||'YOUR_API_KEY';
  var firstModel=_models.length?_models[0].id:'gpt-4o';
  document.getElementById('curl-models').textContent=
    'curl '+V1_BASE+'/models \\\n  -H "Authorization: Bearer '+k+'"';
  document.getElementById('curl-chat').textContent=
    'curl '+V1_BASE+'/chat/completions \\\n'
    +'  -H "Authorization: Bearer '+k+'" \\\n'
    +'  -H "Content-Type: application/json" \\\n'
    +'  -d \'{\n'
    +'    "model": "'+firstModel+'",\n'
    +'    "messages": [{"role": "user", "content": "Hello!"}],\n'
    +'    "stream": false\n'
    +'  }\'';
}

/* ─── Client Guide ─── */
function renderGuide(){
  var k=_apiKey||'YOUR_API_KEY';
  var firstModel=_models.length?_models[0].id:'gpt-4o';
  var guides=[
    {name:'SillyTavern',steps:['API → API 类型选择 Chat Completion → OpenAI','API Base URL 填写：'+V1_BASE,'API Key 填写您的 Key','点击「Connect」确认连接']},
    {name:'CherryStudio / OpenCat',steps:['设置 → AI 服务 → 添加自定义服务商','API Base URL：'+V1_BASE,'选择模型 → 点击刷新即可加载可用模型']},
    {name:'Cursor / Continue（IDE 插件）',steps:['openaiBaseURL: "'+V1_BASE+'"','apiKey: "'+k+'"','选择任意模型即可']},
    {name:'Python openai SDK',code:'from openai import OpenAI\nclient = OpenAI(\n    base_url="'+V1_BASE+'",\n    api_key="'+k+'"\n)\nresp = client.chat.completions.create(\n    model="'+firstModel+'",\n    messages=[{"role": "user", "content": "Hello!"}]\n)\nprint(resp.choices[0].message.content)'},
  ];
  var html='';
  guides.forEach(function(g){
    html+='<div class="guide-item"><div class="guide-name">'+h(g.name)+'</div>';
    if(g.steps){html+='<ul class="guide-steps">';g.steps.forEach(function(s){html+='<li>'+h(s)+'</li>';});html+='</ul>';}
    if(g.code){html+='<div class="codeblock"><pre>'+h(g.code)+'</pre><div class="cb-copy"><button class="copy-btn" onclick="copyText(this.closest(\'.codeblock\').querySelector(\'pre\').textContent,this)">复制</button></div></div>';}
    html+='</div>';
  });
  document.getElementById('client-guide').innerHTML=html;
}

/* ─── Countdown ─── */
function startCountdown(){
  clearInterval(_cdTimer);_cdVal=30;
  var cd=document.getElementById('cd');
  _cdTimer=setInterval(function(){
    _cdVal--;cd.textContent=_cdVal+'s 后刷新';
    if(_cdVal<=0){clearInterval(_cdTimer);load();}
  },1000);
}

/* ─── Init ─── */
async function load(){
  try{
    document.getElementById('err').style.display='none';
    var [hRes,mRes]=await Promise.all([fetch(HEALTH_URL),fetch(MODELS_URL)]);
    if(!hRes.ok)throw new Error('Health HTTP '+hRes.status);
    _data=await hRes.json();
    var nodes=_data.nodes||[];
    renderSummary(nodes);
    renderProviders(nodes);
    renderMatrix(nodes);
    renderNodeTable(nodes);
    if(mRes.ok){var md=await mRes.json();_models=md.data||[];}
    renderModels(_models);
    document.getElementById('base-url-display').textContent=V1_BASE;
    renderEndpoints();
    renderAuth();
    renderCurls();
    renderGuide();
    startCountdown();
  }catch(e){
    var el=document.getElementById('err');el.style.display='block';el.textContent='加载失败：'+e.message;
    startCountdown();
  }
}
load();
</script>
</body>
</html>`;

export default router;
