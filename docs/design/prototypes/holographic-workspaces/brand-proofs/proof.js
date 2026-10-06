/* Review-only adapter. The original app still owns each workspace and renderer. */
(() => {
  'use strict';
  document.body.classList.add('friday-proof');
  const registry = window.FRIDAY_WORKSPACE_REGISTRY.workspaces.filter(w => !w.held);
  let switcherRoot, switcherKey = '';
  function mark(el, cls) { if (el && !el.classList.contains(cls)) el.classList.add(cls); }
  function update() {
    const bar = document.querySelector('.top-bar');
    if (bar && !switcherRoot && window.FridayWorkspaceSwitcher) {
      const scene = bar.querySelector('[aria-label="Scene selection"]');
      const lockup = bar.querySelector('.fr-lockup');
      const anchor = scene && scene.parentElement || lockup;
      if (anchor) {
        const host = document.createElement('div');host.className='pf-switcher-mount';
        anchor.insertAdjacentElement(scene ? 'beforebegin' : 'afterend',host);
        switcherRoot = ReactDOM.createRoot(host);
      }
    }
    if (switcherRoot) {
      const windows = [...document.querySelectorAll('.fwin')].sort((a,b)=>Number(b.style.zIndex)-Number(a.style.zIndex));
      const standalone=window.__FRIDAY_STANDALONE__;
      const current=standalone || (windows[0] && windows[0].querySelector('[data-fwin-max]')?.dataset.fwinMax) || '';
      const key=current+':'+windows.length;
      if (switcherKey!==key) {
        switcherKey=key;
        switcherRoot.render(React.createElement(window.FridayWorkspaceSwitcher,{
          workspaces:registry,currentWorkspace:current,
          onSelect:id=>{if(standalone || new URLSearchParams(location.search).get('chrome')==='chat')location.assign((id==='settings'?'/?workspace=settings':'/w/'+id)+(id==='settings'?'&':'?')+'proof=after');else window.fridayOpenWorkspace({workspace:id});},
          onOpenTab:id=>!!window.open('/w/'+id+'?proof=after','_blank'),
          onHome:()=>{location.assign('/?proof=after');}
        }));
      }
    }
    document.querySelectorAll('.lb-root').forEach(root=>{
      const pc=!!root.querySelector('.lb-side button[aria-current="true"]')?.textContent.includes('Browse this PC');
      if(root.classList.contains('proof-pc')!==pc)root.classList.toggle('proof-pc',pc);
      root.querySelectorAll('.lb-head button').forEach(b=>{if(b.textContent==='Inspector')mark(b,'pf-library-inspector');});
    });
    document.querySelectorAll('.ff3-body > [tabindex="0"]').forEach(root=>{
      const [locationbar,views,kinds,scene]=root.children;
      mark(locationbar,'pf-locationbar');mark(views,'pf-viewbar');mark(kinds,'pf-kindbar');mark(scene,'pf-scene');
      if(locationbar){
        const crumb=locationbar.querySelector(':scope > div');
        if(crumb && crumb.children.length===1)mark(crumb,'pf-root-crumb');
        const dazzle=locationbar.querySelector('label');mark(dazzle,'pf-dazzle');
        locationbar.querySelectorAll(':scope > button').forEach(b=>{if(b.title.includes('screen'))mark(b,'pf-fullscreen');});
        if(dazzle && !locationbar.querySelector('.pf-display')){
          const button=document.createElement('button');button.type='button';button.className='btn pf-display';button.textContent='Display';button.setAttribute('aria-expanded','false');
          button.onclick=()=>{const open=locationbar.classList.toggle('pf-display-open');button.setAttribute('aria-expanded',String(open));};
          locationbar.appendChild(button);
        }
      }
      const close=root.querySelector('[aria-label="Close preview"]');
      if(close)mark(close.parentElement.parentElement,'pf-preview');
    });
  }
  let pending=false;
  new MutationObserver(()=>{if(!pending){pending=true;requestAnimationFrame(()=>{pending=false;update();});}}).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['aria-current','class','style']});
  document.addEventListener('keydown',e=>{
    if(e.key!=='Escape')return;
    const menus=document.querySelectorAll('.pf-display-open');
    if(!menus.length)return;
    e.preventDefault();e.stopPropagation();
    menus.forEach(el=>{el.classList.remove('pf-display-open');const button=el.querySelector('.pf-display');button?.setAttribute('aria-expanded','false');button?.focus();});
  },true);
  update();
})();
