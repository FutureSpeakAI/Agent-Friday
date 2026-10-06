/* Exercise the shipped draft routing without a browser, account or model. */
'use strict';
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
function source(file){const s=fs.readFileSync(path.join(root,file),'utf8').replaceAll('\r\n','\n');const a=s.indexOf('  const experienceDraft = async'),b=s.indexOf('\n  };',a);assert(a>=0&&b>a);return s.slice(a,b+5);}
const code=source('index.html');assert.equal(code,source('ui_parts/app.html'));
function host(options={}){
 const calls=[],drafts={project:'Project draft',personal:'Personal draft'},records=options.records||[{id:'project',project:'studio',status:'active'},{id:'personal',project:null,status:'active'}];let active='project';
 const context={convList:records,getConversationId:()=>active,openConversation:id=>{active=id;calls.push(['open',id]);},setChatIn:update=>{drafts[active]=update(drafts[active]||'');},showChat:()=>calls.push(['show']),refreshConvs:async()=>{},apiFetch:async(url,request)=>{calls.push(['request',url,JSON.parse(request.body)]);if(options.race)active='elsewhere';return {ok:true,json:async()=>({status:'ok',conversation:options.response||{id:'created',project:null}})};}};
 vm.createContext(context);vm.runInContext((options.broken?code.replace('if (projectId || personalContext)','if (projectId)'):code)+'\nthis.prepare=experienceDraft;',context);
 return {prepare:context.prepare,calls,drafts,get active(){return active;}};
}
(async()=>{
 let h=host();await h.prepare('A personal idea',null,{personal:true});assert.equal(h.active,'personal');assert.equal(h.drafts.personal,'Personal draft\n\nA personal idea');assert.equal(h.drafts.project,'Project draft');assert(!h.calls.some(c=>c[0]==='request'));
 h=host({broken:true});await h.prepare('A personal idea',null,{personal:true});assert.notEqual(h.active,'personal','The negative control must reproduce the old routing error');
 h=host();await h.prepare('A project idea','studio');assert.equal(h.active,'project');assert.equal(h.drafts.project,'Project draft\n\nA project idea');
 h=host();await h.prepare('Existing material flow',null);assert.equal(h.active,'project','Existing unscoped material callers retain their selected conversation');
 h=host({records:[{id:'project',project:'studio',status:'active'}]});await h.prepare('New personal draft',null,{personal:true});assert.equal(h.active,'created');assert.equal(h.calls[0][2].project,null);assert.equal(h.drafts.created,'New personal draft');
 h=host({records:[{id:'project',project:'studio',status:'active'}],response:{id:'wrong',project:'studio'}});await assert.rejects(h.prepare('Kept draft',null,{personal:true}),/personal conversation/);assert.equal(h.drafts.project,'Project draft');assert(!h.calls.some(c=>c[0]==='show'));
 h=host({records:[],race:true});await assert.rejects(h.prepare('Kept draft',null,{personal:true}),/active chat changed/);assert.equal(h.drafts.project,'Project draft');assert(!h.calls.some(c=>c[0]==='show'));
 h=host({records:[],response:{project:null}});await assert.rejects(h.prepare('Kept draft',null,{personal:true}),/Could not open/);assert(!h.calls.some(c=>c[0]==='open'));
 console.log('8 native draft context checks passed, including a failing negative control and source mirror agreement.');
})().catch(error=>{console.error(error);process.exitCode=1;});
