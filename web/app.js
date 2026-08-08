const API = "";
let selectedFile = null;
let selectedType = "";
let currentResult = null;
let useCloud = false;
let cloudEnabled = false;
let cloudAvailable = false;
let localOnly = true;

const STAGES = [
  ["parsing", "解析文档"],
  ["chunking", "文本切片"],
  ["embedding", "生成向量嵌入"],
  ["indexing", "构建向量索引"],
  ["analyzing", "规则审查"],
  ["llm_reasoning", "本地大模型分析"],
  ["reporting", "汇总结果"]
];

const $ = (s) => document.querySelector(s);
function esc(s){return (s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));}
function toast(msg, isErr){const t=$("#toast");t.textContent=msg;t.className="toast"+(isErr?" err":"");t.classList.remove("hidden");setTimeout(()=>t.classList.add("hidden"),3500);}

async function api(path, opts={}){
  const res = await fetch(API+path,{headers:{"Content-Type":"application/json"},...opts});
  const data = await res.json().catch(()=>({}));
  if(!res.ok || data.success===false) throw new Error(data.detail||data.error||data.message||("HTTP "+res.status));
  return data.data;
}

async function refreshHealth(){
  try{
    // /api/health 返回裸 HealthStatus（不带 StandardResponse 信封），
    // 因此此处直接 fetch，不使用 api() 的 data.data 解包逻辑。
    const res = await fetch(API+"/api/health",{headers:{"Content-Type":"application/json"}});
    const h = await res.json();
    const dot=$("#dotModel"), label=$("#modelLabel");
    if(h.model_loaded){dot.className="dot ok"; label.textContent="模型 "+h.model_name+" · "+h.model_device;}
    else{dot.className="dot warn"; label.textContent="规则引擎模式（未加载LLM）";}
    $("#localPill").textContent = h.ocr_available ? "localhost · OCR就绪" : "localhost · 本地运行";
    cloudEnabled = h.cloud_enabled; cloudAvailable = h.cloud_available; localOnly = h.local_only;
    updateProviderSwitch();
    refreshDocList();
  }catch(e){
    $("#dotModel").className="dot err";
    $("#modelLabel").textContent="服务未连接 · 请启动后端";
    $("#localPill").textContent="localhost:8099 (默认)";
    cloudEnabled=false; cloudAvailable=false; localOnly=true;
    updateProviderSwitch();
  }
}

function updateProviderSwitch(){
  const track=$("#switchTrack"), thumb=$("#switchThumb"), sw=$("#providerSwitch"), lab=$("#providerLabel");
  if(!track)return;
  const canUseCloud = cloudEnabled && cloudAvailable && !localOnly;
  if(useCloud && canUseCloud){track.classList.add("on"); lab.textContent="云端"; lab.classList.remove("dim");}
  else{track.classList.remove("on"); lab.textContent="本地"; lab.classList.remove("dim");}
  sw.title = canUseCloud
    ? (useCloud?"当前使用云端大模型":"点击切换到云端大模型")
    : (localOnly?"管理员已禁用云端（local_only=true）":"云端未配置（请设置 providers.cloud 与 API key）");
  sw.style.opacity = canUseCloud ? "1" : "0.5";
}

async function loadProviders(){
  try{
    const data=await api("/api/providers");
    const providers=data.providers||[];
    const cloud = providers.find(p=>p.id==="cloud");
    cloudEnabled = cloud?cloud.enabled:false;
    cloudAvailable = cloud?cloud.available:false;
    localOnly = data.current==="cloud"?false:(cloud?cloud.local_only:true);
    updateProviderSwitch();
  }catch(e){}
}

$("#providerSwitch").addEventListener("click",()=>{
  const canUseCloud = cloudEnabled && cloudAvailable && !localOnly;
  if(!canUseCloud){toast(localOnly?"管理员已禁用云端模型":"云端模型未配置",true); return;}
  useCloud=!useCloud; updateProviderSwitch(); toast(useCloud?"已切换至云端模型":"已切换至本地模型");
});

