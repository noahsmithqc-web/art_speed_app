/* Speed tool front end - the speed tab and its drawer.
 *
 * The locked mockup (speed_tab_mock_v15.html) is the contract for what this looks like, so its
 * drawing code is used as it stands: the functions marked "from v15" below are the mockup's own,
 * changed only where noted. What is different is where the numbers come from. The mockup worked
 * everything out in the page from data baked into it; here the engine does all of that, and
 * the page only draws what GET /speed/locations and GET /speed/locations/{id} answer (the
 * contract of docs/CONTRACT.md). Every control sends its change to the engine, which saves it,
 * logs it and answers with the refreshed drawer - nothing is computed or kept here.
 *
 * The plan-over-mockup rules (docs/CONTRACT.md) therefore hold without anything to do here:
 * no low-n tag, a dead cam is 100 % zeros, the suggestion has no 50 % cut-off.
 *
 * Not strict on purpose: the mockup's code is kept as written.
 */

// ---------- from v15: constants and formatting ----------
const DIRS=['NB','SB','EB','WB'],AXES={NS:['NB','SB'],EW:['EB','WB']};
const PRIMARY={NB:['NE','SE'],SB:['SW','NW'],EB:['SE','SW'],WB:['NW','NE']};
const CAMCOL={1:'var(--cam1)',2:'var(--cam2)',3:'var(--cam3)',4:'var(--cam4)',5:'var(--cam5)',6:'var(--cam6)',7:'var(--cam7)',8:'var(--cam8)'};
const PEAK=new Set([7,8,16,17]);const pk=hh=>PEAK.has(hh)?' pk':'';
const f1=x=>x.toFixed(1),pct=x=>(x*100).toFixed(1)+'%',p2=x=>String(Math.round(x)),pU=x=>Number.isInteger(x)?String(x):x.toFixed(2);
const DOW=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];const dl=d=>DOW[new Date(d+'T12:00:00').getDay()]+' '+d.slice(5);
const hh2=h=>String(h).padStart(2,'0')+':00';

// ---------- what the engine last answered ----------
let PROJECT={number:'',name:'',state:'',client:'',db:'',folder:'',is_art_project:false};
let TAB=null;          // GET /speed/locations: summary, generate, rows
let D=null;            // GET /speed/locations/{id}: the open drawer
let CUR=null;          // its location id
let CAMP={};           // cam key -> its entry in D.cams
// The mockup's own shapes, filled from D by setD() so its drawing code reads them unchanged.
let RAW={},LOCS=[];
const enc=encodeURIComponent;
const num=x=>x===null||x===undefined?'—':String(+x);

/** One /speed call. Answers the body, or null after telling the user why not: a refusal
 *  ({ok:false, message}, 4xx) gets a pop-up with the engine's own sentence. */
async function api(method,path,body){
 const url='/speed'+path;let r;
 try{r=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})}
 catch(e){engineDown(new Error('Could not reach the app engine ('+url+').'));return null}
 if(r.status>=500){engineDown(new Error(url+' answered '+r.status+'.'));return null}
 let j=null;try{j=await r.json()}catch(e){}
 if(!r.ok){errorModal(method==='GET'?'That could not be read':'That was not done',(j&&j.message)||(url+' answered '+r.status+'.'));return null}
 return j}

/** Take a drawer payload: keep it, and lay it out the way the mockup's code expects. */
function setD(d){D=d;CUR=d.location.id;RAW={};CAMP={};
 for(const sn of d.location.sitenumbers)RAW[sn.sitenumber]={sitenumber:sn.sitenumber,sitecode:sn.sitecode,name:sn.name,cfg:{},cfgQch:{},cams:{},corner:{},camMask:{},camAck:{}};
 for(const c of d.cams){CAMP[c.key]=c;const r=RAW[c.sitenumber];r.cams[c.cam]={file:c.file,events:c.events,form:c.csv_form};r.corner[c.cam]=c.corner;r.camMask[c.cam]=[...c.masked_hours];r.camAck[c.cam]=c.mask_acknowledged}
 const first=RAW[d.location.sitenumbers[0].sitenumber],w=d.location.window||{};
 const st={include:{},posted:{},same:{...d.posted_same},src:{},mask:{},adjust:{},note:d.note||'',linked:d.location.corners_linked!==false,
  window:{start:w.start_date,startTime:w.start_time,hours:w.duration_hours,src:w.source}};
 for(const dir of DIRS){const x=d.directions[dir];first.cfg[dir]=x.lane_config||'';first.cfgQch[dir]=x.lane_config_qch||'';
  st.include[dir]=x.include_in_report;st.posted[dir]=x.posted;st.src[dir]=x.source_cam;st.mask[dir]={lo:x.mask.low,hi:x.mask.high};st.adjust[dir]=x.adjust_by||0}
 LOCS=[{id:CUR,slices:d.location.slices.map(s=>{const o={site:s.sitenumber};if(s.date)o.date=s.date;if(s.from_hour!==null&&s.from_hour!==undefined){o.from=s.from_hour;o.to=s.to_hour}return o}),state:st}]}

// ---------- from v15: reading the open location ----------
function hasLegCfg(cfg,d){return !!(cfg[d]&&[...cfg[d]].some(ch=>ch!=='0'))}
const L=()=>LOCS.find(l=>l.id===CUR),S=()=>L().state;
const sites=()=>[...new Set(L().slices.map(s=>s.site))];
const R=()=>RAW[sites()[0]];
function sliceLabel(s){const r=RAW[s.site];let t=r.sitenumber;if(s.date)t+=' · '+dl(s.date);if(s.from!==undefined)t+=' · '+hh2(s.from)+'–'+hh2(s.to);return t}
function isSliced(loc){return loc.slices.some(s=>s.date||s.from!==undefined)}
// The engine's cams, in its order. In a grouped location each is a cam group: the same cam number
// on every sitenumber, one camera, under its first sitenumber's key (its files are in .members).
function CAMS(){return D.cams.map(c=>c.key)}
function camFiles(k){const c=CAMP[k];if(!c.members)return esc(camInfo(k).file);const o=c.on_sitenumbers;
 return(o.set<o.of?`<span class="tag warn">on ${o.set} of ${o.of} sitenumbers</span><br>`:'')+c.members.map(m=>`${esc(m.sitenumber)} · ${m.events.toLocaleString()} events${m.dead?' <span class="tag warn">dead</span>':''}`).join('<br>')}
const camSite=k=>k.split(':')[0],camNo=k=>k.split(':')[1];
function camCol(k){return CAMCOL[camNo(k)]||'#5B6573'}
function corner(k){return RAW[camSite(k)].corner[camNo(k)]||null}
function camMaskSet(k){return new Set(RAW[camSite(k)].camMask[camNo(k)]||[])}
function camInfo(k){return RAW[camSite(k)].cams[camNo(k)]}
function role(k,dir){const c=corner(k);if(!c)return'';return c===PRIMARY[dir][0]?'Primary':c===PRIMARY[dir][1]?'Secondary':''}
function modifier(dir){return S().adjust[dir]||0}
function cornersOk(){const live=CAMS().filter(k=>!camDead(k));return CAMS().length<2||live.every(k=>corner(k))}
function cfg(){return R().cfg}
function incl(){return DIRS.filter(d=>S().include[d])}
function dirExists(d){return !!S().include[d]}

// ---------- the mockup's calculations, answered by the engine instead ----------
function camLabel(k){return CAMP[k]?CAMP[k].label:'Cam '+camNo(k)}
function obs(k,dir){const h=(D.histograms[k]||{})[dir];return h?{zero:h.zero,h:h.bins,n:h.events,maskedHours:h.masked_hours}:{zero:0,h:{},n:0,maskedHours:0}}
function rawStats(k,dir){const x=(CAMP[k].directions||{})[dir]||{};return{n:x.n||0,zero:x.zero||0,nz:x.n_nonzero||0,pz:x.pct_zero||0,avg:x.avg||0,p85:x.p85||0}}
function camDead(k){return !!CAMP[k].dead}
function suggest(dir){return D.directions[dir].suggested||null}
function finalStats(dir){const r=D.directions[dir].report;if(!r)return null;
 return{src:r.source_cam,mod:r.adjust_by||0,nRaw:r.events,zero:r.zero,masked:r.masked,n:r.n,avg:r.avg||0,p85:r.p85||0,over:r.over_posted,pover:r.pct_over_posted,maskedHours:r.masked_hours}}
function hasLeg(d){return !!D.directions[d].exists}
function offLeg(){const out={};for(const d of DIRS){const n=D.directions[d].off_leg_events;if(n)out[d]=n}return out}
function totalEvents(){return D.location.events||0}
function ready(){return D.status.state==='ready'}
function missing(){return D.status.missing||[]}
function warnings(){return D.warnings.map(w=>w.text)}
function maskedTotal(){return{n:D.location.masked_hours||0,over:D.warnings.filter(w=>w.code==='over_half_masked'&&w.can_acknowledge).map(w=>({k:w.cam,label:camLabel(w.cam)}))}}
function countDays(){return D.location.count_days||[]}
function winText(w){return w&&w.start?`${w.start} · ${w.startTime||'—'} · ${num(w.hours)} h`:'no count window'}

// ---------- the tab ----------
let SEL=new Set();
/** Called by the shell when the speed tab comes up. */
function speedShow(){const p=APP.project||{};
 PROJECT={number:p.project_number||'',name:p.project_name||'',state:p.state||'',client:p.client||'',db:p.db||'',folder:p.folder||'',is_art_project:!!p.is_art_project};
 renderTab();loadTab()}
