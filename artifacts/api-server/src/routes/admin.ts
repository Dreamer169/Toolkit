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
<title>Gateway Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0a0f1e;color:#cbd5e1;font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;min-height:100vh}
.topbar{background:#0f172a;border-bottom:1px solid #1e293b;padding:12px 20px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.topbar h1{font-size:15px;font-weight:700;color:#a78bfa;letter-spacing:.5px}
.topbar .spacer{flex:1}
.refresh-btn{background:#1e293b;border:1px solid #334155;color:#94a3b8;border-radius:6px;padding:5px 12px;cursor:pointer;font-size:12px;transition:all .2s}
.refresh-btn:hover{background:#334155;color:#e2e8f0}
.countdown{font-size:11px;color:#64748b}
.main{padding:16px 20px;max-width:1600px;margin:0 auto}
.summary{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
.pill{background:#0f172a;border:1px solid #1e293b;border-radius:8px;padding:10px 16px;min-width:100px}
.pill .val{font-size:22px;font-weight:700;line-height:1.2}
.pill .lbl{font-size:11px;color:#64748b;margin-top:2px}
.pill.ready .val{color:#34d399}.pill.down .val{color:#f87171}
.pill.credit .val{color:#fb923c}.pill.disabled .val{color:#475569}
.pill.total .val{color:#a78bfa}.pill.latency .val{color:#60a5fa}
.sec-hdr{font-size:12px;font-weight:600;color:#94a3b8;letter-spacing:.8px;text-transform:uppercase;margin:20px 0 10px;padding-bottom:6px;border-bottom:1px solid #1e293b}
.provider-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
.pcard{background:#0f172a;border:1px solid #1e293b;border-radius:10px;overflow:hidden}
.pcard-hdr{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #1e293b;background:#111827}
.pcard-hdr .icon{width:28px;height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.pcard-hdr .ptitle{font-weight:600;font-size:13px;color:#e2e8f0}
.pcard-hdr .psub{font-size:11px;color:#64748b}
.pcard-hdr .pcount{margin-left:auto;font-size:11px}
.node-list{padding:6px 0}
.node-row{display:flex;align-items:center;gap:8px;padding:6px 14px;border-bottom:1px solid #0d1526;transition:background .15s}
.node-row:last-child{border-bottom:none}
.node-row:hover{background:#111827}
.node-name{flex:1;min-width:0;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.node-url{font-size:10px;color:#475569;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px}
.node-meta{display:flex;flex-direction:column;align-items:flex-end;gap:2px;flex-shrink:0}
.lat{font-size:11px;color:#60a5fa}.sf{font-size:10px;color:#64748b}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 7px;border-radius:99px;font-size:10px;font-weight:600;white-space:nowrap}
.badge::before{content:'';width:5px;height:5px;border-radius:50%;flex-shrink:0}
.badge.ready{background:#052e1680;color:#34d399;border:1px solid #064e2380}.badge.ready::before{background:#34d399}
.badge.down{background:#3f0c0c80;color:#f87171;border:1px solid #7f1d1d80}.badge.down::before{background:#f87171}
.badge.credit-exhausted{background:#431c0080;color:#fb923c;border:1px solid #7c2d1280}.badge.credit-exhausted::before{background:#fb923c}
.badge.disabled{background:#1e293b80;color:#64748b;border:1px solid #33415580}.badge.disabled::before{background:#64748b}
.matrix-wrap{overflow-x:auto;border:1px solid #1e293b;border-radius:10px;margin-bottom:10px}
.matrix-tbl{border-collapse:collapse;width:100%;white-space:nowrap}
.matrix-tbl th,.matrix-tbl td{border:1px solid #1a2744;padding:0}
.matrix-tbl thead th.model-col{writing-mode:vertical-rl;transform:rotate(180deg);padding:8px 4px;font-size:10px;color:#94a3b8;font-weight:500;max-width:32px;height:110px;cursor:default;vertical-align:bottom}
.matrix-tbl thead th.node-col{text-align:left;padding:8px 12px;font-size:11px;color:#64748b;min-width:220px;position:sticky;left:0;z-index:20;background:#0d1526}
.matrix-tbl tbody tr:hover td{background:#0d1a3088}
.matrix-tbl tbody td.node-cell{position:sticky;left:0;z-index:5;background:#0a1526;padding:5px 10px;min-width:220px;border-right:1px solid #1e293b}
.matrix-tbl tbody td.node-cell .nc-inner{display:flex;align-items:center;gap:6px}
.matrix-tbl tbody td.check-cell{text-align:center;padding:4px;width:34px;font-size:13px}
.check-cell.yes{color:#34d399}.check-cell.no{color:#1a2744}
.tabs{display:flex;gap:2px;margin-bottom:14px;border-bottom:1px solid #1e293b}
.tab{padding:8px 16px;font-size:12px;font-weight:500;color:#64748b;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px;transition:all .2s}
.tab:hover{color:#94a3b8}.tab.active{color:#a78bfa;border-bottom-color:#a78bfa}
.tab-panel{display:none}.tab-panel.active{display:block}
.node-tbl-wrap{overflow-x:auto;border:1px solid #1e293b;border-radius:10px}
.node-tbl{border-collapse:collapse;width:100%}
.node-tbl th{background:#0d1526;padding:8px 12px;text-align:left;font-size:11px;color:#64748b;font-weight:600;letter-spacing:.5px;white-space:nowrap}
.node-tbl td{padding:7px 12px;border-top:1px solid #0d1526;font-size:12px;white-space:nowrap}
.node-tbl tr:hover td{background:#0d1a30}
.latbar{display:inline-block;height:4px;background:#1e3a5f;border-radius:2px;vertical-align:middle;margin-left:4px;width:60px}
.latbar .fill{height:100%;background:#3b82f6;border-radius:2px}
.err-cell{max-width:240px;overflow:hidden;text-overflow:ellipsis;color:#f87171;font-size:11px}
.loading{text-align:center;padding:40px;color:#475569}
.empty{text-align:center;padding:30px;color:#475569;font-size:12px}
.err-banner{background:#3f0c0c40;border:1px solid #7f1d1d80;border-radius:8px;padding:12px 16px;color:#f87171;margin-bottom:16px;display:none}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.legend span{font-size:10px;padding:2px 8px;border-radius:99px}
</style>
</head>
<body>
<div class="topbar">
  <h1>&#9889; Gateway Admin</h1>
  <div class="spacer"></div>
  <span class="countdown" id="cd"></span>
  <button class="refresh-btn" onclick="load()">&#8635; 刷新</button>
</div>
<div class="main">
  <div class="err-banner" id="err"></div>
  <div id="summary" class="summary"><div class="loading">加载中&#8230;</div></div>
  <div class="tabs">
    <div class="tab active" onclick="switchTab('providers',this)">Provider 分组</div>
    <div class="tab" onclick="switchTab('matrix',this)">能力矩阵</div>
    <div class="tab" onclick="switchTab('nodes',this)">节点详情</div>
  </div>
  <div id="tab-providers" class="tab-panel active"><div class="loading">加载中&#8230;</div></div>
  <div id="tab-matrix" class="tab-panel"><div class="loading">加载中&#8230;</div></div>
  <div id="tab-nodes" class="tab-panel"><div class="loading">加载中&#8230;</div></div>
</div>
<script>
var _data=null,_cdVal=30,_cdTimer=null;
function switchTab(id,el){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('.tab-panel').forEach(function(p){p.classList.remove('active');});
  el.classList.add('active');
  document.getElementById('tab-'+id).classList.add('active');
}
function h(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function statusBadge(st){
  var labels={ready:'就绪',down:'故障','credit-exhausted':'额度耗尽',disabled:'已禁用'};
  return '<span class="badge '+h(st)+'">'+(labels[st]||h(st))+'</span>';
}
function fmtLat(ms){
  if(ms==null)return '<span style="color:#475569">—</span>';
  var c=ms<1000?'#34d399':ms<3000?'#fbbf24':'#f87171';
  return '<span style="color:'+c+'">'+ms+'ms</span>';
}
function sfRatio(s,f){
  var total=(s||0)+(f||0);
  if(!total)return '<span style="color:#475569">—</span>';
  var rate=Math.round((s||0)/total*100);
  var c=rate>=90?'#34d399':rate>=70?'#fbbf24':'#f87171';
  return '<span style="color:'+c+'">'+rate+'%</span> <span style="color:#475569">'+(s||0)+'&#10003; '+(f||0)+'&#10007;</span>';
}
function providerMeta(t){
  var m={'remote-sub2api':{icon:'&#128462;',color:'#7c3aed',label:'Remote Sub2API',sub:'内部池路由'},
    'reseek-openai':{icon:'&#129302;',color:'#0ea5e9',label:'Reseek OpenAI',sub:'Replit 集成'},
    'reseek-anthropic':{icon:'&#129504;',color:'#f59e0b',label:'Reseek Anthropic',sub:'Replit 集成'},
    'reseek-gemini':{icon:'&#128142;',color:'#10b981',label:'Reseek Gemini',sub:'Replit 集成'},
    'friend-openai':{icon:'&#128279;',color:'#6366f1',label:'Friend OpenAI',sub:'自注册友节点'}};
  return m[t]||{icon:'&#9881;',color:'#475569',label:t,sub:''};
}
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
  var lc2=al==null?'#475569':al<1000?'#34d399':al<3000?'#fbbf24':'#f87171';
  return '<div class="pill total"><div class="val">'+c.total+'</div><div class="lbl">总节点</div></div>'
    +'<div class="pill ready"><div class="val">'+c.ready+'</div><div class="lbl">就绪</div></div>'
    +'<div class="pill down"><div class="val">'+c.down+'</div><div class="lbl">故障</div></div>'
    +'<div class="pill credit"><div class="val">'+c.credit+'</div><div class="lbl">额度耗尽</div></div>'
    +'<div class="pill disabled"><div class="val">'+c.disabled+'</div><div class="lbl">已禁用</div></div>'
    +(al!=null?'<div class="pill latency"><div class="val" style="color:'+lc2+'">'+al+'ms</div><div class="lbl">平均延迟</div></div>':'');
}
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
    var meta=providerMeta(type);
    var ready=ns.filter(function(n){return n.status==='ready';}).length;
    var down=ns.filter(function(n){return n.status==='down';}).length;
    var cred=ns.filter(function(n){return n.status==='credit-exhausted';}).length;
    var countStr='<span style="color:#34d399">'+ready+'&#8593;</span>'
      +(down?' <span style="color:#f87171">'+down+'&#8595;</span>':'')
      +(cred?' <span style="color:#fb923c">'+cred+'$</span>':'');
    html+='<div class="pcard"><div class="pcard-hdr">'
      +'<div class="icon" style="background:'+meta.color+'22;border:1px solid '+meta.color+'44">'+meta.icon+'</div>'
      +'<div><div class="ptitle">'+h(meta.label)+'</div><div class="psub">'+h(meta.sub)+'</div></div>'
      +'<div class="pcount">'+countStr+' / '+ns.length+'</div></div><div class="node-list">';
    ns.forEach(function(n){
      html+='<div class="node-row">'
        +'<div><div class="node-name">'+h(n.name)+'</div>'
        +(!n.type.startsWith('reseek-')&&n.type!=='remote-sub2api'?'<div class="node-url" title="'+h(n.baseUrl)+'">'+h(n.baseUrl)+'</div>':'')
        +'</div><div class="node-meta">'+statusBadge(n.status)
        +(n.lastLatencyMs!=null?'<span class="lat">'+n.lastLatencyMs+'ms</span>':'')
        +'<span class="sf">'+(n.successes||0)+'&#10003; '+(n.failures||0)+'&#10007;</span>'
        +'</div></div>';
    });
    html+='</div></div>';
  });
  return html+'</div>';
}
function modelFamily(m){
  if(/^gpt-5/.test(m))return 'gpt5';
  if(/^gpt-4\.1/.test(m))return 'gpt41';
  if(/^gpt-4o/.test(m))return 'gpt4o';
  if(/^gpt-4/.test(m))return 'gpt4';
  if(/^o[1-9]/.test(m))return 'o-series';
  if(/claude.*opus-4/.test(m))return 'opus4';
  if(/claude.*sonnet-4/.test(m))return 'sonnet4';
  if(/claude.*haiku-4/.test(m))return 'haiku4';
  if(/claude/.test(m))return 'claude3';
  if(/gemini-2\.5/.test(m))return 'g25';
  if(/gemini-2/.test(m))return 'g20';
  if(/gemini/.test(m))return 'g15';
  return 'other';
}
var famColors={gpt5:'#6366f1',gpt41:'#818cf8',gpt4o:'#60a5fa',gpt4:'#38bdf8','o-series':'#a78bfa',
  opus4:'#f59e0b',sonnet4:'#fbbf24',haiku4:'#fb923c',claude3:'#f97316',
  g25:'#10b981',g20:'#34d399',g15:'#6ee7b7',other:'#94a3b8'};
function shortModel(m){
  return m.replace(/-20\d{6}$/,'').replace('claude-','cl-').replace('gemini-','gem-').replace('gpt-','');
}
function renderMatrix(nodes){
  var friendNodes=nodes.filter(function(n){return n.type==='friend-openai'&&Array.isArray(n.models)&&n.models.length>0;});
  if(!friendNodes.length)return '<div class="empty">暂无友节点模型能力数据。友节点自注册时会上报 models[] 能力列表。</div>';
  var seen={};
  friendNodes.forEach(function(n){n.models.forEach(function(m){seen[m]=1;});});
  var allModels=Object.keys(seen).sort();
  var html='<div class="matrix-wrap"><table class="matrix-tbl"><thead><tr>'
    +'<th class="node-col">节点 / 型号 &#8594;</th>';
  allModels.forEach(function(m){
    var fam=modelFamily(m);
    var col=famColors[fam]||'#94a3b8';
    html+='<th class="model-col" title="'+h(m)+'" style="color:'+col+'">'+h(shortModel(m))+'</th>';
  });
  html+='</tr></thead><tbody>';
  friendNodes.forEach(function(n){
    var mset={};n.models.forEach(function(m){mset[m]=1;});
    var has=allModels.filter(function(m){return mset[m];}).length;
    var pct=allModels.length?Math.round(has/allModels.length*100):0;
    html+='<tr><td class="node-cell"><div class="nc-inner">'+statusBadge(n.status)
      +'<div><div style="font-size:12px;font-weight:500;max-width:160px;overflow:hidden;text-overflow:ellipsis">'+h(n.name)+'</div>'
      +'<div style="font-size:10px;color:#64748b">'+has+'/'+allModels.length+' ('+pct+'%)'
      +(n.lastLatencyMs!=null?' &middot; '+n.lastLatencyMs+'ms':'')+'</div></div>'
      +'</div></td>';
    allModels.forEach(function(m){
      var fam=modelFamily(m);var col=famColors[fam]||'#94a3b8';
      if(mset[m]){html+='<td class="check-cell yes" style="background:'+col+'18" title="'+h(n.name)+'&#10003;'+h(m)+'">&#10003;</td>';}
      else{html+='<td class="check-cell no" title="'+h(n.name)+'&#xd7;'+h(m)+'">&#183;</td>';}
    });
    html+='</tr>';
  });
  html+='</tbody></table></div>';
  var famsSeen={};
  friendNodes.forEach(function(n){n.models.forEach(function(m){famsSeen[modelFamily(m)]=1;});});
  html+='<div class="legend">';
  Object.entries(famColors).forEach(function(e){
    var fam=e[0],col=e[1];
    if(famsSeen[fam])html+='<span style="color:'+col+';background:'+col+'18;border:1px solid '+col+'35">'+h(fam)+'</span>';
  });
  return html+'</div>';
}
function renderNodeTable(nodes){
  var maxLat=1;
  nodes.forEach(function(n){if(n.lastLatencyMs>maxLat)maxLat=n.lastLatencyMs;});
  var html='<div class="node-tbl-wrap"><table class="node-tbl"><thead><tr>'
    +'<th>名称</th><th>类型</th><th>来源</th><th>状态</th>'
    +'<th>延迟</th><th>成功率</th><th>下线至</th><th>型号数</th><th>最后错误</th>'
    +'</tr></thead><tbody>';
  nodes.forEach(function(n){
    var barW=n.lastLatencyMs?Math.min(60,Math.round(n.lastLatencyMs/maxLat*60)):0;
    var downStr=n.downUntil
      ?'<span style="color:#f87171;font-size:11px">'+new Date(n.downUntil).toLocaleTimeString('zh-CN')+'</span>'
      :'<span style="color:#475569">—</span>';
    html+='<tr>'
      +'<td><div style="font-weight:500;max-width:180px;overflow:hidden;text-overflow:ellipsis" title="'+h(n.baseUrl)+'">'+h(n.name)+'</div>'
      +(!n.type.startsWith('reseek-')&&n.type!=='remote-sub2api'?'<div style="font-size:10px;color:#475569;max-width:180px;overflow:hidden;text-overflow:ellipsis">'+h(n.baseUrl)+'</div>':'')
      +'</td>'
      +'<td style="color:#94a3b8;font-size:11px">'+h(n.type)+'</td>'
      +'<td style="color:#64748b">'+h(n.source)+'</td>'
      +'<td>'+statusBadge(n.status)+'</td>'
      +'<td>'+fmtLat(n.lastLatencyMs)+(barW?'<div class="latbar"><div class="fill" style="width:'+barW+'px"></div></div>':'')+'</td>'
      +'<td>'+sfRatio(n.successes,n.failures)+'</td>'
      +'<td>'+downStr+'</td>'
      +'<td style="color:#94a3b8;text-align:center">'+(n.models&&n.models.length?n.models.length:'—')+'</td>'
      +'<td class="err-cell" title="'+h(n.lastError||'')+'">'+h((n.lastError||'').slice(0,80))+'</td>'
      +'</tr>';
  });
  return html+'</tbody></table></div>';
}
async function load(){
  try{
    var resp=await fetch('/api/gateway/health');
    if(!resp.ok)throw new Error('HTTP '+resp.status);
    _data=await resp.json();
    document.getElementById('err').style.display='none';
    var nodes=_data.nodes||[];
    document.getElementById('summary').innerHTML=renderSummary(nodes);
    document.getElementById('tab-providers').innerHTML=renderProviders(nodes);
    document.getElementById('tab-matrix').innerHTML=renderMatrix(nodes);
    document.getElementById('tab-nodes').innerHTML=renderNodeTable(nodes);
    startCountdown();
  }catch(e){
    var el=document.getElementById('err');
    el.style.display='block';
    el.textContent='加载失败：'+e.message;
    startCountdown();
  }
}
function startCountdown(){
  clearInterval(_cdTimer);_cdVal=30;
  var cd=document.getElementById('cd');
  _cdTimer=setInterval(function(){
    _cdVal--;cd.textContent=_cdVal+'s 后自动刷新';
    if(_cdVal<=0){clearInterval(_cdTimer);load();}
  },1000);
}
load();
</script>
</body>
</html>`;

export default router;
