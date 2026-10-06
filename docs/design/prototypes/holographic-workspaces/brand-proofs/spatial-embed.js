(()=>{
  const style=document.createElement('style');
  style.textContent=`.top-bar,.dock-container,.dock,.ws-tab-head,.ws-tab-header,.ws-tab-bar,.fx-preview-label,.lb-head,.lb-side,.lb-insp,.lb-count,.lb-foot,.ff3-bar,.pf-locationbar,.pf-viewbar,.pf-kindbar{display:none!important}.ws-tab{position:fixed!important;inset:0!important;margin:0!important;padding:0!important;height:100vh!important;width:100vw!important;max-width:none!important}.ws-tab-body,.lb-root,.lb-body,.lb-main,.ff3-root,.ff3-body{height:100%!important;width:100%!important;max-height:none!important;min-height:0!important;padding:0!important;margin:0!important;gap:0!important;border:0!important}.lb-body{display:block!important}.ff3-body>[tabindex="0"]{height:100%!important}.pf-scene{height:100%!important;min-height:0!important;flex:1!important;border:0!important;border-radius:0!important}.lb-main>div:not(.ff3-root){margin:0!important}.ws-tab>header{display:none!important}body{background:#050913!important}`;
  document.head.appendChild(style);
  const fill=document.createElement('style');fill.textContent='.pf-scene{position:fixed!important;inset:0!important;width:100vw!important;height:100vh!important;min-height:0!important}.pf-scene canvas{width:100%!important;height:100%!important}.chat-handle,.chat-tab-handle{display:none!important}';document.head.appendChild(fill);
  const find=text=>[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===text);
  let initialized=false,last='',viewReady=false;
  const tick=setInterval(()=>{
    if(!initialized){const pc=find('Browse this PC');if(pc){pc.click();initialized=true;}}
    if(initialized&&!document.querySelector('.pf-scene'))return;
    if(!viewReady){choose(new URLSearchParams(location.search).get('view')||'City');viewReady=true;}
    const preview=document.querySelector('.pf-preview strong');
    if(preview&&preview.textContent!==last){last=preview.textContent;parent.postMessage({type:'friday-proof-selection',name:last},'http://127.0.0.1:3193');}
  },250);
  const views={Wall:'▤ Wall',Carousel:'◎ Carousel',Tree:'⋔ Tree',City:'▥ City',Timeline:'⌛ Timeline',Clusters:'❖ Clusters'};
  function choose(name){const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim().endsWith(' '+name));if(b)b.click();}
  window.addEventListener('message',e=>{if(e.origin==='http://127.0.0.1:3193'&&e.data?.type==='friday-proof-arrangement')choose(e.data.view);});
  window.addEventListener('pagehide',()=>clearInterval(tick));
})();