/** Called by the shell when the user goes to another tab: the drawer does not follow. */
function speedLeave(){if(drawerOpen){drawerOpen=false;document.getElementById('scrim').classList.remove('on');document.getElementById('drawer').classList.remove('on')}closeM('gen');closeM('split')}
/** Called by the shell when another project is opened. */
function speedReset(){speedLeave();TAB=null;D=null;CUR=null;SEL.clear()}
async function loadTab(){const t=await api('GET','/locations');if(!t)return;TAB=t;renderTab()}
function renderTab(){
 const head=`<h1>Intersection Speed Data</h1>
 <div class="tabsub">Speed data - ${esc(PROJECT.number)}</div>
 <div class="tabsub">${esc(PROJECT.name)||'(no project name)'}&nbsp;&nbsp;|&nbsp;&nbsp;${esc(PROJECT.state)||'—'}&nbsp;&nbsp;|&nbsp;&nbsp;${esc(PROJECT.client)||'—'}</div>`;
 const tab=document.getElementById('tab');
 if(!TAB){tab.innerHTML=head+'<p class="hint">Reading the speed data…</p>';return}
 const sum=TAB.summary,has=TAB.rows.length>0;
 for(const id of [...SEL])if(!TAB.rows.some(r=>r.id===id))SEL.delete(id);
 const rows=TAB.rows.map(row=>{const win=row.window||{},w=row.warnings||[],rdy=row.status.state==='ready';
 return`<tr class="${row.id===CUR&&drawerOpen?'sel':''}" onclick="openDrawer(${row.id})"><td class="chk" onclick="event.stopPropagation()"><input type="checkbox" ${SEL.has(row.id)?'checked':''} onchange="toggleSel(${row.id},this.checked)"></td>
 <td><b>${esc(row.code)}</b>&nbsp; ${esc(row.name)||'<span style="color:var(--amber)">no name yet</span>'}${row.is_sliced||row.is_grouped?`<div class="hint" style="margin:2px 0 0">${row.slices.map(sl=>'<span class="tag ro">'+esc(sl.label)+'</span>').join(' ')}</div>`:''}</td>
 <td>${win.start_date?`${win.start_date} · ${win.start_time||'—'} · ${num(win.duration_hours)} h${win.duration_hours>24?' <span class="tag ro">'+Math.round(win.duration_hours/24)+' days</span>':''}`:'—'}</td><td class="n">${row.cams}</td><td>${row.in_report.join(' ')||'—'}</td><td class="n">${row.events.toLocaleString()}</td>
 <td>${row.corners.set}/${row.corners.of}</td><td>${row.posted.set}/${row.posted.of}</td><td>${row.source_cams.set}/${row.source_cams.of}</td>
 <td>${rdy?'<span class="pill ok">Ready</span>':`<span class="pill warn" title="${esc((row.status.missing||[]).join(' · '))}">Needs input</span>`}${w.length?` <span class="tag warn" title="${esc(w.join(' · '))}">${w.length} warning${w.length>1?'s':''}</span>`:''}</td>
 <td class="kebab" onclick="event.stopPropagation();toggleMenu(this)">⋯<div class="menu">${row.is_grouped?`<button onclick="ungroup(${row.id})">Ungroup</button>`:''}${row.is_sliced?`<button onclick="unsplit(${row.id})">Unsplit ${esc(row.sitenumbers[0])}</button>`:''}${row.sitenumbers.map(sn=>`<button onclick="deleteSite('${esc(sn)}')">Delete sitenumber ${esc(sn)}…</button>`).join('')}</div></td></tr>`});
 const selRows=TAB.rows.filter(r=>SEL.has(r.id));const canGroup=selRows.length>1&&selRows.every(r=>!r.is_sliced&&!r.is_grouped);
 tab.innerHTML=head+`
 <div class="card"><h3>${has?'Speed data found':'No speed data yet'}</h3><div class="path">${esc(has?PROJECT.db:PROJECT.folder)}</div>
  <div class="hint">${has?`${sum.sitenumbers} sitenumber${sum.sitenumbers===1?'':'s'} · ${sum.locations} report location${sum.locations===1?'':'s'} · ${sum.cams} cam${sum.cams===1?'':'s'} · last import ${sum.last_import||'—'} · QC Hub snapshot ${sum.qch_snapshot||'—'}`:`"Add speed data" imports the Datalens speed CSVs you select (sitenumber_cam-N_speed.csv) into speed_${esc(PROJECT.number)}.db in this project folder.`}</div></div>
 <div class="toolbar"><button class="btn green" onclick="addData()">Add speed data</button>
  <span class="sel-info">${SEL.size?SEL.size+' selected':''}</span>${SEL.size?`<button class="btn sm" ${canGroup?'':'disabled title="Select two or more unsplit, ungrouped locations"'} onclick="groupSel()">Group into one location</button><button class="btn sm ghost" onclick="SEL.clear();renderTab()">Clear</button>`:''}
  <span class="sp"></span><span style="color:var(--ink2)">Last report: ${sum.last_report||'none yet'}</span><button class="btn ${TAB.generate.green?'green':'pri'}" ${TAB.generate.enabled?'':'disabled'} onclick="openGen()">Generate report…</button></div>
 ${has?`<table class="bgtable"><thead><tr><th class="chk"></th><th>Location</th><th>Count window</th><th class="n">Cams</th><th>In report</th><th class="n">Events</th><th>Corners</th><th>Posted</th><th>Source cams</th><th>Status</th><th></th></tr></thead><tbody>${rows.join('')}</tbody></table>
 <p class="hint">One report per location. A location is one or more slices (sitenumber, optional day, optional time window); tick rows to group sitenumbers, split a location from its drawer. Generate turns green when every location is Ready. Hover a warning tag for the detail.</p>`:''}`}
function toggleSel(id,on){if(on)SEL.add(id);else SEL.delete(id);renderTab()}
// The menu is placed on the window, not inside the table: the table clips what hangs out of it
// (rounded corners), which hid the menu of the last row. It opens under the kebab, leftward, and
// above it when there is no room below; scrolling closes it.
function toggleMenu(td){const m=td.querySelector('.menu');document.querySelectorAll('.menu.on').forEach(x=>x!==m&&x.classList.remove('on'));
 if(!m.classList.toggle('on'))return;
 const r=td.getBoundingClientRect();m.style.position='fixed';m.style.left='auto';m.style.right=Math.max(6,window.innerWidth-r.right+6)+'px';
 const h=m.offsetHeight,below=r.bottom-6;m.style.top=(below+h>window.innerHeight-6&&r.top-h>6?r.top-h+6:below)+'px'}
document.addEventListener('scroll',()=>document.querySelectorAll('.menu.on').forEach(x=>x.classList.remove('on')),true);
document.addEventListener('click',e=>{if(!e.target.closest('.kebab'))document.querySelectorAll('.menu.on').forEach(x=>x.classList.remove('on'))});

// ---------- add speed data ----------
let IMPORT_DIR=null;    // where the last picker was left, so the next one opens there
/** The files to import: {files:[...]} from the native multi-file picker, or {folder} typed when
 *  this machine has no picker. Null when cancelled. */
async function pickSpeedFiles(){
 const typed=async()=>{const f=await typeFolder('Folder with the speed CSVs',IMPORT_DIR||PROJECT.folder);return f?{folder:f}:null};
 if(APP.app&&APP.app.can_pick_folder===false)return typed();
 let b;try{b=await post('/api/dialog/files',{title:'Select the speed CSVs to import',start:IMPORT_DIR||PROJECT.folder})}catch(e){engineDown(e);return null}
 if(!b.ok){if(b.error.kind==='no_dialog')return typed();errorModal('The file picker is busy',b.error.message);return null}
 if(b.cancelled||!b.files.length)return null;
 IMPORT_DIR=b.files[0].replace(/[\\/][^\\/]*$/,'');return{files:b.files}}
async function addData(src,choices){
 if(!src){src=await pickSpeedFiles();if(!src)return}
 const n=src.files?src.files.length:0;
 spinnerModal('Add speed data','','Importing speed data','Reading '+(n?n+' speed CSV'+(n>1?'s':''):'the speed CSVs')+' and looking the sitenumbers up in QC Hub…');
 const r=await api('POST','/import',{...src,choices:choices||{}});if(!r)return;
 closeModal();
 if(r.status==='needs_choice')return chooseNumbers(src,r);
 if(r.status==='refused')return errorModal('Nothing was imported',[r.message].concat((r.fatal||[]).slice(1)).concat((r.refused_files||[]).map(f=>f.file+': '+f.error)).join(' '));
 importDone(r)}
/** The file name and the header of a file give different sitenumbers: the user says which. */
function chooseNumbers(src,r){
 showModal('wide',`<div class="wstep"><span>Add speed data</span></div><h3>Which sitenumber is right?</h3><p>${esc(r.message)}</p>
 <div class="body"><table><thead><tr><th>File</th><th>File name says</th><th>Header says</th></tr></thead><tbody>${r.disagreements.map((x,i)=>`<tr><td class="mono">${esc(x.file)}</td>
  <td><label><input type="radio" name="pick${i}" value="file name" checked> ${esc(x.file_name_sitenumber)}</label></td>
  <td><label><input type="radio" name="pick${i}" value="header" ${x.header_usable?'':'disabled'}> ${esc(x.header_sitecode)}${x.header_usable?'':' (not a sitenumber)'}</label></td></tr>`).join('')}</tbody></table></div>
 <div class="actions" style="margin-top:16px"><button data-act="cancel">Cancel</button><div class="sp"></div><button class="primary" data-act="go">Import</button></div>`,act=>{
  if(act==='cancel')return closeModal();if(act!=='go')return;
  const choices={};r.disagreements.forEach((x,i)=>{choices[x.file]=modalBox().querySelector(`input[name=pick${i}]:checked`).value});
  closeModal();addData(src,choices)})}
