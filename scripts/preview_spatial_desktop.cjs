/* Serves the actual desktop with synthetic, in-memory records for design review.
 * It never imports the application, proxies a request, or starts a model.
 * Only index.html, static/, assets/ and the synthetic story reader can be served.
 * Bind loopback only.
 */
'use strict';
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const port = Number(process.env.FRIDAY_DESIGN_PORT || 3188);
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
const sampleCrewAgents = [
 {id:'sample-crew-guide',name:'Sample studio guide',role:'Compare the fictional launch directions and prepare questions for a human review.',persona:'Keep observations clear and distinguish sample records from live work.',provider:'preview',model:'sample-reasoning',voice:{provider:'preview',model:'sample-voice',voice_id:'sample-guide'},caption:{label:'Studio guide'},project_ids:['design-launch'],grants:[],skills:[],allowed_tools:[],memory:{notes:'Fictional profile for layout review. No provider, model or private memory is connected.',read:false,write:false},max_steps:12,time_budget_s:180,status:'active',revision:1},
 {id:'sample-crew-editor',name:'Sample field editor',role:'Shape field observations into useful questions.',persona:'Use direct language and keep the evidence close to the conclusion.',provider:'preview',model:'sample-reasoning',voice:{provider:'preview',model:'sample-voice',voice_id:'sample-editor'},caption:{label:'Field editor'},project_ids:['design-notes'],grants:[],skills:[],allowed_tools:[],memory:{notes:'This suspended profile is synthetic and cannot perform work.',read:false,write:false},max_steps:8,time_budget_s:120,status:'suspended',revision:1}
];
const sampleCrewTasks = [{task_id:'sample-crew-review',agent_id:'sample-crew-guide',speaker_name:'Sample studio guide',conversation_id:'design-main',project_id:'design-launch',status:'completed_unverified'}];
const sampleCrewCapabilities = {
 providers:[{id:'preview',label:'Sample provider (not connected)',available:false,models:[{id:'sample-reasoning',label:'Sample reasoning model'}]}],
 voice_providers:[{id:'preview',label:'Sample voice (not connected)',available:false,reason:'No voice provider is connected in this preview.',models:[{id:'sample-voice',label:'Sample voice model'}],voices:[{id:'sample-guide',label:'Sample guide'},{id:'sample-editor',label:'Sample editor'}]}],
 projects:projects.map(({id,name})=>({id,name})),skills:[],supported_tools:[],limits:{role:1000,persona:8000,memory_notes:8000,max_steps:200,time_budget_s:3600},offline:{available:false,message:'Offline Crew is not operational in this synthetic preview.'}
};
// Fixture shapes follow the native workspace consumers in ui_parts/app.html.
// These records are fictional, stay in this process and never imply a connection.
const isoDay = (offset=0) => { const d=new Date();d.setHours(12,0,0,0);d.setDate(d.getDate()+offset);return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-'); };
const today=isoDay();
const sampleCalendarDay = date => ({date,google_connected:false,annotation:'Sample schedule · Space for focused work, conversations, and a little breathing room.',events:[
 {id:'sample-focus-'+date,title:'Northstar · Opening direction',start_time:date+'T09:00:00',end_time:date+'T10:30:00',type:'normal',all_day:false,location:'Sample studio'},
 {id:'sample-review-'+date,title:'Review the launch story',start_time:date+'T11:00:00',end_time:date+'T11:45:00',type:'normal',all_day:false,attendees:['Sample collaborator']},
 {id:'sample-explore-'+date,title:'Explore the next opportunity',start_time:date+'T14:00:00',end_time:date+'T15:00:00',type:'career',all_day:false},
 {id:'sample-outside-'+date,title:'An hour outside',start_time:date+'T16:00:00',end_time:date+'T17:00:00',type:'normal',all_day:false}
],gaps:[{start_time:date+'T12:00:00',end_time:date+'T14:00:00',label:'Open time',minutes:120}]});
const weekStart=new Date(today+'T12:00:00');weekStart.setDate(weekStart.getDate()-((weekStart.getDay()+6)%7));
const calendarWeek=Array.from({length:7},(_,i)=>{const d=new Date(weekStart);d.setDate(d.getDate()+i);const date=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');return {date,weekday:d.toLocaleDateString('en-US',{weekday:'short'}),day:d.getDate(),is_today:date===today,has_career:true,count:sampleCalendarDay(date).events.length,density:'heavy'};});
const countdowns=[
 {id:'sample-day-out',kind:'personal',label:'A day by the lake',emoji:'☀️',why:'Room to wander',date:isoDay(3),days:3},
 {id:'sample-table',kind:'personal',label:'Dinner around one table',emoji:'🍽️',why:'Bring a favorite recipe',date:isoDay(6),days:6},
 {id:'sample-weekend',kind:'personal',label:'A long weekend away',emoji:'🌿',why:'A change of scenery',date:isoDay(14),days:14}
];
const samplePeople=[
 {name:'Alex Example',aliases:['Northstar · Creative partner'],overall:0.84,evidence_count:12},
 {name:'Morgan Sample',aliases:['Field notes · Editor'],overall:0.79,evidence_count:8},
 {name:'Casey Demo',aliases:['Northstar · Product collaborator'],overall:0.88,evidence_count:16},
 {name:'Jordan Example',aliases:['Community · Workshop host'],overall:0.73,evidence_count:6},
 {name:'Taylor Sample',aliases:['Field notes · Research partner'],overall:0.81,evidence_count:10},
 {name:'Riley Demo',aliases:['Studio · Design collaborator'],overall:0.86,evidence_count:14}
];
const sampleSites=[
 {name:'Northstar',description:'A clear home for a fictional creative studio. The opening, selected work, and the story behind it.',category:'company',repo:'sample/northstar',repo_path:'Sample materials/northstar',status:{deploy_status:'green',cloned:true,branch:'main',last_commit_rel:'2 hours ago',last_commit_msg:'Refine the opening and project stories',uncommitted:0,ahead:0,behind:0,has_replit:false}},
 {name:'Field Notes',description:'An independent sample journal of observations, small experiments, and useful ideas.',category:'personal',repo:'sample/field-notes',repo_path:'Sample materials/field-notes',status:{deploy_status:'yellow',cloned:true,branch:'editorial',last_commit_rel:'yesterday',last_commit_msg:'Introduce the first editorial collection',uncommitted:3,ahead:1,behind:0,has_replit:false}},
 {name:'The Open Workshop',description:'A fictional gathering place for making things together. Sessions, resources, and room to participate.',category:'client',repo:'sample/open-workshop',repo_path:'Sample materials/open-workshop',status:{deploy_status:'green',cloned:true,branch:'main',last_commit_rel:'3 days ago',last_commit_msg:'Polish session details and registration copy',uncommitted:0,ahead:0,behind:0,has_replit:false}},
 {name:'Quiet Objects',description:'A sample collection of simple, considered products with space to see their material and detail.',category:'personal',repo:'sample/quiet-objects',repo_path:'Sample materials/quiet-objects',status:{deploy_status:'yellow',cloned:true,branch:'collection',last_commit_rel:'4 days ago',last_commit_msg:'Explore the first collection',uncommitted:2,ahead:0,behind:0,has_replit:false}}
];
const careerEntries=[
 {company:'Northstar Studio · Sample',role:'Design lead',score:'4.6',status:'Interviewing'},
 {company:'Open Workshop · Sample',role:'Product strategist',score:'4.3',status:'Applied'},
 {company:'Field Notes · Sample',role:'Editorial director',score:'4.1',status:'Applied'},
 {company:'Quiet Objects · Sample',role:'Creative technologist',score:'4.5',status:'Offer'},
 {company:'Common Ground · Sample',role:'Research lead',score:'3.9',status:'Evaluated'}
];
const careerReports={'Northstar — design lead.md':'# Northstar · Sample evaluation\n\nA fictional opportunity for reviewing the Career workspace.\n\n## Strengths\nClear ownership of the product story, close collaboration with a small team, and space to make the design system more coherent.\n\n## Conversation to prepare\nWalk through one project from the first uncertainty to a considered release. Explain the decisions, the evidence, and what changed.', 'Open Workshop — product strategy.md':'# Open Workshop · Sample evaluation\n\nThis fictional role connects product direction with hands-on prototyping.\n\n## Next conversation\nAsk how the team decides what to build, how it evaluates experiments, and what success would look like in the first three months.'};
const sampleNews=[
 ['room-for-ideas','The most useful tools leave room for the next idea','A fictional design dispatch about quieter interfaces, useful context, and the pleasure of staying in flow.','Design','tech','Sample Design Journal'],
 ['field-observations','Small observations, better questions','A sample field notebook follows how a team turns everyday friction into a clearer research question.','Science','science','Sample Field Notes'],
 ['shared-workshop','A workshop designed around participation','A fictional community story explores a space where unfinished work is welcome and learning happens in the open.','Local','local','Sample City Review'],
 ['material-detail','Why material still matters on a screen','An illustrative essay on light, depth, and surface: when visual detail makes something easier to understand.','Design','tech','Sample Design Journal'],
 ['slower-editorial','The case for a more deliberate reading rhythm','A fictional editorial on keeping the context around a story, with room for competing views and follow-up questions.','Media','media','Sample Editorial'],
 ['useful-prototype','A prototype can answer one good question','A sample studio note traces a simple experiment from an uncertain idea to a decision the whole team can review.','Technology','tech','Sample Studio Dispatch']
].map((a,i)=>({id:'sample-news-'+i,url:'/preview/story/'+a[0],title:a[1],snippet:a[2],category:a[3],color:a[4],source:a[5],published_at:new Date((now-i*3600)*1000).toISOString(),relevance_score:0.9-i*0.03,reading_time:3+i%3,new_since_last:i<2,editorial_note:i===0?'Sample editorial context: the useful question is what this changes for the work in front of you.':undefined}));
const sampleEdition={id:'sample-morning',date:today,slot:'morning',headline:'A little perspective for the day',generated_central:today+' · Sample edition',stats:{total_considered:sampleNews.length,sources:5},day_in_context:'Your fictional studio review begins at 11. There is an open stretch after lunch for turning the conversation into something concrete.',lead:sampleNews[0],sections:[{title:'Ideas worth exploring',context:'Design, research, and the places they meet',color:'science',articles:sampleNews.slice(1,4)},{title:'A wider view',context:'Context beyond the immediate brief',color:'media',articles:sampleNews.slice(4)}],contrarian_corner:{title:'When less is simply less',note:'This sample counterpoint asks when a quieter interface hides the very context people need. Clarity still requires useful information.',source:'Sample Editorial',color:'media'},competitor_watch:[]};
const wikiGroups={
 studio:[['Launch brief','A clear starting point for the Northstar launch.','Audience notes','Visual direction'],['Audience notes','People arrive with a goal. Make the next useful action easy to find.','Launch brief','Research questions'],['Visual direction','Generous space, confident typography, and a deliberate sense of depth.','Material studies','Launch brief'],['Working principles','Start with the work. Keep important context nearby. Give each tool a clear purpose.','Visual direction','Prototype review']],
 research:[['Research questions','What helps someone find their place and keep their train of thought?','Audience notes','Field observations'],['Field observations','Small interruptions accumulate. Write them down before proposing a larger solution.','Research questions','Prototype review'],['Prototype review','Compare a concrete interaction against the question it was built to answer.','Working principles','Field observations'],['Reading list','A fictional collection of references for the sample studio project.','Research questions','Material studies']],
 craft:[['Material studies','Compare glass, light, and depth while keeping text stable and readable.','Visual direction','Motion notes'],['Motion notes','Use movement to explain a change in state. Let stillness do the rest.','Material studies','Accessible defaults'],['Accessible defaults','Keep controls predictable. Support keyboards, quiet motion, and compact screens.','Motion notes','Working principles'],['Release checklist','Review the work at a narrow width, with a keyboard, and with reduced motion.','Accessible defaults','Prototype review']]
};
const wikiPages={},wikiNodes=[];
Object.entries(wikiGroups).forEach(([section,rows],community)=>rows.forEach(([title,summary,...links])=>{const file=title.toLowerCase().replaceAll(' ','-')+'.md',p=section+'/'+file;wikiPages[p]='# '+title+'\n\n'+summary+'\n\nThis is a fictional page for the native workspace preview.\n\n## Connected ideas\n'+links.map(l=>'- [['+l+']]').join('\n');wikiNodes.push({id:'page:'+p.slice(0,-3),title,type:'page',section,community,degree:0,description:summary,provenance:{wiki_pages:[p]},created_at:new Date((now-86400*community)*1000).toISOString()});}));
const wikiRelationships=[];
Object.entries(wikiGroups).forEach(([section,rows])=>rows.forEach(([title,,...links])=>{const from=wikiNodes.find(n=>n.section===section&&n.title===title);links.forEach(link=>{const to=wikiNodes.find(n=>n.title===link);if(to&&!wikiRelationships.some(r=>r.source===from.id&&r.target===to.id))wikiRelationships.push({source:from.id,target:to.id,type:'links_to',weight:1});});}));
wikiNodes.forEach(n=>n.degree=wikiRelationships.filter(r=>r.source===n.id||r.target===n.id).length);
const wikiGraph={entities:wikiNodes,relationships:wikiRelationships,communities:Object.keys(wikiGroups).map((title,community)=>({community,title,size:wikiGroups[title].length}))};
const sampleWorkflows=[
 {id:'sample-launch-review',name:'From a brief to a clear direction',description:'A fictional studio workflow that connects the brief, a useful comparison, and a human decision.',enabled:false,when:null,when_text:'When you choose',running:false,asks_first:['share work externally'],steps:[{name:'Read the brief',prompt:'Gather the goal, audience, and decisions that need to be made.'},{name:'Explore the directions',prompt:'Compare three approaches against the same clear criteria.'},{name:'Prepare the review',prompt:'Bring the evidence and open questions into one readable page.'},{name:'Ask for a decision',prompt:'Stop for a human review before sharing or publishing anything.'}],last_run:{at:now-7200,status:'finished',summary:'Sample result: three directions prepared for review. Nothing was sent or published.',steps:[{status:'completed'},{status:'completed'},{status:'completed'},{status:'completed'}]}},
 {id:'sample-field-notes',name:'Turn field notes into useful questions',description:'Keep the observations, the interpretation, and the next experiment connected.',enabled:false,when:null,when_text:'When you choose',running:false,asks_first:[],steps:[{name:'Collect the observations',prompt:'Read the sample notes and group related moments.'},{name:'Find the uncertainty',prompt:'Separate what was observed from what is still an assumption.'},{name:'Frame an experiment',prompt:'Propose one small way to learn what matters next.'}],last_run:{at:now-86400,status:'finished',summary:'Sample result: one research question and a short prototype brief.',steps:[{status:'completed'},{status:'completed'},{status:'completed'}]}}
];
const readOnlyFixtures={
 '/api/crew/agents':{agents:sampleCrewAgents},'/api/crew/capabilities':sampleCrewCapabilities,'/api/crew/tasks':{tasks:sampleCrewTasks},
 '/api/browser/workspaces':{workspaces:[]},'/api/browser/workspaces/permission':{permission:{enabled:false,generation:1}},
 '/api/system':{disk:[],processes:[]},
 '/api/context/stats':{enabled:false,off_record:true,retention_days:0,days:0,first_date:null,last_date:null,total_entries:0,total_bytes:0,avg_entries_per_day:0,log_dir:''},
 '/api/context/compression-stats':{compression:null},'/api/self-improvement/latest':{report:null},
 '/api/tasks/retention':{ok:true,retention_days:0,capture_reasoning:false,encrypt_at_rest:false},
 '/api/actions/receipts':{receipts:[]},
 '/api/governance/receipts':{since:now-86400,until:now,actions:[],counts:{allow:0,confirm:0,card:0,deny:0},verified:0,not_verified:0,unreadable_lines:0,key_available:false,tasks:[],truncated:false},
 '/api/calendar/today':sampleCalendarDay(today),'/api/calendar/tomorrow':{...sampleCalendarDay(isoDay(1)),needs_prep:[]},'/api/calendar/week':{days:calendarWeek},'/api/media/calendar':{cards:[]},
 '/api/meetings':{meetings:[]},'/api/meetings/status':{recording:false,finishing:[],available:false},'/api/countdowns':{countdowns},
 '/api/contacts':{contacts:samplePeople},'/api/contacts/google-write':{accounts:[]},'/api/relationships/config':{config:{sync_enabled:false,cold_after_days:14},last_sync:null,interactions:0},'/api/relationships/follow-ups':{follow_ups:[]},
 '/api/finance/portfolio':{accounts:['Sample account · Fictional'],positions:[{ticker:'DEMO-A',shares:24,cost_basis:1800},{ticker:'DEMO-B',shares:40,cost_basis:2400},{ticker:'DEMO-C',shares:18,cost_basis:1620}]},
 '/api/finance/perks':{perks:[{name:'Sample travel credit',value:'$200',used:false,expires:isoDay(35),notes:'Fictional benefit for this workspace preview.'},{name:'Sample studio membership',value:'$120',used:false,expires:isoDay(120),notes:'Fictional benefit; no account is connected.'},{name:'Sample learning credit',value:'$75',used:true,expires:isoDay(90)}]},
 '/api/health/medications':{medications:[{name:'Sample medication record',notes:'Fictional record for layout review. No medication, dose, or treatment is prescribed.'}]},
 '/api/health/appointments':{appointments:[{provider:'Sample care team',type:'Annual visit',frequency:'Sample annual reminder',next:isoDay(18)},{provider:'Sample dental team',type:'Routine visit',frequency:'Sample six-month reminder',next:isoDay(42)}]},
 '/api/health/insurance':{insurance:{provider:'Sample coverage',plan:'Fictional demonstration plan',policy_number:'SAMPLE-001',group_number:'DEMO',notes:'No insurer or personal account is connected.'}},
 '/api/health/vehicles':{vehicles:[{name:'Sample touring vehicle',miles:18400,notes:'Fictional maintenance record.',mechanic:'Sample workshop',service_history:[{date:isoDay(-40),description:'Sample routine service'}]}],mechanics:[{name:'Sample workshop',specialty:'Fictional service partner'}]},
 '/api/career-ops/status':{ready:true,configured:false,path:'',summary:'Fictional career records for design review.',items:[]},'/api/career-ops/tracker':{entries:careerEntries},'/api/career-ops/reports':{reports:Object.entries(careerReports).map(([name,content])=>({name,size:Buffer.byteLength(content)}))},
 '/api/career-ops/pipeline':{content:'# Sample career pipeline\n\n## In conversation\n- Northstar Studio — design lead\n- Quiet Objects — creative technologist\n\n## Applications prepared\n- Open Workshop — product strategist\n- Field Notes — editorial director\n\nAll organizations and opportunities in this preview are fictional.'},'/api/jobs':{content:'# Sample opportunities\n\nA fictional collection for reviewing the Career workspace.\n\n- Design lead at Northstar Studio\n- Product strategist at Open Workshop\n- Research lead at Common Ground'},'/api/pipeline/jobs':{jobs:careerEntries.map((e,i)=>({job_id:'sample-job-'+i,title:e.role,company:e.company,location:'Sample studio',remote:true,relevance_score:Number(e.score)/5}))},
 '/api/futurespeak/projects':{projects:sampleSites,by_deploy:{green:2,yellow:2,red:0,remote:0}},'/api/futurespeak/scan':{discovered:[],total:sampleSites.length},
 '/api/futurespeak/pipeline':{total:2,total_value:24000,weighted_value:15600,opportunities:[{id:'sample-opportunity',name:'Sample studio engagement',status:'proposal',value_usd:16000,probability:0.7,next_action:'Review the fictional discovery outline.',notes:'Sample data only.',tags:['studio','strategy'],contacts:[]},{id:'sample-workshop',name:'Sample workshop series',status:'discovery',value_usd:8000,probability:0.55,next_action:'Shape the fictional first session.',tags:['workshop'],contacts:[]}]},
 '/api/futurespeak/revenue':{months:[{month:today.slice(0,7),projected:12000,actual:9000}],quarters:[],last_actual_month:9000,ytd_actual:54000,ytd_projected:66000,cash_on_hand:36000,monthly_burn:6000,net_monthly:3000,runway_months:6},'/api/futurespeak/legal':{items:[]},'/api/futurespeak/assets':{assets:[]},
 '/api/news/front-page/latest':{edition:sampleEdition,editions:[{id:sampleEdition.id,date:today,slot:'morning'}],routines:[]},'/api/news/front-page/sample-morning':{edition:sampleEdition},'/api/news/front-page/weekly/latest':{digest:null,digests:[]},'/api/news/editorial/latest':{editorial:null,editorials:[]},
 '/api/news/archive/stats':{total:sampleNews.length,sources:5},'/api/news/read-later':{items:[sampleNews[3]]},'/api/news/annotations':{annotations:[],annotated_ids:[]},'/api/news/clusters':{clusters:[]},'/api/sources/preferences':{banned:[],boosted:[]},'/api/briefing/status':{connectors:[]},'/api/briefings':{briefings:[]},
 '/api/briefing/preferences':{preferences:{section_order:['headlines','technology','science'],sections_enabled:{headlines:true,technology:true,science:true},categories_enabled:{Design:true,Science:true,Local:true,Media:true,Technology:true}},categories:['Design','Science','Local','Media','Technology'].map(name=>({name}))},
 '/api/wiki/structure':{structure:Object.fromEntries(Object.keys(wikiGroups).map(section=>[section,Object.keys(wikiPages).filter(p=>p.startsWith(section+'/')).map(p=>p.split('/')[1])])),recent:wikiNodes.slice(0,4).map(n=>({path:n.provenance.wiki_pages[0],section:n.section,filename:n.provenance.wiki_pages[0].split('/')[1],modified_iso:new Date(now*1000).toISOString()}))},'/api/wiki/pending':{pending:[]},
 '/api/knowledge-graph/graph':wikiGraph,'/api/workflows/overview':{workflows:sampleWorkflows,routines:[],pending_approvals:0}
};
// These stores implement only the preview's fictional UI contract. They are not
// a backend test double and never load or save the application's personal data.
const copy = value => JSON.parse(JSON.stringify(value));
const timestamp = () => Math.floor(Date.now()/1000);
const homeSaved = [
 {id:'sample-home-note',title:'Make room for the next idea',body:'Bring the brief, the references, and one useful question into the conversation. This is your editable sample card.',priority:55,actions:[{label:'Open Library',workspace:'library'}],created_at:now-3600,updated_at:now-1800},
 {id:'sample-home-later',title:'An idea for later',body:'A small collection of observations could become the next Field Notes edition. This sample card is set aside for one hour.',priority:40,actions:[{label:'Open projects',view:'projects'}],created_at:now-7200,updated_at:now-3600}
];
let homeRevision=1, homeOrder=[];
const homePreferences=new Map([['sample-home-later',{snoozed_until:now+3600}]]), homeTrackers=new Map(), homePins=new Map();
function homeSources() {
 const checked=timestamp(), day=isoDay(), source=(kind,label,detail,records,status='local_only')=>({kind,label,status,detail,checked_at:checked,updated_at:now,records,options:records.map(record=>({id:record.id,label:record.title,available:record.available!==false,status:record.available===false?'unimplemented':'local_only'}))});
 return [
  source('task','Local tasks','Fictional task records in this preview. No task or model runs.',tasks.map(task=>({id:task.task_id,title:task.name,body:task.description+'\n'+(task.now||task.status),view:'activity'}))),
  source('calendar','Calendar','Sample local schedule. No calendar account is connected.',sampleCalendarDay(day).events.map(event=>({id:event.id,title:event.title,body:'Sample appointment · '+event.start_time.slice(11,16)+'–'+event.end_time.slice(11,16),workspace:'calendar'})),'cached'),
  source('project','Projects','Fictional projects saved only in this preview process.',projects.map(project=>({id:project.id,title:project.name,body:project.instructions,view:'projects'}))),
  source('routine','Routines','Fictional routine snapshots. Following a card does not run the routine.',sampleWorkflows.map(workflow=>({id:workflow.id,title:workflow.name,body:workflow.description,workspace:'workflows'})).concat([{id:'sample-unavailable-routine',title:'A routine that is not ready',body:'This sample has no runnable handler.',workspace:'workflows',available:false}])) ,
  source('schedule','Schedules','No schedules are configured in the design preview.',[],'missing'),
  source('activity','Working day','Fictional local activity for layout review.',[{id:day,title:'A little perspective for '+new Date(day+'T12:00:00').toLocaleDateString(undefined,{weekday:'long'}),body:'One review to return to, a clear stretch for focused work, and a few ideas worth keeping nearby.',view:'activity'}])
 ];
}
const sourceKey=scope=>scope.kind+':'+scope.id;
function sourceHomeCard(scope,prefix='auto') {
 if(!scope||typeof scope.kind!=='string'||typeof scope.id!=='string')return null;
 const source=homeSources().find(item=>item.kind===scope.kind), record=source?.records.find(item=>item.id===scope.id);
 if(!record)return null;
 const id=prefix+'-'+scope.kind+'-'+scope.id.replace(/[^a-zA-Z0-9_-]/g,'_');
 return {id,title:record.title,body:record.body,priority:scope.kind==='task'?75:scope.kind==='calendar'?65:50,type:scope.kind,origin:prefix==='track'?'tracked':'automatic',created_at:now,updated_at:now,actions:[{label:record.view==='projects'?'Open projects':record.view==='activity'?'Open activity':'Open '+source.label,...(record.view?{view:record.view}:{workspace:record.workspace})}],source:{kind:scope.kind,id:scope.id,label:source.label,status:record.available===false?'unimplemented':source.status,detail:source.detail,checked_at:source.checked_at,updated_at:source.updated_at},expires_at:null,tracking:null};
}
function previewBoard() {
 const at=timestamp(), privateMode=!!settings.off_record&&settings.off_record_stops_storage!==false;
 const scopes=[{kind:'activity',id:isoDay()},{kind:'task',id:tasks[0].task_id},{kind:'calendar',id:sampleCalendarDay(isoDay()).events[1].id}];
 let records=privateMode?[]:scopes.filter(scope=>!Array.from(homeTrackers.values()).some(tracker=>sourceKey(tracker.scope)===sourceKey(scope))).map(scope=>sourceHomeCard(scope)).filter(Boolean);
 if(!privateMode){
  for(const [id,snapshot] of homePins)if(!records.some(card=>card.id===id))records.push(copy(snapshot));
  for(const tracker of homeTrackers.values()){
   const fresh=tracker.enabled?sourceHomeCard(tracker.scope,'track'):null, card=fresh||copy(tracker.snapshot);
   card.tracking={enabled:tracker.enabled,scope:tracker.scope,stopped_at:tracker.stopped_at};
   if(!tracker.enabled)card.source={...card.source,status:'stopped',detail:'Tracking stopped. This is the last sample snapshot; the underlying work is unchanged.'};
   records.push(card);
  }
 }
 records=records.concat(homeSaved.map(card=>({...copy(card),type:'note',origin:'saved',tracking:null,expires_at:null,source:{kind:'saved',id:card.id,label:'Saved sample card',status:'saved',checked_at:at,updated_at:card.updated_at,detail:'Stored only in this preview process.'}})));
 const cards=[],hidden_cards=[];
 for(const original of records){const pref=homePreferences.get(original.id)||{},card={...original,pinned:!!pref.pinned,order:homeOrder.includes(original.id)?homeOrder.indexOf(original.id):null,dismissed:!!pref.dismissed,snoozed_until:pref.snoozed_until||null,expired:false};card.hidden_reason=card.dismissed?'dismissed':card.snoozed_until>at?'snoozed':null;(card.hidden_reason?hidden_cards:cards).push(card);}
 const order=(a,b)=>Number(b.pinned)-Number(a.pinned)||(a.order??10000)-(b.order??10000)||b.priority-a.priority||a.created_at-b.created_at||a.id.localeCompare(b.id);
 cards.sort(order);hidden_cards.sort(order);
 const sources=homeSources().map(({records,...source})=>privateMode?{...source,status:'paused',options:[],detail:'Personal source cards are hidden while off the record.'}:source);
 const retained=new Set([...homePreferences.keys(),...homeOrder,...homeSaved.map(card=>card.id),...homeTrackers.keys(),...homePins.keys()]).size;
 return {status:'ok',revision:homeRevision,generated_at:at,cards,hidden_cards,sources,summary:{visible:cards.length,hidden:hidden_cards.length,saved:homeSaved.length,tracking:Array.from(homeTrackers.values()).filter(item=>item.enabled).length,retained,retained_limit:256,suggestion_capacity_full:retained>=256,private:privateMode,cadence:'Updates from local activity while Home is open.'}};
}
function previewBoardChange(res,body) {
 const error=(message,status=400)=>json(res,{status:'error',message},status), board=previewBoard();
 if(board.summary.private)return error('Off the record is on. Sample Home changes are paused.',403);
 if(!Number.isInteger(body.expected_revision)||body.expected_revision!==homeRevision)return error('Home changed. Refresh the board and review this action again.',409);
 const fields={save:['card'],remove:['id'],pin:['id','pinned'],snooze:['id','until'],dismiss:['id'],restore:['id'],reorder:['ids'],track:['source'],stop_tracking:['id'],reset_suggestions:[]};
 if(!Object.hasOwn(fields,body.op)||Object.keys(body).sort().join('|')!==['op','expected_revision',...fields[body.op]].sort().join('|'))return error('This Home action is not available in the sample preview.');
 const all=board.cards.concat(board.hidden_cards),card=all.find(item=>item.id===body.id), pref={...(homePreferences.get(body.id)||{})};
 if(Object.hasOwn(body,'id')&&!card)return error('That card is no longer available. Refresh Home.',409);
 if(body.op==='save'){
  const incoming=body.card;
  if(!incoming||typeof incoming.id!=='string'||!/^[a-zA-Z0-9_-]{1,64}$/.test(incoming.id)||incoming.id.startsWith('auto-')||incoming.id.startsWith('track-')||typeof incoming.title!=='string'||!incoming.title.trim()||incoming.title.length>120||typeof incoming.body!=='string'||incoming.body.length>2000)return error('Use a short title and plain-text details for this sample card.');
  const previous=homeSaved.find(item=>item.id===incoming.id);
  if(!previous&&homeSaved.length+homeTrackers.size+homePins.size>=24)return error('The sample board is full. Delete a saved card or stopped snapshot first.');
  const actions=Array.isArray(incoming.actions)?incoming.actions:[];
  if(actions.length>3||actions.some(action=>!action||typeof action.label!=='string'||!action.label.trim()||action.label.length>60||!(action.workspace==null?['projects','activity'].includes(action.view):action.view==null&&previewWorkspaces.has(action.workspace))))return error('Choose a native sample workspace or Home destination.');
  const saved={id:incoming.id,title:incoming.title.trim(),body:incoming.body,priority:Number.isInteger(incoming.priority)?incoming.priority:50,actions:copy(actions),created_at:previous?.created_at||timestamp(),updated_at:timestamp()};
  if(previous)homeSaved.splice(homeSaved.indexOf(previous),1,saved);else homeSaved.push(saved);
 }else if(body.op==='track'){
  const scope=body.source, fresh=scope&&sourceHomeCard(scope,'track');
  if(!fresh||fresh.source.status==='unimplemented')return error('That sample source is not available to follow.');
  if(!homeTrackers.has(fresh.id)&&homeSaved.length+homeTrackers.size+homePins.size>=24)return error('The sample board is full. Delete a saved card or stopped snapshot first.');
  const automatic=all.find(item=>item.origin==='automatic'&&sourceKey(item.source)===sourceKey(scope));
  if(automatic&&!homeTrackers.has(fresh.id)){if(homePreferences.has(automatic.id))homePreferences.set(fresh.id,homePreferences.get(automatic.id));homePreferences.delete(automatic.id);homeOrder=homeOrder.map(id=>id===automatic.id?fresh.id:id);homePins.delete(automatic.id);}
  homeTrackers.set(fresh.id,{scope:copy(scope),snapshot:copy(fresh),enabled:true,stopped_at:null});
 }else if(body.op==='stop_tracking'){
  const tracker=homeTrackers.get(body.id);if(!tracker)return error('This sample card is not being tracked.');
  tracker.snapshot=sourceHomeCard(tracker.scope,'track')||tracker.snapshot;tracker.enabled=false;tracker.stopped_at=timestamp();
 }else if(body.op==='remove'){
  if(card.origin==='saved')homeSaved.splice(homeSaved.findIndex(item=>item.id===body.id),1);
  else if(card.tracking&&!card.tracking.enabled)homeTrackers.delete(body.id);
  else return error('Dismiss a source card or stop its tracking before deleting a snapshot.');
  homePreferences.delete(body.id);homePins.delete(body.id);homeOrder=homeOrder.filter(id=>id!==body.id);
 }else if(body.op==='reorder'){
  if(!Array.isArray(body.ids)||body.ids.some(id=>typeof id!=='string')||new Set(body.ids).size!==body.ids.length||body.ids.slice().sort().join('|')!==board.cards.map(item=>item.id).sort().join('|'))return error('Visible sample cards changed. Refresh before reordering.',409);
  homeOrder=body.ids.concat(homeOrder.filter(id=>!body.ids.includes(id)));
 }else if(body.op==='reset_suggestions'){
  for(const [id,value] of homePreferences)if(id.startsWith('auto-')&&!value.pinned)homePreferences.delete(id);
  homeOrder=homeOrder.filter(id=>!id.startsWith('auto-')||homePreferences.get(id)?.pinned);
 }else{
  if(body.op==='pin'){if(typeof body.pinned!=='boolean')return error('Pinned must be true or false.');if(body.pinned&&card.origin==='automatic'&&!homePins.has(card.id)){if(homeSaved.length+homeTrackers.size+homePins.size>=24)return error('The sample board is full.');homePins.set(card.id,copy(card));}if(!body.pinned)homePins.delete(card.id);pref.pinned=body.pinned;}
  if(body.op==='snooze'){if(!Number.isFinite(body.until)||body.until<=timestamp()||body.until>timestamp()+366*86400)return error('Choose a future time within the next year.');pref.snoozed_until=body.until;}
  if(body.op==='dismiss')pref.dismissed=true;
  if(body.op==='restore'){pref.dismissed=false;pref.snoozed_until=null;}
  homePreferences.set(body.id,pref);
 }
 homeRevision++;return json(res,previewBoard());
}
const previewCatalog=fs.readFileSync(path.join(root,'static/workspace_registry.js'),'utf8').match(/\/\*BEGIN JSON\*\/([\s\S]+?)\/\*END JSON\*\//);
if(!previewCatalog)throw new Error('Workspace registry is unavailable for preview.');
const previewWorkspaces=new Set(JSON.parse(previewCatalog[1]).workspaces.filter(workspace=>workspace.boundary?.kind==='native'&&(!workspace.held||settings.held_features?.[workspace.held]===true)).map(workspace=>workspace.id));
const customizations = Object.create(null), histories = Object.create(null), workspaceChats=Object.create(null), studioRevisions=Object.create(null);
let studioVersion=0;
function appearanceState(id) {
 return {workspace:id,revision:Object.hasOwn(studioRevisions,id)?createHash('sha256').update(id+':'+studioRevisions[id]).digest('hex'):'new',customization:copy(customizations[id]||{}),versions:copy(histories[id]||[])};
}
function saveAppearance(id,next,label,kind='change') {
 const version={id:'sample-version-'+(++studioVersion),ts:new Date().toISOString(),label,kind,customization:copy(customizations[id]||{})};
 histories[id]=(histories[id]||[]).concat(version).slice(-40);customizations[id]=copy(next);studioRevisions[id]=(studioRevisions[id]||0)+1;return version;
}
function previewHistory(id) {
 const versions=histories[id]||[],current=customizations[id]||{};
 const entries=versions.map((version,index)=>{const after=versions[index+1]?.customization||current,keys=Object.keys(version.customization),changed=Array.from(new Set(keys.concat(Object.keys(after)))).filter(key=>JSON.stringify(version.customization[key])!==JSON.stringify(after[key])).sort();return {version_id:version.id,when:version.ts,label:version.label,kind:version.kind,describes:'state BEFORE: '+version.label,undo_hint:'Restoring this sample version undoes this change and later changes.',keys:keys.sort(),changed,changed_label:changed.join(', ')||'nothing'};}).reverse();
 return {workspace:id,current:copy(current),current_keys:Object.keys(current).sort(),entries};
}
function previewAppearance(res,id,method,body) {
 const state=appearanceState(id),error=(message,status=400)=>json(res,{status:'error',message},status);
 if(method==='GET')return ok(res,state);
 if(method!=='POST')return error('This appearance action is not simulated.',405);
 if(body.expected_revision!==state.revision)return json(res,{status:'conflict',message:'This workspace changed. Reload its appearance before applying your draft.'},409);
 const patch=body.patch,validObject=value=>value&&typeof value==='object'&&!Array.isArray(value);
 if(!validObject(patch)||!Object.keys(patch).length||Object.keys(patch).some(key=>!['note','accent','density','actions'].includes(key))||Object.keys(body).some(key=>!['patch','expected_revision','apply'].includes(key))||body.apply!==undefined&&typeof body.apply!=='boolean')return error('Choose supported appearance fields and whether to apply.');
 if(patch.note!=null&&(typeof patch.note!=='string'||patch.note.length>1500)||patch.accent!=null&&(typeof patch.accent!=='string'||!/^#[\da-f]{6}$/i.test(patch.accent))||patch.density!=null&&!['comfortable','compact'].includes(patch.density))return error('The sample appearance contains an invalid note, accent or spacing.');
 if(patch.actions!=null&&(!Array.isArray(patch.actions)||patch.actions.length>8||patch.actions.some(action=>!validObject(action)||Object.keys(action).sort().join('|')!=='label|prompt'||typeof action.label!=='string'||!action.label.trim()||action.label.length>40||typeof action.prompt!=='string'||!action.prompt.trim()||action.prompt.length>400)))return error('Each quick action needs a short label and prompt.');
 const next=copy(state.customization);for(const [key,value] of Object.entries(patch)){if(value===null)delete next[key];else next[key]=copy(value);}
 const changed=Object.keys(patch).filter(key=>JSON.stringify(state.customization[key])!==JSON.stringify(next[key]));
 if(!body.apply)return ok(res,{...state,preview:next,changed,applied:false});
 if(settings.off_record&&settings.off_record_stops_storage!==false)return error('Off the record is on. The sample appearance was not saved.',403);
 if(!changed.length)return ok(res,{...state,changed:[],applied:false});
 const version=saveAppearance(id,next,'Sample workspace appearance');return ok(res,{...appearanceState(id),changed,applied:true,revert_to:version.id});
}
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
 const blocked=()=>json(res,{status:'blocked',message:'This action is not simulated in the design preview. No live action was taken.'},409);
 if(method!=='GET'){
  if(!b||typeof b!=='object'||Array.isArray(b))return json(res,{status:'error',message:'Provide a JSON object for a sample action.'},400);
  const supported=method==='POST'&&['/api/settings','/api/conversations','/api/projects','/api/chat/stream','/api/desktop/board','/api/desktop/cards'].includes(p)
   ||['PATCH','DELETE'].includes(method)&&/^\/api\/projects\/[^/]+$/.test(p)
   ||method==='DELETE'&&/^\/api\/desktop\/cards\/[^/]+$/.test(p)
   ||method==='POST'&&/^\/api\/workspace\/[^/]+\/(appearance|chat|chat\/clear|revert|reset)$/.test(p);
  if(!supported)return blocked();
 }
 if(p==='/api/desktop/board')return method==='GET'?json(res,previewBoard()):previewBoardChange(res,b);
 if(p==='/api/desktop/cards')return method==='GET'?ok(res,{cards:copy(homeSaved)}):previewBoardChange(res,{op:'save',expected_revision:homeRevision,card:b});
 const oldCard=p.match(/^\/api\/desktop\/cards\/([^/]+)$/);
 if(oldCard&&method==='DELETE')return previewBoardChange(res,{op:'remove',expected_revision:homeRevision,id:decodeURIComponent(oldCard[1])});
 if (p.endsWith('/events') || p==='/api/command-stream') {res.writeHead(204);return res.end();}
 if(method==='GET') {
  if(Object.hasOwn(readOnlyFixtures,p))return ok(res,readOnlyFixtures[p]);
  const crewRoom=p.match(/^\/api\/crew\/rooms\/([^/]+)(\/turns)?$/);
  if(crewRoom){
   const id=decodeURIComponent(crewRoom[1]);
   if(!conversations.some(conversation=>conversation.id===id))return json(res,{status:'error',message:'Sample conversation not found'},404);
   if(crewRoom[2])return ok(res,{tasks:sampleCrewTasks.filter(task=>task.conversation_id===id),messages:[]});
   return ok(res,{room:{conversation_id:id,revision:1,enabled:id==='design-main',member_ids:id==='design-main'?['sample-crew-guide']:[]}});
  }
  if(/^\/api\/calendar\/day\/\d{4}-\d{2}-\d{2}$/.test(p))return ok(res,sampleCalendarDay(p.slice(-10)));
  if(p==='/api/news/archive'){
   const category=u.searchParams.get('category'),offset=Math.max(0,Number(u.searchParams.get('offset'))||0),limit=Math.max(1,Math.min(100,Number(u.searchParams.get('limit'))||40));
   const items=category?sampleNews.filter(n=>n.category===category):sampleNews;
   return ok(res,{items:items.slice(offset,offset+limit),total:items.length,has_more:offset+limit<items.length});
  }
  if(p==='/api/wiki/page'){
   const content=wikiPages[u.searchParams.get('path')];
   return content===undefined?json(res,{status:'error',message:'Sample page not found'},404):ok(res,{content,locked:false});
  }
  if(p.startsWith('/api/career-ops/report/')){
   const content=careerReports[decodeURIComponent(p.slice('/api/career-ops/report/'.length))];
   return content===undefined?json(res,{status:'error',message:'Sample report not found'},404):ok(res,{content});
  }
  if(p.startsWith('/api/relationships/person/')){
   const name=decodeURIComponent(p.slice('/api/relationships/person/'.length));
   if(!samplePeople.some(c=>c.name.toLowerCase()===name.toLowerCase()))return json(res,{status:'error',message:'Sample person not found'},404);
   return ok(res,{timeline:{count:3,last_contact:isoDay(-1),last_heard_from:isoDay(-2),last_wrote_to:isoDay(-1),emails_received:1,emails_sent:1,meetings:1,interactions:[{at:isoDay(-1),kind:'email',direction:'out',subject:'Sample: a clearer opening for the launch'},{at:isoDay(-2),kind:'email',direction:'in',subject:'Sample: notes from the first review'},{at:isoDay(-5),kind:'meeting',title:'Sample: studio direction conversation'}],follow_ups:[]}});
  }
  if(/^\/api\/contacts\/[^/]+$/.test(p)){
   const name=decodeURIComponent(p.slice('/api/contacts/'.length)),person=samplePeople.find(c=>c.name.toLowerCase()===name.toLowerCase());
   if(person)return ok(res,{contact:{...person,scores:{overall:person.overall,reliability:person.overall,information_quality:0.83,emotional_trust:0.8,timeliness:0.76,domain_expertise:0.88},domains:['Sample studio collaboration'],last_interaction:isoDay(-1),evidence:[{type:'sample',timestamp:isoDay(-1),notes:'Fictional observation for visual review; no evaluation of a real person.'}]},research:'This is a fictional collaborator in the design preview. No personal information or external research is used.'});
  }
 }
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
 if(p==='/api/tasks')return ok(res,{tasks});
 if(method==='GET'&&p.startsWith('/api/tasks/'))return ok(res,{...(tasks.find(t=>t.task_id===p.split('/')[3])||tasks[0]),log:[]});
 if(p==='/api/approvals'||p==='/api/approvals/pending')return ok(res,{approvals:[],pending:[]});
 if(p==='/api/health')return ok(res,{connected:true,preview:true,model_ready:false});
 if(p==='/api/workspace/customizations')return ok(res,{customizations});
 m=p.match(/^\/api\/workspace\/([^/]+)\/(appearance|chat|chat\/clear|history|versions|revert|reset|customization)$/);
 if(m){
  const id=m[1],action=m[2];
  if(!previewWorkspaces.has(id))return json(res,{status:'blocked',message:'This workspace is unavailable in the sample preview.'},404);
  if(action==='appearance')return previewAppearance(res,id,method,b);
  if(method==='GET'){
   if(action==='chat')return ok(res,{workspace:id,chat:copy(workspaceChats[id]||[]),customization:copy(customizations[id]||{}),versions:copy(histories[id]||[])});
   if(action==='history')return ok(res,previewHistory(id));
   if(action==='versions'||action==='customization')return ok(res,appearanceState(id));
   return blocked();
  }
  if(settings.off_record&&settings.off_record_stops_storage!==false)return json(res,{status:'blocked',message:'Off the record is on. Sample workspace changes are paused.'},403);
  if(action==='chat'){
   const message=typeof b.message==='string'?b.message.trim():'';
   if(!message)return json(res,{status:'error',message:'Message required.'},400);
   if(!/\b(compact|dense|comfortable)\b/i.test(message))return json(res,{status:'blocked',applied:false,message:'The sample chat only demonstrates compact or comfortable spacing. Use Preview appearance for notes, accents and quick actions. No model or live action runs.'},409);
   const density=/\b(compact|dense)\b/i.test(message)?'compact':'comfortable',next={...(customizations[id]||{}),density};
   const applied=customizations[id]?.density!==density,version=applied?saveAppearance(id,next,'Sample '+density+' spacing'):null;
   const response=(applied?'The sample workspace now uses ':'This sample workspace already uses ')+density+' spacing. This is an in-memory demonstration; no model was called.';
   workspaceChats[id]=(workspaceChats[id]||[]).concat([{role:'user',text:message,ts:new Date().toISOString()},{role:'friday',text:response,ts:new Date().toISOString(),...(version?{revert_to:version.id,change:version.label}:{})}]).slice(-40);
   studioRevisions[id]=(studioRevisions[id]||0)+1;
   return ok(res,{workspace:id,response,applied,revert_to:version?.id||null,change:version?.label||null,customization:copy(customizations[id]||{}),versions:copy(histories[id]||[])});
  }
  if(action==='chat/clear'){workspaceChats[id]=[];studioRevisions[id]=(studioRevisions[id]||0)+1;return ok(res,{chat:[]});}
  if(action==='revert'){
   const target=(histories[id]||[]).find(version=>version.id===b.version_id);
   if(!target)return json(res,{status:'error',message:'Sample version not found.'},404);
   const version=saveAppearance(id,target.customization,'Restore sample version','revert');return ok(res,{...appearanceState(id),applied:true,revert_to:version.id});
  }
  if(action==='reset'){
   const changed=Object.keys(customizations[id]||{}).length>0,version=changed?saveAppearance(id,{},'Reset sample appearance','reset'):null;
   return ok(res,{...appearanceState(id),applied:changed,revert_to:version?.id||null});
  }
  return blocked();
 }
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
 if(p==='/api/models'||p.includes('model-catalog'))return ok(res,{models:[],roles:{orchestrator:[],subagent:[],creative:[],voice:[]},providers:[],local:[],cloud:[]});
 if(p==='/api/seat'||p==='/api/compute/status')return ok(res,{seat:null,preview:true});
 if(method!=='GET')return blocked();
 return ok(res,{items:[],data:[],accounts:[],models:[],tasks:[],events:[],notifications:[],processes:[],artifacts:[],codebases:[],workflows:[],schedules:[],connections:[],available:false,preview:true});
}
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp','.ico':'image/x-icon','.woff':'font/woff','.woff2':'font/woff2','.ttf':'font/ttf','.json':'application/json'};
const server=http.createServer(async(req,res)=>{
 try{
  const u=new URL(req.url,'http://127.0.0.1:'+port);
  if(u.pathname.startsWith('/api/'))return api(req,res,u,req.method==='GET'?{}:await readBody(req));
  if(req.method==='GET'&&u.pathname.startsWith('/preview/story/')){
   const story=sampleNews.find(n=>n.url===u.pathname);
   if(!story){res.writeHead(404);return res.end('Sample story not found');}
   const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
   const body=Buffer.from('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>'+esc(story.title)+'</title><style>body{margin:0;background:var(--fr-bg,#070914);color:var(--fr-text,#edf1ff);font:18px/1.7 system-ui,sans-serif}main{max-width:740px;margin:auto;padding:10vh 24px}small{color:var(--fr-label,#aab5c9)}h1{font-size:clamp(32px,5vw,56px);line-height:1.1;letter-spacing:-.035em}a{color:var(--fr-cyan,#00d4ff)}</style></head><body><main><small>Friday design preview · Fictional article</small><h1>'+esc(story.title)+'</h1><p>'+esc(story.snippet)+'</p><p>This local sample reader keeps the native News links usable during design review. No real reporting, external source, or live account is connected.</p><p><a href="/w/news">Return to News</a></p></main></body></html>');
   return bytes(res,body,'text/html; charset=utf-8',{'Content-Security-Policy':"default-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'"});
  }
  let rel=decodeURIComponent(u.pathname), workspace='';
  if(rel==='/'||rel==='/index.html'||/^\/w\/[a-z0-9_-]+$/.test(rel)){if(rel.startsWith('/w/'))workspace=rel.slice(3);rel='/index.html';}
  if(rel!=='/index.html'&&!rel.startsWith('/static/')&&!rel.startsWith('/assets/')){res.writeHead(404);return res.end('Not found');}
  const file=path.resolve(root,'.'+rel);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);return res.end('Not found');}
  let body=fs.readFileSync(file);
  if(rel==='/index.html'){
   const boot='<script>window.__FRIDAY_PREVIEW__=true;window.__FRIDAY_API_TOKEN="design-preview";'+(workspace?'window.__FRIDAY_STANDALONE__='+JSON.stringify(workspace)+';document.documentElement.classList.add("ws-standalone");':'')+'</script>'; // Synthetic fixture marker, never a credential. # pragma: allowlist secret
   const label='<style>:root{--friday-safe-bottom:26px!important}.fx-preview-label{position:fixed;box-sizing:border-box;bottom:0;left:0;right:0;height:26px;z-index:9999;display:flex;align-items:center;justify-content:center;padding:3px 8px;background:var(--fr-surface);color:var(--fr-label);font:10px Inter,sans-serif;border-top:1px solid var(--fr-glass-edge);pointer-events:none}</style><div class="fx-preview-label" role="note">Design preview · Sample data · No live actions</div>';
   let html=body.toString('utf8').replace('<head>','<head>'+boot);
   const closeBody=html.lastIndexOf('</body>');
   html=closeBody<0?html+label:html.slice(0,closeBody)+label+html.slice(closeBody);
   body=Buffer.from(html);
  }
  res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Content-Length':body.length,'Cache-Control':'no-store'});res.end(body);
 }catch(e){json(res,{status:'error',message:e.message},500);}
});
server.listen(port,'127.0.0.1',()=>process.stdout.write('Design preview: http://127.0.0.1:'+port+'\nActual branch UI; synthetic data only.\n'));
module.exports={projects,conversations,documents,cards};
