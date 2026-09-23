const REQUIRED_COLUMNS=["Conversation","Conversation Id","Id","Message","Message Id","Role","Timestamp","User Id"];
const WRAPUP_PHRASES=["would you like to continue","would you like a brief summary","would you like to explore","shall we conclude","shall we wrap up","is there anything specific","are you ready to begin"];
const QUESTION_MARKER=/(^|\n)\s*Question\s*:?\s*/ig;
const CODE_TOKEN="[A-Z0-9]+(?:[-_][A-Z0-9]+){1,6}";
const CODE_CUE=new RegExp(`(?:completion|access)\\s+code(?:\\s+is)?\\s*[:\\-]?\\s*(?<code>${CODE_TOKEN})|(?:here(?:'s| is)|your)\\s+(?:completion\\s+)?code(?:\\s+is)?\\s*[:\\-]?\\s*(?<code2>${CODE_TOKEN})`,'i');
const REQUEST_INTENT=/\b(?:give|tell|show|send|get|have|need|want|provide|receive|ready|finish|end)\b|\b(?:can|could|may|please|plz)\b/i;
const CLASS_ORDER=["Wednesday 08:00–10:00","Wednesday 10:00–12:00","Thursday 12:00–14:00","Friday 12:00–14:00","Outside scheduled classes"];

const clean=v=>String(v??'').replace(/\s+/g,' ').trim();
const role=v=>clean(v).toLowerCase();
const truth=v=>v===true||v===1||String(v).toLowerCase()==='true';

function parseDate(value){
  if(value instanceof Date&&!Number.isNaN(value.valueOf()))return value;
  if(typeof value==='number'&&Number.isFinite(value))return new Date(Date.UTC(1899,11,30)+value*86400000);
  let text=clean(value);
  if(!text)return null;
  if(/^\d{4}-\d\d-\d\d(?:[ T]\d\d:\d\d(?::\d\d(?:\.\d+)?)?)?$/.test(text))text=text.replace(' ','T')+'Z';
  const date=new Date(text);
  return Number.isNaN(date.valueOf())?null:date;
}

