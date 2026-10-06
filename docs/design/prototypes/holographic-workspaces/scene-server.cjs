/* Serves the actual desktop with synthetic, in-memory records for design review.
 * It never imports the application, proxies a request, or starts a model.
 * Only index.html, static/ and assets/ can be served. Bind loopback only.
 */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../../..');
const originalIndex = require('node:child_process').execFileSync('git', ['-c', 'safe.directory='+root, '-C', root, 'show', 'ed30ed8431b0ed81dd4160a098aa95c990fd0b58:index.html'], {maxBuffer:16*1024*1024});
const outputs = __dirname;
const port = Number(process.env.FRIDAY_DESIGN_PORT || 3192);
const now = Math.floor(Date.now()/1000);
const projects = [
  {id:'design-launch',name:'Northstar launch',instructions:'Build a thoughtful launch for a fictional creative studio. Keep the writing direct and the visual direction spacious.',type:'general',conversations:2,files:0,codebases:[],created_at:now-172800,updated_at:now},
  {id:'design-notes',name:'Field notes',instructions:'Collect useful references and turn observations into something worth sharing.',type:'general',conversations:1,files:0,codebases:[],created_at:now-86400,updated_at:now-3600}
];
const conversations = [
  {id:'design-main',title:'A place to begin',project:null,status:'active',last_active_at:now-3600},
  {id:'design-direction',title:'A clearer opening for the launch',project:'design-launch',status:'active',last_active_at:now-300},
  {id:'design-copy',title:'The story we want to tell',project:'design-launch',status:'active',last_active_at:now-1800},
  {id:'design-field',title:'Ideas worth returning to',project:'design-notes',status:'active',last_active_at:now-7200}
];
const messages = {'design-direction':[
  {role:'user',text:'Let’s make the launch feel clear, confident, and useful.',ts:now-600},
  {role:'friday',text:'We have a direction to work with. Bring the launch brief and a few visual references into the project, then we can compare the options for the opening.\n\nThis review uses sample records. No model is running.',ts:now-590}
]};
const settings = {agent_name:'Friday',setup_complete:true,show_all_workspaces:true,model_routing:{mode:'local_only'},orchestrator_model:'Preview (no model)',chat_model:'preview',voice_enabled:false,head_tracking_enabled:false,hand_tracking_enabled:false,workspace_layouts:{},held_features:{federation:false},appearance:{},avatars:{}};
const documents = [
 {id:1,title:'Launch brief',name:'Launch brief',kind:'pdf',ext:'pdf',pages:1,shelf:'open',status:'ready',cloud_grant:false},
 {id:2,title:'Visual direction',name:'Visual direction',kind:'pdf',ext:'pdf',pages:1,shelf:'open',status:'ready',cloud_grant:false},
 {id:3,title:'Audience notes',name:'Audience notes',kind:'pdf',ext:'pdf',pages:1,shelf:'open',status:'ready',cloud_grant:false}
];
const cards = [
 {id:'design-hero',title:'Opening direction',kind:'draft',status:'draft',stage:'draft',channel:'website',text:'Make room for your next idea.\n\nA clear starting point for thoughtful work.',created_at:now-7200,updated_at:now-600,tags:['launch'],files:[],outputs:[]},
 {id:'design-story',title:'The launch story',kind:'draft',status:'draft',stage:'review',channel:'website',text:'A small studio with room for ambitious ideas.',created_at:now-6400,updated_at:now-1600,tags:['launch'],files:[],outputs:[]},
 {id:'design-notebook',title:'Notes from the field',kind:'draft',status:'draft',stage:'idea',channel:'article',text:'Useful observations become the foundation for better work.',created_at:now-86000,updated_at:now-3200,tags:['notes'],files:[],outputs:[]}
];
const tasks = [
 {task_id:'design-review',name:'Review the launch direction',description:'Compare the opening against the project brief',now:'Ready for a human review',status:'paused',created_at:now-2400,result:''},
 {task_id:'design-outline',name:'A first outline',description:'Gather the story into a useful structure',status:'completed_unverified',created_at:now-4800,result:'Sample outline prepared for this design preview.'}
];
const customizations = {};
const histories = {};
const textById = {1:'# Launch brief\n\nCreate a clear, confident home for a fictional creative studio.\n\n## The opening\nLead with the value of the work, then show a useful example.\n\n## Tone\nDirect, welcoming, and precise.',2:'# Visual direction\n\nUse generous space, strong typography, and a deliberate sense of depth.\n\nKeep the material easy to compare.',3:'# Audience notes\n\nPeople arrive with a goal. Help them find the next useful action.\n\nLeave room to explore without losing their place.'};
// The Library's document reader consumes PDFs; Files exposes the same sample copy as Markdown.
function sampleLines(d) {
 return ['FRIDAY DESIGN PREVIEW', 'Sample reference - no live document', '', ...textById[d.id].replace(/^#+ /gm,'').split('\n').flatMap(line=>line.match(/.{1,68}(?:\s|$)|\S{1,68}/g)||[''])];
}
function samplePdf(d) {
 const esc=s=>s.replace(/[\\()]/g,'\\$&');
 const stream='BT /F1 12 Tf 18 TL 48 744 Td '+sampleLines(d).map((s,i)=>(i?'T* ':'')+'('+esc(s)+') Tj').join('\n')+' ET';
 const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>','<< /Length '+Buffer.byteLength(stream)+' >>\nstream\n'+stream+'\nendstream'];
 let out='%PDF-1.4\n';const offsets=[0];
 objects.forEach((s,i)=>{offsets.push(Buffer.byteLength(out));out+=(i+1)+' 0 obj\n'+s+'\nendobj\n';});
 const xref=Buffer.byteLength(out);
 out+='xref\n0 '+offsets.length+'\n0000000000 65535 f \n'+offsets.slice(1).map(n=>String(n).padStart(10,'0')+' 00000 n \n').join('')+'trailer\n<< /Size '+offsets.length+' /Root 1 0 R >>\nstartxref\n'+xref+'\n%%EOF\n';
 return Buffer.from(out);
}
function samplePage(d) {
 const esc=s=>s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
 return Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="612" height="792" viewBox="0 0 612 792"><rect width="612" height="792" fill="white"/><g fill="#222" font-family="Helvetica,Arial,sans-serif" font-size="12">'+sampleLines(d).map((s,i)=>'<text x="48" y="'+(48+i*18)+'">'+esc(s)+'</text>').join('')+'</g></svg>');
}
const pdfById=Object.fromEntries(documents.map(d=>[d.id,samplePdf(d)]));
documents.forEach(d=>Object.assign(d,{size:pdfById[d.id].length,indexed_at:now,ocr_pages:0,cited_in:0,has_page_images:true,folder:'Sample materials'}));
const libraryNode=d=>({id:'d:'+d.id,kind:'document',title:d.title,ext:d.ext,pages:d.pages,shelf:d.shelf,size:d.size,ocr:false,indexed_at:now,parent:'f:1'});
const librarySection=d=>({id:'s:'+d.id,kind:'section',title:'Overview',heading:'Overview',level:1,page_from:1,page_to:1,passages:1,parent:'d:'+d.id,doc:'d:'+d.id});
function bytes(res,body,type,headers={}) {res.writeHead(200,{'Content-Type':type,'Content-Length':body.length,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...headers});res.end(body);}
function json(res, data, status=200) { const body=JSON.stringify(data);res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','Content-Length':Buffer.byteLength(body)});res.end(body); }
function ok(res, data={}) { json(res,{status:'ok',...data}); }
function readBody(req) { return new Promise((resolve,reject)=>{let raw='';req.on('data',b=>{raw+=b;if(raw.length>200000){reject(new Error('Request too large'));req.destroy();}});req.on('end',()=>{try{resolve(raw?JSON.parse(raw):{});}catch(e){reject(e);}});req.on('error',reject);}); }
function api(req,res,u,b) {
 const p=u.pathname, method=req.method;
 if (p.endsWith('/events') || p==='/api/command-stream') {res.writeHead(204);return res.end();}
 if (p==='/api/setup/status') return ok(res,{initialized:true,setup_complete:true});
 if (p==='/api/privacy/cloud-consent') return ok(res,{needs_prompt:false});
 if (p==='/api/settings') { if(method==='POST')Object.assign(settings,b.settings||{});return ok(res,{settings}); }
 if (p==='/api/local-address') return json(res,{host:'agent.preview',source:'name',platform:'preview',secure:false,resolves:false,serve:false,https:{works:false},http:{works:false},ca:{trusted:false},oauth:{override:false},preferred_origin:'https://agent.preview'});
 if (p==='/api/conversations') {
  if(method==='POST'){const c={id:'design-chat-'+Date.now(),title:b.title||'New chat',project:b.project||null,status:'active',last_active_at:Math.floor(Date.now()/1000)};conversations.unshift(c);return ok(res,{conversation:c});}
  return ok(res,{main_id:'design-main',conversations,projects:projects.map(p=>({...p,conversations:conversations.filter(c=>c.project===p.id).length}))});
 }
 let m=p.match(/^\/api\/conversations\/([^/]+)(\/messages)?$/);
 if(m){const c=conversations.find(c=>c.id===m[1]);if(!c)return json(res,{status:'error',message:'Sample conversation not found'},404);if(m[2])return ok(res,{messages:messages[c.id]||[]});return ok(res,{conversation:c});}
 if(p==='/api/projects'&&method==='POST'){const v={id:'design-project-'+Date.now(),name:b.name,instructions:b.instructions||'',type:'general',conversations:0,files:0,codebases:[],created_at:now,updated_at:now};projects.push(v);return ok(res,{project:v});}
 m=p.match(/^\/api\/projects\/([^/]+)(\/files)?$/);
 if(m){const i=projects.findIndex(v=>v.id===m[1]);if(i<0)return json(res,{status:'error',message:'Sample project not found'},404);if(m[2])return ok(res,{files:[]});if(method==='DELETE'){projects.splice(i,1);conversations.forEach(c=>{if(c.project===m[1])c.project=null;});return ok(res);}if(method==='PATCH')Object.assign(projects[i],{name:b.name,instructions:b.instructions});return ok(res,{project:projects[i]});}
 if(p==='/api/chat/stream') {
  const cid=b.conversation_id||'design-main';
  const reply='This is an interactive design preview using sample data. Your draft and project context are connected, but no model or external action runs here. The same interface uses the normal conversation service when the branch runs as the application.';
  messages[cid]=(messages[cid]||[]).concat([{role:'user',text:b.message||b.text||'',ts:now},{role:'friday',text:reply,ts:now}]);
  res.writeHead(200,{'Content-Type':'text/event-stream'});return res.end('data: '+JSON.stringify({delta:reply})+'\n\ndata: '+JSON.stringify({done:true,payload:{response:reply,model:'preview',seat:'local'}})+'\n\n');
 }
 if(p==='/api/work/forecast')return ok(res,{will_pause:false});
 if(p==='/api/calendar/today'){const date=new Date().toISOString().slice(0,10);return ok(res,{date,google_connected:false,annotation:'Sample appointments for this design preview.',events:[{id:'design-meeting',title:'Review the launch',summary:'Review the launch',start_time:date+'T14:00:00',end_time:date+'T14:30:00'},{id:'design-focus',title:'Time to make something',summary:'Time to make something',start_time:date+'T15:00:00',end_time:date+'T16:00:00'}]});}
 if(p==='/api/tasks')return ok(res,{tasks});
 if(p.startsWith('/api/tasks/'))return ok(res,{task:tasks.find(t=>t.task_id===p.split('/')[3])||tasks[0],log:[]});
 if(p==='/api/approvals'||p==='/api/approvals/pending')return ok(res,{approvals:[],pending:[]});
 if(p==='/api/health')return ok(res,{connected:true,preview:true,model_ready:false});
 if(p==='/api/workspace/customizations')return ok(res,{customizations});
 m=p.match(/^\/api\/workspace\/([^/]+)\/(chat|history|versions|revert|customization)$/);
 if(m){const id=m[1];if(m[2]==='chat'){const prev=customizations[id]||{};histories[id]=(histories[id]||[]).concat([{id:Date.now(),customization:prev}]);const dense=/compact|dense/i.test(b.message||b.prompt||'');customizations[id]={...prev,density:dense?'compact':'comfortable',note:'Appearance adjustment in the sample preview'};return ok(res,{response:'The sample workspace now uses '+(dense?'compact':'comfortable')+' spacing. This preview demonstrates the customization connection; no model was called.',customization:customizations[id]});}return ok(res,{customization:customizations[id]||{},history:histories[id]||[],versions:histories[id]||[]});}
 if(p==='/api/library/tree'){
  const node=u.searchParams.get('node')||'',d=documents.find(d=>'d:'+d.id===node);
  if(node&&node!=='f:1'&&!d)return json(res,{status:'error',message:'Sample Library node not found'},404);
  return ok(res,{nodes:d?[librarySection(d)]:node==='f:1'?documents.map(libraryNode):[{id:'f:1',kind:'folder',title:'Sample materials',parent:'',documents:documents.length},...documents.map(libraryNode)],truncated:false,locked:false});
 }
 if(p==='/api/library/status')return ok(res,{counts:{indexed:documents.length,queued:0,failed:0,total:documents.length},scopes:[],failures:[],skipped:[],reading:0,paused:false,waiting_because:null,vault:{documents:0,unlocked:false},encoder:{available:false},laya:{loaded:false},index_bytes:0,index_encrypted:false,index_key_protection:null,index_note:'Synthetic in-memory records; no index file is created.',empty:false,kg_learn:'off',tracked:{on:false}});
 if(p==='/api/library/documents')return ok(res,{documents});
 if(p==='/api/library/tracked')return ok(res,{on:false});
 if(p==='/api/library/shelves')return ok(res,{shelves:[]});
 m=p.match(/^\/api\/library\/(document|section|block|raw)\/(\d+)$/);
 if(m){
  const id=Number(m[2]),d=documents.find(d=>d.id===(m[1]==='block'?id/100:id));
  if(!d)return json(res,{status:'error',message:'Sample Library reference not found'},404);
  if(m[1]==='raw')return bytes(res,pdfById[d.id],'application/pdf',{'Content-Security-Policy':"sandbox; default-src 'none'"});
  if(m[1]==='document')return ok(res,{document:{...d,id:'d:'+d.id}});
  if(m[1]==='section')return ok(res,{section:{...librarySection(d),doc_id:d.id,passages:[{id:d.id*100,text:textById[d.id],block:d.id*100,page:1,t_start:null}]}});
  return ok(res,{block:{id,doc:'d:'+d.id,doc_id:d.id,title:d.title,kind:'paragraph',page:1,para:1,pages:1,bbox:null,text:textById[d.id],t_start:null,t_end:null,section:'Overview',neighbours:[{id,kind:'paragraph',text:textById[d.id],current:true}],page_image:true,doc_kind:'pdf'}});
 }
 m=p.match(/^\/api\/library\/page\/(\d+)\/(\d+)\.webp$/);
 if(m){const d=documents.find(d=>d.id===Number(m[1]));return d&&m[2]==='1'?bytes(res,samplePage(d),'image/svg+xml',{'X-Page-Points':'612,792','X-Pdf-Pages':'1'}):json(res,{status:'error',error:'Sample page not found'},404);}
 if(p==='/api/media'||p==='/api/media/cards')return ok(res,{cards,items:cards,total:cards.length,indexing:false});
 if(p==='/api/media/status')return ok(res,{indexed:cards.length,count:cards.length,indexing:false});
 if(p==='/api/media/collections')return ok(res,{collections:[]});
 if(p==='/api/media/channels')return ok(res,{channels:[]});
 m=p.match(/^\/api\/media\/([^/]+)$/);
 if(m){const c=cards.find(c=>c.id===m[1]);if(c)return ok(res,{card:c,relations:[],text:c.text});}
 if(p==='/api/studio-files/roots')return ok(res,{roots:[{id:'preview',label:'Sample materials',path:'Sample materials',available:true}]});
 if(p==='/api/studio-files/scan'){
  if(u.searchParams.get('root')!=='preview'||u.searchParams.get('path'))return json(res,{status:'denied',error:'Sample folder not found'},404);
  return ok(res,{root:'preview',label:'Sample materials',path:'',entries:documents.map(d=>[d.title+'.md',0,Buffer.byteLength(textById[d.id]),now]),truncated:false,creations_prefix:null,skipped:0,elapsed_ms:0});
 }
 if(p==='/api/studio-files/raw'||p==='/api/studio-files/thumb'){
  const d=documents.find(d=>u.searchParams.get('root')==='preview'&&u.searchParams.get('path')===d.title+'.md');
  if(!d)return json(res,{status:'denied',error:'Sample file not found'},404);
  if(p.endsWith('/thumb')){res.writeHead(204);return res.end();}
  return u.searchParams.get('text')?ok(res,{text:textById[d.id],binary:false,truncated:false}):bytes(res,Buffer.from(textById[d.id]),'text/plain; charset=utf-8',{'Content-Security-Policy':"sandbox; default-src 'none'"});
 }
 if(p==='/api/privacy/file-grants')return ok(res,{grants:[],suspended:false,held:[],denied:[]});
 if(p==='/api/countdowns')return ok(res,{countdowns:[]});
 if(p==='/api/models'||p.includes('model-catalog'))return ok(res,{models:[],roles:{orchestrator:[],subagent:[],creative:[],voice:[]},providers:[],local:[],cloud:[]});
 if(p==='/api/seat'||p==='/api/compute/status')return ok(res,{seat:null,preview:true});
 if(method!=='GET')return json(res,{status:'error',message:'This action is not simulated in the design preview. No live action was taken.'},409);
 return ok(res,{items:[],data:[],accounts:[],models:[],tasks:[],events:[],notifications:[],processes:[],artifacts:[],codebases:[],workflows:[],schedules:[],connections:[],available:false,preview:true});
}
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf','.json':'application/json'};
const server=http.createServer(async(req,res)=>{
 try{
  const u=new URL(req.url,'http://127.0.0.1:'+port);
  if(u.pathname === '/scene-bridge.js') {const body=fs.readFileSync(path.join(outputs,'friday-remake','scene-bridge.js'));res.writeHead(200,{'Content-Type':'text/javascript; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});return res.end(body);}
  if(u.pathname === '/proof.css' || u.pathname === '/proof.js' || u.pathname === '/spatial-embed.js') {const file=path.join(outputs,'brand-proofs',u.pathname.slice(1));res.writeHead(200,{'Content-Type':u.pathname.endsWith('.css')?'text/css':'text/javascript','Cache-Control':'no-store'});return res.end(fs.readFileSync(file));}
  if(u.pathname.startsWith('/brand-proofs/')) {const rel=decodeURIComponent(u.pathname).slice(1);const candidate=path.resolve(outputs,rel);const assetRoot=path.resolve(outputs,'brand-proofs')+path.sep;if(!candidate.startsWith(assetRoot)||!fs.existsSync(candidate)||!fs.statSync(candidate).isFile()){res.writeHead(404);return res.end('Not found');}res.writeHead(200,{'Content-Type':mime[path.extname(candidate)]||'application/octet-stream','Cache-Control':'no-store'});return res.end(fs.readFileSync(candidate));}
  if(u.pathname.startsWith('/api/'))return api(req,res,u,req.method==='GET'?{}:await readBody(req));
  let rel=decodeURIComponent(u.pathname), workspace='';
  if(rel==='/'||rel==='/index.html'||rel==='/spatial-embed'||rel==='/scene-embed'||/^\/w\/[a-z0-9_-]+$/.test(rel)){if(rel.startsWith('/w/'))workspace=rel.slice(3);if(rel==='/spatial-embed')workspace='library';rel='/index.html';}
  if(rel!=='/index.html'&&!rel.startsWith('/static/')&&!rel.startsWith('/assets/')){res.writeHead(404);return res.end('Not found');}
  const file=path.resolve(root,'.'+rel);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end('Not found');}
  let body=rel==='/index.html'?originalIndex:fs.readFileSync(file);
  if(rel==='/index.html'){
   const boot='<script>window.__FRIDAY_PREVIEW__=true;window.__FRIDAY_API_TOKEN="design-preview";'+(workspace?'window.__FRIDAY_STANDALONE__='+JSON.stringify(workspace)+';document.documentElement.classList.add("ws-standalone");':'')+'</script>'; // Synthetic fixture marker, never a credential. # pragma: allowlist secret
   const label='<div class="fx-preview-label" role="note" style="position:fixed;bottom:8px;right:16px;z-index:9999;padding:6px 12px;border-radius:8px;background:var(--fr-surface);color:var(--fr-label);font:12px Inter,sans-serif;border:1px solid var(--fr-glass-edge);pointer-events:none">Brand proof · Sample data · No live actions</div>';
   let html=body.toString('utf8').replace('<head>','<head>'+boot);
   if(u.searchParams.get('proof')==='after') {
    html=html.replace('</head>','<link rel="stylesheet" href="/proof.css"></head>');
    const at=html.lastIndexOf('</body>');html=html.slice(0,at)+'<script src="/static/friday_workspace_switcher.js"></script><script src="/proof.js"></script>'+html.slice(at);
   }
   if(u.pathname==='/scene-embed'){const end=html.lastIndexOf('</body>');html=html.slice(0,end)+'<script src="/scene-bridge.js"></script>'+html.slice(end);}
   if(u.pathname==='/spatial-embed'){const end=html.lastIndexOf('</body>');html=html.slice(0,end)+'<script src="/spatial-embed.js"></script>'+html.slice(end);}
   const closeBody=html.lastIndexOf('</body>');
   html=closeBody<0?html+label:html.slice(0,closeBody)+label+html.slice(closeBody);
   body=Buffer.from(html);
  }
  res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Content-Length':body.length,'Cache-Control':'no-store'});res.end(body);
 }catch(e){json(res,{status:'error',message:e.message},500);}
});
server.listen(port,'127.0.0.1',()=>process.stdout.write('Design preview: http://127.0.0.1:'+port+'\nActual branch UI; synthetic data only.\n'));
module.exports={projects,conversations,documents,cards};
