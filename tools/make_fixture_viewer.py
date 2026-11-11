"""SP-0: turn the locked mockup into a viewer for any speed_fixtures.json.

    python tools/make_fixture_viewer.py <speed_tab_mock_v15.html> <speed_fixtures.json> <out folder>

Writes three files into the out folder (the design repo's `ui mockup` folder):

    speed_fixtures.json                 the contract fixture (same file as tests/fixtures/)
    speed_fixtures.js                   the same JSON as `window.SPEED_FIXTURES = ...;` so the
                                        page can load it when opened straight from disk
    speed_tab_mock_v15_fixtures.html    v15 with its baked-in data removed: it reads the JSON

The locked mockup itself is never edited. Every change made to its text is listed in PATCHES,
and each one must match exactly once or the script stops - so a different mockup version can't
be half-converted without anyone noticing.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOADER = r"""
// ---------- SP-0: data comes from speed_fixtures.json (contract_version 1) ----------
function loadFixtures(fx){
 HOURLY={};RAW={};LOCS=[];nextId=1;FAST=(fx.viewer&&fx.viewer.fast_rows)||{};
 PROJECT={number:fx.project.number,name:fx.project.name||'',state:fx.project.state||'',client:fx.project.client||'',db:fx.project.db||''};
 FXSUM=fx.tab.summary||{};
 for(const row of fx.tab.rows){const d=fx.locations[row.id];
  for(const sn of d.location.sitenumbers){if(RAW[sn.sitenumber])continue;const h=sn.header||{};
   RAW[sn.sitenumber]={sitenumber:sn.sitenumber,sitecode:sn.sitecode,name:sn.name,cfg:{},cfgQch:{},
    hdr:{'Sitecode':h.sitecode,'Start date':h.start_date,'Start time':h.start_time,'Duration (hrs)':String(h.duration_hours)},
    cams:{},corner:{},camMask:{},camAck:{},trailCam:[]}}
  for(const c of d.cams){const r=RAW[c.sitenumber];r.cams[c.cam]={file:c.file,events:c.events,form:c.csv_form};r.corner[c.cam]=c.corner;
   r.camMask[c.cam]=[...c.masked_hours];r.camAck[c.cam]=c.mask_acknowledged;(HOURLY[c.sitenumber]=HOURLY[c.sitenumber]||{})[c.cam]=d.bins[c.key]||{}}
  const first=RAW[d.location.sitenumbers[0].sitenumber];
  const st={include:{},posted:{},same:{...d.posted_same},src:{},mask:{},adjust:{},note:d.note||'',linked:d.location.corners_linked!==false,trail:[],
   window:{start:d.location.window.start_date,startTime:d.location.window.start_time,hours:d.location.window.duration_hours,src:d.location.window.source}};
  for(const dir of DIRS){const x=d.directions[dir];first.cfg[dir]=x.lane_config||'';first.cfgQch[dir]=x.lane_config_qch||'';
   st.include[dir]=x.include_in_report;st.posted[dir]=x.posted;st.src[dir]=x.source_cam;st.mask[dir]={lo:x.mask.low,hi:x.mask.high};st.adjust[dir]=x.adjust_by||0}
  for(const e of d.paper_trail){const t={when:e.when,what:e.what,who:e.who};
   if(e.scope==='cam'){const tc=RAW[e.sitenumber].trailCam;if(!tc.some(o=>o.when===t.when&&o.what===t.what))tc.push(t)}else st.trail.push(t)}
  LOCS.push({id:'L'+row.id,slices:d.location.slices.map(s=>{const o={site:s.sitenumber};if(s.date)o.date=s.date;if(s.from_hour!==null&&s.from_hour!==undefined){o.from=s.from_hour;o.to=s.to_hour}return o}),state:st});
  nextId=Math.max(nextId,row.id+1)}
 const want=fx.viewer&&fx.viewer.open_location;CUR=LOCS.some(l=>l.id==='L'+want)?'L'+want:(LOCS[0]?LOCS[0].id:null)}
