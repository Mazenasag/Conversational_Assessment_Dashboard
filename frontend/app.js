const app=document.getElementById('app');
const state={
  data:null,
  page:'Dashboard',
  week:'All weeks',
  klass:'All classes',
  N:5,
  nav:false,
  sidebarCollapsed:localStorage.getItem('ca-sidebar-collapsed')==='1'
};
const plotRegistry={};
let plotlyPromise=null;

function loadPlotly(){
  if(window.Plotly)return Promise.resolve(window.Plotly);
  if(plotlyPromise)return plotlyPromise;
  plotlyPromise=new Promise((resolve,reject)=>{
    const s=document.createElement('script');
    s.src='https://cdn.plot.ly/plotly-2.35.2.min.js';
    s.async=true;
    s.onload=()=>resolve(window.Plotly);
    s.onerror=()=>reject(new Error('Could not load Plotly.'));
    document.head.appendChild(s);
  });
  return plotlyPromise;
}

function registerPlot(kind,data,opts={}){
  const id=`plot-${Math.random().toString(36).slice(2,10)}`;
  plotRegistry[id]={kind,data,opts};
  return `<div id="${id}" class="plotly-chart" style="height:${opts.height||190}px"></div>`;
}
const STOP=new Set("the a an and or but if then than to of in on for with at by from is are was were be been being it this that these those i you he she we they my your our their me him her them as so do does did can could would should will just yes no not dont don't idk ok okay yeah yep nah what why how when where who which have has had".split(' '));
const bool=v=>v===true||v===1||String(v).toLowerCase()==='true';
const num=v=>v==null||v===''?NaN:Number(v); const pct=(n,d)=>d?Math.round(100*n/d):0;
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const median=a=>{a=a.filter(Number.isFinite).sort((x,y)=>x-y);if(!a.length)return NaN;const m=Math.floor(a.length/2);return a.length%2?a[m]:(a[m-1]+a[m])/2};
const hist=a=>Object.entries(a.filter(Number.isFinite).reduce((m,v)=>{const k=Math.round(v);m[k]=(m[k]||0)+1;return m},{})).map(([value,count])=>({value:Number(value),count})).sort((a,b)=>a.value-b.value);
const pack=o=>encodeURIComponent(JSON.stringify(o));
const unpack=s=>{try{return JSON.parse(decodeURIComponent(s))}catch{return null}};
function detailPayload(title,subtitle,rows=[]){return {title,subtitle,rows}}
function detailRows(items,mapper){return items.map(mapper).slice(0,100)}
function uniq(arr){return [...new Set(arr.filter(v=>v!=null&&String(v).trim()!==''))]}
function compactList(arr,max=3){
  const u=uniq(arr).map(String);
  if(!u.length)return '—';
  return u.length<=max?u.join(', '):`${u.slice(0,max).join(', ')} +${u.length-max} more`;
}
function hoverLine(label,value){return `<b>${esc(label)}:</b> ${esc(value)}`}
function stat(label,value,foot){return `<div class="stat-chip"><div class="stat-label">${esc(label)}</div><div class="stat-value">${esc(value)}</div><div class="stat-foot">${esc(foot)}</div></div>`}
function title(t,s){return `<h2 class="section-title">${esc(t)}</h2><p class="section-sub">${esc(s)}</p>`}
function bars(data,{value='value',count='count',color='#4F46E5',height=190,chart='chart',xTitle='',yTitle='Students'}={}){
  if(!data.length)return `<div class="empty">No data available.</div>`;
  return registerPlot('bar',data,{value,count,color,height,chart,xTitle,yTitle});
}
function ranked(data){
  if(!data.length)return `<div class="empty">No non-serious answers detected.</div>`;
  return registerPlot('ranked',data,{height:205});
}
function rateBars(data){
  if(!data.length)return `<div class="empty">No classified coding/conceptual attempts available.</div>`;
  return registerPlot('rate',data,{height:205});
}
function filtered(){let sessions=state.data.sessions.filter(s=>(state.week==='All weeks'||Number(s.week)===Number(state.week))&&(state.klass==='All classes'||s.class===state.klass));const keys=new Set(sessions.map(s=>`${s.week}::${s.conversation_id}`));return{sessions,turns:state.data.turns.filter(t=>keys.has(`${t.week}::${t.conversation_id}`)),requests:state.data.requests.filter(r=>keys.has(`${r.week}::${r.conversation_id}`))}}
function compute({sessions,turns},N){
  const students=[...new Set(sessions.map(s=>String(s.student_id)).filter(Boolean))];
  const asked=sessions.filter(s=>bool(s.completion_code_requested));
  const first=asked.map(s=>num(s.first_code_request_after_questions)).filter(Number.isFinite);
  const ans=turns.filter(t=>bool(t.is_answer_attempt));
  const ns=ans.filter(t=>String(t.seriousness).toLowerCase()==='non-serious');

  const q1=hist(first).map(bucket=>{
    const rows=asked.filter(s=>Math.round(num(s.first_code_request_after_questions))===bucket.value);
    const studentsInBucket=uniq(rows.map(s=>String(s.student_id))).length;
    const classesInBucket=compactList(rows.map(s=>s.class));
    const weeksInBucket=compactList(rows.map(s=>`Week ${s.week}`));
    const receivedInBucket=rows.filter(s=>bool(s.completion_code_received)).length;
    return {...bucket,
      tooltip:`After ${bucket.value} question(s): ${studentsInBucket} student(s)`,
      hoverHtml:[
        `<b>Code request after ${bucket.value} question(s)</b>`,
        hoverLine('Students',studentsInBucket),
        hoverLine('Share of code-requesting students',`${pct(studentsInBucket,asked.length)}%`),
        hoverLine('Class',classesInBucket),
        hoverLine('Week',weeksInBucket),
        hoverLine('Received code',`${receivedInBucket}/${rows.length}`)
      ].join('<br>'),
      detail:detailPayload(`Code request after ${bucket.value} question(s)`,`${studentsInBucket} student(s) in this bucket`,detailRows(rows,s=>({
        Student:s.student_id,Conversation:s.conversation_id,Week:s.week,Class:s.class,Received:bool(s.completion_code_received)?'Yes':'No'
      })))
    }
  });

  const nsMap=new Map(students.map(s=>[s,0]));
  ns.forEach(t=>{const sid=String(t.student_id);nsMap.set(sid,(nsMap.get(sid)||0)+1)});
  const counts=[...nsMap.values()];
  const q2=hist(counts).map(bucket=>{
    const ids=[...nsMap.entries()].filter(([,c])=>c===bucket.value).map(([id])=>id);
    const bucketSessions=sessions.filter(s=>ids.includes(String(s.student_id)));
    return {...bucket,
      tooltip:`${bucket.value} non-serious answer(s): ${bucket.count} student(s)`,
      hoverHtml:[
        `<b>${bucket.value} non-serious answer(s)</b>`,
        hoverLine('Students',bucket.count),
        hoverLine('Share of students',`${pct(bucket.count,students.length)}%`),
        hoverLine('Class',compactList(bucketSessions.map(s=>s.class))),
        hoverLine('Week',compactList(bucketSessions.map(s=>`Week ${s.week}`))),
        hoverLine('Student IDs',compactList(ids,4))
      ].join('<br>'),
      detail:detailPayload(`${bucket.value} non-serious answer(s)`,`${bucket.count} student(s)`,ids.map(id=>({Student:id,Class:compactList(sessions.filter(s=>String(s.student_id)===id).map(s=>s.class)),Week:compactList(sessions.filter(s=>String(s.student_id)===id).map(s=>`Week ${s.week}`)),Examples:ns.filter(r=>String(r.student_id)===id).map(r=>String(r.student_answer||'').replace(/\s+/g,' ').trim()).filter(Boolean).slice(0,3).join(' · ')||'—'})))
    }
  });
  const rank=[...nsMap.entries()].filter(([,c])=>c>0).sort((a,b)=>b[1]-a[1]).map(([student_id,count])=>{
    const rows=ns.filter(r=>String(r.student_id)===student_id);
    const studentSessions=sessions.filter(s=>String(s.student_id)===student_id);
    return {student_id,count,
      tooltip:`Student ${student_id}: ${count} non-serious answer(s)`,
      hoverHtml:[
        `<b>Student ${esc(student_id)}</b>`,
        hoverLine('Non-serious answers',count),
        hoverLine('Class',compactList(studentSessions.map(s=>s.class))),
        hoverLine('Week',compactList(studentSessions.map(s=>`Week ${s.week}`))),
        hoverLine('Conversations',compactList(studentSessions.map(s=>s.conversation_id),3))
      ].join('<br>'),
      detail:detailPayload(`Student ${student_id}`,`${count} non-serious answer(s)`,detailRows(rows,r=>({Week:r.week,Class:r.class||compactList(studentSessions.map(s=>s.class)),Conversation:r.conversation_id,Answer:String(r.student_answer||'').replace(/\s+/g,' ').trim(),Question:r.assistant_question||'—'})))
    }
  });

  const wc={};
  ns.forEach(r=>(String(r.student_answer||'').toLowerCase().match(/[a-zA-Z][a-zA-Z']{1,}/g)||[]).forEach(w=>{w=w.replace(/^'+|'+$/g,'');if(w.length>2&&!STOP.has(w))wc[w]=(wc[w]||0)+1}));
  const words=Object.entries(wc).map(([word,count])=>({word,count})).sort((a,b)=>b.count-a.count||a.word.localeCompare(b.word)).slice(0,35);

  const res={Correct:0,'Partially correct':0,Incorrect:0};
  ans.forEach(r=>{if(res[r.answer_result]!=null)res[r.answer_result]++});
  const classified=Object.values(res).reduce((a,b)=>a+b,0);
  const resultBars=['Incorrect','Partially correct','Correct'].map(label=>{
    const rows=ans.filter(r=>r.answer_result===label);
    const rowSessions=sessions.filter(s=>rows.some(r=>String(r.conversation_id)===String(s.conversation_id)&&Number(r.week)===Number(s.week)));
    return {label,count:rows.length,
      tooltip:`${label}: ${rows.length} answer(s) · ${pct(rows.length,classified)}%`,
      hoverHtml:[
        `<b>${esc(label)}</b>`,
        hoverLine('Answers',rows.length),
        hoverLine('Share of classified answers',`${pct(rows.length,classified)}%`),
        hoverLine('Students',uniq(rows.map(r=>String(r.student_id))).length),
        hoverLine('Class',compactList(rowSessions.map(s=>s.class))),
        hoverLine('Week',compactList(rows.map(r=>`Week ${r.week}`))),
        hoverLine('Question type',compactList(rows.map(r=>r.question_type||'—')))
      ].join('<br>'),
      detail:detailPayload(label,`${rows.length} classified answer(s)`,detailRows(rows,r=>({Student:r.student_id,Conversation:r.conversation_id,Week:r.week,Class:compactList(sessions.filter(s=>String(s.conversation_id)===String(r.conversation_id)&&Number(s.week)===Number(r.week)).map(s=>s.class)),Type:r.question_type||'—',Question:r.assistant_question||'—',Answer:r.student_answer||'—'})))
    }
  });

  const flags=['is_followup','is_follow_up','followup_question','is_followup_question'];
  const flag=flags.find(c=>turns.some(t=>Object.prototype.hasOwnProperty.call(t,c)));
  const conv=sessions.map(s=>String(s.conversation_id));
  const fm=new Map(conv.map(c=>[c,0]));
  if(flag) turns.forEach(t=>{if(bool(t[flag]))fm.set(String(t.conversation_id),(fm.get(String(t.conversation_id))||0)+1)});
  else ans.forEach(t=>{const v=num(t.followups_to_recovery);if(Number.isFinite(v))fm.set(String(t.conversation_id),(fm.get(String(t.conversation_id))||0)+v)});
  const fv=[...fm.values()];
  const follow=hist(fv).map(bucket=>{
    const convs=[...fm.entries()].filter(([,c])=>Math.round(c)===bucket.value).map(([id])=>id);
    const rows=sessions.filter(s=>convs.includes(String(s.conversation_id)));
    return {...bucket,
      tooltip:`${bucket.value} follow-up question(s): ${bucket.count} session(s)`,
      hoverHtml:[
        `<b>${bucket.value} follow-up question(s)</b>`,
        hoverLine('Sessions',bucket.count),
        hoverLine('Students',uniq(rows.map(s=>String(s.student_id))).length),
        hoverLine('Class',compactList(rows.map(s=>s.class))),
        hoverLine('Week',compactList(rows.map(s=>`Week ${s.week}`))),
        hoverLine('Share of sessions',`${pct(bucket.count,sessions.length)}%`)
      ].join('<br>'),
      detail:detailPayload(`${bucket.value} follow-up question(s)`,`${bucket.count} session(s)`,detailRows(rows,s=>({Student:s.student_id,Conversation:s.conversation_id,Week:s.week,Class:s.class})))
    }
  });

  const q4=['Coding','Conceptual'].map(label=>{
    const rows=ans.filter(r=>r.question_type===label&&['Correct','Partially correct','Incorrect'].includes(r.answer_result));
    const bad=rows.filter(r=>r.answer_result==='Incorrect');
    const incorrect=bad.length;
    const badSessions=sessions.filter(s=>bad.some(r=>String(r.conversation_id)===String(s.conversation_id)&&Number(r.week)===Number(s.week)));
    return {label,attempts:rows.length,incorrect,rate:rows.length?100*incorrect/rows.length:0,
      hoverHtml:[
        `<b>${esc(label)} questions</b>`,
        hoverLine('Incorrect rate',`${Math.round(rows.length?100*incorrect/rows.length:0)}%`),
        hoverLine('Incorrect / attempts',`${incorrect}/${rows.length}`),
        hoverLine('Students with incorrect answers',uniq(bad.map(r=>String(r.student_id))).length),
        hoverLine('Class',compactList(badSessions.map(s=>s.class))),
        hoverLine('Week',compactList(bad.map(r=>`Week ${r.week}`)))
      ].join('<br>'),
      detail:detailPayload(`${label} questions`,`${incorrect} incorrect / ${rows.length} attempts`,detailRows(bad,r=>({Student:r.student_id,Conversation:r.conversation_id,Week:r.week,Class:compactList(sessions.filter(s=>String(s.conversation_id)===String(r.conversation_id)&&Number(s.week)===Number(r.week)).map(s=>s.class)),Question:r.assistant_question||'—',Answer:r.student_answer||'—'})))
    }
  }).filter(x=>x.attempts);

  const bs=new Map();
  sessions.forEach(s=>{
    const id=String(s.student_id),o=bs.get(id)||{student_id:id,received:false,asked:false,work:0,conversations:[]};
    o.received=o.received||bool(s.completion_code_received);o.asked=o.asked||bool(s.completion_code_requested);o.work=Math.max(o.work,Number(s.work_count_for_threshold||0));o.conversations.push(s.conversation_id);bs.set(id,o)
  });
  const no=[...bs.values()].filter(x=>!x.received);
  const groups={ea:no.filter(x=>x.asked&&x.work>=N),na:no.filter(x=>x.asked&&x.work<N),en:no.filter(x=>!x.asked&&x.work>=N),nn:no.filter(x=>!x.asked&&x.work<N)};
  const total=no.length;
  const groupDetail=(key,title)=>detailPayload(title,`${groups[key].length} of ${total} no-code student(s)`,groups[key].map(x=>({Student:x.student_id,'Answers counted':x.work,Asked:x.asked?'Yes':'No',Conversations:x.conversations.join(', ')})));

  return {studentsTotal:students.length,askedStudents:new Set(asked.map(s=>String(s.student_id))).size,medianFirst:median(first),earlyN:first.filter(x=>x<=2).length,earlyPct:pct(first.filter(x=>x<=2).length,first.length),q1,q2,rank,counts,words,maxWord:Math.max(1,...words.map(x=>x.count)),results:res,resultBars,classified,follow,avgFollow:fv.length?fv.reduce((a,b)=>a+b,0)/fv.length:0,q4,q5:{ea:groups.ea.length,na:groups.na.length,en:groups.en.length,nn:groups.nn.length,total,eap:pct(groups.ea.length,total),nap:pct(groups.na.length,total),enp:pct(groups.en.length,total),nnp:pct(groups.nn.length,total),details:{ea:groupDetail('ea','Asked code + enough work'),na:groupDetail('na','Asked code + not enough work'),en:groupDetail('en','Did not ask + enough work'),nn:groupDetail('nn','Did not ask + not enough work')}}}
}
function dashboard(){const m=compute(filtered(),state.N);return `<div class="content"><div class="grid two"><section class="card">${title('1. How many questions has the chatbot asked when the student asks for the code?','Distribution of when students first ask for the completion code. Hover for a concise summary; click a bar for student/session details.')}${bars(m.q1,{height:230,chart:'code-request-timing',xTitle:'Questions before first code request',yTitle:'Students'})}</section><section class="card">${title('2. How many non-serious answers has the student made?','Distribution per student, ranked cases, and words used in non-serious answers.')}<div class="split"><div><div class="eyebrow">A · Distribution per student</div>${bars(m.q2,{color:'#D97706',height:170,chart:'non-serious-distribution',xTitle:'Non-serious answers per student',yTitle:'Students'})}</div><div><div class="eyebrow">B · Ranked non-serious counts</div>${ranked(m.rank)}</div></div><div class="eyebrow word-label">Overview · words used in non-serious answers</div><div class="word-cloud">${m.words.length?m.words.map((w,i)=>`<button type="button" class="word-hit chart-hit" data-tooltip="${esc(`${w.word}: ${w.count} occurrence(s)`)}" data-detail="${pack(detailPayload(`Word: ${w.word}`,`${w.count} occurrence(s) in non-serious answers`,[]))}" style="font-size:${12+18*w.count/m.maxWord}px;font-weight:${w.count>=m.maxWord*.6?800:600};color:${i%3?'#4F46E5':'#64748B'}">${esc(w.word)}</button>`).join(''):'<span class="muted">No usable words available.</span>'}</div></section></div><div class="grid two"><section class="card">${title('3. How many answers are incorrect or partially correct, and how many follow-up questions were asked?','Answer results and follow-up questions per session.')}${bars(m.resultBars,{value:'label',height:180,chart:'answer-results',xTitle:'Answer result',yTitle:'Answers'})}<div class="eyebrow">Follow-up questions per session</div>${bars(m.follow,{height:155,chart:'follow-ups',xTitle:'Follow-up questions',yTitle:'Sessions'})}</section><section class="card">${title('4. Are the incorrectly answered questions coding or conceptual?','Incorrect rate by question type.')}${m.q4.length?rateBars(m.q4):'<div class="empty">No classified coding/conceptual attempts available.</div>'}${m.q4.length?`<div class="stat-row">${m.q4.map(x=>stat(x.label,`${x.incorrect} incorrect`,`${x.attempts} attempts`)).join('')}</div>`:''}</section></div><section class="card">${title('5. Among students who did not get the completion code: did they ask for it, and did they do enough work (N answers)?','2×2 matrix: asked vs did not ask × enough vs not enough work. The instructor sets N.')}<label class="slider-label">Minimum answers to count as “enough work” (N)<div class="slider-row"><input id="n-slider" type="range" min="1" max="10" step="1" value="${state.N}"><strong>${state.N}</strong></div></label><div class="matrix"><div></div><div class="head">Enough work<br>≥ ${state.N}</div><div class="head">Not enough<br>&lt; ${state.N}</div><div class="rowhead">Asked code</div><button type="button" class="bluecell matrix-hit chart-hit" data-tooltip="Asked + enough: ${m.q5.ea} (${m.q5.eap}%)" data-detail="${pack(m.q5.details.ea)}"><span>${m.q5.ea}</span><br>${m.q5.eap}%</button><button type="button" class="warmcell matrix-hit chart-hit" data-tooltip="Asked + not enough: ${m.q5.na} (${m.q5.nap}%)" data-detail="${pack(m.q5.details.na)}"><span>${m.q5.na}</span><br>${m.q5.nap}%</button><div class="rowhead">Did not ask</div><button type="button" class="bluecell matrix-hit chart-hit" data-tooltip="Did not ask + enough: ${m.q5.en} (${m.q5.enp}%)" data-detail="${pack(m.q5.details.en)}"><span>${m.q5.en}</span><br>${m.q5.enp}%</button><button type="button" class="warmcell matrix-hit chart-hit" data-tooltip="Did not ask + not enough: ${m.q5.nn} (${m.q5.nnp}%)" data-detail="${pack(m.q5.details.nn)}"><span>${m.q5.nn}</span><br>${m.q5.nnp}%</button></div><div class="matrix-note">Total: ${m.q5.total} students who did not receive the code</div>${m.q5.na?`<div class="attn red"><b>${m.q5.na} no-code student(s)</b>&nbsp;asked for the code without reaching N=${state.N} answers.</div>`:''}</section></div>`}
function inspector(){const d=state.data;return `<div class="content"><div class="group-label">Processed data & validation</div><section class="card">${title('Processed data inspector','Review the exported live dataset and download the complete processed workbook.')}<div class="stat-row">${stat('Sessions',d.meta.session_count,'processed')}${stat('Turns',d.meta.turn_count,'turn-level rows')}${stat('Requests',d.meta.request_event_count,'code request events')}</div><a class="download-btn" href="/latest_processed_analysis.xlsx" download>↓ Download complete workbook</a><div class="validation-table"><div class="tr th"><span>Check</span><span>Status</span><span>Severity</span><span>Detail</span></div>${(d.validation||[]).map(r=>`<div class="tr"><span>${esc(r.check)}</span><span class="${r.status==='PASS'?'good':'bad'}">${esc(r.status)}</span><span>${esc(r.severity)}</span><span>${esc(r.detail)}</span></div>`).join('')}</div></section></div>`}
function render(){const d=state.data;if(!d)return;Object.keys(plotRegistry).forEach(k=>delete plotRegistry[k]);const weeks=d.meta.weeks||[],classes=d.meta.classes||[];app.innerHTML=`<div class="app-shell ${state.sidebarCollapsed?'sidebar-collapsed':''}"><aside class="sidebar ${state.nav?'open':''}"><div class="side-brand"><div class="app-badge">CA</div><div class="side-brand-copy"><strong>Conversational<br>Assessment</strong><span>Analytics</span></div><button class="sidebar-collapse" id="collapse-sidebar" title="Hide sidebar" aria-label="Hide sidebar">‹</button><button class="nav-close" id="close-nav" aria-label="Close menu">✕</button></div><nav><button id="nav-dashboard" class="${state.page==='Dashboard'?'active':''}">▦ Dashboard</button><button id="nav-inspector" class="${state.page==='Processed data & validation'?'active':''}">▤ Processed data & validation</button></nav></aside><main class="main"><header class="fixed-header"><div class="header-inner"><button class="menu-btn" id="menu-btn" title="Show sidebar" aria-label="Show sidebar">☰</button><div class="title-wrap"><div class="app-badge">CA</div><div><div class="dashboard-title">Conversational Assessment Analytics</div><div class="dashboard-sub">${state.week==='All weeks'?'All weeks':`Week ${state.week}`} · ${esc(state.klass)} · evidence for the five instructor questions</div></div></div><div class="filters"><label>Week<select id="week-select"><option>All weeks</option>${weeks.map(w=>`<option value="${w}" ${String(state.week)===String(w)?'selected':''}>Week ${w}</option>`).join('')}</select></label><label>Class<select id="class-select"><option>All classes</option>${classes.map(c=>`<option ${state.klass===c?'selected':''}>${esc(c)}</option>`).join('')}</select></label></div></div></header><div class="header-space"></div>${state.page==='Dashboard'?dashboard():inspector()}</main><div id="chart-tooltip" class="chart-tooltip" role="tooltip"></div><div id="detail-backdrop" class="detail-backdrop"></div><aside id="detail-drawer" class="detail-drawer" aria-hidden="true"><div class="detail-drawer-head"><div><div class="eyebrow">Chart details</div><h3 id="detail-title"></h3><p id="detail-subtitle"></p></div><button type="button" id="detail-close" class="detail-close" aria-label="Close details">✕</button></div><div id="detail-body" class="detail-body"></div></aside></div>`;bind();hydratePlotlyCharts()}
function openDetail(detail,source){
  if(!detail)return;
  document.querySelectorAll('.chart-hit.selected').forEach(el=>el.classList.remove('selected'));
  source?.classList.add('selected');
  const drawer=document.getElementById('detail-drawer'),backdrop=document.getElementById('detail-backdrop');
  document.getElementById('detail-title').textContent=detail.title||'Details';
  document.getElementById('detail-subtitle').textContent=detail.subtitle||'';
  const rows=detail.rows||[];
  const body=document.getElementById('detail-body');
  if(!rows.length) body.innerHTML='<div class="empty">No row-level details are available for this item.</div>';
  else {
    const cols=[...new Set(rows.flatMap(r=>Object.keys(r)))];
    body.innerHTML=`<div class="detail-count">${rows.length} row${rows.length===1?'':'s'}</div><div class="detail-table-wrap"><table class="detail-table"><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(r[c]??'—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  drawer.classList.add('open');backdrop.classList.add('open');drawer.setAttribute('aria-hidden','false');
}
function closeDetail(){
  document.getElementById('detail-drawer')?.classList.remove('open');
  document.getElementById('detail-backdrop')?.classList.remove('open');
  document.querySelectorAll('.chart-hit.selected').forEach(el=>el.classList.remove('selected'));
}

async function hydratePlotlyCharts(){
  const ids=Object.keys(plotRegistry);
  if(!ids.length)return;
  let Plotly;
  try{ Plotly=await loadPlotly(); }
  catch(err){
    console.error(err);
    ids.forEach(id=>{
      const el=document.getElementById(id);
      if(el)el.innerHTML='<div class="empty">Interactive chart library could not be loaded.</div>';
    });
    return;
  }

  const commonLayout={
    paper_bgcolor:'rgba(0,0,0,0)',
    plot_bgcolor:'rgba(0,0,0,0)',
    font:{family:'Inter, system-ui, -apple-system, Segoe UI, sans-serif',color:'#475569',size:12},
    margin:{l:44,r:14,t:12,b:42},
    hoverlabel:{bgcolor:'#0F172A',bordercolor:'#0F172A',font:{color:'#fff',size:12}},
    showlegend:false,
    autosize:true
  };
  const config={
    responsive:true,
    displaylogo:false,
    scrollZoom:true,
    modeBarButtonsToRemove:['lasso2d','select2d','toImage']
  };

  for(const id of ids){
    const spec=plotRegistry[id];
    const el=document.getElementById(id);
    if(!el)continue;
    const {kind,data,opts}=spec;
    let traces=[],layout={...commonLayout};

    if(kind==='bar'){
      const x=data.map(d=>String(d[opts.value]));
      const y=data.map(d=>Number(d[opts.count]||0));
      traces=[{
        type:'bar',
        x,y,
        marker:{color:opts.color,line:{width:0},cornerradius:5},
        customdata:data.map(d=>d.detail||null),
        text:y.map(v=>String(v)),
        textposition:'outside',
        textfont:{color:'#475569',size:11},
        cliponaxis:false,
        hovertemplate:data.map((d,i)=>`${d.hoverHtml||esc(d.tooltip||`${x[i]}: ${y[i]}`)}<extra></extra>`)
      }];
      layout={...layout,
        bargap:.28,
        xaxis:{
          fixedrange:false,
          tickfont:{size:11,color:'#475569'},
          showgrid:false,
          zeroline:false,
          automargin:true,
          title:opts.xTitle?{text:opts.xTitle,font:{size:11,color:'#475569'},standoff:8}:undefined
        },
        yaxis:{
          fixedrange:false,
          rangemode:'tozero',
          nticks:6,
          tickmode:'auto',
          tickfont:{size:11,color:'#475569'},
          gridcolor:'#EEF2F7',
          zeroline:false,
          automargin:true,
          title:{text:opts.yTitle||'Students',font:{size:11,color:'#475569'}}
        }
      };
    }

    if(kind==='ranked'){
      const rows=[...data]
        .filter(d=>Number(d.count||0) > 0)
        .sort((a,b)=>Number(b.count||0)-Number(a.count||0));

      const studentLabels=rows.map(d=>String(d.student_id));

      traces=[{
        type:'scatter',
        mode:'markers',
        x:studentLabels,
        y:rows.map(d=>Number(d.count||0)),
        marker:{
          color:'#E35D5D',
          size:8,
          line:{width:0}
        },
        customdata:rows.map(d=>d.detail||null),
        hovertemplate:rows.map(
          d=>`${d.hoverHtml||esc(d.tooltip||`Student ${d.student_id}: ${d.count} non-serious answer${Number(d.count)===1?'':'s'}`)}<extra></extra>`
        )
      }];

      const maxCount=Math.max(1,...rows.map(d=>Number(d.count||0)));

      layout={...layout,
        margin:{l:52,r:18,t:14,b:54},
        xaxis:{
          type:'category',
          categoryorder:'array',
          categoryarray:studentLabels,
          showticklabels:false,
          showgrid:false,
          zeroline:false,
          automargin:true,
          title:{
            text:'Students with ≥1 non-serious answer',
            font:{size:11,color:'#64748B'},
            standoff:10
          }
        },
        yaxis:{
          rangemode:'tozero',
          range:[0,Math.max(2.35,maxCount+0.35)],
          tickmode:'linear',
          dtick:1,
          tickfont:{size:11,color:'#64748B'},
          showgrid:true,
          gridcolor:'#EEF2F7',
          gridwidth:1,
          zeroline:false,
          automargin:true,
          title:{
            text:'Non-serious answers',
            font:{size:11,color:'#64748B'},
            standoff:8
          }
        }
      };
    }

    if(kind==='rate'){
      const rows=[...data].reverse();
      traces=[{
        type:'bar',
        orientation:'h',
        x:rows.map(d=>Number(d.rate||0)),
        y:rows.map(d=>String(d.label)),
        marker:{color:'#4F46E5',cornerradius:5},
        customdata:rows.map(d=>d.detail||null),
        text:rows.map(d=>`${Math.round(d.rate)}%`),
        textposition:'outside',
        cliponaxis:false,
        hovertemplate:rows.map(d=>`${d.hoverHtml||esc(`${d.label}: ${Math.round(d.rate)}% incorrect · ${d.incorrect}/${d.attempts}`)}<extra></extra>`)
      }];
      layout={...layout,
        margin:{l:90,r:35,t:12,b:38},
        xaxis:{range:[0,Math.max(100,...rows.map(d=>Number(d.rate||0)+8))],ticksuffix:'%',gridcolor:'#EEF2F7',zeroline:false,title:{text:'Incorrect rate',font:{size:11}}},
        yaxis:{showgrid:false}
      };
    }

    await Plotly.newPlot(el,traces,layout,config);

    el.on('plotly_click',ev=>{
      const p=ev?.points?.[0];
      if(p?.customdata)openDetail(p.customdata,el);
    });

    el.on('plotly_hover',()=>el.classList.add('plot-hover'));
    el.on('plotly_unhover',()=>el.classList.remove('plot-hover'));
  }
}

function bindChartInteractions(){
  const tooltip=document.getElementById('chart-tooltip');
  document.querySelectorAll('.chart-hit').forEach(el=>{
    el.addEventListener('pointerenter',()=>{const text=el.dataset.tooltip;if(!text)return;tooltip.textContent=text;tooltip.classList.add('show')});
    el.addEventListener('pointermove',e=>{if(!tooltip.classList.contains('show'))return;const pad=14,w=tooltip.offsetWidth||180,h=tooltip.offsetHeight||40;let x=e.clientX+14,y=e.clientY+14;if(x+w>window.innerWidth-pad)x=e.clientX-w-14;if(y+h>window.innerHeight-pad)y=e.clientY-h-14;tooltip.style.transform=`translate(${Math.max(pad,x)}px,${Math.max(pad,y)}px)`});
    el.addEventListener('pointerleave',()=>tooltip.classList.remove('show'));
    el.addEventListener('click',()=>openDetail(unpack(el.dataset.detail),el));
  });
  document.getElementById('detail-close')?.addEventListener('click',closeDetail);
  document.getElementById('detail-backdrop')?.addEventListener('click',closeDetail);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDetail()},{once:true});
}
function bind(){
  document.getElementById('week-select')?.addEventListener('change',e=>{state.week=e.target.value;render()});
  document.getElementById('class-select')?.addEventListener('change',e=>{state.klass=e.target.value;render()});
  document.getElementById('n-slider')?.addEventListener('input',e=>{state.N=Number(e.target.value);render()});
  document.getElementById('nav-dashboard')?.addEventListener('click',()=>{state.page='Dashboard';state.nav=false;render()});
  document.getElementById('nav-inspector')?.addEventListener('click',()=>{state.page='Processed data & validation';state.nav=false;render()});
  document.getElementById('menu-btn')?.addEventListener('click',()=>{
    if(window.innerWidth<=920){
      state.nav=true;
    }else{
      state.sidebarCollapsed=false;
      localStorage.setItem('ca-sidebar-collapsed','0');
    }
    render();
  });
  document.getElementById('collapse-sidebar')?.addEventListener('click',()=>{
    state.sidebarCollapsed=true;
    localStorage.setItem('ca-sidebar-collapsed','1');
    render();
  });
  document.getElementById('close-nav')?.addEventListener('click',()=>{state.nav=false;render()});
  bindChartInteractions();
}
fetch('/dashboard_data.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('dashboard_data.json was not found. Run export_dashboard.py first.');return r.json()}).then(d=>{state.data=d;render()}).catch(e=>app.innerHTML=`<div class="fatal"><div class="app-badge">CA</div><h1>Dashboard data not generated</h1><p>${esc(e.message)}</p></div>`);