function importDone(r){const t=r.totals,list=a=>a&&a.length?`<div style="font-size:12px;color:var(--ink-2);white-space:normal">${a.map(esc).join('<br>')}</div>`:'';
 showModal('wide',`<div class="wstep"><span>Add speed data</span></div><h3>${t.sitenumbers} sitenumber${t.sitenumbers===1?'':'s'} imported</h3>
 <p>${t.files} file${t.files===1?'':'s'} · ${t.events.toLocaleString()} events${t.overwritten?` · ${t.overwritten} replaced (re-import is delete-and-replace)`:''}${r.created_db?` · speed_${esc(PROJECT.number)}.db created`:''}</p>
 <div class="body"><table><thead><tr><th>Sitenumber</th><th>Location</th><th class="num">Cams</th><th class="num">Events</th><th>QC Hub</th></tr></thead><tbody>${r.sites.map(s=>`<tr><td class="mono">${esc(s.sitenumber)}${s.overwritten?' <span style="color:var(--warn-ink)">(replaced)</span>':''}</td><td>${esc(s.name)||'<i>no name yet</i>'}${list(s.warnings)}${list(s.advisories)}</td><td class="num">${s.cams.length}</td><td class="num">${s.events.toLocaleString()}</td><td>${s.qch_lookup?'found':'—'}</td></tr>`).join('')}</tbody></table>
 ${(r.refused_files||[]).length?`<div class="banner warn" style="margin-top:14px"><b>${r.refused_files.length} file${r.refused_files.length===1?'':'s'} left out:</b><br>${r.refused_files.map(f=>esc(f.file)+': '+esc(f.error)).join('<br>')}</div>`:''}</div>
 <div class="actions" style="margin-top:16px"><div class="sp"></div><button class="primary" data-act="ok">OK</button></div>`,()=>closeModal());
 if(APP.project)APP.project.has_speed_data=true;PROJECT.db=PROJECT.db||'';
 if(drawerOpen)closeDrawer();loadTab();loadRecent()}
function deleteSite(sn){confirmModal('Delete sitenumber '+sn+'?','This removes the sitenumber, its events, cams, time masks and every location built on it from speed_'+PROJECT.number+'.db. The CSV files are not touched; importing them again brings the data back, without the decisions.','Delete',async()=>{
 const r=await api('DELETE','/sitenumbers/'+enc(sn));if(!r)return;TAB=r.tab;if(drawerOpen)closeDrawer();else renderTab();toast('Sitenumber '+sn+' deleted')})}

// ---------- group / split ----------
/** A change to which locations exist. The engine answers the new ids and the refreshed tab. */
async function regroup(path,body,msg,reopen){const r=await api('POST','/locations'+path,body);if(!r)return;TAB=r.tab;SEL.clear();toast(msg);
 if(reopen&&drawerOpen&&r.location_ids.length)openDrawer(r.location_ids[0]);else if(drawerOpen)closeDrawer();else renderTab()}
function groupSel(){regroup('/group',{ids:TAB.rows.filter(r=>SEL.has(r.id)).map(r=>r.id)},'Grouped into one location')}
function ungroup(id){regroup('/'+id+'/ungroup',undefined,'Ungrouped')}
function splitByDay(){regroup('/'+CUR+'/split-by-day',undefined,'Split into one location per day',true)}
function openSplitTime(){const m=document.getElementById('splitmodal');m.innerHTML=`<div class="mhead"><h3>Split ${sites()[0]} by time of day</h3><button class="btn icon ghost x" onclick="closeM('split')">✕</button><div style="font-size:13px;color:var(--ink2)">Each window becomes its own report location${isSliced(L())&&L().slices[0].date?' for '+dl(L().slices[0].date):''}. Hours are whole; a window runs from the start hour up to the end hour.</div></div>
 <div class="mbody" id="winrows"></div><div class="mfoot"><span class="note"><label style="display:inline-flex;gap:6px;align-items:center"><input type="checkbox" id="keeprem" checked> keep the remainder as its own location</label></span><button class="btn" onclick="addWin()">Add window</button><button class="btn pri" onclick="doSplitTime()">Split</button></div>`;
 WINS=[{from:6,to:10},{from:15,to:19}];drawWins();openM('split')}
let WINS=[];
function drawWins(){document.getElementById('winrows').innerHTML=WINS.map((w,i)=>`<div class="modline" style="margin:8px 0"><span style="width:80px">Window ${i+1}</span><input class="f num" type="number" min="0" max="23" value="${w.from}" onchange="WINS[${i}].from=+this.value"> to <input class="f num" type="number" min="1" max="24" value="${w.to}" onchange="WINS[${i}].to=+this.value"> <button class="btn sm ghost" onclick="WINS.splice(${i},1);drawWins()">remove</button></div>`).join('')}
function addWin(){WINS.push({from:0,to:24});drawWins()}
function doSplitTime(){const wins=WINS.filter(w=>w.to>w.from).sort((a,b)=>a.from-b.from);if(!wins.length)return;
 const keep=document.getElementById('keeprem').checked;closeM('split');
 regroup('/'+CUR+'/split-by-time',{windows:wins.map(w=>({from:w.from,to:w.to})),keep_remainder:keep},'Split by time',true)}
function unsplit(id){regroup('/'+id+'/unsplit',undefined,'Unsplit',true)}

// ---------- generate ----------
function openGen(){const m=document.getElementById('genmodal');const list=TAB.rows.map(l=>({l,r:l.status.state==='ready'}));
 m.innerHTML=`<div class="mhead"><h3>Generate speed reports, project ${esc(PROJECT.number)}</h3><button class="btn icon ghost x" onclick="closeM('gen')">✕</button><div style="font-size:13px;color:var(--ink2)">One workbook per report location (summary sheet + one sheet per direction with the raw records), written to a new reports folder in the project folder.</div></div>
 <div class="mbody"><table class="t"><thead><tr><th style="width:30px"></th><th>Location</th><th>Slices</th><th>In report</th><th>Status</th></tr></thead><tbody>${list.map(({l,r})=>`<tr class="${r?'':'dim'}"><td><input type="checkbox" data-id="${l.id}" ${r?'checked':'disabled'}></td><td><b>${esc(l.code)}</b> ${esc(l.name)}</td><td>${l.slices.map(s=>esc(s.label)).join(', ')}</td><td>${l.in_report.join(' ')}</td><td>${r?'<span class="pill ok">Ready</span>':'<span class="pill warn">Needs input</span>'}</td></tr>`).join('')}</tbody></table></div>
 <div class="mfoot"><span class="note">${list.filter(x=>x.r).length} of ${list.length} ready · locations that need input are left out</span><button class="btn pri" onclick="genBatch()">Generate</button></div>`;openM('gen')}
function genBatch(){const ids=[...document.querySelectorAll('#genmodal input[data-id]:checked')].map(b=>+b.dataset.id);if(!ids.length)return;closeM('gen');runReport(ids)}
function genOne(){runReport([CUR])}
async function runReport(ids){
 spinnerModal('Generate report','','Generating '+(ids.length>1?ids.length+' speed reports':'the speed report'),'Writing the workbook'+(ids.length>1?'s':'')+', then opening '+(ids.length>1?'each one':'it')+' in Excel to calculate and save. Excel stays hidden.');
 const r=await api('POST','/report',{ids});if(!r)return;
 TAB=r.tab;renderTab();
 showModal('',`<div class="wstep"><span>Generate report</span></div><h3>${r.files.length} report${r.files.length===1?'':'s'} written</h3>
 <div class="pathbox" style="margin-bottom:12px">${esc(r.path)}</div>
 <div style="font-size:13px;margin-bottom:14px">${r.files.map(f=>'<b>'+esc(f.code)+'</b> &nbsp;<span class="mono">'+esc(f.file)+'</span>').join('<br>')}</div>
 <div class="banner ${r.excel&&r.excel.done?'info':'warn'}">${esc(r.excel?r.excel.message:'')}</div>
 <div class="actions"><button data-act="open">Open the folder</button><div class="sp"></div><button class="primary" data-act="ok">Close</button></div>`,async act=>{
  if(act==='open'){try{const b=await post('/api/project/reveal',{report_folder:r.folder});if(!b.ok)errorModal('The folder could not be opened',b.error.message)}catch(e){engineDown(e)}return}
  if(act==='ok')closeModal()})}

// ---------- drawer ----------
let drawerOpen=false;const showAll={NB:false,SB:false,EB:false,WB:false};const OPEN={setup:false,ov:false,dec:false,det:false,trail:false};
function allSec(o){for(const k in OPEN)OPEN[k]=o;document.querySelectorAll('#drawer details.sec').forEach(d=>d.open=o)}
async function openDrawer(id){const d=await api('GET','/locations/'+id);if(!d)return;
 if(id!==CUR||!drawerOpen){Object.assign(OPEN,{setup:false,ov:false,dec:false,det:false,trail:false});for(const x in showAll)showAll[x]=false}
 setD(d);drawerOpen=true;document.getElementById('scrim').classList.add('on');document.getElementById('drawer').classList.add('on');renderDrawer();renderTab()}
function closeDrawer(){drawerOpen=false;document.getElementById('scrim').classList.remove('on');document.getElementById('drawer').classList.remove('on');renderTab()}
function step(n){if(!TAB)return;const i=TAB.rows.findIndex(r=>r.id===CUR)+n;if(i<0||i>=TAB.rows.length)return;openDrawer(TAB.rows[i].id)}
/** A drawer save: send the change, draw what the engine answers. A refusal has already been
 *  shown by api(); the drawer is drawn again so the control goes back to what is stored. */
async function save(path,body,method){const d=await api(method||'PATCH','/locations/'+CUR+path,body);
 if(!d){if(D&&drawerOpen)renderDrawer();return false}
 setD(d);renderDrawer();loadTab();return true}
