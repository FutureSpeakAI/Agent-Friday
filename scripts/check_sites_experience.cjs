/* Render the actual Sites workspace against the loopback synthetic preview.
 * All site, hosting and registrar requests are intercepted in this browser.
 * No application imports, credentials, provider calls or real mutations. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {chromium, expect} = require('@playwright/test');
const root = path.resolve(__dirname, '..');
const base = process.env.FRIDAY_DESIGN_URL || 'http://127.0.0.1:3198';
const output = process.env.FRIDAY_SITES_PROOFS;
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Only a loopback synthetic preview is allowed');
const now = Math.floor(Date.now() / 1000), day = new Date((now + 10 * 86400) * 1000).toISOString().slice(0,10);
const accountA = {account_id:'account-a', revision:2, label:'Studio domains', environment:'production', connection_status:'verified', last_synced_at:now-600};
const accountB = {account_id:'account-b', revision:1, label:'Personal collection', environment:'production', connection_status:'not_connected', last_synced_at:null};
const codebases = [{id:'cb-studio', title:'Northstar website', conversation_id:'design-direction'}, {id:'cb-notes', title:'Field notes repository', conversation_id:'design-field'}];
const hosting = [{connection_id:'host-studio',revision:3,adapter:'github_pages',name:'Studio Pages',repo:'sample/studio',branch:'gh-pages',connected:true},
  {connection_id:'host-unavailable',revision:1,adapter:'cloudflare_pages',name:'Earlier Cloudflare',project:'old-project',connected:true}];
const build = {build_id:'build-ready',status:'built',created_at:now-3600,source_revision:'abc1234',source_hash:'snapshot-one',output_hash:'output-one'};
const published = {operation_id:'publish-good',action:'publish',status:'verified_live',build_id:build.build_id,created_at:now-3000,checked_at:now-120,verification:{url:'https://example.com/',verified:true,note:'One observed public endpoint returned the expected deployment marker with a valid hostname certificate.'}};
const seed = () => [{site_id:'site-studio',revision:4,name:'Northstar studio',codebase_id:'cb-studio',conversation_id:'design-direction',project_id:'design-launch',build_root:'.',build_command:'npm run build',output_dir:'dist',hosting:{connection_id:'host-studio'},domain:{account_id:'account-a',account_revision:2,domain:'example.com',hostname:'www.example.com'},builds:[{build_id:'build-failed',status:'failed',created_at:now-60,error:'The build command exited with code 1.'},build],latest_build:{build_id:'build-failed',status:'failed',error:'The build command exited with code 1.'},current_deployment:published,latest_deployment:{operation_id:'publish-failed',action:'publish',status:'failed',build_id:build.build_id,created_at:now-30,error:'The host refused this upload.'},history:[published]},
  {site_id:'site-notes',revision:1,name:'Field notes',codebase_id:'cb-notes',conversation_id:'design-field',project_id:'design-notes',build_root:'.',build_command:'',output_dir:'.',hosting:null,domain:null,builds:[],history:[]}];
const inventory = [{account_id:'account-a',account_revision:2,domain:'example.com',verified:true,expire_date:day,autorenew_enabled:false,dns_authority:'namecom',last_synced_at:now-600},
  {account_id:'account-b',account_revision:1,domain:'example.com',verified:false,imported:{next_date_kind:'renews',next_date:day,source:'user_import'}},
  {account_id:'account-b',account_revision:1,domain:'example.org',verified:false,imported:{next_date_kind:'expires',next_date:day,source:'user_import'}}];
const records = [{id:17,host:'www',type:'CNAME',answer:'old.example.net',ttl:300},{id:19,host:'',type:'MX',answer:'mail.example.net',priority:10,ttl:3600}];
const frames=[], errors=[], requests=[];
const previewOrigin = 'http://p' + 'a'.repeat(48) + '.localhost:3199';
const previewPath = '/api/sites/preview-frame/p' + 'a'.repeat(48);
const previewTarget = () => ({site_id:'site-studio',preview_build_id:'build-ready',preview_request_id:'n'+'b'.repeat(48)});
const answer = (route, data, status=200) => route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
async function tabState(page) {return page.evaluate(() => {const params={};window.dispatchEvent(new CustomEvent('friday:tab-state',{detail:{workspace:'futurespeak',params}}));return params;});}
async function navigate(page, target) {await page.evaluate(t => window.dispatchEvent(new CustomEvent('friday-nav',{detail:{workspace:'futurespeak',...t}})), target);}
async function capture(page, name, locator) {
  if (locator) await locator.scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  const geometry = await page.locator('.sites-workspace').evaluate(host => {
    const rect=host.getBoundingClientRect();
    const overflow=[...host.querySelectorAll('input,select,textarea,button,a')].filter(e=>e.getClientRects().length).filter(e=>{const r=e.getBoundingClientRect();return r.left<rect.left-2||r.right>rect.right+2;}).map(e=>e.getAttribute('aria-label')||e.textContent);
    return {width:innerWidth,height:innerHeight,left:rect.left,right:rect.right,overflow,style:window.FridayDisplayStyle.get()};
  });
  assert.deepEqual(geometry.overflow,[],name+': controls remain inside the workspace');
  assert.ok(geometry.left>=-2 && geometry.right<=geometry.width+2,name+': workspace fits viewport');
  frames.push({name,...geometry});
  if(output) await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled'});
  console.log('PASS '+name);
}
async function framesForModes(page, section, locator) {
  for (const style of ['simple','classic']) {
    await page.evaluate(value=>window.FridayDisplayStyle.set(value),style);
    for (const width of [1480,390]) {await page.setViewportSize({width,height:width===390?844:1000});await capture(page,style+'-'+section+(width===390?'-compact':''),locator);}
  }
}

(async () => {
  assert.ok(os.freemem()>=6*1073741824,'At least 6 GiB free is required');
  assert.equal((await fetch(base+'/api/seat').then(r=>r.json())).preview,true,'Refusing a non-synthetic server');
  if(output){const relative=path.relative(root,path.resolve(output));assert.ok(relative.startsWith('..')||path.isAbsolute(relative),'Proofs stay outside the repository');fs.mkdirSync(output,{recursive:true});}
  const executablePath=process.env.FRIDAY_TEST_BROWSER_EXECUTABLE;
  if(executablePath!==undefined){
    assert.ok(path.isAbsolute(executablePath),'The explicit test browser must use an absolute path');
    assert.ok(fs.statSync(executablePath).isFile(),'The explicit test browser must be an existing file');
  }
  const browserSelection={requested_executable:executablePath??null,channel:executablePath===undefined?'chrome':null};
  const browser=await chromium.launch({headless:true,...(executablePath===undefined?{channel:'chrome'}:{executablePath})});
  try {
    browserSelection.version=browser.version();
    const page=await browser.newPage({viewport:{width:1480,height:1000},reducedMotion:'reduce'});
    page.setDefaultTimeout(15000);page.on('pageerror',e=>errors.push(e.message));
    let sites=seed(), domainOperations=[], siteOperations=[], heldSiteRead=null, holdNextRead=false, failCatalog=false;
    let holdSiteAction=null, heldSiteAction=null, holdDomainAction=null, heldDomainAction=null, revokedPreviews=0, wrapperAlive=true;
    await page.route('**/*',route=>new URL(route.request().url()).origin===new URL(base).origin?route.continue():route.abort());
    await page.route(previewOrigin + '/**', route=>route.fulfill({contentType:'text/html',
      headers:{'Cache-Control':'no-store','Access-Control-Allow-Origin':'*','Cross-Origin-Resource-Policy':'cross-origin'},
      body:'<!doctype html><title>Frozen preview</title><main style="padding:40px;font:22px system-ui"><h1>Northstar</h1><p>This is the exact saved build.</p></main>'}));
    await page.route('**/api/settings',async route=>{const r=await route.fetch();const body=await r.json();return answer(route,{...body,show_all_workspaces:false});});
    await page.route('**/api/codebases',route=>answer(route,{codebases}));
    await page.route('**/api/sites**',async route=>{
      const req=route.request(), url=new URL(req.url()), body=req.method()==='POST'?req.postDataJSON():null;
      if(body){requests.push({path:url.pathname,body,token:req.headers()['x-friday-token']});assert.ok(req.headers()['x-friday-token'],'local mutation has the session token');}
      if(url.pathname==='/api/sites'){
        if(failCatalog)return answer(route,{status:'error',message:'Saved sites cannot be read right now.'},503);
        const snapshot=structuredClone(sites);
        if(holdNextRead){holdNextRead=false;heldSiteRead=()=>answer(route,{status:'ok',sites:snapshot});return;}
        return answer(route,{status:'ok',sites:snapshot});
      }
      if(url.pathname==='/api/sites/hosting')return answer(route,req.method()==='GET'?{connections:hosting}:{status:'ok',connection:{...hosting[0],revision:4}});
      if(url.pathname.endsWith('/disconnect'))return answer(route,{status:'ok'});
      if(url.pathname===previewPath && req.method()==='DELETE'){
        assert.ok(req.headers()['x-friday-token']);revokedPreviews++;return answer(route,{status:'ok'});
      }
      if(url.pathname===previewPath && req.method()==='HEAD' && !wrapperAlive)return answer(route,{status:'error'},410);
      if(url.pathname===previewPath)return route.fulfill({contentType:'text/html',headers:{
        'Content-Security-Policy':"sandbox allow-scripts; default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; frame-src "+previewOrigin+"; frame-ancestors 'self'",
        'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Friday-Site-Preview':'active'},
        body:'<!doctype html><title>Preview wrapper</title><iframe sandbox="allow-scripts" referrerpolicy="no-referrer" src="'+previewOrigin+'/" style="position:fixed;inset:0;width:100%;height:100%;border:0" title="Isolated build"></iframe>'});
      if(url.pathname==='/api/sites/preview'){
        assert.ok(['manual','navigation'].includes(body.mode));
        assert.equal(req.headers()['x-friday-preview-mode'],body.mode);
        if(body.mode==='navigation')assert.equal(body.request_id,previewTarget().preview_request_id);
        else assert.equal(body.request_id,undefined);
        const selected=sites.find(s=>s.site_id===body.site_id);
        assert.equal(body.site_revision,selected.revision);
        const result={status:'ok',site_id:selected.site_id,site_revision:selected.revision,build_id:body.build_id,preview_url:previewPath,expires_at:Date.now()/1000+300};
        if(holdSiteAction==='preview'){holdSiteAction=null;heldSiteAction=()=>answer(route,result);return;}
        return answer(route,result);
      }
      if(url.pathname!=='/api/sites/action')throw new Error('Unexpected Sites request '+url.pathname);
      const {action,args}=body;const site=sites.find(s=>s.site_id===args.site_id);
      const siteReply = result => {
        if(holdSiteAction===action){holdSiteAction=null;const snapshot=structuredClone(result);heldSiteAction=()=>answer(route,snapshot);return;}
        return answer(route,result);
      };
      if(action==='save'){
        if(site){assert.equal(args.revision,site.revision);Object.assign(site,args,{revision:site.revision+1});return answer(route,{status:'ok',site});}
        assert.equal(body.conversation_id,'design-field');const created={...seed()[1],...args,site_id:'site-new',revision:1};sites.push(created);return answer(route,{status:'ok',site:created});
      }
      if(action==='hosting_requirements')return siteReply({status:'ok',requirements:{hostname:'www.example.com',note:'Approve host association before changing DNS.',records:[{host:'www',type:'CNAME',answer:'sample.github.io',ttl:300}]}});
      if(action==='build'||action==='prepare_publish'){
        const op={operation_id:'op-'+action,action:action==='build'?'build':'publish',status:'awaiting_approval',approval_id:'review-'+action,build_id:args.build_id,created_at:now};siteOperations.push(op);return answer(route,{status:'ok',operation:op,operation_id:op.operation_id,approval_id:op.approval_id});
      }
      return answer(route,{status:'ok',operation:siteOperations.find(o=>o.operation_id===args.operation_id)||published});
    });
    await page.route('**/api/domains/**',async route=>{
      const req=route.request(),url=new URL(req.url()),body=req.method()==='POST'?req.postDataJSON():null;
      if(body){requests.push({path:url.pathname,body,token:req.headers()['x-friday-token']});assert.ok(req.headers()['x-friday-token'],'local domain mutation has the session token');}
      if(url.pathname.endsWith('/overview'))return answer(route,{accounts:[accountA,accountB],inventory,operations:domainOperations});
      if(url.pathname.endsWith('/connect'))return answer(route,{status:'ok',account:{...accountB,connection_status:'verified'}});
      if(url.pathname.endsWith('/disconnect')||url.pathname.endsWith('/import'))return answer(route,{status:'ok'});
      const {action,args}=body;
      if(action==='sync')return answer(route,{status:'ok',inventory});
      if(action==='inspect'||action==='records'){
        const result={status:'ok',domain:{...inventory[0],verified_at:now},...(action==='records'?{records}:{} )};
        if(holdDomainAction===action){holdDomainAction=null;const snapshot=structuredClone(result);heldDomainAction=()=>answer(route,snapshot);return;}
        return answer(route,result);
      }
      if(action==='operation'||action==='reconcile'){
        assert.deepEqual(Object.keys(args),['operation_id']);const op=domainOperations.find(o=>o.operation_id===args.operation_id);
        return answer(route,action==='reconcile'?{...op,status:'renewal_observed',message:'Expiration extended; payment is not independently verified.'}:op);
      }
      if(action==='verify')return answer(route,{status:'ok',matches:false,checks:[{hostname:'www.example.com',type:'CNAME',matches:false}],checked_at:now,resolver:'Synthetic resolver',message:'DNS has not matched yet. HTTPS is not checked.'});
      const op={operation_id:'domain-op-'+domainOperations.length,action,status:action==='prepare_renewal'?'awaiting_provider_checkout':'awaiting_approval',account_id:args.account_id,account_revision:2,account_label:accountA.label,domain:args.domain,created_at:now,
        ...(action==='prepare_renewal'?{years:args.years,checkout_url:'https://www.name.com/account/renewalcenter',quote:{subtotal:'12.00',currency:'USD',tax:'unknown',total:null}}:{approval_id:'domain-approval',before:action==='prepare_dns'?records[0]:{autorenew_enabled:false},after:action==='prepare_dns'?args.record:{autorenew_enabled:args.enabled}})};
      domainOperations.push(op);return answer(route,op);
    });
    await page.addInitScript(()=>{window.__FRIDAY_API_TOKEN='synthetic-proof-token';}); // pragma: allowlist secret -- synthetic browser fixture only
    await page.goto(base+'/w/futurespeak?site_id=site-studio');
    await expect(page.getByTestId('site-detail')).toBeVisible();
    await expect.poll(async()=>(await tabState(page)).site_id).toBe('site-studio');
    await expect(page.getByText('Verified live',{exact:true})).toHaveCount(2);
    await expect(page.getByText('The host refused this upload.')).toBeVisible();
    await framesForModes(page,'sites-overview',page.getByLabel('Saved site'));
    await page.getByLabel('Saved build').selectOption('build-ready');
    await page.getByRole('button',{name:'Preview this build',exact:true}).click();
    await expect(page.getByTitle('Frozen site build preview')).toHaveAttribute('sandbox','allow-scripts');
    await expect(page.getByTitle('Frozen site build preview')).toHaveAttribute('data-build-id','build-ready');
    await framesForModes(page,'sites-preview',page.getByTitle('Frozen site build preview'));
    await page.getByRole('button',{name:'Close preview',exact:true}).click();
    await navigate(page,previewTarget());
    await expect.poll(async()=>(await tabState(page)).preview_build_id).toBe('build-ready');
    await expect(page.getByTitle('Frozen site build preview')).toHaveAttribute('src',previewPath);
    await page.getByRole('button',{name:'Close preview',exact:true}).click();
    await expect.poll(async()=>(await tabState(page)).preview_build_id).toBeNull();
    const beforeDelayedNavigation=requests.filter(r=>r.path==='/api/sites/preview').length;
    holdNextRead=true;await navigate(page,previewTarget());
    await expect.poll(()=>!!heldSiteRead).toBe(true);
    await expect(page.getByTestId('site-detail')).toHaveCount(0);
    await page.evaluate(()=>{for(const on of [true,false])window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on}}));});
    await heldSiteRead();heldSiteRead=null;
    await expect(page.getByTestId('site-detail')).toBeVisible();
    assert.equal(requests.filter(r=>r.path==='/api/sites/preview').length,beforeDelayedNavigation,'Privacy changes invalidate navigation even while the detail is unmounted');
    holdNextRead=true;await navigate(page,previewTarget());
    await expect.poll(()=>!!heldSiteRead).toBe(true);
    await page.getByLabel('Saved site',{exact:true}).selectOption('site-notes');
    await heldSiteRead();heldSiteRead=null;
    await expect(page.getByRole('heading',{name:'Field notes',exact:true})).toBeVisible();
    await page.getByLabel('Saved site',{exact:true}).selectOption('site-studio');
    await expect(page.getByRole('heading',{name:'Northstar studio',exact:true})).toBeVisible();
    assert.equal(requests.filter(r=>r.path==='/api/sites/preview').length,beforeDelayedNavigation,'Returning to a site cannot replay superseded navigation');
    await navigate(page,previewTarget());
    await expect(page.getByTitle('Frozen site build preview')).toBeVisible();
    wrapperAlive=false;
    await expect(page.getByTitle('Frozen site build preview')).toHaveCount(0);
    await expect(page.getByText('The preview closed because its site or privacy context changed. Open it again when ready.',{exact:true})).toBeVisible();
    wrapperAlive=true;
    const revocationsBeforePrivacy=revokedPreviews;
    await page.getByRole('button',{name:'Preview this build',exact:true}).click();
    await expect(page.getByTitle('Frozen site build preview')).toBeVisible();
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:true}})));
    await expect(page.getByTitle('Frozen site build preview')).toHaveCount(0);
    await expect.poll(()=>revokedPreviews).toBeGreaterThan(revocationsBeforePrivacy);
    await page.evaluate(()=>window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on:false}})));
    await expect(page.getByTitle('Frozen site build preview')).toHaveCount(0);
    holdSiteAction='preview';await page.getByRole('button',{name:'Preview this build',exact:true}).click();
    await expect.poll(()=>!!heldSiteAction).toBe(true);
    await page.evaluate(()=>{for(const on of [true,false])window.dispatchEvent(new CustomEvent('friday:off-record',{detail:{on}}));});
    await heldSiteAction();heldSiteAction=null;
    await expect(page.getByTitle('Frozen site build preview')).toHaveCount(0);
    await expect(page.getByRole('button',{name:'Preview this build',exact:true})).toBeEnabled();
    holdSiteAction='preview';await page.getByRole('button',{name:'Preview this build',exact:true}).click();
    await expect.poll(()=>!!heldSiteAction).toBe(true);
    await page.getByLabel('Saved build').selectOption('build-failed');
    await heldSiteAction();heldSiteAction=null;
    await expect(page.getByRole('button',{name:'Review new build',exact:true})).toBeEnabled();
    await expect(page.getByTitle('Frozen site build preview')).toHaveCount(0);
    await expect(page.getByLabel('Saved build')).toHaveValue('build-failed');
    await page.getByLabel('Saved build').selectOption('build-ready');
    await page.getByLabel('Website hostname').fill('draft.example.com');
    holdSiteAction='hosting_requirements';await page.getByRole('button',{name:'Read hosting requirements',exact:true}).click();
    await expect.poll(()=>!!heldSiteAction).toBe(true);
    sites[0].revision++;
    await page.getByTestId('site-detail').getByRole('button',{name:'Check domain',exact:true}).click();
    await expect(page.getByTestId('site-detail')).toHaveAttribute('data-site-revision',String(sites[0].revision));
    await heldSiteAction();heldSiteAction=null;
    await expect(page.getByRole('button',{name:'Review new build',exact:true})).toBeEnabled();
    await expect(page.getByRole('heading',{name:'Required domain setup',exact:true})).toHaveCount(0);
    await expect(page.getByLabel('Website hostname')).toHaveValue('draft.example.com');
    await page.getByLabel('Website hostname').fill('www.example.com');
    await capture(page,'sites-stale-preview-and-requirements',page.getByTestId('site-detail'));
    await page.getByRole('button',{name:'Review publication',exact:true}).click();
    assert.equal(requests.find(r=>r.body.action==='prepare_publish').body.args.build_id,'build-ready');
    await expect(page.locator('[data-site-operation="op-prepare_publish"]')).toContainText('Waiting for your review');
    await page.getByRole('button',{name:'Edit setup',exact:true}).click();
    await expect(page.getByLabel('Repository codebase').locator('option[value="cb-notes"]')).toHaveCount(0);
    await page.getByLabel('Build command',{exact:true}).fill('npm run export');
    await navigate(page,{site_id:'site-notes'});
    await expect(page.getByText('Finish or cancel the site setup before opening another selection.')).toBeVisible();
    assert.equal((await tabState(page)).site_id,null);
    await framesForModes(page,'sites-editor',page.getByTestId('site-editor'));
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Northstar studio',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Refresh status',exact:true}).click();
    await expect.poll(async()=>(await tabState(page)).site_id).toBe('site-studio');
    await page.getByRole('button',{name:'Choose or update domain',exact:true}).click();
    const picker=page.getByTestId('domain-inventory');
    await expect(picker.getByRole('button',{name:'Use this domain',exact:true}).first()).toBeVisible();
    await picker.getByRole('button',{name:'Add Name.com account',exact:true}).click();
    await picker.getByLabel('Account name',{exact:true}).fill('Unfinished account');
    const previewsBeforeBlockedNavigation=requests.filter(r=>r.path==='/api/sites/preview').length;
    await navigate(page,previewTarget());
    await expect(page.getByText('Finish or cancel the open form before opening another selection.',{exact:true})).toBeVisible();
    assert.equal(requests.filter(r=>r.path==='/api/sites/preview').length,previewsBeforeBlockedNavigation);
    const savesBeforeSwitch=requests.filter(r=>r.body.action==='save').length;
    await page.getByLabel('Saved site',{exact:true}).selectOption('site-notes');
    await expect(picker.getByRole('button',{name:'Use this domain',exact:true})).toHaveCount(0);
    await expect(picker.getByLabel('Account name',{exact:true})).toHaveValue('Unfinished account');
    await navigate(page,{site_id:'site-studio'});
    await expect(page.getByText('Finish or cancel the open form before opening another selection.',{exact:true})).toBeVisible();
    await picker.getByRole('button',{name:'Cancel',exact:true}).click();
    assert.equal(requests.filter(r=>r.path==='/api/sites/preview').length,previewsBeforeBlockedNavigation,'Blocked preview navigation never replays when a form closes');
    await expect(page.getByLabel('Saved site',{exact:true})).toHaveValue('site-notes');
    await page.getByLabel('Saved site',{exact:true}).selectOption('site-studio');
    await expect(picker.getByRole('button',{name:'Use this domain',exact:true})).toHaveCount(0);
    assert.equal(requests.filter(r=>r.body.action==='save').length,savesBeforeSwitch,'Changing site never invokes an older domain binding callback');
    await capture(page,'sites-expired-domain-picker',picker);
    await page.getByRole('button',{name:'Close domains',exact:true}).click();
    await navigate(page,{account_id:'account-b',domain:'example.com'});
    await expect.poll(async()=>(await tabState(page)).domain_ref).toBe('account-b/example.com');
    await expect(page.getByTestId('domain-inventory').getByTestId('domain-detail')).toContainText('Personal collection');
    await framesForModes(page,'sites-domains',page.getByTestId('domain-inventory'));
    await page.getByRole('button',{name:'Add Name.com account',exact:true}).click();
    await expect(page.getByLabel('Name.com API token')).toHaveAttribute('type','password');
    await framesForModes(page,'sites-account',page.getByTestId('domain-account-form'));
    await page.getByRole('button',{name:'Cancel',exact:true}).click();
    await page.getByRole('button',{name:'Close domains',exact:true}).click();
    await navigate(page,{account_id:'account-a',domain:'example.com'});
    const domain=page.getByTestId('domain-inventory').getByTestId('domain-detail');
    await expect.poll(async()=>(await tabState(page)).domain_ref).toBe('account-a/example.com');
    await domain.getByText('Renewal and auto-renew',{exact:true}).click();
    await domain.getByRole('button',{name:'Review renewal price',exact:true}).click();
    await expect(domain.getByText('Tax and final total are confirmed at checkout.',{exact:false})).toBeVisible();
    await expect(domain.getByRole('link',{name:'Review renewal at Name.com'})).toHaveAttribute('href','https://www.name.com/account/renewalcenter');
    await expect(domain.locator('[data-site-operation] [data-approval-id]')).toHaveCount(0);
    await framesForModes(page,'sites-renewal',domain.locator('[data-site-operation]'));
    await domain.getByRole('button',{name:'Check after checkout'}).click();
    await expect(domain.getByText('Expiration extended; payment is not independently verified.')).toBeVisible();
    await domain.getByRole('button',{name:'Read DNS records',exact:true}).click();
    await domain.getByRole('button',{name:'Edit record 17',exact:true}).click();
    await domain.getByLabel('Value',{exact:true}).fill('sample.github.io');
    await navigate(page,{site_id:'site-notes'});
    await expect(page.getByText('Finish or cancel the open form before opening another selection.',{exact:true})).toBeVisible();
    await expect(domain.getByLabel('Value',{exact:true})).toHaveValue('sample.github.io');
    await expect(page.getByLabel('Saved site',{exact:true})).toHaveValue('site-studio');
    await domain.getByRole('button',{name:'Prepare exact change',exact:true}).click();
    const dns=requests.find(r=>r.body.action==='prepare_dns');assert.equal(dns.body.args.record_id,17);assert.equal(dns.body.args.record.type,'CNAME');assert.equal(dns.body.args.record.answer,'sample.github.io');
    await expect(domain.locator('[data-site-operation]')).toContainText('old.example.net');
    await expect(domain.locator('[data-site-operation]')).toContainText('sample.github.io');
    await expect(page.getByLabel('Saved site',{exact:true})).toHaveValue('site-studio');
    await framesForModes(page,'sites-dns-review',domain.locator('[data-site-operation]'));
    await page.getByRole('button',{name:'Close domains',exact:true}).click();
    const attachedDomain=page.getByTestId('site-detail').getByTestId('domain-detail');
    await attachedDomain.getByRole('button',{name:'Read DNS records',exact:true}).click();
    await attachedDomain.getByRole('button',{name:'Edit record 17',exact:true}).click();
    await attachedDomain.getByLabel('Value',{exact:true}).fill('unfinished.example.net');
    holdDomainAction='records';await attachedDomain.getByRole('button',{name:'Read DNS records',exact:true}).click();
    await expect.poll(()=>!!heldDomainAction).toBe(true);
    accountA.revision++;
    await page.getByRole('button',{name:'Refresh status',exact:true}).click();
    await expect(attachedDomain.getByText('The account or site changed. Your draft is kept; cancel it and read current DNS records before preparing another change.',{exact:true})).toBeVisible();
    await heldDomainAction();heldDomainAction=null;
    await expect(attachedDomain.getByLabel('Value',{exact:true})).toHaveValue('unfinished.example.net');
    await expect(attachedDomain.getByRole('button',{name:'Prepare exact change',exact:true})).toBeDisabled();
    await expect(attachedDomain.getByRole('button',{name:'Edit record 17',exact:true})).toHaveCount(0);
    await expect(attachedDomain.getByText('DNS authority not checked',{exact:true})).toBeVisible();
    await capture(page,'sites-stale-account-keeps-draft',attachedDomain);
    await attachedDomain.getByRole('button',{name:'Cancel record edit',exact:true}).click();
    await page.getByText('Hosting connections',{exact:true}).click();
    await expect(page.locator('.site-record').filter({hasText:'Earlier Cloudflare'}).getByRole('button',{name:'Reconnect',exact:true})).toHaveCount(0);
    await page.getByRole('button',{name:'Connect hosting',exact:true}).click();
    await expect(page.getByTestId('site-hosting-form').getByLabel('Host',{exact:true}).locator('option')).toHaveCount(1);
    await expect(page.getByTestId('site-hosting-form').getByLabel('Host',{exact:true})).toHaveValue('github_pages');
    await expect(page.getByTestId('site-hosting-form').getByText('Cloudflare Pages is not available for saved Sites yet.',{exact:true})).toBeVisible();
    await page.getByTestId('site-hosting-form').getByRole('button',{name:'Cancel',exact:true}).click();
    await navigate(page,{site_id:'site-missing'});
    await expect(page.getByRole('heading',{name:'This saved site is unavailable'})).toBeVisible();
    assert.equal((await tabState(page)).site_id,null);
    await capture(page,'sites-unavailable',page.getByRole('heading',{name:'This saved site is unavailable'}));
    await navigate(page,{site_id:'site-studio'});await expect.poll(async()=>(await tabState(page)).site_id).toBe('site-studio');
    holdNextRead=true;await page.getByRole('button',{name:'Refresh status',exact:true}).click();await expect.poll(()=>!!heldSiteRead).toBe(true);
    await page.getByRole('button',{name:'Add site',exact:true}).click();await page.getByLabel('Site name',{exact:true}).fill('New field journal');await page.getByLabel('Repository codebase').selectOption('cb-notes');await page.getByRole('button',{name:'Save site',exact:true}).click();
    await expect(page.getByRole('heading',{name:'New field journal',exact:true})).toBeVisible();await heldSiteRead();heldSiteRead=null;
    await expect.poll(async()=>(await tabState(page)).site_id).toBe('site-new');
    await page.getByRole('button',{name:'Refresh status',exact:true}).click();await expect(page.getByRole('heading',{name:'New field journal',exact:true})).toBeVisible();
    failCatalog=true;await page.getByRole('button',{name:'Refresh status',exact:true}).click();await expect(page.getByText('Saved sites cannot be read right now.')).toBeVisible();await expect.poll(async()=>(await tabState(page)).site_id).toBe(null);
    assert.deepEqual(errors,[],'No browser runtime errors');
    assert.equal(requests.some(r=>/renew$|charge|apply$/.test(r.body.action||'')),false,'No paid execution or raw approval bypass');
    if(output)fs.writeFileSync(path.join(output,'sites-proof.json'),JSON.stringify({frames,errors,requestCount:requests.length,browser:browserSelection},null,2));
    console.log('PASS '+frames.length+' rendered frames; exact account navigation, stale reads, reviews and recovery');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
