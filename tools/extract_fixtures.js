// SP-0: extract speed_fixtures.json from the locked mockup (speed_tab_mock_v15.html).
//
//   node tools/extract_fixtures.js <mockup.html> <out.json>
//
// The mockup's own script is run as is (with a stub page, so nothing is drawn) and its own
// functions produce every number: obs / rawStats / finalStats / hourStats / pinc are the
// mockup's, not re-implementations. The JSON this writes is the contract the engine (SP-3)
// and the API (SP-4) are built to: tests/test_contract.py pins its shape and
// tests/test_engine_fixtures.py makes the Python engine reproduce its values.
//
// Three rules follow build plan 1.4 where the mockup's script still has an older rule
// (see docs/CONTRACT.md, "Where the contract differs from the mockup script"):
//   dead cam   = 100 % zero speeds over the whole file            (SP-Q9; mockup: > 95 %)
//   suggestion = every cam with a non-zero speed in the direction (plan 6; mockup: <= 50 % zero)
//   no low-n warning                                              (SP-Q9 / SP-Q14)
const fs = require('fs'), vm = require('vm');
const [, , src, out] = process.argv;
if (!src || !out) { console.error('usage: node tools/extract_fixtures.js <mockup.html> <out.json>'); process.exit(2); }
const html = fs.readFileSync(src, 'utf8');
const script = html.slice(html.lastIndexOf('<script>') + 8, html.lastIndexOf('</script>'));

const el = () => ({ innerHTML: '', textContent: '', style: {}, open: false,
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  querySelector() { return el(); }, querySelectorAll() { return []; }, appendChild() {}, remove() {}, click() {} });
const els = {};
const sandbox = { console, setTimeout() {}, clearTimeout() {},
  document: { getElementById: id => (els[id] = els[id] || el()), addEventListener() {},
    querySelectorAll() { return []; }, createElement: el, body: el() } };
sandbox.window = sandbox; sandbox.globalThis = sandbox;