async function refreshDocList(){
  try{
    const d = await api("/api/documents");
    const wrap=$("#docList");
    if(!d.documents.length){wrap.innerHTML='<div style="color:var(--text-mute);font-size:12px;">暂无文档</div>';return;}
    wrap.innerHTML = d.documents.map(x=>'<div class="doc-item" data-id="'+esc(x.document_id)+'"><div class="doc-icon">&#128196;</div><div class="doc-meta"><div class="doc-name">'+esc(x.document_id)+'</div><div class="doc-sub">'+x.chunks+' 个文本块</div></div></div>').join("");
  }catch(e){}
}

const uploader=$("#uploader"), fileInput=$("#fileInput");
uploader.addEventListener("click",()=>fileInput.click());
["dragover","dragenter"].forEach(ev=>uploader.addEventListener(ev,e=>{e.preventDefault();uploader.classList.add("drag");}));
["dragleave","drop"].forEach(ev=>uploader.addEventListener(ev,e=>{e.preventDefault();uploader.classList.remove("drag");}));
uploader.addEventListener("drop",e=>{if(e.dataTransfer.files[0])handleFile(e.dataTransfer.files[0]);});
fileInput.addEventListener("change",e=>{if(e.target.files[0])handleFile(e.target.files[0]);});
function handleFile(file){selectedFile=file; uploader.querySelector(".uploader-text").innerHTML='已选择：<b>'+esc(file.name)+'</b>'; $("#analyzeBtn").disabled=false;}

$("#typeRow").addEventListener("click",e=>{
  const chip=e.target.closest(".type-chip"); if(!chip)return;
  document.querySelectorAll(".type-chip").forEach(c=>c.classList.remove("active"));
  chip.classList.add("active"); selectedType=chip.dataset.type;
});

$("#analyzeBtn").addEventListener("click",runAnalyze);

async function uploadFile(){
  const fd=new FormData(); fd.append("file",selectedFile);
  const res=await fetch(API+"/api/upload",{method:"POST",body:fd});
  const data=await res.json();
  if(!data.success)throw new Error(data.detail||"上传失败");
  return data.data;
}

function resetStages(){
  $("#stageList").innerHTML = STAGES.map(function(s){return '<li class="stage-item" data-stage="'+s[0]+'"><span class="ico">&#9675;</span>'+s[1]+'</li>';}).join("");
  $("#stageFill").style.width="0%"; $("#progressWrap").classList.add("active");
}
function setStage(stage, status){
  const li=document.querySelector('.stage-item[data-stage="'+stage+'"]'); if(!li)return;
  li.classList.remove("active","done","failed"); li.classList.add(status);
  li.querySelector(".ico").innerHTML = status==="done"?"&#10003;":status==="failed"?"&#10007;":"<span class='spin'>&#8635;</span>";
}

async function runAnalyze(){
  if(!selectedFile)return;
  $("#analyzeBtn").disabled=true; $("#analyzeBtnText").textContent="分析中…"; resetStages();
  try{
    const up=await uploadFile();
    const payload={file_path:up.file_path,use_llm:true,use_cloud:useCloud};
    if(selectedType)payload.doc_type_hint=selectedType;
    const result=await streamAnalyze(payload);
    currentResult=result; renderResult(result); toast("审查完成"); refreshDocList();
  }catch(e){toast("分析失败："+e.message,true); STAGES.forEach(s=>setStage(s[0],"failed"));}
  finally{$("#analyzeBtn").disabled=false; $("#analyzeBtnText").textContent="开始智能审查";}
}
// Appended by build step
function streamAnalyze(payload){
  return new Promise((resolve,reject)=>{
    fetch(API+"/api/analyze/stream",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)}).then(res=>{
      if(!res.ok){reject(new Error("HTTP "+res.status));return;}
      const reader=res.body.getReader(), dec=new TextDecoder(); let buf="";
      function pump(){
        reader.read().then(({done,value})=>{
          if(done)return;
          buf+=dec.decode(value,{stream:true});
          const lines=buf.split("\n"); buf=lines.pop();
          for(const line of lines){
            if(!line.startsWith("data:"))continue;
            try{
              const evt=JSON.parse(line.slice(5).trim());
              if(evt.type==="progress"){
                const d=evt.data; $("#stageFill").style.width=Math.round(d.progress*100)+"%";
                const curIdx=STAGES.findIndex(s=>s[0]===d.stage);
                STAGES.forEach((s,i)=>{if(i<curIdx)setStage(s[0],"done");else if(i===curIdx)setStage(s[0],"active");});
              }else if(evt.type==="result"){resolve(evt.data);}
              else if(evt.type==="error"){reject(new Error(evt.data));}
            }catch(_){}
          }
          pump();
        }).catch(reject);
      }
      pump();
    }).catch(reject);
  });
}