function pickFixtures(inp){const f=inp.files[0];if(!f)return;const rd=new FileReader();
 rd.onload=()=>{try{const fx=JSON.parse(rd.result);if(!fx.tab||!fx.locations)throw new Error('not a speed_fixtures.json (no tab / locations)');
  closeDrawer();SEL.clear();loadFixtures(fx);cc();renderTab();toast('Loaded '+f.name+': '+LOCS.length+' location'+(LOCS.length>1?'s':''))}catch(e){toast('Could not load '+f.name+': '+e.message)}};rd.readAsText(f)}
let FXSUM={};let CUR=null;
if(window.SPEED_FIXTURES)loadFixtures(window.SPEED_FIXTURES);
"""

# (what it is, pattern, replacement, is_regex)
PATCHES = [
    ("HOURLY const", r"^const HOURLY=\{.*\};?$", "let HOURLY={};", True),
    ("FAST const", r"^const FAST=\{.*\};?$", "let FAST={};", True),
    ("RAW const", r"^const RAW=\{.*\};?$", "let RAW={};", True),
    ("PROJECT const", r"^const PROJECT=\{.*\};?$", "let PROJECT={number:'',name:'',state:'',client:'',db:''};", True),
    ("baked-in locations", "for(const site of Object.keys(RAW))newLoc([{site}]);", "", False),
    ("baked-in mask on 13", "RAW['17889013'].camMask['1']=['2026-10-07 10','2026-10-07 11'];", "", False),
    ("baked-in trail on 13",
     "RAW['17889013'].trailCam=[{when:'2026-11-10 08:12',what:'Cam 1: masked 2026-10-07 10:00–12:00',who:'nsmith'}];",
     "", False),
    ("first open location", "let CUR=LOCS[3].id;", LOADER, False),
    ("post-macro tag from the data", "camSite(k)==='17889013'?'<span class=\"tag warn\" title=\"file header says",
     "ci.form==='post-macro'?'<span class=\"tag warn\" title=\"file header says", False),
    ("last import from the data", "last import 2026-10-12 09:37 · QC Hub snapshot 2026-10-12 09:37",
     "last import ${FXSUM.last_import||'—'} · QC Hub snapshot ${FXSUM.qch_snapshot||'—'}", False),
    ("title bar", "<b>Speed tab · mockup v15</b>",
     "<b>Speed tab · mockup v15 · from speed_fixtures.json</b>", False),
    ("load button", "<button class=\"btn sm\" onclick=\"openM('review')\" style=\"color:#1F2A37\">What to review</button>",
     "<label class=\"btn sm\" style=\"color:#1F2A37;cursor:pointer\">Load fixtures JSON…<input type=\"file\" accept=\".json\" "
     "style=\"display:none\" onchange=\"pickFixtures(this)\"></label>"
     "<button class=\"btn sm\" onclick=\"openM('review')\" style=\"color:#1F2A37\">What to review</button>", False),
    ("fixtures script tag", "<script>\n", "<script src=\"speed_fixtures.js\"></script>\n<script>\n", False),
]


def convert(html: str) -> str:
    for what, pattern, replacement, is_regex in PATCHES:
        if is_regex:
            html, n = re.subn(pattern, lambda _m, r=replacement: r, html, flags=re.M)
        else:
            n = html.count(pattern)
            html = html.replace(pattern, replacement)
        if n != 1:
            raise SystemExit(f"patch '{what}' matched {n} times, expected 1 - is this mockup v15?")
    return html


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    mockup, fixtures, out = Path(argv[1]), Path(argv[2]), Path(argv[3])
    text = fixtures.read_text(encoding="utf-8").strip()      # copied as is: the two files stay identical
    json.loads(text)                                         # ... but it must be JSON
    out.mkdir(parents=True, exist_ok=True)
    (out / "speed_fixtures.json").write_text(text, encoding="utf-8", newline="\n")
    (out / "speed_fixtures.js").write_text("window.SPEED_FIXTURES=" + text + ";\n", encoding="utf-8", newline="\n")
    page = convert(mockup.read_text(encoding="utf-8"))
    (out / (mockup.stem + "_fixtures.html")).write_text(page, encoding="utf-8", newline="\n")
    print(f"wrote speed_fixtures.json, speed_fixtures.js and {mockup.stem}_fixtures.html to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