function chev(){return'<svg class="chev" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 4l6 6-6 6"/></svg>'}
function sec(id,src,title,state,sum,body){return`<details class="sec src-${src}" ${OPEN[id]?'open':''} ontoggle="OPEN['${id}']=this.open"><summary>${chev()}<span class="state ${state}">${state==='ok'?'✓':'!'}</span><span class="stitle">${title}</span><span class="ssum">${sum}</span></summary><div class="sbody">${body}</div></details>`}
function renderDrawer(){if(!D||!drawerOpen)return;const loc=L(),s=S(),rdy=ready(),miss=missing(),w=warnings(),i=TAB?TAB.rows.findIndex(r=>r.id===CUR):0,nLoc=TAB?TAB.rows.length:1,win=s.window,mh=maskedTotal();
 const was=document.querySelector('#drawer .dbody'),keep=was&&renderDrawer.id===CUR?was.scrollTop:0;renderDrawer.id=CUR;
 document.getElementById('drawer').innerHTML=`
 <div class="dhead"><div class="dtop"><div class="dtitle"><div class="code">Location ${esc(D.location.code)} · ${loc.slices.map(sliceLabel).join(' + ')} · ${i+1} of ${nLoc}</div><h2>${esc(D.location.name)||'<span style="color:var(--amber)">No name yet: type one in Setup</span>'}</h2></div>
  <div class="dnav"><button class="btn icon" ${i===0?'disabled':''} onclick="step(-1)" title="previous location">◀</button><button class="btn icon" ${i>=nLoc-1?'disabled':''} onclick="step(1)" title="next location">▶</button><button class="btn ${rdy?'green':''}" ${rdy?'':'disabled title="Needs input first"'} onclick="genOne()">Generate report</button><button class="btn icon ghost" onclick="closeDrawer()">✕</button></div></div>
  <div class="dmeta">${rdy?'<span class="pill ok">Ready</span>':'<span class="pill warn">Needs input</span>'}<span class="sep">|</span>${winText(win)}<span class="sep">|</span>${CAMS().length} cam${CAMS().length>1?'s':''} · ${totalEvents().toLocaleString()} events${mh.n?' · '+mh.n+' h masked':''}<span class="sep">|</span>In report: ${incl().join(' ')||'none'}<span class="sep">|</span><span class="srcb qch">QC Hub</span> name, lane configs, count<span class="tools"><button class="btn ghost sm" onclick="allSec(true)">Expand all</button><button class="btn ghost sm" onclick="allSec(false)">Collapse all</button></span></div>
  ${miss.length?`<div class="alert w" style="margin:10px 0 0">${miss.join(' · ')}</div>`:''}
  ${w.length?`<div class="alert i" style="margin:8px 0 0"><span>${w.join(' · ')}</span></div>`:''}</div>
 <div class="dbody">
  <div class="srckey">Sections by source: <span class="srcb qch">QC Hub</span><span class="srcb dl">Datalens</span><span class="srcb app">Added in the speed tool</span></div>
  ${sec('setup','app','Setup',miss.length?'warn':'ok','posted '+incl().filter(d=>s.posted[d]).length+'/'+incl().length+' · corners '+CAMS().filter(k=>corner(k)).length+'/'+CAMS().length+' · '+loc.slices.length+' slice'+(loc.slices.length>1?'s':''),setupBody())}
  ${sec('ov','app','Overview',rdy?'ok':'warn',incl().map(d=>{const f=finalStats(d);return d+' '+(f?p2(f.p85):'—')}).join(' · ')+' mph (85th)',ovBody())}
  ${sec('dec','app','Decisions',rdy?'ok':'warn',incl().map(d=>d+' → '+(s.src[d]?camLabel(s.src[d]).toLowerCase():'—')).join(' · ')+' · histograms, adjust-by, mask',decBody())}
  ${sec('det','app','Detail',mh.over.length?'warn':'ok','hourly 85th percentile per approach · '+D.hourly.hours_with_data+' hours'+(mh.n?' · '+mh.n+' hours masked':''),detBody())}
  ${sec('trail','app','Paper trail','ok',(D.paper_trail||[]).length+' entries',trailBody())}
 </div>
 <div class="dfoot"><span>Every change saves at once to speed_${esc(PROJECT.number)}.db.</span><span class="note">${PROJECT.is_art_project?'nm_'+esc(PROJECT.number)+'.db is never touched.':''}</span></div>`;
 const now=document.querySelector('#drawer .dbody');if(now)now.scrollTop=keep}

// ---------- Setup ----------
/** Not in the mockup: the report cannot be written without a name, and a sitenumber QC Hub
 *  does not have comes in without one. */
function nameBlock(){const l=D.location,manual=l.name_source==='manual';
 return`<h4>Report name <span class="tag edit">editable</span> <span class="srcb qch" style="margin-left:4px">QC Hub</span></h4>
 <div class="namerow"><input class="f ${manual?'ed':''} ${l.name?'':'bad'}" value="${esc(l.name||'')}" placeholder="Main St  &  1st Ave" onchange="setName(this.value)">${manual?`<span class="tag edit" title="QC Hub: ${esc(l.qch_name||'none')}">edited</span>${l.qch_name?' <button class="lnk" onclick="save(\'\',{name_source:\'qch\'})">use the QC Hub name</button>':''}`:''}</div>
 <p class="hint" style="margin-bottom:16px">Printed on the report and used in the file name. From QC Hub when the sitenumber is there; type over it to use your own.</p>`}
function setName(v){v=(v||'').trim();const l=D.location;
 if(!v||v===l.qch_name){if(l.qch_name)save('',{name_source:'qch'});else renderDrawer();return}
 save('',{name_source:'manual',manual_name:v})}