const TYPE_CN={contract:"合同",tender:"招标",technical:"技术方案",prd:"产品需求",policy:"制度文件",general:"通用文档"};

function renderResult(r){
  $("#emptyState").classList.add("hidden");
  const rv=$("#resultView"); rv.classList.remove("hidden");
  const c=r.risk_count_by_level||{};
  const keyPoints=(r.summary.key_points||[]).map(p=>"<li>"+esc(p)+"</li>").join("");
  const parties=(r.summary.parties||[]).length?'<div class="risk-row" style="margin-top:6px;"><b>相关方：</b>'+esc(r.summary.parties.join("、"))+"</div>":"";
  let tabs='<div class="tabs"><div class="tab active" data-tab="risks">风险明细<span class="count">'+r.risks.length+"</span></div>";
  if(r.requirements.length) tabs+='<div class="tab" data-tab="tender">招标要求<span class="count">'+r.requirements.length+"</span></div>";
  if(r.chapter_checks.length) tabs+='<div class="tab" data-tab="tech">技术审查<span class="count">'+r.chapter_checks.length+"</span></div>";
  tabs+="</div>";
  const lvlText=r.overall_risk_level==="High"?"高风险":r.overall_risk_level==="Medium"?"中风险":"低风险";
  rv.innerHTML=
    '<div class="doc-header"><div><div class="doc-title">'+esc(r.summary.title||r.file_name)+'</div>'+
    '<div class="doc-path">'+esc(r.file_path)+'</div></div>'+
    '<div class="risk-badge '+r.overall_risk_level+'">'+lvlText+'</div></div>'+
    '<div class="stat-grid">'+
    '<div class="stat"><div class="stat-num red">'+(c.High||0)+'</div><div class="stat-label">高风险</div></div>'+
    '<div class="stat"><div class="stat-num amber">'+(c.Medium||0)+'</div><div class="stat-label">中风险</div></div>'+
    '<div class="stat"><div class="stat-num green">'+(c.Low||0)+'</div><div class="stat-label">低风险</div></div>'+
    '<div class="stat"><div class="stat-num silver">'+(r.page_count||0)+"页 / "+r.chunk_count+'块</div><div class="stat-label">'+(TYPE_CN[r.summary.doc_type]||"文档")+" · "+(r.llm_used?"AI增强":"规则引擎")+'</div></div></div>'+
    '<ul class="key-points">'+keyPoints+'</ul>'+parties+tabs+
    '<div id="tabContent"></div>'+
    '<div class="report-actions">'+
    '<button class="btn primary" id="btnReportMd">生成 Markdown 报告</button>'+
    '<button class="btn" id="btnReportHtml">生成 HTML 报告</button>'+
    '<button class="btn ghost" id="btnReportJson">导出 JSON</button></div>';
  renderRiskTab(r,"all");
  rv.querySelectorAll(".tab").forEach(t=>t.addEventListener("click",()=>{
    rv.querySelectorAll(".tab").forEach(x=>x.classList.remove("active")); t.classList.add("active");
    if(t.dataset.tab==="risks")renderRiskTab(r,window._riskFilter||"all");
    else if(t.dataset.tab==="tender")renderTenderTab(r);
    else if(t.dataset.tab==="tech")renderTechTab(r);
  }));
  $("#btnReportMd").onclick=()=>genReport(r,"markdown");
  $("#btnReportHtml").onclick=()=>genReport(r,"html");
  $("#btnReportJson").onclick=()=>genReport(r,"json");
}