function classFromTimestamp(value){
  const date=parseDate(value);
  if(!date)return CLASS_ORDER[4];
  const parts=Object.fromEntries(new Intl.DateTimeFormat('en-AU',{timeZone:'Australia/Adelaide',weekday:'long',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(date).map(p=>[p.type,p.value]));
  const minutes=Number(parts.hour)*60+Number(parts.minute);
  if(parts.weekday==='Wednesday'&&minutes>=480&&minutes<600)return CLASS_ORDER[0];
  if(parts.weekday==='Wednesday'&&minutes>=600&&minutes<720)return CLASS_ORDER[1];
  if(parts.weekday==='Thursday'&&minutes>=720&&minutes<840)return CLASS_ORDER[2];
  if(parts.weekday==='Friday'&&minutes>=720&&minutes<840)return CLASS_ORDER[3];
  return CLASS_ORDER[4];
}

function isAssessmentQuestion(value){
  const text=String(value??'').trim();
  if(!text)return false;
  QUESTION_MARKER.lastIndex=0;
  if(QUESTION_MARKER.test(text))return true;
  if(!text.includes('?'))return false;
  const low=text.toLowerCase();
  if(WRAPUP_PHRASES.some(p=>low.includes(p))&&(low.match(/\?/g)||[]).length===1)return false;
  if((low.includes('completion code')||low.includes('access code'))&&(low.match(/\?/g)||[]).length===1&&low.length<240)return false;
  return true;
}

function extractQuestion(value){
  const text=String(value??'').trim();
  QUESTION_MARKER.lastIndex=0;
  const matches=[...text.matchAll(QUESTION_MARKER)];
  if(matches.length){const m=matches[matches.length-1];return text.slice((m.index||0)+m[0].length).trim()}
  if(text.includes('?')){const lines=text.split(/\n+/).map(x=>x.trim()).filter(Boolean).filter(x=>x.includes('?'));if(lines.length)return lines[lines.length-1]}
  return text;
}

function completionCode(value){
  const match=String(value??'').match(CODE_CUE);
  const code=match?.groups?.code||match?.groups?.code2||null;
  return code?String(code).toUpperCase():null;
}

function codeRequest(value,nextAssistant=''){
  const text=clean(value),low=text.toLowerCase(),next=clean(nextAssistant).toLowerCase(),words=text?text.split(' ').length:0;
  if(/\b(?:completion|access)\s+code\b/i.test(text)&&REQUEST_INTENT.test(text))return true;
  if(words<=6&&/\bcode\b/i.test(text)&&(REQUEST_INTENT.test(text)||/^(?:the\s+)?code(?:\s+(?:please|plz))?$/.test(low)))return true;
  const ack=['asked for the completion code','asking for the completion code','asking about the code','requested the completion code'].some(x=>next.includes(x));
  return ack&&words<=12&&/\bcode\b/i.test(text);
}

function seriousness(value,isAnswer,isRequest){
  if(!isAnswer||isRequest)return 'Not applicable';
  const text=clean(value),low=text.toLowerCase().replace(/[ .!?\\]+$/,'');
  if(!text||/^(.)\1{3,}$/.test(low)||['asdf','asdfgh','qwerty','blah','random'].includes(low))return 'Non-serious';
  return 'Pending LLM';
}

function correctness(value){
  const low=clean(value).toLowerCase();
  if(!low)return 'Unclear';
  if([/\bpartially correct\b/,/\bpartly correct\b/,/\bpartially right\b/,/\bpartly right\b/,/\bmostly correct\b/,/\bright direction,? but\b/,/\bon the right track,? but\b/].some(r=>r.test(low)))return 'Partially correct';
  if(/^(?:correct|exactly)\b/.test(low))return 'Correct';
  if([/^incorrect\b/,/^not quite\b/,/\bthat's incorrect\b/,/\bthat is incorrect\b/,/\bthat's not correct\b/,/\bthat is not correct\b/,/\bnot quite correct\b/,/\bthe correct answer is\b/,/\bthe correct option is\b/].some(r=>r.test(low.slice(0,320))))return 'Incorrect';
  if([/^correct\b/,/^exactly\b/,/^yes[,! ]+.*\bcorrect\b/,/^nice[,! —-]+.*\bcorrect\b/,/^good[,! —-]+.*\bcorrect\b/,/\bthat's correct\b/,/\bthat is correct\b/,/\bthat's right\b/,/\bthat is right\b/,/\bexactly right\b/,/\b[a-d] is (?:correct|right)\b/].some(r=>r.test(low.slice(0,320))))return 'Correct';
  return 'Unclear';
}

function questionType(value){
  const text=clean(value);
  if(!text)return 'Not applicable';
  const codingEvidence=/```|&lt;-|<\-|\b(?:code snippet|code interpretation|coding question|server logic|server code|shiny snippet|minimal (?:change|fix))\b|\bggplot\s*\(|\bgeom_[a-z_]+\s*\(|\brenderPlot\s*\(|\b(?:output|input)\$|\b(?:sliderInput|plotOutput|reactive)\s*\(/i;
  return codingEvidence.test(text)?'Coding':'Conceptual';
}

function groupBy(rows,key){const out=new Map();for(const row of rows){const k=String(row[key]??'');if(!out.has(k))out.set(k,[]);out.get(k).push(row)}return out}
function idCompare(a,b){const av=Number(a.Id),bv=Number(b.Id);if(Number.isFinite(av)&&Number.isFinite(bv)&&av!==bv)return av-bv;return String(a.Id??'').localeCompare(String(b.Id??''),undefined,{numeric:true})}

export function processRows(inputRows,week,sourceFile){
  const missing=REQUIRED_COLUMNS.filter(c=>!Object.prototype.hasOwnProperty.call(inputRows[0]||{},c));
  if(missing.length)throw new Error(`Missing required columns: ${missing.join(', ')}`);
  const rows=inputRows.map(r=>({...r})).sort((a,b)=>String(a['Conversation Id']).localeCompare(String(b['Conversation Id']))||((parseDate(a.Timestamp)?.valueOf()||0)-(parseDate(b.Timestamp)?.valueOf()||0))||idCompare(a,b));
  const turns=[],sessions=[],requests=[];
  for(const [cid,msgs] of groupBy(rows,'Conversation Id')){
    const student=msgs[0]?.['User Id']??null;
    const klass=classFromTimestamp(msgs[0]?.Timestamp);
    const counts=[];let count=0;
    msgs.forEach((m,i)=>{counts[i]=count;if(role(m.Role)==='assistant'&&isAssessmentQuestion(m.Message))count++});
    const codes=[];msgs.forEach((m,i)=>{if(role(m.Role)==='assistant'){const code=completionCode(m.Message);if(code)codes.push({i,code,questions:counts[i]})}});
    const convTurns=[],convRequests=[];
    for(let i=0;i<msgs.length;i++){
      const m=msgs[i];if(role(m.Role)!=='user')continue;
      const prev=i>0&&role(msgs[i-1].Role)==='assistant'?msgs[i-1]:null;
      const next=i+1<msgs.length&&role(msgs[i+1].Role)==='assistant'?msgs[i+1]:null;
      const request=codeRequest(m.Message,next?.Message||'');
      const answer=Boolean(prev&&isAssessmentQuestion(prev.Message)&&!request);
      const result=answer?correctness(next?.Message||''):'Not applicable';
      const question=answer?extractQuestion(prev.Message):'';
      const turn={week,student_id:student,conversation_id:cid,class:klass,is_answer_attempt:answer,seriousness:seriousness(m.Message,answer,request),student_answer:m.Message??'',answer_result:result,followups_to_recovery:null,question_type:questionType(question),is_followup_question:false,assistant_question:question,completion_code_requested:request};
      convTurns.push(turn);
      if(request){
        const future=codes.find(c=>c.i>i),immediate=next?completionCode(next.Message):null;
        convRequests.push({week,student_id:student,conversation_id:cid,class:klass,request_number:convRequests.length+1,questions_before_request:counts[i],answer_attempts_before_request:convTurns.slice(0,-1).filter(t=>t.is_answer_attempt).length,request_text:m.Message??'',observed_request_outcome:immediate?'Provided immediately':future?'Not provided immediately; provided later':'Not provided in session'});
      }
    }
    const answers=convTurns.filter(t=>t.is_answer_attempt);
    const answerPositions=convTurns.map((t,i)=>t.is_answer_attempt?i:-1).filter(i=>i>=0);
    answerPositions.forEach((idx,p)=>{if(convTurns[idx].answer_result!=='Incorrect')return;let n=0;for(const later of answerPositions.slice(p+1)){n++;convTurns[later].is_followup_question=true;if(convTurns[later].answer_result==='Correct'){convTurns[idx].followups_to_recovery=n;break}}});
    turns.push(...convTurns);requests.push(...convRequests);
    const firstRequest=convRequests[0];
    sessions.push({week,student_id:student,conversation_id:cid,class:klass,completion_code_requested:convRequests.length>0,first_code_request_after_questions:firstRequest?.questions_before_request??null,completion_code_received:codes.length>0,work_count_for_threshold:answers.length,code_request_count:convRequests.length,answer_attempts:answers.length,non_completion_reason:codes.length?'Completed':convRequests.length?'Requested but no code issued':'No code issued; semantic reason pending'});
  }
  return {sessions,turns,requests,validation:[
    {check:'Required source columns',status:'PASS',severity:'error',detail:'All required columns were found.'},
    {check:'Sessions reconstructed',status:sessions.length?'PASS':'FAIL',severity:'error',detail:`Reconstructed ${sessions.length} session(s).`},
    {check:'Browser processing mode',status:'PASS',severity:'info',detail:'Processed locally in JavaScript with deterministic rules and no external AI call.'}
  ]};
}

function rowsFromSheet(XLSX,workbook,name){return XLSX.utils.sheet_to_json(workbook.Sheets[name],{defval:null,raw:true})}
function records(rows,columns){return rows.map(row=>Object.fromEntries(columns.filter(c=>Object.prototype.hasOwnProperty.call(row,c)).map(c=>[c,row[c]])))}

export function processedWorkbookPayload(XLSX,workbook){
  const turnSheet=workbook.SheetNames.includes('Turn_Level_Analysis')?'Turn_Level_Analysis':workbook.SheetNames.includes('Turn_Analysis')?'Turn_Analysis':null;
  const required=['Session_Summary','Code_Request_Events'];
  if(!turnSheet||!required.every(name=>workbook.SheetNames.includes(name)))return null;
  const sessions=records(rowsFromSheet(XLSX,workbook,'Session_Summary'),['week','student_id','conversation_id','class','completion_code_requested','first_code_request_after_questions','completion_code_received','work_count_for_threshold','code_request_count','answer_attempts','non_completion_reason']);
  const turns=records(rowsFromSheet(XLSX,workbook,turnSheet),['week','student_id','conversation_id','class','is_answer_attempt','seriousness','student_answer','answer_result','followups_to_recovery','question_type','is_followup','is_follow_up','followup_question','is_followup_question','assistant_question']);
  const requests=records(rowsFromSheet(XLSX,workbook,'Code_Request_Events'),['week','student_id','conversation_id','class','request_number','questions_before_request','answer_attempts_before_request','request_text','observed_request_outcome']);
  const validation=workbook.SheetNames.includes('Validation_Report')?records(rowsFromSheet(XLSX,workbook,'Validation_Report'),['check','status','severity','detail']):[];
  return makePayload(sessions,turns,requests,validation);
}

export function rawWorkbookPayload(XLSX,workbook,fileName){
  const match=decodeURIComponent(fileName).match(/\bweek\s*[_\-\s]*(\d+)\b/i);
  if(!match)throw new Error(`Could not determine the week from “${fileName}”. Include “Week N” in the filename.`);
  const result=processRows(rowsFromSheet(XLSX,workbook,workbook.SheetNames[0]),Number(match[1]),fileName);
  return makePayload(result.sessions,result.turns,result.requests,result.validation);
}

export function makePayload(sessions,turns,requests,validation=[]){
  const weeks=[...new Set(sessions.map(r=>Number(r.week)).filter(Number.isFinite))].sort((a,b)=>a-b);
  const classes=CLASS_ORDER.filter(c=>sessions.some(r=>r.class===c));
  return {meta:{schema_version:1,weeks,classes,session_count:sessions.length,turn_count:turns.length,request_event_count:requests.length},sessions,turns,requests,validation};
}

export function combinePayloads(base,incoming,{replaceWeeks=true}={}){
  if(!base)return incoming;
  const weeks=new Set((incoming.meta.weeks||[]).map(Number));
  const keep=row=>!replaceWeeks||!weeks.has(Number(row.week));
  return makePayload([...base.sessions.filter(keep),...incoming.sessions],[...base.turns.filter(keep),...incoming.turns],[...base.requests.filter(keep),...incoming.requests],incoming.validation||[]);
}
