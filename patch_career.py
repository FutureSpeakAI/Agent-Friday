import os
file = os.path.join('C:\\Users\\user\\Projects\\friday-desktop\\ui_parts', 'app.html')
content = open(file, 'r', encoding='utf-8').read()
start = content.index('function CareerWS(){')
end = content.index('function WikiWS(){')
new_career = """function CareerWS(){
  const[tracker,setTracker]=useState(null);const[reports,setReports]=useState([]);const[pipeline,setPipeline]=useState('');
  const[jobs,setJobs]=useState('');const[loading,setLoading]=useState(true);const[view,setView]=useState('overview');
  const[reportContent,setReportContent]=useState('');const[evalUrl,setEvalUrl]=useState('');const[storyBank,setStoryBank]=useState('');
  useEffect(()=>{Promise.all([
    fetch('/api/career-ops/tracker').then(r=>r.json()).then(setTracker).catch(()=>null),
    fetch('/api/career-ops/reports').then(r=>r.json()).then(d=>setReports(d.reports||[])).catch(()=>[]),
    fetch('/api/career-ops/pipeline').then(r=>r.json()).then(d=>setPipeline(d.content||'')).catch(()=>''),
    fetch('/api/jobs').then(r=>r.json()).then(d=>setJobs(d.content||d.raw||'')).catch(()=>'')
  ]).then(()=>setLoading(false))},[]);
  const loadReport=name=>{fetch('/api/career-ops/report/'+name).then(r=>r.json()).then(d=>{setReportContent(d.content||'');setView('report')}).catch(()=>{})};
  const launchEval=()=>{if(!evalUrl.trim())return;fetch('/api/vibe-code/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tasks:['Evaluate this job: '+evalUrl],cwd:'C:\\\\Users\\\\user\\\\Projects\\\\career-ops'})}).then(()=>{setEvalUrl('');alert('Evaluation launched')}).catch(()=>{})};
  const launchScan=()=>{fetch('/api/vibe-code/launch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tasks:['Run portal scan'],cwd:'C:\\\\Users\\\\user\\\\Projects\\\\career-ops'})}).then(()=>alert('Scan launched')).catch(()=>{})};
  if(loading)return <div style={{color:'#555',textAlign:'center',padding:40}}>Loading career pipeline...</div>;
  const entries=tracker?.entries||[];const applied=entries.filter(e=>(e.status||'').toLowerCase().includes('appli')).length;
  const interviewing=entries.filter(e=>(e.status||'').toLowerCase().includes('interview')).length;
  const offered=entries.filter(e=>(e.status||'').toLowerCase().includes('offer')).length;
  const hasData=entries.length>0||reports.length>0||pipeline.length>10;
  return(<div>
    {hasData?<FridaySays text={entries.length+' evaluated, '+applied+' applied, '+interviewing+' interviewing, '+reports.length+' reports. Target: $500K'}/>:<FridaySays text="Career-ops ready. Paste a job URL below to evaluate, or scan portals for new opportunities."/>}
    <div className="card" style={{marginBottom:12,padding:10}}>
      <div style={{display:'flex',gap:6,alignItems:'center',marginBottom:8}}><input className="input" style={{flex:1}} value={evalUrl} onChange={e=>setEvalUrl(e.target.value)} onKeyDown={e=>e.key==='Enter'&&launchEval()} placeholder="Paste a job URL to evaluate..."/><button className="btn" onClick={launchEval}>Evaluate</button></div>
      <div style={{display:'flex',gap:4,flexWrap:'wrap'}}><button className="btn" onClick={launchScan} style={{fontSize:10}}>Scan Portals</button><button className="btn" style={{fontSize:10}} onClick={()=>setView('interview')}>Interview Prep</button><button className="btn" style={{fontSize:10}} onClick={()=>setView('overview')}>Overview</button></div>
    </div>
    <div style={{display:'flex',gap:4,marginBottom:10}}>{['overview','tracker','reports','pipeline','curated','interview'].map(v=><button key={v} className={'btn '+(view===v?'':'btn-magenta')} style={{fontSize:9,padding:'3px 8px'}} onClick={()=>setView(v)}>{v}</button>)}</div>
    {view==='overview'&&<div>
      <div style={{display:'flex',gap:8,marginBottom:12}}>
        <div className="card" style={{flex:1,textAlign:'center'}}><div className="stat" style={{fontSize:20}}>{entries.length}</div><div className="stat-label">evaluated</div></div>
        <div className="card" style={{flex:1,textAlign:'center'}}><div className="stat" style={{fontSize:20,color:'#00d4ff'}}>{applied}</div><div className="stat-label">applied</div></div>
        <div className="card" style={{flex:1,textAlign:'center'}}><div className="stat" style={{fontSize:20,color:'#7c3aed'}}>{interviewing}</div><div className="stat-label">interviewing</div></div>
        <div className="card" style={{flex:1,textAlign:'center'}}><div className="stat" style={{fontSize:20,color:'#00ff80'}}>{offered}</div><div className="stat-label">offers</div></div>
      </div>
      <div className="card" style={{marginBottom:12}}><div className="card-title">Pipeline Funnel</div>
        {[{l:'Evaluated',c:entries.length,cl:'#f59e0b',p:100},{l:'Applied',c:applied,cl:'#00d4ff',p:entries.length?Math.round(applied/entries.length*100):0},{l:'Interviewing',c:interviewing,cl:'#7c3aed',p:entries.length?Math.round(interviewing/entries.length*100):0},{l:'Offers',c:offered,cl:'#00ff80',p:entries.length?Math.round(offered/entries.length*100):0}].map((s,i)=><div key={i} style={{marginBottom:6}}><div style={{display:'flex',justifyContent:'space-between',fontSize:11,marginBottom:2}}><span style={{color:'#aaa'}}>{s.l}</span><span style={{fontFamily:'Orbitron',color:s.cl}}>{s.c} ({s.p}%)</span></div><div className="progress-bar"><div className="progress-fill" style={{width:Math.max(s.p,2)+'%',background:s.cl}}/></div></div>)}
      </div>
      {reports.length>0&&<div className="card"><div className="card-title">Recent Evaluations</div>{reports.slice(0,5).map((r,i)=><div key={i} style={{padding:'4px 0',borderBottom:'1px solid rgba(255,255,255,0.03)',fontSize:11,cursor:'pointer',color:'#aaa'}} onClick={()=>loadReport(r.name)}>{r.name}</div>)}</div>}
      {!hasData&&<div className="card" style={{textAlign:'center',padding:20}}><div style={{fontSize:16,marginBottom:8}}>Get Started</div><div style={{fontSize:12,color:'#888',marginBottom:12}}>Paste a job URL above to evaluate your first role.</div><div style={{fontSize:11,color:'#555'}}>Career-ops uses A-F scoring across 10 weighted dimensions. Generates tailored CVs and tracks applications.</div></div>}
    </div>}
    {view==='tracker'&&(entries.length>0?entries.map((e,i)=>{const score=parseFloat(e.score)||0;const color=score>=4?'#00ff80':score>=3?'#f59e0b':'#ff0080';return(<div key={i} className="card" style={{marginBottom:4}}><div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}><span style={{fontWeight:600,fontSize:12}}>{e.company}</span><span style={{fontFamily:'Orbitron',fontSize:13,color}}>{e.score}</span></div><div className="progress-bar" style={{marginTop:3}}><div className="progress-fill" style={{width:Math.min(score*20,100)+'%',background:color}}/></div>{e.status&&<span className="badge-cyan" style={{fontSize:9,marginTop:4,display:'inline-block'}}>{e.status}</span>}</div>)}):<div style={{color:'#555',fontSize:12,textAlign:'center',padding:20}}>No evaluated roles yet. Paste a job URL above.</div>)}
    {view==='reports'&&(reports.length>0?reports.map((r,i)=><div key={i} className="card" style={{marginBottom:4,cursor:'pointer'}} onClick={()=>loadReport(r.name)}><div style={{display:'flex',justifyContent:'space-between'}}><span style={{fontSize:12}}>{r.name}</span><span style={{fontSize:10,color:'#555'}}>{(r.size/1024).toFixed(1)}KB</span></div></div>):<div style={{color:'#555',fontSize:12,textAlign:'center',padding:20}}>No reports yet</div>)}
    {view==='report'&&<div><button className="btn" style={{marginBottom:8,fontSize:10}} onClick={()=>setView('reports')}>Back</button><pre style={{whiteSpace:'pre-wrap',fontSize:11,color:'#bbb',fontFamily:'Inter',lineHeight:1.6}}>{reportContent}</pre></div>}
    {view==='pipeline'&&<div>{pipeline?<pre style={{whiteSpace:'pre-wrap',fontSize:11,color:'#888',fontFamily:'JetBrains Mono'}}>{pipeline}</pre>:<div style={{color:'#555',fontSize:12,textAlign:'center',padding:20}}>Pipeline empty. Add job URLs to career-ops/data/pipeline.md</div>}</div>}
    {view==='curated'&&<pre style={{whiteSpace:'pre-wrap',fontSize:11,color:'#888',fontFamily:'JetBrains Mono'}}>{jobs.substring(0,4000)||'No curated roles. Daily job intelligence runs at 8 AM.'}</pre>}
    {view==='interview'&&<div><div className="card-title">Interview Prep</div><div style={{fontSize:11,color:'#888',marginBottom:8}}>STAR+R stories from evaluations. Your story bank builds as you evaluate more roles.</div><pre style={{whiteSpace:'pre-wrap',fontSize:11,color:'#bbb',fontFamily:'Inter',lineHeight:1.6}}>{storyBank||'Loading story bank...'}</pre></div>}
  </div>);
}
"""
content = content[:start] + new_career + content[end:]
open(file, 'w', encoding='utf-8').write(content)
print(f'Done: replaced {end-start} chars with {len(new_career)} chars')
