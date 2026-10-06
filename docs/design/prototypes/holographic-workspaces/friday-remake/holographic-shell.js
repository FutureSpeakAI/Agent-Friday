(() => {
 'use strict';
 const api=window.fridayRemake, $=s=>document.querySelector(s), host=$('#scene-host');
 const origin='http://127.0.0.1:3192';
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const state={mode:'balanced',source:'off',arrangement:'companion',framing:1.45,scenes:[],ready:false,scene:null,status:null,studio:false,suspended:false,error:'',lastScreen:'',lastTab:'',origin:null};
 const motion=new window.FridayWorkspaceDepth({root:document.documentElement});
 const reduced=matchMedia('(prefers-reduced-motion: reduce)');
 const frame=document.createElement('iframe');
 frame.title="Friday's original holographic scene";frame.allow='camera';frame.src=origin+'/scene-embed';frame.tabIndex=-1;
 host.append(frame);
 const send=(type,data={})=>frame.contentWindow?.postMessage({source:'friday-remake',type,...data},origin);
 const button=(action,label,extra='')=>`<button data-holo="${action}" ${extra}>${label}</button>`;
 function statusText(){
  if(state.suspended)return 'Scene resting · Files renderer active';
  if(state.error)return state.error;
  if(!state.ready)return 'Loading original scene…';
  if(state.source==='preview')return 'Pointer preview · camera off';
  if(state.source==='camera'){
   if(state.status?.trackingBusy||state.status?.camera?.status==='requesting'||(state.status?.camera?.wanted&&state.status?.camera?.status==='off'))return 'Starting camera…';
   if(state.status?.camera?.status==='live')return state.status?.faceSeen?'Face tracked · depth follows you':'Camera on · looking for a face';
   if(['denied','error','busy','missing','paused'].includes(state.status?.camera?.status))return state.status.camera.detail||'Camera unavailable · try again';
   return 'Camera off';
  }
  return state.status?.transitionProgress<1?'Transforming avatar · camera off':'Original scene live · camera off';
 }
 function setSource(source){
  state.error='';state.source=source;motion.setSource(source);
  if(source!=='preview')send('scene:preview-pose',{pose:null});
  send('scene:tracking',{enabled:source==='camera',userInitiated:source==='camera'});
  updateLabels();
 }
 function setMode(mode){state.mode=mode;motion.setMode(mode);send('scene:mode',{mode});if(mode==='flat')setSource('off');updateLabels();}
 function updateLabels(){
  document.body.dataset.sceneReady=String(state.ready);document.body.dataset.holoSource=state.source;
  document.body.dataset.holoArrangement=state.arrangement;
  document.querySelectorAll('[data-holo-status]').forEach(e=>{e.textContent=statusText();});
  document.querySelectorAll('[data-holo="mode"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.value===state.mode)));
  document.querySelectorAll('[data-holo="arrangement"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.value===state.arrangement)));
  document.querySelectorAll('[data-holo="source"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.value===state.source)));
  document.querySelectorAll('[data-holo="form"]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.index===String(state.scene?.index??state.scenes.find(s=>s.id===state.scene?.id)?.index))));
  const note=$('.scene-note');if(note)note.textContent=state.ready?'Original Friday renderer · '+(state.scene?.name||'Living hologram'):'Loading the original Friday scene';
  const detail=$('.review-detail');if(detail)detail.textContent=' · Sample content · No model or external actions · '+(state.ready?'Live original scene':'Scene loading');
 }
 function controls(){return `<div class="holo-control-section"><span class="eyebrow">Workspace depth</span><div class="holo-segment">${['flat','quiet','balanced','immersive'].map(v=>button('mode',v[0].toUpperCase()+v.slice(1),`data-value="${v}" aria-pressed="${state.mode===v}"`)).join('')}</div><p>Stable reading surfaces. Light, edges and layered artwork respond around them.</p></div><div class="holo-control-section"><span class="eyebrow">Motion source</span><div class="holo-segment">${button('source','Off','data-value="off"')}${button('source','Pointer preview','data-value="preview"')}${button('source','Enable head tracking','data-value="camera"')}</div><p>Head tracking requests your camera through Friday’s original tracker. Pointer preview uses no camera.</p><div class="holo-status" role="status" data-holo-status>${esc(statusText())}</div>${reduced.matches?'<p>Reduced motion is on: workspace travel and tilt are disabled.</p>':''}</div><div class="holo-control-section"><span class="eyebrow">Friday’s place in your work</span><div class="holo-segment">${[['companion','Companion'],['present','Present'],['focus','Focus']].map(([v,n])=>button('arrangement',n,`data-value="${v}" aria-pressed="${state.arrangement===v}"`)).join('')}</div><p>Companion keeps Friday beside the work. Present gives her the stage. Focus gives the canvas its full width.</p></div>`;}
 function studio(){
  state.studio=true;
  api.modal('Friday · Scene & depth',`<div class="holo-studio"><div><div class="holo-studio-preview" data-scene-preview></div><div class="holo-preview-caption"><span>YOUR ORIGINAL FRIDAY</span><strong data-holo-scene-name>${esc(state.scene?.name||'Holographic scene')}</strong><small data-holo-status>${esc(statusText())}</small></div><div class="holo-form-grid">${state.scenes.map(s=>button('form',`<span>${String(s.index+1).padStart(2,'0')}</span>${esc(s.name)}`,`data-index="${s.index}" aria-pressed="false"`)).join('')||'<p>Loading the original avatar collection…</p>'}</div></div><aside>${controls()}</aside></div>`,{wide:true,kind:'hologram',subtitle:'The actual avatar collection, with one continuous scene.'});
  $('.holo-form-grid').insertAdjacentHTML('beforebegin',`<div class="holo-segment" style="margin:0 0 16px">${[[1.45,'Wide'],[3,'Closer'],[5,'Close-up']].map(([v,label])=>button('framing',label,`data-value="${v}" aria-label="Avatar framing ${label}" aria-pressed="${state.framing===v}"`)).join('')}</div>`);
  updateLabels();requestAnimationFrame(placeScene);
 }
 function placeScene(){
  const slot=$('[data-scene-preview]');state.studio=!!slot;
  send('scene:framing',{factor:state.studio?state.framing:1});
  host.classList.toggle('in-studio',state.studio);
  if(slot){const r=slot.getBoundingClientRect();const modal=slot.closest('[role="dialog"]').getBoundingClientRect();host.style.setProperty('--scene-left',r.left+'px');host.style.setProperty('--scene-top',r.top+'px');host.style.setProperty('--scene-width',r.width+'px');host.style.setProperty('--scene-height',r.height+'px');host.style.clipPath=`inset(${Math.max(0,modal.top-r.top)}px 0 ${Math.max(0,r.bottom-modal.bottom)}px 0)`;}
  else{for(const n of ['left','top','width','height'])host.style.removeProperty('--scene-'+n);host.style.clipPath='';}
 }
 function layout(){
  const files=!!$('#spatial-engine');
  if(files!==state.suspended){state.suspended=files;send('scene:visibility',{visible:!files&&!document.hidden});if(files){state.source='off';motion.setSource('off');send('scene:tracking',{enabled:false});}}
  document.body.dataset.sceneSuspended=String(files);
  const screen=api.state.screen;const companion=$('#scene-companion');
  companion.innerHTML=screen==='desktop'?'':`<div class="companion-caption"><span class="eyebrow">Friday / ${state.arrangement==='present'?'Present':'Alongside you'}</span><strong>${esc(state.scene?.name||'Holographic companion')}</strong><small data-holo-status>${esc(statusText())}</small><div>${button('studio','Scene & depth')}${state.source!=='off'?button('source','Stop motion','data-value="off"'):''}</div></div>`;
  const top=$('.topbar');if(top&&!top.querySelector('[data-holo]')){const b=document.createElement('button');b.dataset.holo='studio';b.className='depth-trigger';b.textContent='◈ Depth';b.setAttribute('aria-label','Scene and workspace depth');top.querySelector('[data-act="appearance"]')?.replaceWith(b);}
  if(screen==='desktop'&&!$('.desktop-scene-controls')){const div=document.createElement('div');div.className='desktop-scene-controls';div.innerHTML=`<div><span class="eyebrow">A living desktop</span><strong data-holo-status>${esc(statusText())}</strong></div>${button('studio','Explore avatars & depth ↗','class="primary"')}${button('source','Try motion','data-value="preview"')}`;$('.scene-note')?.replaceWith(div);}
  document.querySelectorAll('.home-context,.home-composer,.resume-panel,.panel,.chat-artifact,.document').forEach(el=>el.classList.add('friday-depth-surface'));
  if(screen==='settings')document.querySelectorAll('.setting-row').forEach(row=>{if(row.querySelector('strong')?.textContent==='Face tracking'){const old=row.querySelector('button');if(old){old.dataset.holo='studio';delete old.dataset.act;old.className='';old.removeAttribute('role');old.removeAttribute('aria-checked');old.textContent='Scene & tracking';old.setAttribute('aria-label','Configure head tracking');}}});
  motion.refresh();updateLabels();placeScene();
 }
 function navigateMotion(){
  const screen=api.state.screen,tab=api.state.tab,changed=screen!==state.lastScreen;
  if(!reduced.matches&&(changed||tab!==state.lastTab)){
   const main=$('.main');const originBox=state.origin;
   if(main&&main.animate){const rect=main.getBoundingClientRect();const ox=originBox?Math.max(0,Math.min(rect.width,originBox.x-rect.left)):rect.width/2;main.style.transformOrigin=ox+'px 100%';main.animate([{opacity:.35,transform:changed?'translateY(14px) scale(.975)':'translateY(5px)'},{opacity:1,transform:'none'}],{duration:changed?340:180,easing:'cubic-bezier(.2,.75,.2,1)'});}
  }
  state.lastScreen=screen;state.lastTab=tab;state.origin=null;
 }
 document.addEventListener('click',e=>{
  const holo=e.target.closest('[data-holo]');
  const old=e.target.closest('[data-act="appearance"],[data-act="camera"]');
  if(old){e.preventDefault();e.stopImmediatePropagation();studio();return;}
  if(!holo){const nav=e.target.closest('.dock button,[data-act="materials"]');if(nav){const r=nav.getBoundingClientRect();state.origin={x:r.x+r.width/2,y:r.y};}return;}
  e.preventDefault();e.stopImmediatePropagation();
  const value=holo.dataset.value;
  if(holo.dataset.holo==='studio')studio();
  if(holo.dataset.holo==='mode')setMode(value);
  if(holo.dataset.holo==='source'){
   if(state.suspended&&value!=='off'){api.toast('The spatial Files renderer has the stage. Return to the desktop to enable tracking.');return;}
   if(state.mode==='flat'&&value!=='off')setMode('balanced');
   setSource(value);
  }
  if(holo.dataset.holo==='arrangement'){state.arrangement=value;updateLabels();layout();}
  if(holo.dataset.holo==='form'){send('scene:set',{index:Number(holo.dataset.index)});}
  if(holo.dataset.holo==='framing'){state.framing=Number(value);send('scene:framing',{factor:state.framing});document.querySelectorAll('[data-holo="framing"]').forEach(b=>b.setAttribute('aria-pressed',String(b===holo)));}
 },true);
 let pointerTime=0;
 document.addEventListener('pointermove',e=>{
  if(state.source!=='preview'||state.suspended||performance.now()-pointerTime<40)return;
  pointerTime=performance.now();const pose={x:(e.clientX/innerWidth-.5)*.8,y:(.5-e.clientY/innerHeight)*.55,z:0};
  send('scene:preview-pose',{pose});
 });
 document.addEventListener('pointerleave',()=>{if(state.source==='preview'){send('scene:preview-pose',{pose:null});motion.setPose({x:0,y:0,z:0,seen:false});}});
 window.addEventListener('message',e=>{
  if(e.origin!==origin||e.source!==frame.contentWindow||e.data?.source!=='friday-scene')return;
  const d=e.data;
  if(d.type==='pose'){if(d.pose?.source===state.source)motion.setPose(d.pose);return;}
  if(d.type==='error'){state.error=d.message;updateLabels();return;}
  if(d.scenes)state.scenes=d.scenes;
  if(d.type==='ready'||d.type==='status'){
   state.ready=!!d.ready;state.status=d;state.scene=d.scene;
   if(d.type==='ready'){send('scene:mode',{mode:state.mode});send('scene:visibility',{visible:!state.suspended&&!document.hidden});if(state.studio)studio();}
   const label=$('[data-holo-scene-name]');if(label)label.textContent=state.scene?.name||'Holographic scene';
   const title=$('.companion-caption strong');if(title)title.textContent=state.scene?.name||'Holographic companion';
   updateLabels();
  }
 });
 document.addEventListener('friday:render',()=>{layout();navigateMotion();});
 document.addEventListener('friday:variety-ready',()=>motion.refresh());
 const overlayObserver=new MutationObserver(()=>{placeScene();if(!reduced.matches){const dialog=$('[role="dialog"]');if(dialog&&!dialog.dataset.motionEntered){dialog.dataset.motionEntered='true';dialog.animate([{opacity:0,transform:'translateY(8px) scale(.99)'},{opacity:1,transform:'none'}],{duration:190,easing:'ease-out'});}}});
 overlayObserver.observe($('#overlay-root'),{childList:true});
 window.addEventListener('resize',placeScene);document.addEventListener('scroll',()=>{if(state.studio)placeScene();},true);
 document.addEventListener('visibilitychange',()=>{send('scene:visibility',{visible:!state.suspended&&!document.hidden});if(document.hidden)setSource('off');});
 window.addEventListener('pagehide',()=>{send('scene:tracking',{enabled:false});send('scene:visibility',{visible:false});motion.destroy();overlayObserver.disconnect();});
 window.fridayHologram={state,motion,studio,setMode,setSource};
 setMode('balanced');layout();state.lastScreen=api.state.screen;state.lastTab=api.state.tab;
})();
