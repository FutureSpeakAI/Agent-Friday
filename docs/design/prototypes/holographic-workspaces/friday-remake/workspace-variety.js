/* Workspace compositions for the local design proof. No live services or user files. */
(() => {
'use strict';
const q = (s, root=document) => root.querySelector(s);
const qa = (s, root=document) => [...root.querySelectorAll(s)];
const safe = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pill = (text, cls='') => `<span class="v-pill ${cls}">${text}</span>`;
const action = (act, label, value='', cls='') => `<button data-act="${act}" ${value?`data-value="${safe(value)}"`:''} class="${cls}">${label}</button>`;
const tag = text => `<span class="v-tag">${text}</span>`;
const waves = () => Array.from({length:62},(_,i)=>`<i style="--bar:${10+Math.abs(Math.sin(i*.74)*Math.cos(i*.12))*65}%"></i>`).join('');

/* These are artwork previews of sample deliverables, never replacements for the avatar. */
function artwork(kind, title='') {
  if (kind==='site') return `<div class="v-art v-art-site" data-depth="12" aria-hidden="true"><div class="v-site-nav"><b>NORTHSTAR</b><span>Work &nbsp; Studio &nbsp; Contact ↗</span></div><div class="v-site-hero"><div><small>INDEPENDENT CREATIVE STUDIO</small><h3>Ideas deserve<br>to take shape.</h3><span class="v-site-cta">See what we make ↗</span></div><div class="v-sculpture"><i></i><i></i><i></i></div></div><div class="v-site-foot"><span>STRATEGY</span><span>DESIGN</span><span>EXPERIENCES</span></div></div>`;
  if (kind==='writing') return `<div class="v-art v-art-writing" data-depth="8" aria-hidden="true"><div class="v-paper-head">NORTHSTAR JOURNAL <span>NO. 01</span></div><small>INTRODUCING THE STUDIO</small><h3>A little clarity.<br>A new beginning.</h3><p>Good work begins with a question. What could this become?</p><div class="v-paper-rule"></div><p>We bring ideas into focus, then make them real. This is a place for the work that comes next.</p><div class="v-paper-footer">DRAFT / 4 MIN READ</div></div>`;
  if (kind==='audio') return `<div class="v-art v-art-audio" data-depth="14" aria-hidden="true"><div class="v-album-title">THE<br>OPENING</div><small>A NORTHSTAR AUDIO STUDY</small><div class="v-waveform">${waves()}</div><div class="v-audio-time"><span>00:00</span><span>02:48</span></div></div>`;
  if (kind==='storyboard') return `<div class="v-art v-art-storyboard" data-depth="9" aria-hidden="true"><div class="v-story-frame v-story-one"><span>01 / THE SPARK</span><i></i></div><div class="v-story-frame v-story-two"><span>02 / TAKING SHAPE</span><i></i></div><div class="v-story-frame v-story-three"><span>03 / THE INVITATION</span><b>Make<br>something<br>matter.</b></div><div class="v-filmline"><span>00:00</span><i></i><i></i><i></i><span>00:30</span></div></div>`;
  if (kind==='image') return `<div class="v-art v-art-image" data-depth="18" aria-hidden="true"><div class="v-orbital-art"><i></i><i></i><i></i></div><div class="v-image-label"><span>FORM / 003</span><strong>Different by<br>design.</strong></div></div>`;
  if (kind==='notes') return `<div class="v-art v-art-notes" data-depth="7" aria-hidden="true"><small>FIELDNOTES / 06 OCT</small><h3>Keep the<br>interesting bits.</h3><div class="v-note-swatch"><i></i><i></i><i></i><i></i></div><p>Observations · Fragments · Possibilities</p><div class="v-note-lines"><i></i><i></i><i></i></div></div>`;
  return '';
}

function media(content) {
  const cards=q('.threecol', content);
  if(!cards)return;
  cards.className='v-lighttable';
  cards.setAttribute('aria-label','Creative work light table');
  const records=[
    ['Launch page','Website / v2','Ready to review','site','go-code',''],
    ['The launch story','Writing / 426 words','Draft','writing','media-editor','The launch story'],
    ['Notes from the field','Collection / 12 notes','In progress','notes','media-editor','Notes from the field'],
    ['The opening','Audio / 02:48','Concept','audio','creation-tool','Audio'],
    ['Thirty seconds of possibility','Storyboard / 3 scenes','Concept','storyboard','creation-tool','Video'],
    ['Form study / 003','Image / 1600 × 1200','Concept','image','creation-tool','Image']
  ];
  cards.innerHTML=records.map(([name,type,status,kind,act,value],i)=>`<article class="v-media-piece v-piece-${kind}" style="--piece-order:${i}"><button class="v-art-button" data-act="${act}" ${value?`data-value="${safe(value)}"`:''} aria-label="Open ${safe(name)}">${artwork(kind)}</button><div class="v-piece-meta"><div><span class="v-type">${type}</span><h3>${name}</h3></div>${pill(status,status==='Ready to review'?'ready':'')}</div></article>`).join('');
  const toolbar=q('.toolbar',content);
  if(toolbar) {
    toolbar.classList.add('v-media-toolbar');
    toolbar.insertAdjacentHTML('afterend',`<div class="v-edition-line"><span>CREATIVE LIBRARY</span><span>6 sample studies · one shared context</span><div class="v-density"><button data-variety="lighttable-size" data-value="roomy" aria-label="Roomy light table" aria-pressed="true">▦</button><button data-variety="lighttable-size" data-value="compact" aria-label="Compact light table" aria-pressed="false">▤</button></div></div>`);
    const search=q('input',toolbar);
    if(search)search.dataset.varietySearch='media';
  }
  cards.insertAdjacentHTML('afterend','<div class="v-search-empty" hidden>No pieces match that search.</div>');
}

function sites(content) {
  const container=q('.twocol',content);
  if(!container)return;
  container.className='v-site-gallery';
  container.innerHTML=`<div class="v-site-gallery-top"><div><div class="eyebrow">Your publication wall</div><h2>Spaces you’ve made.</h2></div><div class="row">${pill('2 sites')}${pill('Nothing published','neutral')}</div></div><article class="v-site-project v-site-project-main"><div class="v-browser-preview"><div class="v-browser-bar"><span class="v-browser-dots">● ● ●</span><span>northstar.example / local draft</span><button data-act="go-code" aria-label="Open Northstar launch in Code">↗</button></div><button class="v-art-button" data-act="go-code" aria-label="Edit Northstar launch">${artwork('site')}</button></div><div class="v-site-project-info"><div>${tag('NORTHSTAR LAUNCH')}<h3>A place for the next idea.</h3><p>Launch page · version 2<br>Connected to 3 project materials</p></div><div class="v-site-actions">${action('go-code','Open workbench ↗','','primary')}${action('approval','Review publication','','ghost')}</div></div></article><article class="v-site-project v-site-project-secondary"><div class="v-browser-preview"><div class="v-browser-bar"><span class="v-browser-dots">● ● ●</span><span>Studio notes / local draft</span><button data-variety="site-study" aria-label="Open Studio notes preview">↗</button></div><button class="v-art-button" data-variety="site-study" aria-label="Open Studio notes preview"><div class="v-art v-studio-site" data-depth="12"><small>STUDIO NOTES / AN OPEN NOTEBOOK</small><h3>Work in<br>the open.</h3><div class="v-studio-grid"><span>01<br><b>On making</b></span><span>02<br><b>Small things</b></span><span>03<br><b>What's next</b></span></div></div></button></div><div class="v-site-caption"><div><h3>Studio notes</h3><small>Journal · version 1 · sample local site</small></div>${pill('Draft')}</div></article><aside class="v-release-note"><span class="v-release-index">01 → 02</span><div><h3>Every release has a before and after.</h3><p>Compare the page, review its destination, and decide when it is ready to leave the workbench.</p>${action('tab','Deployment history →','','ghost').replace('data-act="tab"','data-act="tab" data-tab="Deployments"')}</div></aside>`;
}

function knowledge(content) {
  const graph=q('.graph',content);
  if(!graph)return;
  graph.classList.add('v-constellation');
  graph.innerHTML=`<div class="v-graph-orbit v-orbit-one" aria-hidden="true"></div><div class="v-graph-orbit v-orbit-two" aria-hidden="true"></div><svg viewBox="0 0 900 530" preserveAspectRatio="none" aria-hidden="true"><defs><linearGradient id="v-link"><stop stop-color="#00d4ff" stop-opacity=".55"/><stop offset="1" stop-color="#7b61ff" stop-opacity=".4"/></linearGradient></defs><g fill="none" stroke="url(#v-link)" stroke-width="1.3"><path d="M450 260 Q290 210 145 140"/><path d="M450 260 Q650 210 760 122"/><path d="M450 260 Q320 350 172 415"/><path d="M450 260 Q650 350 760 407"/><path d="M145 140 Q440 20 760 122" stroke-dasharray="4 6"/><path d="M172 415 Q100 280 145 140" stroke-dasharray="3 5"/></g></svg><span class="v-graph-caption">CONNECTED KNOWLEDGE / NORTHSTAR</span>${[
    ['Northstar launch','PROJECT PAGE','The shared account of the work',50,49,'center',18],
    ['Visual direction','WORKING PRINCIPLES','3 linked ideas',16,26,'',7],
    ['Audience notes','SOURCE MATERIAL','Questions worth answering',84,23,'',12],
    ['Creative practice','RELATED PAGE','How the work takes shape',19,78,'',9],
    ['Launch research','SOURCE MATERIAL','Evidence and references',84,77,'',6]
  ].map(([name,kind,detail,left,top,cls,depth])=>`<button class="v-knowledge-node ${cls}" data-act="graph-node" data-value="${safe(name)}" style="left:${left}%;top:${top}%"><span class="v-node-halo" data-depth="${depth}" aria-hidden="true"></span><small>${kind}</small><strong>${name}</strong><span>${detail}</span></button>`).join('')}<div class="v-map-key"><span><i></i> Connected source</span><span><i class="dotted"></i> Related context</span></div>`;
  graph.parentElement.classList.add('v-knowledge-layout');
  const panel=q('.panel',graph.parentElement);
  if(panel){panel.classList.add('v-knowledge-reading');panel.insertAdjacentHTML('afterbegin','<span class="v-reading-number">01 / READ THE CONNECTION</span>');}
}

function news(content) {
  const lead=q('.lead-story',content);
  if(!lead)return;
  lead.classList.add('v-editorial-lead');
  lead.innerHTML=`<div class="v-editorial-art" data-depth="14" aria-hidden="true"><svg viewBox="0 0 800 500" preserveAspectRatio="xMidYMid slice"><defs><linearGradient id="v-news-grad" x2="1" y2="1"><stop stop-color="#00d4ff"/><stop offset=".6" stop-color="#7b61ff"/><stop offset="1" stop-color="#ff00ff"/></linearGradient></defs><g fill="none" stroke="url(#v-news-grad)" stroke-width="1.1">${Array.from({length:24},(_,i)=>`<ellipse cx="440" cy="260" rx="${60+i*10}" ry="${60+i*4}" transform="rotate(${i*7} 440 260)"/>`).join('')}</g><circle cx="440" cy="260" r="7" fill="#00d4ff"/></svg><span>SCIENCE / IDEAS / CONTEXT</span></div><div class="v-editorial-copy"><div class="eyebrow">The Front Page / sample edition</div><h2>Ideas travel further<br>when knowledge opens.</h2><p>A new public collection becomes a starting point for research, creative work, and questions that cross disciplines.</p><div class="v-byline"><span>Illustrative feature · 5 min read</span>${pill('3 linked sources')}</div>${action('article','Read the feature ↗','Ideas travel further when knowledge opens.','primary')}</div>`;
  const layout=q('.news-layout',content);
  layout.classList.add('v-edition');
  layout.insertAdjacentHTML('beforebegin','<div class="v-edition-masthead"><span>THE FRONT PAGE</span><div>YOUR EDITION <b>06 / 10</b></div></div>');
  const aside=q('aside',layout);
  if(aside)aside.classList.add('v-editorial-column');
}

function workflow(content) {
  const flow=q('.routine-flow',content);
  if(!flow)return;
  flow.classList.add('v-flow-canvas');
  flow.insertAdjacentHTML('afterbegin','<div class="v-flow-label"><span>LOCAL DRAFT FLOW</span><span>4 steps · sharing waits for you</span></div>');
  qa('.routine-step',flow).forEach((node,i)=>{
    node.classList.add('v-flow-node');node.dataset.step=String(i);
    node.insertAdjacentHTML('afterbegin',`<div class="v-flow-symbol" aria-hidden="true" data-depth="${8+i*3}">${['◷','▧','✧','◇'][i]}</div>`);
    node.insertAdjacentHTML('beforeend','<span class="v-node-status">Ready for preview</span>');
  });
  flow.insertAdjacentHTML('afterend',`<div class="v-flow-player"><div><span class="v-flow-live-dot"></span><span id="v-flow-status" role="status" aria-live="polite">Follow a sample run through the flow.</span></div><button class="primary" data-variety="run-flow">▶ Preview the flow</button></div>`);
}

function workflowOverview(content) {
  const table=q('.table-wrap',content);if(!table)return;
  table.insertAdjacentHTML('beforebegin',`<section class="v-routine-feature"><div class="v-routine-feature-head"><div><span class="eyebrow">Featured routine / local draft</span><h2>Morning briefing</h2></div>${action('workflow-open','Open the flow ↗','','ghost')}</div><button class="v-routine-strip" data-act="workflow-open" aria-label="Open Morning briefing flow"><span><i data-depth="7" aria-hidden="true">◷</i><small>WHEN</small><strong>Weekdays, 8:00 AM</strong></span><b aria-hidden="true">→</b><span><i data-depth="10" aria-hidden="true">▧</i><small>GATHER</small><strong>Selected sources</strong></span><b aria-hidden="true">→</b><span><i data-depth="13" aria-hidden="true">✧</i><small>PREPARE</small><strong>A useful briefing</strong></span><b aria-hidden="true">→</b><span><i data-depth="16" aria-hidden="true">◇</i><small>REVIEW</small><strong>Your decision</strong></span></button><div class="v-routine-feature-foot"><span>Four readable steps. One source trail.</span><span>No schedule is active in this proof.</span></div></section>`);
}

function health(content) {
  const grid=q('.health-grid',content);
  if(!grid)return;
  grid.classList.add('v-care-desk');
  grid.insertAdjacentHTML('beforebegin','<div class="v-health-ribbon"><div><small>PERSONAL REFERENCE</small><strong>A little preparation goes a long way.</strong></div><span>Notes and records stay together.</span></div>');
  const first=q('section',grid);
  const heading=q('h1',first);
  if(heading)heading.textContent='Up next';
  const eyebrow=q('.eyebrow',first);if(eyebrow)eyebrow.remove();
  first.insertAdjacentHTML('afterbegin','<div class="v-reference-tab">TODAY</div>');
  const panel=q('.panel',grid);if(panel)panel.classList.add('v-reference-index');
}

function finance(content) {
  const chart=q('.chart',content);
  if(!chart)return;
  chart.className='v-balance-chart';
  chart.setAttribute('aria-label','Illustrative balance history; sample data, January to October');
  chart.innerHTML=`<div class="v-chart-guides"><span>$25k</span><span>$20k</span><span>$15k</span></div><svg viewBox="0 0 700 200" preserveAspectRatio="none" role="img" aria-label="Sample balance rises from January to October"><defs><linearGradient id="v-chart-fill" x2="0" y2="1"><stop stop-color="#00d4ff" stop-opacity=".18"/><stop offset="1" stop-color="#00d4ff" stop-opacity="0"/></linearGradient></defs><path d="M0 166 C32 166 38 142 65 149 S107 128 126 140 S157 101 190 114 S233 91 253 104 S293 72 318 83 S353 62 382 69 S414 99 446 80 S483 48 511 52 S556 28 575 35 S634 20 650 25 S683 9 700 12 L700 200 H0 Z" fill="url(#v-chart-fill)"/><path d="M0 166 C32 166 38 142 65 149 S107 128 126 140 S157 101 190 114 S233 91 253 104 S293 72 318 83 S353 62 382 69 S414 99 446 80 S483 48 511 52 S556 28 575 35 S634 20 650 25 S683 9 700 12" fill="none" stroke="#00d4ff" stroke-width="2.5"/></svg>`;
  const h=q('h1',content);if(h)h.textContent='The numbers, with context.';
  chart.parentElement.classList.add('v-ledger-main');
  chart.insertAdjacentHTML('afterend','<div class="v-ledger-foot"><span>MANUAL SAMPLE</span><span>Updated today · illustrative figures</span></div>');
}

function people(content) {
  const list=q('.split>div',content);
  if(!list)return;
  list.classList.add('v-people-directory');
  qa('.list-row',list).forEach((row,i)=>row.insertAdjacentHTML('afterbegin',`<span class="v-person-monogram" aria-hidden="true">${['SC','ST','WC','RP'][i]||'·'}</span>`));
}

function family(content) {
  const panel=q('.twocol>.panel',content);
  if(!panel)return;
  panel.classList.add('v-family-countdown');
  panel.insertAdjacentHTML('afterbegin','<div class="v-family-calendar" aria-hidden="true"><span>OCTOBER</span><b>18</b><small>A DAY TOGETHER</small></div>');
}

function enhance() {
  const app=window.fridayRemake;
  if(!app)return;
  const {screen,tab,stateMode}=app.state;
  document.body.dataset.workspace=screen;
  document.body.dataset.workspaceTab=tab;
  const content=q('#workspace-content');
  if(!content||content.dataset.varietyReady)return;
  content.dataset.varietyReady='true';
  if(stateMode==='Normal') {
    if(screen==='media'&&tab==='Library')media(content);
    if(screen==='futurespeak'&&tab==='Sites')sites(content);
    if(screen==='knowledge'&&['Explore','Connections'].includes(tab))knowledge(content);
    if(screen==='news'&&tab==='Front page')news(content);
    if(screen==='workflows'&&tab==='Build')workflow(content);
    if(screen==='workflows'&&tab==='Routines')workflowOverview(content);
    if(screen==='health'&&tab==='Today')health(content);
    if(screen==='finance'&&tab==='Overview')finance(content);
    if(screen==='contacts'&&tab==='People')people(content);
    if(screen==='family'&&tab==='Plans')family(content);
  }
  document.dispatchEvent(new CustomEvent('friday:variety-ready',{bubbles:true,detail:{screen,tab}}));
}

let runGeneration=0;
document.addEventListener('click', async event=>{
  const button=event.target.closest('[data-variety]');if(!button)return;
  if(button.dataset.variety==='site-study') {
    const state=window.fridayRemake?.state;
    const overlay=q('#overlay-root');
    if(!state||!overlay)return;
    state.previousFocus=button;state.overlay='site-study';
    overlay.innerHTML=`<div class="overlay-shade" data-backdrop="true"><section class="modal wide" role="dialog" aria-modal="true" aria-label="Studio notes preview"><header class="modal-header"><div><h2>Studio notes</h2><small>Sample local site · version 1 · not published</small></div><button data-act="close" aria-label="Close dialog">×</button></header><div class="modal-content"><div class="v-studio-site v-studio-expanded"><small>STUDIO NOTES / AN OPEN NOTEBOOK</small><h3>Work in<br>the open.</h3><p>A place to collect what we notice, share what we learn, and follow the work while it is still becoming.</p><div class="v-studio-grid"><span>01<br><b>On making</b><p>The value of making something small enough to finish.</p></span><span>02<br><b>Small things</b><p>Notes from ordinary moments that held our attention.</p></span><span>03<br><b>What's next</b><p>Questions we have not answered yet.</p></span></div></div><div class="notice">This site is a visual study. Its individual pages and editor are not implemented in this proof. The Northstar launch site has the connected workbench demonstration.</div></div><footer class="modal-footer"><button data-act="close">Close preview</button><button data-act="go-code" class="primary">Open Northstar workbench →</button></footer></section></div>`;
    requestAnimationFrame(()=>q('[aria-label="Close dialog"]',overlay)?.focus());
  }
  if(button.dataset.variety==='lighttable-size') {
    const table=q('.v-lighttable');if(!table)return;
    table.classList.toggle('is-compact',button.dataset.value==='compact');
    qa('[data-variety="lighttable-size"]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
  }
  if(button.dataset.variety==='run-flow') {
    if(button.disabled)return;
    const generation=++runGeneration;
    const steps=qa('.v-flow-node');
    button.disabled=true;button.textContent='Previewing…';
    const status=q('#v-flow-status');
    document.dispatchEvent(new CustomEvent('friday:proof-activity',{bubbles:true,detail:{status:'working',label:'Previewing the morning briefing flow'}}));
    const labels=['Schedule reached · sample trigger','Three sample sources gathered','Briefing draft prepared','Sharing paused for your review'];
    for(let i=0;i<steps.length;i++) {
      if(generation!==runGeneration||!steps[i].isConnected)break;
      steps.forEach((step,n)=>{step.classList.toggle('is-running',n===i);step.classList.toggle('is-complete',n<i);});
      status.textContent=labels[i];
      q('.v-node-status',steps[i]).textContent=i===3?'Waiting for your decision':'Preview in progress';
      await new Promise(resolve=>setTimeout(resolve,900));
      if(generation!==runGeneration)break;
      q('.v-node-status',steps[i]).textContent=i===3?'Waiting for your decision':'Preview complete';
    }
    if(generation===runGeneration&&button.isConnected) {
      steps.forEach((s,i)=>{s.classList.remove('is-running');s.classList.toggle('is-complete',i<3);s.classList.toggle('is-waiting',i===3);});
      button.disabled=false;button.textContent='↻ Replay the flow';
      document.dispatchEvent(new CustomEvent('friday:proof-activity',{bubbles:true,detail:{status:'waiting',label:'Sample briefing ready; sharing waits for your decision'}}));
    }
  }
});
document.addEventListener('input',event=>{
  if(event.target.dataset.varietySearch!=='media')return;
  const search=event.target.value.trim().toLowerCase();let count=0;
  qa('.v-media-piece').forEach(piece=>{piece.hidden=!piece.textContent.toLowerCase().includes(search);if(!piece.hidden)count++;});
  const empty=q('.v-search-empty');if(empty)empty.hidden=!!count;
});
document.addEventListener('friday:render',()=>{runGeneration++;enhance();});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhance,{once:true});else enhance();
window.FridayWorkspaceVariety={enhance};
})();