function renderRiskTab(r,filter){
  window._riskFilter=filter;
  let risks=r.risks;
  if(filter&&filter!=="all")risks=risks.filter(x=>x.risk_level===filter);
  const c=r.risk_count_by_level||{};
  const filters=[["all","全部",r.risks.length],["High","高",c.High||0],["Medium","中",c.Medium||0],["Low","低",c.Low||0]];
  const filterHtml='<div class="filter-row">'+filters.map(f=>'<button class="filter-chip '+(filter===f[0]?"active":"")+'" data-f="'+f[0]+'">'+f[1]+" ("+f[2]+")</button>").join("")+"</div>";
  const html=risks.length?risks.map(x=>
    '<div class="risk-card '+x.risk_level+'">'+
    '<div class="risk-head"><span class="risk-id">'+x.id+'</span><span class="risk-cat">'+esc(x.category)+'</span>'+
    '<span class="lvl-tag '+x.risk_level+'">'+(x.risk_level==="High"?"高":x.risk_level==="Medium"?"中":"低")+'</span></div>'+
    '<div class="risk-issue">'+esc(x.issue)+'</div>'+
    '<div class="risk-row"><b>位置：</b>'+esc(x.location)+'</div>'+
    '<div class="risk-row"><b>说明：</b>'+esc(x.explanation)+'</div>'+
    '<div class="risk-row"><b>建议：</b>'+esc(x.suggestion)+'</div>'+
    '<div class="risk-evidence">'+esc(x.evidence)+'</div></div>').join("")
    :'<div class="empty"><div class="empty-title">未发现该级别风险</div></div>';
  $("#tabContent").innerHTML=filterHtml+html;
  $("#tabContent").querySelectorAll(".filter-chip").forEach(ch=>ch.addEventListener("click",()=>renderRiskTab(r,ch.dataset.f)));
}

function renderTenderTab(r){
  const score=r.capability_match_score??0;
  const deg=Math.round(score*3.6);
  const rows=r.requirements.map((q,i)=>
    "<tr><td>"+q.id+"</td><td>"+esc(q.category)+"</td><td>"+esc(q.requirement)+"</td>"+
    '<td><button class="match-toggle '+(q.matched?"yes":"")+'" data-i="'+i+'">'+(q.matched?"已满足":"待确认")+"</button></td></tr>").join("");
  const missing=(r.missing_capabilities||[]).map(m=>"<li>"+esc(m)+"</li>").join("");
  $("#tabContent").innerHTML=
    '<div class="match-score"><div class="score-ring" style="background:conic-gradient(var(--green) '+deg+'deg,var(--bg-elev) 0deg);"><span>'+score+'%</span></div>'+
    '<div class="score-label">企业能力匹配度（点击右侧"待确认"切换状态实时计算）</div></div>'+
    '<table class="req-table"><thead><tr><th>编号</th><th>类别</th><th>要求</th><th>状态</th></tr></thead><tbody>'+rows+"</tbody></table>"+
    (missing?'<div class="card" style="margin-top:14px;"><div class="section-title" style="margin-bottom:8px;color:var(--amber)">待确认/缺失能力</div><ul style="padding-left:18px;color:var(--text-dim);font-size:13px;">'+missing+"</ul></div>":"");
  $("#tabContent").querySelectorAll(".match-toggle").forEach(btn=>btn.addEventListener("click",()=>{
    const i=+btn.dataset.i; r.requirements[i].matched=!r.requirements[i].matched;
    const matched=r.requirements.filter(x=>x.matched).length;
    r.capability_match_score=+((matched/r.requirements.length)*100).toFixed(1);
    renderTenderTab(r);
  }));
}