const extractor = `
;(() => {
const CONTRACT_VERSION = 1;
const num = id => +String(id).replace(/^L/, '');
const allBins = k => (HOURLY[camSite(k)] || {})[camNo(k)] || {};
// plan SP-Q9: a dead cam has 100 % zero speeds (whole file)
function deadP(k) { let n = 0, z = 0; const b = allBins(k);
  for (const d in b) for (const key in b[d]) for (const sp in b[d][key]) { n += b[d][key][sp]; if (+sp === 0) z += b[d][key][sp]; }
  return n > 0 && z === n; }
function cornersOkP() { const live = CAMS().filter(k => !deadP(k)); return CAMS().length < 2 || live.every(k => corner(k)); }
function suggestP(dir) { if (!cornersOkP()) return null;
  const withRows = CAMS().filter(k => rawStats(k, dir).n > 0);
  const cands = withRows.filter(k => !deadP(k) && rawStats(k, dir).nz > 0).map(k => { const st = rawStats(k, dir), r = role(k, dir);
    return { k, pz: st.pz, r: r === 'Primary' ? 0 : r === 'Secondary' ? 1 : 2 }; });
  if (!cands.length) return null;
  cands.sort((a, b) => a.r - b.r || a.pz - b.pz || (a.k < b.k ? -1 : 1));
  const b = cands[0], n = cands.filter(x => x.r === b.r).length;
  const why = b.r === 0 ? 'primary corner' : b.r === 1 ? 'no primary · secondary corner' : 'no on-axis cam · lowest % zero';
  return { cam: b.k, why: why + (n > 1 && b.r < 2 ? ' · lowest % zero of ' + n : '') + (cands.length < withRows.length ? ' · dead cam skipped' : '') }; }
function readyP() { return cornersOkP() && incl().length > 0 && incl().every(d => S().src[d] && S().posted[d]); }
function missingP() { const o = [];
  if (!cornersOkP()) o.push('Corners needed for every live cam (' + CAMS().filter(k => !corner(k) && !deadP(k)).map(camLabel).join(', ') + ')');
  if (!incl().length) o.push('No approach included in the report');
  for (const d of incl()) { if (!S().src[d]) o.push(d + ' has no source cam'); if (!S().posted[d]) o.push(d + ' has no posted speed'); }
  return o; }
function warningsP() { const w = [];
  const dead = CAMS().filter(deadP);
  if (dead.length) w.push({ code: 'dead_cam', text: dead.map(camLabel).join(', ') + ' dead (100 % zero speeds)' });
  const ol = offLeg(), k = Object.keys(ol);
  if (k.length) w.push({ code: 'off_leg', text: k.map(d => ol[d] + ' ' + d + ' events').join(', ') + ' in approaches not included in the report (ignored)' });
  const wk = windowKeys();
  if (wk) { const outside = hourKeys().filter(key => !wk.has(key)).length;
    if (outside) w.push({ code: 'outside_window', text: outside + ' hour' + (outside > 1 ? 's' : '') + ' of data outside the count window' }); }
  for (const x of maskedTotal().over) w.push({ code: 'over_half_masked', text: x.label + ': over half the hours masked', cam: x.k, can_acknowledge: true });
  return w; }
function slicesOut(loc) { return loc.slices.map(s => ({ sitenumber: s.site, date: s.date || null,
  from_hour: s.from === undefined ? null : s.from, to_hour: s.to === undefined ? null : s.to, label: sliceLabel(s) })); }
function windowOut(w) { return { start_date: w.start, start_time: w.startTime, duration_hours: w.hours, source: w.src }; }
function kept(dir) { const s = S(), src = s.src[dir], m = s.mask[dir], mod = modifier(dir), o = [];
  for (const v of expand(src, dir)) { if ((m.lo !== null && v < m.lo) || (m.hi !== null && v > m.hi)) continue; o.push(v + mod); } return o; }
function reportOut(dir) { const f = finalStats(dir); if (!f) return null; const kv = kept(dir);
  return { source_cam: f.src, adjust_by: f.mod, events: f.nRaw, zero: f.zero, pct_zero: f.nRaw ? f.zero / f.nRaw : 0,
    masked: f.masked, pct_masked: (f.nRaw - f.zero) ? f.masked / (f.nRaw - f.zero) : 0, n: f.n,
    avg: f.n ? f.avg : null, p85: f.n ? f.p85 : null, p95: f.n ? pinc(kv, .95) : null,
    over_posted: f.over, pct_over_posted: f.pover, masked_hours: f.maskedHours }; }
function dirOut(d) { const s = S(), r = R(), sg = suggestP(d), m = s.mask[d]; let off = 0;
  if (!s.include[d]) for (const k of CAMS()) off += obs(k, d).n;
  return { exists: hasLeg(d), include_in_report: !!s.include[d], posted: s.posted[d],
    lane_config: r.cfg[d] || '', lane_config_qch: r.cfgQch[d] || '',
    source_cam: s.src[d] || null, suggested: sg, adjust_by: s.adjust[d] || 0,
    mask: { low: m.lo, high: m.hi, is_default: m.lo === 5 && m.hi === null },
    report: s.include[d] ? reportOut(d) : null, off_leg_events: off }; }
function camOut(k) { const site = camSite(k), c = camNo(k), ci = camInfo(k), keys = hourKeysFor(k), msk = camMaskSet(k);
  const dirs = {}; for (const d of DIRS) { const st = rawStats(k, d);
    dirs[d] = { n: st.n, zero: st.zero, n_nonzero: st.nz, pct_zero: st.pz, avg: st.nz ? st.avg : null, p85: st.nz ? st.p85 : null, role: role(k, d) }; }
  const masked = keys.filter(key => msk.has(key)).length;
  return { key: k, sitenumber: site, cam: +c, label: camLabel(k), corner: corner(k), file: ci.file,
    csv_form: site === '17889013' ? 'post-macro' : 'raw', events: ci.events, events_in_location: camEvents(k),
    dead: deadP(k), masked_hours: [...(RAW[site].camMask[c] || [])].sort(), mask_acknowledged: !!RAW[site].camAck[c],
    hours_with_data: keys.length, over_half_masked: keys.length > 0 && masked > keys.length / 2, directions: dirs }; }
function histOut(k, d) { const o = obs(k, d); if (!o.n) return null; const s = S(), m = s.mask[d], isSrc = s.src[d] === k;
  const vals = Object.keys(o.h).map(Number).sort((a, b) => a - b); const mx = vals.length ? vals[vals.length - 1] : 0, mn = vals.length ? vals[0] : 0;
  const bins10 = []; if (vals.length) for (let b0 = Math.floor(mn / 10) * 10; b0 <= mx; b0 += 10) { let n = 0;
    for (const v of vals) if (v >= b0 && v < b0 + 10) n += o.h[v];
    bins10.push({ from: b0, to: b0 + 9, n, above_upper_mask: isSrc && m.hi !== null && b0 > m.hi }); }
  const bins = {}; for (const v of vals) bins[v] = o.h[v];
  return { events: o.n, zero: o.zero, masked_hours: o.maskedHours, bins, occupied: vals, min_speed: vals.length ? mn : null,
    max_speed: vals.length ? mx : null, bins10 }; }
function hourlyOut() { const keys = hourKeys(), s = S(); const dates = [...new Set(keys.map(k => k.slice(0, 10)))].sort();
  const cells = {}; let hi = -Infinity;
  for (const d of incl()) { const src = s.src[d]; for (const k of keys) { const st = hourStats(d, k); cells[d + k] = st;
    if (st && st.n >= 30 && st.p85 !== null && src && !camMaskSet(src).has(k)) hi = Math.max(hi, st.p85); } }
  if (!isFinite(hi)) hi = 1;
  const dirs = {};
  for (const d of incl()) { const src = s.src[d]; if (!src) { dirs[d] = null; continue; }
    const msk = camMaskSet(src), m = s.mask[d], mod = modifier(d);
    const inSl = key => L().slices.some(sl => sl.site === camSite(src) && sliceHas(sl, key));
    const days = dates.map(dt => { const dv = [], row = []; 
      for (let hh = 0; hh < 24; hh++) { const key = dt + ' ' + String(hh).padStart(2, '0'); const b = hourBins(src, d)[key];
        if (!(inSl(key) && b)) { row.push(null); continue; }
        const st = cells[d + key], isM = msk.has(key), v = [];
        for (const kk in b) { const sp = +kk; if (sp === 0) continue; if ((m.lo !== null && sp < m.lo) || (m.hi !== null && sp > m.hi)) continue;
          for (let i = 0; i < b[kk]; i++) v.push(sp + mod); }
        if (!isM) dv.push(...v);
        row.push({ hour: hh, rows: st.raw, zero: st.zero, n: st.n, p85: st.p85, p95: v.length ? pinc(v, .95) : null, masked: isM, peak: false }); }
      const rowMax = Math.max(-1, ...row.map(c => c && c.n >= 5 && !c.masked ? Math.round(c.p85) : -1));
      for (const c of row) if (c && c.n >= 5 && !c.masked && Math.round(c.p85) === rowMax) c.peak = true;
      return { date: dt, n: dv.length, p85: dv.length ? pinc(dv) : null, p95: dv.length ? pinc(dv, .95) : null, cells: row }; });
    dirs[d] = { source_cam: src, masked_hours: keys.filter(k => msk.has(k) && hourBins(src, d)[k]).length,
      shared_with: incl().filter(x => x !== d && s.src[x] === src), days }; }
  return { dates, hours_with_data: keys.length, peak_hours: [...PEAK].sort((a, b) => a - b), scale_max: hi,
    thin_below: 30, blank_below: 5, directions: dirs }; }
function trailOut() { const t = [...S().trail.map(e => ({ ...e, scope: 'location' })), ...sites().flatMap(s => (RAW[s].trailCam || []).map(e => ({ ...e, scope: 'cam', sitenumber: s })))];
  return t.sort((a, b) => b.when.localeCompare(a.when)); }
function statusOut() { const r = readyP(); return { state: r ? 'ready' : 'needs_input', label: r ? 'Ready' : 'Needs input', missing: missingP() }; }
function drawer(loc) { const s = S(), ss = sites(), mh = maskedTotal();
  const dirs = {}; for (const d of DIRS) dirs[d] = dirOut(d);
  const hist = {}, bins = {};
  for (const k of CAMS()) { hist[k] = {}; for (const d of DIRS) { const h = histOut(k, d); if (h) hist[k][d] = h; } bins[k] = allBins(k); }
  return {
    location: { id: num(loc.id), code: locCode(loc), name: RAW[ss[0]].name, name_source: 'qch', manual_name: null, qch_name: RAW[ss[0]].name,
      sitenumbers: ss.map(x => ({ sitenumber: x, sitecode: RAW[x].sitecode, name: RAW[x].name,
        street_ns: RAW[x].name.split('  &  ')[0] || null, street_ew: RAW[x].name.split('  &  ')[1] || null, qch_lookup: 'legacycounts',
        header: { sitecode: RAW[x].hdr['Sitecode'], start_date: RAW[x].hdr['Start date'], start_time: RAW[x].hdr['Start time'], duration_hours: +RAW[x].hdr['Duration (hrs)'] } })),
      slices: slicesOut(loc), is_sliced: isSliced(loc), is_grouped: ss.length > 1, latitude: null, longitude: null,
      geometry_source: 'qch', nm_grouping_used: false, corners_linked: !!s.linked, window: windowOut(s.window), count_days: countDays(),
      cams: CAMS().length, events: totalEvents(), masked_hours: mh.n },
    posted_same: { NS: !!s.same.NS, EW: !!s.same.EW },
    directions: dirs, cams: CAMS().map(camOut), histograms: hist, hourly: hourlyOut(), bins,
    note: s.note || '', status: statusOut(), warnings: warningsP(), paper_trail: trailOut() }; }
function tabRow(loc) { const s = S(), ss = sites(), w = warningsP();
  return { id: num(loc.id), code: locCode(loc), name: RAW[ss[0]].name, sitenumbers: ss, slices: slicesOut(loc),
    is_sliced: isSliced(loc), is_grouped: ss.length > 1, window: windowOut(s.window), cams: CAMS().length, in_report: incl(),
    events: totalEvents(), corners: { set: CAMS().filter(k => corner(k)).length, of: CAMS().length },
    posted: { set: incl().filter(d => s.posted[d]).length, of: incl().length },
    source_cams: { set: incl().filter(d => s.src[d]).length, of: incl().length },
    status: statusOut(), warnings: w.map(x => x.text) }; }
const rows = LOCS.map(l => withLoc(l.id, () => tabRow(l)));
const locations = {}; for (const l of LOCS) locations[num(l.id)] = withLoc(l.id, () => drawer(l));
const anyReady = rows.some(r => r.status.state === 'ready'), allReady = rows.every(r => r.status.state === 'ready');
globalThis.__OUT = { contract_version: CONTRACT_VERSION, source: 'speed_tab_mock_v15.html',
  project: { number: PROJECT.number, name: PROJECT.name, state: PROJECT.state, client: PROJECT.client, db: PROJECT.db, is_art_project: true },
  tab: { summary: { sitenumbers: Object.keys(RAW).length, locations: LOCS.length, cams: Object.values(RAW).reduce((a, r) => a + Object.keys(r.cams).length, 0),
      last_import: '2026-10-12 09:37', qch_snapshot: '2026-10-12 09:37', last_report: null },
    generate: { enabled: anyReady, green: allReady && anyReady }, rows },
  locations, viewer: { fast_rows: FAST, open_location: num(CUR) } };
})();`;
vm.createContext(sandbox);
vm.runInContext(script + extractor, sandbox, { filename: 'mockup+extractor' });
const o = sandbox.__OUT;
fs.writeFileSync(out, JSON.stringify(o));
console.log('wrote', out, (fs.statSync(out).size / 1024).toFixed(0) + ' KB,', o.tab.rows.length, 'locations');
for (const r of o.tab.rows) { const d = o.locations[r.id];
  console.log(' ', r.id, r.code, r.status.label, '|', r.in_report.map(x => x + ' ' + (d.directions[x].report ? d.directions[x].report.p85 : '-')).join(' '), '|', r.warnings.join(' ; ')); }