function setupBody(){const s=S(),loc=L(),r=R(),days=countDays();
 const axis=ax=>{const [a,b]=AXES[ax];const same=s.same[ax];const anyIn=s.include[a]||s.include[b];
  const fld=(d,lab)=>`<div class="fld ${s.include[d]?'':'off'}"><b>${lab}</b><input class="f num ${s.include[d]&&!s.posted[d]?'bad':''}" type="number" min="5" max="80" step="5" value="${s.posted[d]??''}" placeholder="mph" ${s.include[d]?'':'disabled'} onchange="setPosted('${d}',this.value)"> mph</div>`;
  return`<div class="axis"><h5>${ax==='NS'?'North / South':'East / West'} axis</h5>
   <div class="inc">${[a,b].map(d=>`<label><input type="checkbox" ${s.include[d]?'checked':''} onchange="setInclude('${d}',this.checked)"> ${d} in report${s.include[d]!==hasLeg(d)?` <span class="tag warn" title="QC Hub lane config says ${hasLeg(d)?'this approach has lanes':'no lanes on this approach'}">override</span>`:''}${hasLeg(d)?'':' <span class="tag ro">no leg</span>'}</label>`).join('')}</div>
   <label class="same"><input type="checkbox" ${same?'checked':''} onchange="setSame('${ax}',this.checked)"> Same both ways</label>
   <div class="flds">${same?`<div class="fld ${anyIn?'':'off'}"><b>${a} &amp; ${b}</b><input class="f num ${anyIn&&!(s.posted[a]||s.posted[b])?'bad':''}" type="number" min="5" max="80" step="5" value="${s.posted[a]??s.posted[b]??''}" placeholder="mph" ${anyIn?'':'disabled'} onchange="setPostedAxis('${ax}',this.value)"> mph</div>`:fld(a,a)+fld(b,b)}</div></div>`};
 return`<div class="setup">
 <div class="sub">${nameBlock()}<h4>Posted speeds <span class="tag edit">editable</span></h4><div class="posted">${axis('NS')}${axis('EW')}</div>
  <p class="hint">"Same both ways" is ticked by default: one field, written to both directions. Untick for a divided road or school zone that posts differently by direction. "In report" starts ticked for every approach QC Hub gives a lane.</p>
  <h4 style="margin-top:16px">Lane configs <span class="tag edit">editable</span> <span class="srcb qch" style="margin-left:4px">QC Hub</span></h4>
  <div class="lanes">${DIRS.map(d=>`<div class="ln"><b>${d}${r.cfg[d]!==r.cfgQch[d]?' <span class="tag edit" title="QC Hub: '+(r.cfgQch[d]||'none')+'">edited</span>':''}</b><input class="f ${r.cfg[d]!==r.cfgQch[d]?'ed':''}" value="${r.cfg[d]||''}" placeholder="0000000" maxlength="7" onchange="setCfg('${d}',this.value)"></div>`).join('')}</div>
  <p class="hint">One digit per lane, from the kerb: 0 none · 1 R · 2 T · 3 TR · 4 L · 5 LR · 6 LT · 7 LTR. Edits write to the speed db only (never the near miss db), are logged, and re-run the "approach exists" check: a new approach appears above with "in report" ticked.</p>
  <h4 style="margin-top:16px">Count window <span class="tag edit">editable</span> <span class="tag ro">${s.window.src}</span></h4>
  <div class="win"><div class="fld">Start date<input class="f" type="date" value="${s.window.start||''}" onchange="setWin('start',this.value)"></div><div class="fld">Start time<input class="f" value="${s.window.startTime||''}" style="width:92px" onchange="setWin('startTime',this.value)"></div><div class="fld">Duration (h)<input class="f num" type="number" min="1" max="744" value="${s.window.hours??''}" onchange="setWin('hours',+this.value)"></div></div>
  <p class="hint">From QC Hub when the sitenumber is there, else the file header. Report metadata only — statistics always use the rows' own timestamps; a window that disagrees with the data raises an advisory.</p>
  <h4 style="margin-top:16px">Report note <span class="tag edit">optional</span></h4><textarea class="f" placeholder="Printed under the approach summary, e.g. queueing that explains a low 85th, or a masked period worth mentioning" onchange="setNote(this.value)">${esc(s.note)}</textarea></div>
 <div class="sub"><h4>Camera corners <span class="tag edit">editable</span> <span class="srcb dl" style="margin-left:4px">files from Datalens</span></h4>
  ${CAMS().map(k=>{const c=corner(k),dead=camDead(k),ci=camInfo(k),msk=(RAW[camSite(k)].camMask[camNo(k)]||[]).length;return`<div class="camrow ${dead?'dead':''}"><div><span class="camdot" style="background:${camCol(k)}"></span><b>${camLabel(k)}</b></div><div class="cf"><b>${ci.events.toLocaleString()}</b> events · ${ci.form==='post-macro'?'<span class="tag warn" title="file header says Duration 0 and uses US dates: post-macro form, duration not checked">header: post-macro form</span>':'<span class="tag ok">header ok</span>'}${dead?' <span class="tag warn">~100% zero speeds</span>':''}${msk?' <span class="tag warn">'+msk+' h time-masked</span>':''}<br>${camFiles(k)}${c?'<br>'+(DIRS.filter(dirExists).map(d=>{const kk=role(k,d);return kk?`<span class="tag ${kk==='Primary'?'pri':'sec'}">${d} ${kk.toLowerCase()}</span>`:''}).join(' ')||'<span class="tag ro">off-axis for every approach</span>'):CAMS().length>1?(dead?'<br><span class="tag ro">dead cam · corner optional</span>':'<br><span class="tag block">corner needed</span>'):'<br><span class="tag ro">single cam · corner optional</span>'}</div>
   <div class="corner">${['NW','NE','SW','SE'].map(q=>`<span class="${c===q?'on':''}" onclick="setCorner('${k}','${q}')">${q}${(c===q?CAMS().filter(o=>o!==k&&corner(o)===q):[]).map(o=>`<i style="background:${camCol(o)}" title="${camLabel(o)} is on this corner too">${camNo(o)}</i>`).join('')}</span>`).join('')}</div></div>`}).join('')}
  ${sites().length>1?`<p class="hint" style="margin-top:10px">This location groups ${sites().length} sitenumbers: each cam number is one camera across all of them, with one corner, one source-cam choice and statistics over the whole count.</p>`:`<div class="maskrow" style="margin-top:10px"><label><input type="checkbox" ${s.linked?'checked':''} onchange="setLinked(this.checked)"> Corners linked across this location's sitenumbers</label></div>`}
  <p class="hint">Cams, corners and time masks belong to the sitenumber and are shared by every location built on it. With one cam the corner is optional; with several, every live cam needs one before the source-cam suggestion runs.</p>
  <h4 style="margin-top:16px">Slices <span class="tag edit">editable</span></h4>
  <div class="slices">${loc.slices.map(sl=>`<span class="slice"><b>${RAW[sl.site].sitenumber}</b>${sl.date?dl(sl.date):'all days'}${sl.from!==undefined?' · '+hh2(sl.from)+'–'+hh2(sl.to):''}</span>`).join('')}
   ${sites().length===1?`<button class="btn sm" ${days.length>1&&!loc.slices[0].date?'':'disabled'} onclick="splitByDay()">Split by day</button><button class="btn sm" ${loc.slices[0].from===undefined?'':'disabled'} onclick="openSplitTime()">Split by time…</button>${isSliced(loc)?`<button class="btn sm" onclick="unsplit(CUR)">Unsplit</button>`:''}`:`<button class="btn sm" onclick="ungroup(CUR)">Ungroup</button>`}</div>
  <p class="hint">${days.length>1?days.length+' count days in this location — "Split by day" makes one report per day.':'Single count day.'} Decisions are per location and are copied to the new locations when you split; the raw rows, cams, corners and time masks stay on the sitenumber.</p></div></div>`}
function setInclude(d,on){save('/directions/'+d,{include_in_report:on})}
function setSame(ax,on){save('',{posted_same:{[ax]:on}})}
// While "Same both ways" is ticked the engine writes the one value to both directions.
function setPostedAxis(ax,v){save('/directions/'+AXES[ax][0],{posted:v===''?null:+v})}
function setPosted(d,v){save('/directions/'+d,{posted:v===''?null:+v})}
function setCfg(d,v){v=(v||'').replace(/[^0-7]/g,'').slice(0,7);if(v===R().cfg[d]){renderDrawer();return}save('',{lane_config:{[d]:v}})}
function setWin(k,v){save('',{window:{[{start:'start_date',startTime:'start_time',hours:'duration_hours'}[k]]:v}})}
function setNote(v){save('',{note:v})}
function setLinked(on){save('',{corners_linked:on})}
function setCorner(k,q){save('/cams/'+enc(k),{corner:corner(k)===q?null:q})}

// ---------- Overview ----------
function useSuggestions(){save('/use-suggestions',undefined,'POST')}
function ovBody(){return`<div class="gridov"><div><div class="sdiag">${speedSVG()}</div><p class="hint">Lanes and movements from the lane configs in Setup; one card per approach included in the report, with what the report prints. Cam markers sit on their corners; a red-edged card has no source cam yet.${(()=>{const ol=offLeg(),k=Object.keys(ol);return k.length?` <b style="color:var(--amber)">${k.map(d=>ol[d]+' '+d).join(', ')} events are in approaches not included in the report.</b>`:''})()}</p></div>
 <div><h4 style="margin:0 0 6px;font-size:13px;color:var(--ink2);display:flex;gap:8px;align-items:center">All cams × directions <span class="tag edit">click a cell to make it the source</span><span style="margin-left:auto">${cornersOk()?`<button class="btn sm" onclick="useSuggestions()">Use suggestions</button>`:'<span class="tag warn">set every corner to get suggestions</span>'}</span></h4><div class="mxwrap">${matrix()}</div>
 <div class="mxkey"><span><i style="background:#EAF4EC"></i>primary corner</span><span><i style="background:#F3F6E8"></i>secondary corner</span><span><i style="box-shadow:inset 0 0 0 2px var(--road)"></i>source cam</span><span><i style="border:2px dashed var(--green)"></i>suggested</span><span>avg and 85th ignore zero speeds and time-masked hours · raw, before adjustment and speed mask</span></div>
 <div style="margin-top:14px">${repTable()}</div></div></div>`}
function matrix(){const s=S(),ds=incl();
 return`<table class="mx"><thead><tr><th class="l">Cam</th>${ds.map(d=>`<th>${d}</th>`).join('')}</tr></thead><tbody>
 ${CAMS().map(k=>{const dead=camDead(k);return`<tr><th class="l"><span class="camdot" style="background:${camCol(k)}"></span>${camLabel(k)}<div style="font-weight:400">${corner(k)||'no corner'}${dead?'<br><span class="tag warn">dead</span>':''}</div></th>${ds.map(d=>{const st=rawStats(k,d),r=role(k,d),isSrc=s.src[d]===k;const sg=suggest(d),isSug=sg&&sg.cam===k;
  if(!st.n)return`<td class="dim" style="color:var(--mute);cursor:default">no events</td>`;
  return`<td class="${r==='Primary'?'pri':r==='Secondary'?'sec':''} ${isSrc?'src':''} ${isSug?'sug':''}" style="${dead&&!isSrc?'opacity:.5':''}" onclick="setSrc('${d}','${k}')" title="${r||'off-axis corner'}${isSrc?' · source':''}${isSug?' · suggested: '+sg.why:''}"><div class="big">${st.nz?p2(st.p85):'—'} <small>85th</small></div><div class="r">avg ${st.nz?f1(st.avg):'—'} · n ${st.n.toLocaleString()}</div><div class="r">${r?r.toLowerCase()+' · ':''}<span class="${st.pz>.05?'z':''}">${pct(st.pz)} zero</span></div>${isSug?`<div class="r" style="margin-top:3px"><span class="tag ok" style="font-size:10px;white-space:normal">suggested · ${sg.why}</span></div>`:''}</td>`}).join('')}</tr>`}).join('')}
 <tr><th class="l">Source</th>${ds.map(d=>{const f=finalStats(d),sg=suggest(d);return`<th>${f?`${camLabel(f.src).toLowerCase()} → <b style="color:var(--ink)">${p2(f.p85)}</b>${f.mod?` <span class="tag warn">adj ${f.mod>0?'+':''}${f.mod}</span>`:''}${sg&&sg.cam!==f.src?`<br><span class="tag warn" title="suggested ${camLabel(sg.cam)}: ${sg.why}">differs from suggestion</span>`:''}`:'<span class="tag warn">none</span>'}</th>`}).join('')}</tr></tbody></table>`}
const L4=['N','E','S','W'],APPR={N:'SB',S:'NB',E:'WB',W:'EB'},OUTL={N:'EB',S:'WB',E:'SB',W:'NB'},OUTT={N:'NB',S:'SB',E:'EB',W:'WB'},OUTR={N:'WB',S:'EB',E:'NB',W:'SB'};
const DMV={1:'R',2:'T',3:'TR',4:'L',5:'LR',6:'LT',7:'LTR',8:'U'},GLY={T:'M12 22 L12 7',L:'M12 22 L12 14 Q12 9 7 9 L5 9',R:'M12 22 L12 14 Q12 9 17 9 L19 9',U:'M12 22 L12 11 Q12 5 7 5 Q3 5 3 11 L3 14'};
function geom(cfg){const has=(c,set)=>[...(c||'')].some(ch=>set.includes(ch)),g={};for(const Lg of L4){const a=cfg[APPR[Lg]]||'';
 const ex=[has(a,'1234567'),has(cfg[OUTL[Lg]],'4567'),has(cfg[OUTT[Lg]],'2367'),has(cfg[OUTR[Lg]],'1357')].some(Boolean);
 g[Lg]={exists:ex,inL:[...a].filter(ch=>'1234567'.includes(ch)),out:ex?Math.max(1,[...(cfg[OUTT[Lg]]||'')].filter(ch=>'2367'.includes(ch)).length):0}}return g}
function laneGlyph(x,y,rot,d){return `<g transform="translate(${x},${y}) rotate(${rot}) scale(.8) translate(-12,-12)">`+[...DMV[d]].map(k=>`<path d="${GLY[k]}" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round" marker-end="url(#gw)"/>`).join('')+'</g>'}
function lanesSVG(W,H,cx,cy,lw){const g=geom(cfg()),road='#A7B1BB',n=g.N,so=g.S,e=g.E,w=g.W;
 const west=Math.max(n.exists?n.inL.length:0,so.exists?so.out:0,1)*lw,east=Math.max(n.exists?n.out:0,so.exists?so.inL.length:0,1)*lw,north=Math.max(e.exists?e.inL.length:0,w.exists?w.out:0,1)*lw,south=Math.max(e.exists?e.out:0,w.exists?w.inL.length:0,1)*lw;
 const bx0=cx-west,bx1=cx+east,by0=cy-north,by1=cy+south,xo=Lg=>g[Lg].exists&&!g[Lg].inL.length;
 let o='';const ln=(x1,y1,x2,y2,c,dash)=>`<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${c}" stroke-width="${c==='#F2C94C'?2:1.2}" ${dash?'stroke-dasharray="7 7"':''}/>`;let marks='';
 if(xo('N')){o+=`<rect x="${cx-lw}" y="0" width="${2*lw}" height="${by0}" fill="${road}"/>`;marks+=laneGlyph(cx,by0/2,0,2)}
 else if(n.exists){const a=n.inL.length,b=n.out;o+=`<rect x="${cx-a*lw}" y="0" width="${(a+b)*lw}" height="${by0}" fill="${road}"/>`;if(a&&b)marks+=ln(cx,0,cx,by0,'#F2C94C');for(let i=1;i<a;i++)marks+=ln(cx-i*lw,0,cx-i*lw,by0-40,'#fff',1);for(let i=1;i<b;i++)marks+=ln(cx+i*lw,0,cx+i*lw,by0,'#fff',1);n.inL.forEach((d,i)=>marks+=laneGlyph(cx-a*lw+lw*(i+.5),by0-20,180,d))}
 if(xo('S')){o+=`<rect x="${cx-lw}" y="${by1}" width="${2*lw}" height="${H-by1}" fill="${road}"/>`;marks+=laneGlyph(cx,(by1+H)/2,180,2)}
 else if(so.exists){const a=so.inL.length,b=so.out;o+=`<rect x="${cx-b*lw}" y="${by1}" width="${(a+b)*lw}" height="${H-by1}" fill="${road}"/>`;if(a&&b)marks+=ln(cx,by1,cx,H,'#F2C94C');for(let i=1;i<a;i++)marks+=ln(cx+i*lw,by1+40,cx+i*lw,H,'#fff',1);for(let i=1;i<b;i++)marks+=ln(cx-i*lw,by1,cx-i*lw,H,'#fff',1);so.inL.forEach((d,i)=>marks+=laneGlyph(cx+a*lw-lw*(i+.5),by1+20,0,d))}
 if(xo('E')){o+=`<rect x="${bx1}" y="${cy-lw}" width="${W-bx1}" height="${2*lw}" fill="${road}"/>`;marks+=laneGlyph((bx1+W)/2,cy,90,2)}
 else if(e.exists){const a=e.inL.length,b=e.out;o+=`<rect x="${bx1}" y="${cy-a*lw}" width="${W-bx1}" height="${(a+b)*lw}" fill="${road}"/>`;if(a&&b)marks+=ln(bx1,cy,W,cy,'#F2C94C');for(let i=1;i<a;i++)marks+=ln(bx1+40,cy-i*lw,W,cy-i*lw,'#fff',1);for(let i=1;i<b;i++)marks+=ln(bx1,cy+i*lw,W,cy+i*lw,'#fff',1);e.inL.forEach((d,i)=>marks+=laneGlyph(bx1+20,cy-a*lw+lw*(i+.5),-90,d))}
 if(xo('W')){o+=`<rect x="0" y="${cy-lw}" width="${bx0}" height="${2*lw}" fill="${road}"/>`;marks+=laneGlyph(bx0/2,cy,-90,2)}
 else if(w.exists){const a=w.inL.length,b=w.out;o+=`<rect x="0" y="${cy-b*lw}" width="${bx0}" height="${(a+b)*lw}" fill="${road}"/>`;if(a&&b)marks+=ln(0,cy,bx0,cy,'#F2C94C');for(let i=1;i<a;i++)marks+=ln(0,cy+i*lw,bx0-40,cy+i*lw,'#fff',1);for(let i=1;i<b;i++)marks+=ln(0,cy-i*lw,bx0,cy-i*lw,'#fff',1);w.inL.forEach((d,i)=>marks+=laneGlyph(bx0-20,cy+a*lw-lw*(i+.5),90,d))}
 o+=`<rect x="${bx0}" y="${by0}" width="${bx1-bx0}" height="${by1-by0}" fill="${road}"/>`+marks;return{svg:o,box:[bx0,by0,bx1,by1],g}}
function speedSVG(){const s=S(),W=560,H=560,c=280;const lanes=lanesSVG(W,H,c,c,18),[bx0,by0,bx1,by1]=lanes.box;const cornerXY={NW:[bx0-13,by0-13],NE:[bx1+13,by0-13],SW:[bx0-13,by1+13],SE:[bx1+13,by1+13]};
 let g=`<svg viewBox="0 0 ${W} ${H}" width="100%" font-family="system-ui,-apple-system,Segoe UI,sans-serif"><defs><marker id="gw" markerUnits="userSpaceOnUse" markerWidth="7" markerHeight="7" refX="3" refY="3.5" orient="auto"><path d="M0 0 L7 3.5 L0 7 Z" fill="#fff"/></marker></defs>`+lanes.svg;
 const sn0=D.location.sitenumbers[0],ns=esc(sn0.street_ns||''),ew=esc(sn0.street_ew||'');const lg=lanes.g;
 if(lg.N.exists)g+=`<text x="${c}" y="${H-4}" text-anchor="middle" font-size="10.5" fill="#4A5868" font-weight="600">${ns}</text>`;
 if(lg.W.exists)g+=`<text x="8" y="${c-lg.W.out*18-5}" font-size="10.5" fill="#4A5868" font-weight="600">${ew||''}</text>`;else if(lg.E.exists)g+=`<text x="${W-8}" y="${c-lg.E.inL.length*18-5}" text-anchor="end" font-size="10.5" fill="#4A5868" font-weight="600">${ew||''}</text>`;
 const placed={};for(const k of CAMS()){const kc=corner(k);if(!kc)continue;const [x,y]=cornerXY[kc];const off=(placed[kc]||0)*22;placed[kc]=(placed[kc]||0)+1;g+=`<g transform="translate(${x+off},${y})"><rect x="-10" y="-10" width="20" height="20" rx="5" fill="${camCol(k)}" stroke="#fff" stroke-width="1.5"/><text x="0" y="4" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">${camNo(k)}</text></g>`}
 for(const kc in cornerXY){const [x,y]=cornerXY[kc];if(!placed[kc])g+=`<text x="${x}" y="${y+4}" font-size="10" fill="#fff" text-anchor="middle" opacity=".8">${kc}</text>`}
 const box={SB:[6,6],WB:[W-166,6],EB:[6,H-166],NB:[W-166,H-166]};
 for(const d of DIRS){if(!dirExists(d))continue;const f=finalStats(d),[x,y]=box[d],posted=s.posted[d];const col=f?camCol(f.src):'#B7C2CE';
  g+=`<g transform="translate(${x},${y})"><rect width="160" height="160" rx="10" fill="#fff" stroke="${f?'#DCE2E8':'#E5B4AF'}"/><rect x="0" y="0" width="160" height="30" rx="10" fill="${f?'#F6F8FA':'#FBE8E6'}"/><rect x="0" y="20" width="160" height="10" fill="${f?'#F6F8FA':'#FBE8E6'}"/><line x1="0" y1="30" x2="160" y2="30" stroke="#EBEFF3"/>`;
  g+=`<text x="12" y="20" font-size="15" font-weight="700" fill="#1F2A37">${d}</text>`;
  if(f){g+=`<rect x="44" y="9" width="12" height="12" rx="3" fill="${col}"/><text x="60" y="20" font-size="11.5" fill="#4A5868">${camLabel(f.src).toLowerCase()} · ${corner(f.src)||'?'}${role(f.src,d)==='Primary'?' ✓':''}</text>`;
   const big=p2(f.p85);g+=`<text x="12" y="76" font-size="${big.length>3?30:38}" font-weight="700" fill="#1F2A37">${big}</text><text x="${12+big.length*(big.length>3?17:22)}" y="76" font-size="11" fill="#7A8796">mph 85th</text>`;
   g+=`<text x="12" y="98" font-size="12" fill="#4A5868">avg ${f1(f.avg)} · n ${f.n.toLocaleString()}${f.maskedHours?' · '+f.maskedHours+' h masked':''}</text>`;
   if(posted){g+=`<rect x="12" y="110" width="30" height="24" rx="3" fill="#fff" stroke="#1F2A37" stroke-width="1.5"/><text x="27" y="127" font-size="13" font-weight="700" fill="#1F2A37" text-anchor="middle">${posted}</text><text x="50" y="126" font-size="12" fill="${f.pover>.15?'#B4352C':'#4A5868'}" font-weight="${f.pover>.15?700:400}">${pct(f.pover)} over posted</text>`}
   else g+=`<rect x="12" y="110" width="136" height="24" rx="4" fill="#FFF3D6"/><text x="80" y="126" font-size="11.5" fill="#A86F00" font-weight="600" text-anchor="middle">posted speed missing</text>`;
   const tags=[];if(f.mod)tags.push('adjusted '+(f.mod>0?'+':'')+f.mod);const m=s.mask[d];if(m.lo!==null||m.hi!==null)tags.push('mask '+(m.lo!==null?'<'+m.lo:'')+(m.lo!==null&&m.hi!==null?' ':'')+(m.hi!==null?'>'+m.hi:'')+' · '+f.masked+' out');
   g+=`<text x="12" y="150" font-size="11" fill="${tags.length?'#A86F00':'#9AA6B2'}">${tags.length?tags.join(' · '):'no adjustment, no mask'}</text>`}
  else g+=`<text x="12" y="76" font-size="13" fill="#B4352C" font-weight="600">No source cam</text><text x="12" y="98" font-size="11.5" fill="#7A8796">pick one in the matrix or below</text>`;
  g+=`</g>`}
 return g+'</svg>'}
function repTable(){const s=S();return`<table class="t"><thead><tr><th>Dir</th><th>Source</th><th class="n">Adj</th><th class="n">Events</th><th class="n">Zero</th><th class="n">Masked</th><th class="n">n</th><th class="n">Avg</th><th class="n">85th</th><th class="n">Posted</th><th class="n">% over</th></tr></thead><tbody>
 ${incl().map(d=>{const fs=finalStats(d);if(!fs)return`<tr class="dim"><td>${d}</td><td colspan="10">no source cam</td></tr>`;return`<tr><td><b>${d}</b></td><td><span class="camdot" style="background:${camCol(fs.src)}"></span>${camLabel(fs.src).toLowerCase()} (${corner(fs.src)||'?'})${fs.maskedHours?' <span class="tag warn">'+fs.maskedHours+' h</span>':''}</td><td class="n">${fs.mod>0?'+':''}${fs.mod}</td><td class="n">${fs.nRaw.toLocaleString()}</td><td class="n">${fs.zero.toLocaleString()}</td><td class="n">${fs.masked||'—'}</td><td class="n">${fs.n.toLocaleString()}</td><td class="n">${f1(fs.avg)}</td><td class="n"><b>${p2(fs.p85)}</b>${Number.isInteger(fs.p85)?'':` <span style="color:var(--mute);font-size:11px">${pU(fs.p85)}</span>`}</td><td class="n">${s.posted[d]??'—'}</td><td class="n">${fs.pover!==null?pct(fs.pover):'—'}</td></tr>`}).join('')}</tbody></table>
 <p class="hint">Read-only report values. Time-masked hours are removed first, then zeros, then the speed mask and the adjustment. 85th = PERCENTILE.INC printed as whole mph (unrounded in grey); average to one decimal; "over posted" is strictly greater than posted on the masked, adjusted speeds.${s.note?` Report note: <i>${esc(s.note)}</i>`:''}</p>`}

// ---------- Decisions ----------
function decBody(){return`<div class="sub"><h4>Per direction: source cam histogram, adjust-by and mask <span class="tag edit">editable</span></h4>${incl().map(dirBlock).join('')}</div>`}
function dirBlock(d){const s=S(),fs=finalStats(d),mod=modifier(d),sg=suggest(d);
 const rows=CAMS().map(k=>{const st=rawStats(k,d),r=role(k,d),isSrc=s.src[d]===k,dead=camDead(k);
  return`<tr class="${isSrc?'src':''} ${dead?'dim':''}"><td><input type="radio" name="src-${d}" ${isSrc?'checked':''} onchange="setSrc('${d}','${k}')"></td><td><span class="camdot" style="background:${camCol(k)}"></span>${camLabel(k)} <span class="hint" style="display:inline;margin:0">${corner(k)||'—'}</span>${dead?' <span class="tag warn">dead</span>':''}${sg&&sg.cam===k?' <span class="tag ok">suggested</span>':''}</td><td>${r?`<span class="tag ${r==='Primary'?'pri':'sec'}">${r}</span>`:'<span class="tag ro">—</span>'}</td><td class="n">${st.n.toLocaleString()}</td><td class="n" style="${st.pz>.05?'color:var(--amber);font-weight:600':''}">${pct(st.pz)}</td><td class="n">${st.nz?f1(st.avg):'—'}</td><td class="n">${st.nz?p2(st.p85):'—'}</td></tr>`}).join('');
 const src=s.src[d];
 return`<div class="dirblock"><div class="dirhead"><h5>${d}</h5><span class="meta">theoretical: ${PRIMARY[d][0]} primary, ${PRIMARY[d][1]} secondary</span><span class="sp"></span>
  ${fs?`<span class="meta">result: <b style="color:var(--ink)">85th ${p2(fs.p85)} mph</b> · avg ${f1(fs.avg)} · n ${fs.n.toLocaleString()}${s.posted[d]?' · '+pct(fs.pover)+' over '+s.posted[d]:''}${fs.maskedHours?' · '+fs.maskedHours+' h time-masked':''}</span>`:'<span class="pill warn">no source cam</span>'}</div>
 <div class="dirbody">
  <table class="t"><thead><tr><th style="width:30px">Source</th><th>Cam</th><th>Role</th><th class="n">Events</th><th class="n">% zero</th><th class="n">Avg (no zeros)</th><th class="n">85th</th></tr></thead><tbody>${rows}</tbody></table>
  <div class="charts">${src?chart(src,d):''}${showAll[d]?CAMS().filter(k=>k!==src).map(k=>chart(k,d)).join(''):''}</div>
  ${CAMS().length>1?`<div style="margin-top:6px"><button class="lnk" onclick="showAll['${d}']=!showAll['${d}'];renderDrawer()">${showAll[d]?'Hide the other cams':'Show the other '+(CAMS().length-1)+' cam'+(CAMS().length>2?'s':'')+' for '+d}</button></div>`:''}
  <div class="decision" style="margin-top:12px">
   <div class="dbox"><h6>Adjust by (whole mph, applied after the mask)</h6><div class="modline"><input class="f num ${mod?'ed':''}" type="number" step="1" value="${mod}" ${src?'':'disabled'} onchange="setAdjust('${d}',+this.value,'typed')"> mph${mod?` <span class="tag warn">adjusted</span>`:''}</div>
    <div class="hint">Added to every kept speed of the source cam. Logged with who set it.</div></div>
   <div class="dbox"><h6>Mask (raw speed, before the adjustment)</h6>${src?maskCtl(d):'<span class="hint">pick a source cam first</span>'}</div>
  </div></div></div>`}
const DLI='<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2v8M4.5 7 8 10.5 11.5 7M3 13h10"/></svg>';
// The bin downloads are written by the engine from speed_######.db (GET /speed/export).
function download(url){const a=document.createElement('a');a.href=url;a.download='';document.body.appendChild(a);a.click();a.remove()}
function dlBand(k,d,b0,n){download(`/speed/export/${camSite(k)}/${camNo(k)}/${d}?bin=${b0}-${b0+9}&location=${CUR}`)}
function dlAbove(k,d,hi,n){download(`/speed/export/${camSite(k)}/${camNo(k)}/${d}?above=${hi}&location=${CUR}`)}
function chart(k,d){const s=S(),o=obs(k,d),m=s.mask[d],isSrc=s.src[d]===k,W=600,H=156,Lx=22,B=24;const vals=Object.keys(o.h).map(Number);const maxX=Math.max(40,...vals,(s.posted[d]||0)+5);const max=Math.max(1,...Object.values(o.h));
 const bw=(W-Lx-4)/(maxX+1);const x=v=>Lx+v*bw;const y=n=>H-B-(n/max)*(H-B-8);
 let bars='',dots='';for(const v of vals){const n=o.h[v];const dead=isSrc&&((m.lo!==null&&v<m.lo)||(m.hi!==null&&v>m.hi));bars+=`<rect x="${x(v)}" y="${y(n)}" width="${Math.max(bw-1,1)}" height="${H-B-y(n)}" fill="${dead?'#D7DDE3':camCol(k)}" opacity="${isSrc?1:.55}"><title>${v} mph · ${n}</title></rect>`;dots+=`<circle cx="${x(v)+bw/2}" cy="${H-B+5}" r="1.8" fill="${dead?'#B9C3CD':camCol(k)}"><title>${v} mph · ${n} vehicle${n>1?'s':''}</title></circle>`}
 const st=rawStats(k,d);const posted=s.posted[d];
 const mk=(v,col,lab,ty=12)=>`<line x1="${x(v)+bw/2}" x2="${x(v)+bw/2}" y1="6" y2="${H-B}" stroke="${col}" stroke-dasharray="3 2"/><text x="${x(v)+bw/2+3}" y="${ty}" font-size="9" fill="${col}">${lab}</text>`;
 const step=maxX>60?20:10;let ticks='';for(let v=0;v<maxX-4;v+=step)ticks+=`<text x="${x(v)+bw/2}" y="${H-4}" font-size="9" text-anchor="middle" fill="#7A8796">${v}</text>`;
 const maskband=isSrc&&(m.lo!==null||m.hi!==null)?`<rect x="${m.lo!==null?x(m.lo):Lx}" y="6" width="${(m.hi!==null?x(m.hi+1):x(maxX+1))-(m.lo!==null?x(m.lo):Lx)}" height="${H-B-6}" fill="none" stroke="var(--road)" stroke-width="1" stroke-dasharray="2 2"/>`:'';
 const mx=vals.length?Math.max(...vals):0,mn=vals.length?Math.min(...vals):0;let bands='';for(let b0=Math.floor(mn/10)*10;b0<=mx;b0+=10){let n=0;for(const v of vals)if(v>=b0&&v<b0+10)n+=o.h[v];const out=isSrc&&m.hi!==null&&b0>m.hi;bands+=`<div class="band ${out?'out':''}${n&&b0>=50?' hot':''}"><span>${b0}–${b0+9}</span><b>${n.toLocaleString()}</b>${n?`<button class="dl" title="Download the ${n.toLocaleString()} vehicles in ${b0}–${b0+9} mph as CSV" onclick="dlBand('${k}','${d}',${b0},${n})">${DLI}</button>`:'<span class="dl ph"></span>'}</div>`}
 return`<div class="chart ${isSrc?'src':''}"><div class="ch"><span class="camdot" style="background:${camCol(k)}"></span><b>${camLabel(k)}</b> ${d}${isSrc?' <span class="tag edit">source</span>':''}<span class="sp">${st.n.toLocaleString()} events · ${o.zero.toLocaleString()} zero${o.maskedHours?' · '+o.maskedHours+' h time-masked':''}</span></div>
 <div class="chartrow"><div class="plot"><svg viewBox="0 0 ${W} ${H}">${bars}${st.nz?mk(st.p85,'#2E7D4F','85th '+pU(st.p85)):''}${posted?mk(posted,'#B4352C','posted '+posted,23):''}${maskband}<line x1="${Lx}" x2="${W}" y1="${H-B}" y2="${H-B}" stroke="#DCE2E8"/>${dots}${ticks}<text x="${W-2}" y="${H-4}" font-size="9" text-anchor="end" fill="#7A8796">mph</text></svg>
 ${isSrc?slider(d,maxX):''}</div>
 <div class="tail"><div class="mx"><span>max</span><b>${mx}</b> mph</div>
 ${(()=>{const hiOn=isSrc&&m.hi!==null;const nAbove=hiOn?vals.filter(v=>v>m.hi).reduce((a,v)=>a+o.h[v],0):0;return`<div class="above ${hiOn?'':'off'}">${hiOn?`<span>above upper mask (${m.hi} mph)</span><b>${nAbove.toLocaleString()}</b><button class="dl" title="Download the ${nAbove.toLocaleString()} vehicles above ${m.hi} mph" onclick="dlAbove('${k}','${d}',${m.hi},${nAbove})">${DLI}</button>`:`<span>above upper mask</span><b>—</b><span class="dl ph2" style="flex:none" title="Set an upper mask with the right-hand slider handle to enable">${DLI}</span>`}</div>`})()}<div class="bands">${bands}</div><div class="hint" style="margin:4px 0 0;font-size:11px">vehicles per 10 mph, before the speed mask · a dot under the axis with no bar above it is a lone speed</div></div></div></div>`}
/** The two mask handles. Dragging moves the handle and the green bar only; letting go sends
 *  the mask to the engine, which answers the new numbers. */
function slider(d,maxX){const m=S().mask[d],lo=m.lo??0,hi=m.hi??maxX;
 return`<div class="rng"><div class="track"></div><div class="fill" style="left:${lo/maxX*100}%;width:${(hi-lo)/maxX*100}%"></div>
 <input type="range" min="0" max="${maxX}" step="1" value="${lo}" oninput="slide(this,'${d}',${maxX},'lo')" onchange="setMask('${d}')">
 <input type="range" min="0" max="${maxX}" step="1" value="${hi}" oninput="slide(this,'${d}',${maxX},'hi')" onchange="setMask('${d}')"></div>`}
function slide(inp,d,maxX,which){const m=S().mask[d];
 if(which==='lo'){const v=Math.min(+inp.value,(m.hi??maxX)-1);inp.value=v;m.lo=v>0?v:null}
 else{const v=Math.max(+inp.value,(m.lo??0)+1);inp.value=v;m.hi=v>=maxX?null:v}
 const lo=m.lo??0,hi=m.hi??maxX,fill=inp.parentNode.querySelector('.fill');fill.style.left=lo/maxX*100+'%';fill.style.width=(hi-lo)/maxX*100+'%'}
function setMask(d){const m=S().mask[d];save('/directions/'+d,{mask:{low:m.lo,high:m.hi}})}
function resetMask(d){save('/directions/'+d,{mask:{low:5,high:null}})}
function maskCtl(d){const m=S().mask[d],fs=finalStats(d);const on=m.lo!==null||m.hi!==null;const isDefault=m.lo===5&&m.hi===null;
 return`<div class="maskrow"><span>Masking <b>${m.lo!==null?'below '+m.lo+' mph':'nothing low'}</b> · <b>${m.hi!==null?'above '+m.hi+' mph':'nothing high'}</b></span>${isDefault?'<span class="tag ro">default</span>':`<button class="lnk" onclick="resetMask('${d}')">reset to default</button>`}</div>
 <div class="hint">% zero ${pct(fs.zero/fs.nRaw)} (always shown)${on?` · <b style="color:var(--ink)">% masked ${pct(fs.masked/(fs.nRaw-fs.zero))}</b> (${fs.masked.toLocaleString()} of ${(fs.nRaw-fs.zero).toLocaleString()} non-zero speeds)`:' · no mask applied'}. Set with the two handles under the histogram; the lower handle starts at 5.</div>`}
function setSrc(d,k){if(S().src[d]===k)return;save('/directions/'+d,{source_cam:k})}
function setAdjust(d,v){save('/directions/'+d,{adjust_by:v||0})}

// ---------- Detail + time mask ----------
function heat(t){t=Math.max(0,Math.min(1,t));const a=[255,255,255],b=[214,86,74];const c=a.map((x,i)=>Math.round(x+(b[i]-x)*t));return`rgb(${c.join(',')})`}
// Click or drag paints the cells; letting go sends the run to the engine as one change.
let DRAG=null;
document.addEventListener('mouseup',()=>{if(!DRAG)return;const g=DRAG;DRAG=null;save('/cams/'+enc(g.k),{mask:{hours:[...g.touched].sort(),masked:g.on}})});
function cellDown(e,k,key){e.preventDefault();const on=!camMaskSet(k).has(key);DRAG={k,on,touched:new Set([key])};paintCell(e.currentTarget,on)}
function cellEnter(e,k,key){if(!DRAG||DRAG.k!==k||DRAG.touched.has(key))return;DRAG.touched.add(key);paintCell(e.currentTarget,DRAG.on)}
function paintCell(td,on){td.classList.toggle('msk',on)}
function ackMask(k){save('/cams/'+enc(k),{acknowledge_mask:true})}
/** The mockup's Detail table, drawn from the engine's hourly block instead of from bins. */
function detBody(){const H=D.hourly;if(!H||!H.hours_with_data)return'<span class="hint">No hourly data.</span>';
 const hours=Array.from({length:24},(_,i)=>i);const s=S(),mh=maskedTotal();const hi=H.scale_max||1,lo=0;
 let out=`<div class="hint" style="margin:0 0 10px">85th percentile speed (mph) per hour from the source cam, after the speed mask and adjustment, zeros excluded. Colour: <span class="sw" style="background:${heat(0)}"></span>0 → <span class="sw" style="background:${heat(.5)}"></span> → <span class="sw" style="background:${heat(1)}"></span>${Math.round(hi)} mph across this location. Bold: the highest hour of that day (ties all bold). Grey bands: peak hours 07–08 and 16–17. Grey italics: fewer than ${H.thin_below} speeds in the hour; blank: fewer than ${H.blank_below}. <b>Click an hour to mask it for that camera, drag to mask a run</b>; hatched cells are masked and excluded from every statistic.${mh.n?` <b>${mh.n} hour${mh.n>1?'s':''} masked.</b>`:''}</div>`;
 for(const w of mh.over)out+=`<div class="alert w"><span>${w.label}: more than half of this location's hours are masked — the report will rest on a small part of the count.</span><span class="sp"></span><button class="btn sm" onclick="ackMask('${w.k}')">Acknowledge</button></div>`;
 for(const d of incl()){const f=finalStats(d),src=s.src[d],hd=(H.directions||{})[d];const nMasked=hd?hd.masked_hours:0;const sharers=hd?(hd.shared_with||[]):[];
  out+=`<div class="dirblock"><div class="dirhead"><h5>${d}</h5><span class="meta">${f?`${camLabel(f.src).toLowerCase()} · count 85th <b style="color:var(--ink)">${p2(f.p85)}</b> · posted ${s.posted[d]??'—'}`:'no source cam'}${nMasked?` · <b style="color:var(--amber)">${nMasked} h masked</b>`:''}</span></div><div class="dirbody" style="overflow-x:auto">`;
  if(!f||!hd){out+='<span class="hint">Pick a source cam to see hourly values.</span></div></div>';continue}
  out+=`<table class="hm"><tr><td class="lab"></td><td class="hh dayc">day</td>${hours.map(hh=>`<td class="hh${pk(hh)}">${String(hh).padStart(2,'0')}</td>`).join('')}</tr>`;
  for(const day of hd.days){
   out+=`<tr><td class="lab">${dl(day.date)}</td><td class="tot dayc">${day.n?p2(day.p85):''}</td>`;
   for(const hh of hours){const c=day.cells[hh],key=day.date+' '+String(hh).padStart(2,'0');
    if(!c){out+=`<td class="na${pk(hh)}" title="no data"></td>`;continue}
    const isM=c.masked;const ev=`onmousedown="cellDown(event,'${src}','${key}')" onmouseenter="cellEnter(event,'${src}','${key}')"`;
    if(c.n<H.blank_below){out+=`<td class="na cell${pk(hh)}${isM?' msk':''}" ${ev} title="${key}:00 · ${c.rows} rows${isM?' · masked':''}">${isM?'–':''}</td>`;continue}
    const thin=c.n<H.thin_below;const t=(c.p85-lo)/((hi-lo)||1);const peak=c.peak;
    out+=`<td style="background:${thin?'#F1F4F7':heat(t)};color:var(--ink);${peak?'font-weight:700':''}" class="cell ${thin?'thin':''}${pk(hh)}${isM?' msk':''}" ${ev} title="${key}:00 · n ${c.n} · zero ${c.zero} · 85th ${pU(c.p85)}${peak?' · highest hour of the day':''}${isM?' · masked (click to unmask)':' · click to mask'}">${p2(c.p85)}</td>`}
   out+=`</tr><tr class="nrow"><td class="lab">vehicles</td><td class="dayc">${(day.n||0).toLocaleString()}</td>${hours.map(hh=>{const c=day.cells[hh];return`<td class="${pk(hh).trim()}">${c&&c.n?c.n.toLocaleString():''}</td>`}).join('')}</tr>`}
  out+='</table>';
  if(nMasked)out+=`<div class="mnote">${nMasked} hour${nMasked>1?'s':''} masked on ${camLabel(src).toLowerCase()}${sharers.length?' — shared with '+sharers.join(', ')+' (same camera)':''}. The mask stays with the camera: it is kept if you try another source cam and come back, and cleared only by re-importing the sitenumber.</div>`;
  out+='</div></div>'}
 return out}

// ---------- trail ----------
function trailBody(){const t=D.paper_trail||[];return t.length?`<ul class="hist">${t.map((e,i)=>`<li class="${i===0?'new':''}">${esc(e.what)}<div class="when">${esc(e.when)} &nbsp; [by ${esc(e.who)}]</div></li>`).join('')}</ul>`:'<span class="hint">Nothing logged yet.</span>'}
function openM(id){document.getElementById('m-'+id).classList.add('on')}function closeM(id){document.getElementById('m-'+id).classList.remove('on')}