function renderTechTab(r){
  const chapters=r.chapter_checks.map(c=>
    '<div class="chapter-row"><div class="chapter-check '+(c.present?"ok":"miss")+'">'+(c.present?"&#10003;":"!")+'</div>'+
    '<div class="chapter-name">'+esc(c.chapter)+'</div><div class="chapter-note">'+esc(c.note)+"</div></div>").join("");
  const sec=(r.security_issues||[]).map(x=>
    '<div class="risk-card High"><div class="risk-issue">'+esc(x.issue)+'</div><div class="risk-row"><b>说明：</b>'+esc(x.explanation)+'</div><div class="risk-row"><b>建议：</b>'+esc(x.suggestion)+"</div></div>").join("");
  const perf=(r.performance_risks||[]).map(x=>
    '<div class="risk-card Medium"><div class="risk-issue">'+esc(x.issue)+'</div><div class="risk-row"><b>说明：</b>'+esc(x.explanation)+'</div><div class="risk-row"><b>建议：</b>'+esc(x.suggestion)+"</div></div>").join("");
  $("#tabContent").innerHTML=
    '<div class="section-title">章节完整性检查</div><div class="card" style="padding:0">'+chapters+"</div>"+
    (sec?'<div class="section-title" style="margin-top:18px">安全问题 ('+r.security_issues.length+")</div>"+sec:"")+
    (perf?'<div class="section-title" style="margin-top:18px">性能风险 ('+r.performance_risks.length+")</div>"+perf:"");
}

async function genReport(r,fmt){
  try{
    const data=await api("/api/report",{method:"POST",body:JSON.stringify({analysis_result:r,format:fmt})});
    window.open(data.download_url,"_blank");
    toast("报告已生成");
  }catch(e){toast("报告生成失败："+e.message,true);}
}

// ---------- Q&A ----------
$("#qaBtn").addEventListener("click",askQuestion);
$("#qaInput").addEventListener("keydown",e=>{if(e.key==="Enter")askQuestion();});
document.querySelectorAll("[data-q]").forEach(b=>b.addEventListener("click",()=>{$("#qaInput").value=b.dataset.q;askQuestion();}));

async function askQuestion(){
  const q=$("#qaInput").value.trim(); if(!q)return;
  const docId=currentResult?currentResult.document_id:null;
  const hist=$("#qaHistory");
  const msg=document.createElement("div"); msg.className="qa-msg";
  msg.innerHTML='<div class="qa-q">Q: '+esc(q)+'</div><div class="qa-a">检索中…</div>';
  hist.prepend(msg);
  $("#qaInput").value="";
  try{
    const data=await api("/api/search",{method:"POST",body:JSON.stringify({query:q,document_id:docId,top_k:5,use_cloud:useCloud})});
    const sources=data.chunks.map((c,i)=>
      '<div class="src-chip"><span class="sc">['+(i+1)+']</span>'+
      esc((c.section?c.section+" · ":"")+(c.page?("第"+c.page+"页 · "):""))+
      '<span class="sp">相似度 '+(c.score*100).toFixed(1)+"%</span><br>"+esc(c.text.slice(0,160))+"…</div>").join("");
    msg.querySelector(".qa-a").textContent=data.answer||"（未找到相关内容）";
    if(sources)msg.insertAdjacentHTML("beforeend",'<div class="qa-src"><div class="qa-src-title">引用来源 ('+data.chunks.length+')</div>'+sources+"</div>");
  }catch(e){msg.querySelector(".qa-a").textContent="查询失败："+e.message;}
}

// ---------- Init ----------
loadProviders();
refreshHealth();
setInterval(refreshHealth,15000);
