/* Speed tool front end - the shell: the rail, Home, Settings and About, the pop-ups, the saved
 * banner. Adapted from A.R.T.'s app.js, home.js and settings.js (see COPIED_FROM_ART.md), so
 * these screens look and behave as they do there. The speed tab itself is speed.js.
 *
 * Two rules worth knowing before reading the rest:
 *
 *   - Every /api call answers {ok:true, ...} or {ok:false, error:{kind, message}}, always with
 *     200, so `call` only throws when the engine could not be reached at all. (The /speed calls
 *     answer differently and go through `api` in speed.js.)
 *   - One project is open at a time, and the engine is the one that holds it (session.py).
 *     Home is a tab switch, not a close: the project stays open.
 */
"use strict";

const TABS = [
  {id: "home",  label: "Home"},
  {id: "speed", label: "Speed"},
];

/* Everything the shell knows. `project` is a copy of what the engine last told us. */
const APP = {
  tab: "home",
  app: null,           // GET /api/app
  project: null,
  recent: null,        // the cards, as GET /api/projects/recent gave them
  status: {},          // card id -> its pill, filled in afterwards
  notes: [],           // repaired-file lines, dismissible
  wiz: null,           // New project's answers while it is open (wizard.js)
  inotes: null,        // the internal notes as stored: {number, html, saved_utc} (notes.js)
  inEdit: null,        // the notes box while it is open: {number, html, was}
};

const esc = (s) => String(s == null ? "" : s)
  .replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

const el = (h) => {
  const d = document.createElement("div");
  d.innerHTML = h.trim();
  return d.firstElementChild;
};

const veil = () => document.getElementById("veil");
const modalBox = () => document.getElementById("modal");

/* ------------------------------------------------------------------ talking to the engine */

/** One call. Throws only when the engine could not be reached - an expected failure comes
 *  back as a body with ok:false and is the caller's to render. */
async function call(path, opts) {
  let r;
  try {
    r = await fetch(path, opts);
  } catch (e) {
    throw new Error(`Could not reach the app engine (${path}).`);
  }
  if (!r.ok) throw new Error(`${path} answered ${r.status}.`);
  return r.json();
}

const get = (path) => call(path);
const send = (method, path, body) => call(path, {
  method,
  headers: {"Content-Type": "application/json"},
  body: body === undefined ? undefined : JSON.stringify(body),
});
const post = (path, body) => send("POST", path, body);
const put = (path, body) => send("PUT", path, body);
const del = (path, body) => send("DELETE", path, body);

/** The last resort: the engine is not answering at all. */
function engineDown(err) {
  console.error(err);
  const text = err && err.message ? err.message : String(err);
  if (/ answered 5\d\d\./.test(text)) {            // the engine is running: one request failed inside it
    return showModal("fail", `
      <div class="ahead"><div class="alerticon bad">!</div><div>
        <h3>That step failed inside the app</h3>
        <p>${esc(text)}</p></div></div>
      <p class="note">The speed tool is still running, and restarting will not fix this: it is a fault in
        the app for this project's data. The step did not complete. The details are in the black
        Speed tool window; copy them for whoever maintains the app.</p>
      <div class="actions"><div class="sp"></div>
        <button class="primary" data-act="ok">OK</button></div>`, () => closeModal());
  }
  showModal("fail", `
    <div class="ahead"><div class="alerticon bad">!</div><div>
      <h3>The app engine stopped answering</h3>
      <p>${esc(text)}</p></div></div>
    <p class="note">Close this tab and start the speed tool again. Nothing on disk was changed by
      whatever was in progress.</p>
    <div class="actions"><div class="sp"></div>
      <button class="primary" data-act="ok">OK</button></div>`, () => closeModal());
}

/* ------------------------------------------------------------------ modal, toast */

function closeModal() {
  veil().hidden = true;
  modalBox().className = "modal";
  modalBox().innerHTML = "";
  modalBox().onclick = null;
}

/** `onClick(act, event)` is handed the data-act of whatever was clicked. */
function showModal(cls, html, onClick) {
  const m = modalBox();
  m.className = "modal" + (cls ? " " + cls : "");
  m.innerHTML = html;
  veil().hidden = false;
  m.onclick = (e) => {
    const a = e.target.closest("[data-act]");
    if (a && onClick) onClick(a.dataset.act, e);
  };
  const input = m.querySelector("input:not([type=hidden]):not([type=checkbox]):not([type=radio])");
  if (input) { input.focus(); if (input.select) input.select(); }
}

function spinnerModal(step, cmd, title, line) {
  showModal("", `
    <div class="wstep"><span>${step}</span>${cmd ? `<span class="cmd">${esc(cmd)}</span>` : ""}</div>
    <h3>${esc(title)}</h3>
    <div class="spin"><i></i><span>${esc(line)}</span></div>`, () => {});
}

function toast(msg) {
  const t = document.getElementById("toast");
  t.innerHTML = `<span class="tick">&#10003;</span><span>${esc(msg)}</span>`;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, 3000);   // 3.0 s
}

/* ------------------------------------------------------------------ branch pop-ups
 *
 * New project's pop-ups. A branch arrives as {step, branch, message, choices, default}: the
 * engine decides which buttons there are; this is only their wording and their placement.
 */

const CHOICE_LABELS = {
  cancel: "Cancel",
  retry: "Retry",
  re_enter: "Re-enter project number",
  change_folder: "Pick a different folder",
  continue: "Continue",
  "lookup:not_found:continue": "Create project",
  "lookup:connection_failed:continue": "Continue without details",
};

const label = (branch, choice) =>
  CHOICE_LABELS[`${branch.step}:${branch.branch}:${choice}`] || CHOICE_LABELS[choice] || choice;

/** The pop-up a branch puts on the screen. `onChoice(choice)` gets the button that was hit. */
function branchModal(branch, onChoice, opts) {
  opts = opts || {};
  const choices = branch.choices || [];
  const hasCancel = choices.indexOf("cancel") !== -1;
  const rest = choices.filter(c => c !== "cancel");
  const primary = branch.default && branch.default !== "cancel" ? branch.default : null;
  const button = (c) =>
    `<button class="${c === primary ? "primary" : ""}" data-act="${esc(c)}">${esc(label(branch, c))}</button>`;

  showModal("fail", `
    <div class="ahead"><div class="alerticon ${opts.bad ? "bad" : "warn"}">!</div><div>
      <h3>${esc(opts.title || "Something needs a decision")}</h3>
      <p>${esc(branch.message || "")}</p></div></div>
    ${opts.note ? `<p class="note">${esc(opts.note)}</p>` : ""}
    <div class="actions">
      ${hasCancel ? button("cancel") : ""}<div class="sp"></div>
      ${rest.map(button).join("")}
    </div>`, (act) => {
      if (choices.indexOf(act) !== -1) onChoice(act);
    });
}

/** An expected failure: one message, one OK. */
function errorModal(title, message, after) {
  showModal("fail", `
    <div class="ahead"><div class="alerticon bad">!</div><div>
      <h3>${esc(title)}</h3>
      <p>${esc(message)}</p></div></div>
    <div class="actions"><div class="sp"></div>
      <button class="primary" data-act="ok">OK</button></div>`,
    () => { closeModal(); if (after) after(); });
}

/** A yes / no question. `onYes` runs after the pop-up has closed. */
function confirmModal(title, message, yesLabel, onYes) {
  showModal("fail", `
    <div class="ahead"><div class="alerticon warn">!</div><div>
      <h3>${esc(title)}</h3>
      <p>${esc(message)}</p></div></div>
    <div class="actions">
      <button data-act="no">Cancel</button><div class="sp"></div>
      <button class="primary" data-act="yes">${esc(yesLabel)}</button>
    </div>`, (act) => { closeModal(); if (act === "yes") onYes(); });
}

/* ------------------------------------------------------------------ leaving a project
 *
 * Home does not ask: it is a tab switch, the project stays open and a notes draft is kept. New
 * project, Open other project and a recent card do ask, and only while the internal notes box
 * is open with something typed into it (A.R.T.'s rule).
 */

function leavingProject(go) {
  if (!APP.project || !notesAreDirty()) return go();
  showModal("fail", `
    <div class="ahead"><div class="alerticon warn">!</div><div>
      <h3>Are you sure you want to leave ${esc(APP.project.project_number)}?</h3>
      <p>The internal notes you have typed have not been saved.</p></div></div>
    <div class="actions">
      <button data-act="stay">Cancel</button><div class="sp"></div>
      <button class="primary" data-act="leave">Leave</button>
    </div>`, (act) => {
      closeModal();
      if (act === "leave") { APP.inEdit = null; go(); }
    });
}

/* ------------------------------------------------------------------ the rail */

function renderTabs() {
  const bar = document.getElementById("tabbar");
  bar.innerHTML =
    `<div class="mark"><img src="assets/qc_logo_white.png" alt="Quality Counts"><span>Speed</span></div>` +
    TABS.map(t => {
      const live = t.id === "home" || APP.project;             // the speed tab needs a project
      return `<button class="tab ${APP.tab === t.id ? "on" : ""}" data-tab="${t.id}" ${live ? "" : "disabled"}` +
        `${live ? "" : ` title="Open a project first"`}>${esc(t.label)}</button>`;
    }).join("") +
    `<div class="sp"></div>` +
    `<div class="proj"><span>Project</span>${APP.project ? esc(APP.project.project_number) : "none open"}</div>`;
  bar.onclick = (e) => {
    const b = e.target.closest("[data-tab]");
    if (b && !b.disabled) goTab(b.dataset.tab);
  };
}

function goTab(tab) {
  APP.tab = tab;
  render();
}

function render() {
  renderTabs();
  const home = APP.tab === "home" || !APP.project;
  document.getElementById("homewrap").hidden = !home;
  document.getElementById("speedwrap").hidden = home;
  speedLeave();                        // the drawer does not follow the user to another tab
  if (home) {
    const main = document.getElementById("main");
    main.innerHTML = "";
    main.appendChild(screenHome());
  } else {
    speedShow();
    renderNotes();
  }
}

/* ------------------------------------------------------------------ boot */

/** Re-read the app's own state: settings, whether a project is open, repaired-file notes. */
async function refreshApp() {
  APP.app = await get("/api/app");
  APP.project = APP.app.project;
  APP.notes = (APP.app.notes || []).slice();
  document.title = `${APP.app.name} ${APP.app.version}`;
  return APP.app;
}

async function start() {
  try {
    await refreshApp();
  } catch (e) {
    renderTabs();
    document.getElementById("main").innerHTML = `
      <div class="panel" style="margin-top:26px"><h3>Nothing is answering</h3>
        <p class="note">${esc(e.message)}</p></div>`;
    return;
  }
  // A reload lands back on the speed tab when a project is open.
  APP.tab = APP.project ? "speed" : "home";
  render();
  loadRecent();
}

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !veil().hidden) e.preventDefault();
});

/* ================================================================== Home */

/** "today, 11:42" / "yesterday" / "Sep 3" from a UTC stamp, in the machine's own time. */
function whenText(utc) {
  if (!utc) return "";
  const t = new Date(utc.replace(" ", "T"));
  if (isNaN(t)) return utc;
  const now = new Date();
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const day = new Date(t.getFullYear(), t.getMonth(), t.getDate());
  const days = Math.round((midnight - day) / 86400000);
  const clock = t.toLocaleTimeString([], {hour: "numeric", minute: "2-digit"});
  if (days === 0) return `today, ${clock}`;
  if (days === 1) return "yesterday";
  return t.toLocaleDateString([], {month: "short", day: "numeric"});
}

function pillFor(status) {
  if (!status) return `<span class="s nodata" style="opacity:.45">&nbsp;&nbsp;&nbsp;&nbsp;</span>`;
  if (status.found === false) return `<span class="s nodata">Folder not found</span>`;
  return status.has_speed_data ? `<span class="s nm">Speed</span>` : `<span class="s nodata">No data</span>`;
}

function screenHome() {
  const cards = (APP.recent && APP.recent.projects) || [];
  // Two cards can carry the same number from two different folders; those say which folder.
  const seen = {};
  cards.forEach(c => { seen[c.project_number] = (seen[c.project_number] || 0) + 1; });

  const card = (c) => {
    const status = APP.status[c.id];
    const gone = status && status.found === false;
    const name = (status && status.project_name) || c.project_name || "";
    const parent = c.folder.replace(/[\\/]+$/, "").replace(/[\\/][^\\/]*$/, "");
    const isOpen = APP.project && APP.project.folder === c.folder;
    return `
      <div class="rcard${gone ? " gone" : ""}" ${gone ? "" : `data-open="${esc(c.id)}"`}>
        <div class="n">${esc(c.project_number)}${isOpen ? " &middot; open" : ""}</div>
        <div class="d">${esc(name || "(no name)")}</div>
        <div class="d" style="color:#9aa4ae">${esc(whenText(c.last_opened_utc))}</div>
        ${gone || seen[c.project_number] > 1
          ? `<div class="d path" title="${esc(c.folder)}">${esc(gone ? c.folder : parent)}</div>` : ""}
        <div class="pills">${pillFor(status)}</div>
        ${gone ? `<div class="cardacts">
            <button class="sm" data-locate="${esc(c.id)}">Locate&hellip;</button>
            <button class="sm" data-forget="${esc(c.id)}">Remove from list</button>
          </div>` : ""}
      </div>`;
  };

  const notes = APP.notes.concat((APP.recent && APP.recent.notes) || []);

  const screen = el(`<div class="splash">
    <div class="brand">
      <span class="logotile"><img src="assets/qc_logo_white.png" alt="Quality Counts"></span>
      <div class="t"><h2>${esc(APP.app ? APP.app.title : "Intersection Speed tool")}</h2>
        <p>Intersection speed reporting</p></div>
      <div class="sp"></div>
      <button data-act="settings">Settings</button>
    </div>

    ${notes.map((n, i) => `<div class="banner warn repaired">
        <span>${esc(n)}</span>
        <button class="sm" data-dismiss="${i}">Dismiss</button></div>`).join("")}

    <div class="panel" style="display:flex;gap:14px;align-items:center">
      <div style="flex:1">
        <h3>New project</h3>
        <p class="note" style="margin:0">Create a local project folder to hold imported data.</p>
      </div>
      <button class="primary" data-act="new">New project</button>
    </div>

    <div class="panel">
      <div style="display:flex;align-items:center;margin-bottom:12px">
        <h3 style="margin:0;flex:1">Recent projects</h3>
        <button class="sm" data-act="open">Open other project&hellip;</button>
      </div>
      <p class="note">Click any project to load it. One project is open at a time; the pill shows
        whether speed data has been imported.</p>
      <div class="recent">${cards.map(card).join("")}</div>
      ${cards.length ? "" : `<p class="note">Nothing here yet. Create a project, or open one
        that was made somewhere else (an A.R.T. project folder opens as it is; any other folder
        asks for the project number once).</p>`}
    </div>
  </div>`);

  screen.addEventListener("click", (e) => {
    const open = e.target.closest("[data-open]");
    if (open) return openCard(open.dataset.open);
    const locate = e.target.closest("[data-locate]");
    if (locate) return locateCard(locate.dataset.locate);
    const forget = e.target.closest("[data-forget]");
    if (forget) return forgetCard(forget.dataset.forget);
    const dismiss = e.target.closest("[data-dismiss]");
    if (dismiss) {
      const i = Number(dismiss.dataset.dismiss);
      const mine = APP.notes.length;
      if (i < mine) APP.notes.splice(i, 1);
      else if (APP.recent && APP.recent.notes) APP.recent.notes.splice(i - mine, 1);
      return render();
    }
    const act = e.target.dataset.act;
    if (act === "new") return leavingProject(startWizard);
    if (act === "open") return leavingProject(openOtherProject);
    if (act === "settings") return showSettings();
  });
  return screen;
}

/** The cards first, then each card's pill. Failures here never stop the screen. */
async function loadRecent() {
  try {
    APP.recent = await get("/api/projects/recent");
  } catch (e) {
    console.error(e);
    return;
  }
  APP.status = {};
  if (APP.tab === "home") render();
  const cards = APP.recent.projects || [];
  await Promise.all(cards.map(async (c) => {
    try {
      APP.status[c.id] = await get(`/api/projects/recent/${encodeURIComponent(c.id)}/status`);
    } catch (e) {
      console.error(e);
      return;
    }
    if (APP.tab === "home") render();
  }));
}

function openCard(cardId) {
  const card = ((APP.recent && APP.recent.projects) || []).find(c => c.id === cardId);
  if (!card) return;
  if (APP.project && APP.project.folder === card.folder) return goTab("speed");   // already open
  leavingProject(() => openFolder(card.folder, card.project_number));
}

/** Open a project folder and land on the speed tab. Used by a card, by Open project folder and
 *  by Locate... - all three are the same call. Opening one closes whichever was open. */
async function openFolder(folder, number) {
  let body;
  try {
    body = await post("/api/projects/open", {folder: folder, project_number: number || null});
  } catch (e) {
    engineDown(e);
    return false;
  }
  if (!body.ok && body.error.kind === "needs_number") return askNumber(folder, body.error.message);
  if (!body.ok) {
    errorModal("That folder could not be opened", body.error.message);
    return false;
  }
  closeModal();
  APP.project = body.project;
  APP.inEdit = null;
  APP.inotes = null;             // read again for the project just opened
  APP.tab = "speed";
  speedReset();
  render();
  loadRecent();
  return true;
}

/** A folder that is not an A.R.T. project does not say which project it is: ask once. The
 *  number is remembered with the recent card, so it is not asked again. */
function askNumber(folder, message) {
  return new Promise(resolve => {
    showModal("", `
      <div class="wstep"><span>Project number</span></div>
      <h3>Which project is this folder for?</h3>
      <p>${esc(message)}</p>
      <div class="pathbox" style="margin-bottom:14px">${esc(folder)}</div>
      <div class="field"><label for="projnum">Project number (six digits)</label>
        <input type="text" id="projnum" inputmode="numeric" maxlength="6" placeholder="174083"></div>
      <div class="actions"><div class="sp"></div>
        <button data-act="cancel">Cancel</button>
        <button class="primary" data-act="use">Open</button>
      </div>`, (act) => {
        if (act === "cancel") { closeModal(); return resolve(false); }
        const box = document.getElementById("projnum");
        const typed = (box.value || "").trim();
        if (!/^\d{6}$/.test(typed)) { box.classList.add("bad"); return box.focus(); }
        closeModal();
        openFolder(folder, typed).then(resolve);
      });
    document.getElementById("projnum").addEventListener("keydown", e => {
      if (e.key === "Enter") modalBox().querySelector('[data-act="use"]').click();
    });
  });
}

async function openOtherProject() {
  const folder = await pickFolder("Select a project folder", (APP.app && APP.app.default_folder) || null);
  if (!folder) return;
  openFolder(folder, null);
}

/** Locate...: the project moved. Open it where it is now, then drop the card that pointed at
 *  where it used to be, so the two do not sit side by side. */
async function locateCard(cardId) {
  const card = ((APP.recent && APP.recent.projects) || []).find(c => c.id === cardId);
  if (!card) return;
  const folder = await pickFolder(`Where is project ${card.project_number} now?`);
  if (!folder) return;
  const opened = await openFolder(folder, card.project_number);
  if (opened) {
    try { await del(`/api/projects/recent/${encodeURIComponent(cardId)}`); } catch (e) { console.error(e); }
    loadRecent();
  }
}

async function forgetCard(cardId) {
  try {
    const body = await del(`/api/projects/recent/${encodeURIComponent(cardId)}`);
    if (!body.ok) return errorModal("Could not remove that project", body.error.message);
    toast(`${body.project_number} removed from Recent projects`);
    loadRecent();
  } catch (e) {
    engineDown(e);
  }
}

/* ------------------------------------------------------------------ the folder picker
 *
 * A web page cannot hand back a real Windows path, so the picker is the engine's native dialog
 * (dialogs.py). A machine with no Tk gets a box to type into instead - this is the only place
 * the front end asks for a path in text.
 */

/** Returns the chosen folder, or null if it was cancelled or could not be chosen. */
function pickFolder(title, start) {
  if (APP.app && APP.app.can_pick_folder === false) return typeFolder(title, start);
  return post("/api/dialog/folder", {title: title, start: start || null})
    .then(body => {
      if (body.ok) return body.cancelled ? null : body.folder;
      if (body.error.kind === "no_dialog") return typeFolder(title, start);
      return new Promise(resolve => errorModal("The folder picker is busy",
                                               body.error.message, () => resolve(null)));
    })
    .catch(e => { engineDown(e); return null; });
}

/** The fallback: no native picker on this machine, so the folder is typed. */
function typeFolder(title, start) {
  return new Promise(resolve => {
    showModal("", `
      <div class="wstep"><span>Folder</span></div>
      <h3>${esc(title || "Select folder")}</h3>
      <p>This copy of Python has no folder picker, so type the full path instead.</p>
      <div class="field">
        <input type="text" id="typedfolder" value="${esc(start || "")}"
               placeholder="C:\\ART_projects\\174083" style="font-family:var(--mono)">
      </div>
      <div class="actions"><div class="sp"></div>
        <button data-act="cancel">Cancel</button>
        <button class="primary" data-act="use">Use this folder</button>
      </div>`, (act) => {
        if (act === "cancel") { closeModal(); return resolve(null); }
        const typed = (document.getElementById("typedfolder").value || "").trim();
        if (!typed) return;
        closeModal();
        resolve(typed);
      });
    document.getElementById("typedfolder").addEventListener("keydown", e => {
      if (e.key === "Enter") modalBox().querySelector('[data-act="use"]').click();
    });
  });
}

/* ================================================================== Settings and About
 *
 * One modal with two tabs, opened from Home - the same screen as A.R.T.'s. Two things live
 * here: the default project folder, and the QC Hub connection the speed tool keeps for itself
 * (it never reads A.R.T.'s). The password is never read back - the engine only ever says
 * whether one is stored.
 */

let qchState = null;     // the last GET /api/settings/qch-credentials
let qchEditing = false;  // showing the form rather than what is stored
let settingsError = "";

function showSettings(tab) {
  tab = tab || "settings";
  if (tab === "about") return drawSettings("about");
  // The folder line and the QCH line are both read fresh every time this opens.
  Promise.all([refreshApp(), get("/api/settings/qch-credentials")])
    .then(([, qch]) => {
      qchState = qch;
      qchEditing = !(qch.user && qch.password_stored);
      drawSettings("settings");
    })
    .catch(engineDown);
}

function drawSettings(tab) {
  const body = tab === "settings" ? settingsBody() : aboutBody();
  showModal("", `
    <div class="mtabs">
      <button class="mtab ${tab === "settings" ? "on" : ""}" data-act="t-settings">Settings</button>
      <button class="mtab ${tab === "about" ? "on" : ""}" data-act="t-about">About</button>
    </div>
    ${body}
    <div class="actions" style="margin-top:18px"><div class="sp"></div>
      <button class="primary" data-act="ok">Close</button></div>`,
    (act) => settingsClick(act));
  if (tab === "settings" && qchEditing) wireQchForm();
}

function settingsBody() {
  const app = APP.app || {};
  const status = app.default_folder_status;
  const folderNote = status === "missing"
    ? `<div class="banner warn" style="margin:8px 0 0">The saved default project folder is no
         longer there. The folder picker will start in your Documents folder until this is set
         again.</div>`
    : status === "unset"
    ? `<div class="banner warn" style="margin:8px 0 0">No default project folder is set, so the
         folder picker starts in your Documents folder.</div>`
    : "";

  return `
    <div class="setrow">
      <div class="lbl">Default project folder</div>
      <div class="row">
        <div class="pathbox">${esc(app.default_folder || "(not set)")}</div>
        <button data-act="modify">${app.can_pick_folder === false ? "Type&hellip;" : "Modify&hellip;"}</button>
      </div>
      ${folderNote}
      <p class="note" style="margin:8px 0 0">"Open project folder" starts looking here.</p>
    </div>
    <div class="setrow" style="border-top:1px solid var(--line);padding-top:16px">
      <div class="lbl">QCH database connection</div>
      ${qchBody()}
    </div>`;
}

function qchBody() {
  const q = qchState || {};
  const devBanner = q.dev_file_in_use
    ? `<div class="banner info">A development credentials file is in use
         (<span class="mono">${esc(q.dev_file)}</span>), and it overrides whatever is saved here.
         Unset SPEED_QCH_CREDENTIALS and restart the speed tool to use the saved connection.</div>`
    : "";
  const error = settingsError
    ? `<div class="banner warn">${esc(settingsError)}</div>` : "";

  if (!qchEditing) {
    return `${devBanner}${error}
      <div class="kvalign" style="margin-bottom:10px">
        <div class="r"><div class="k">Host</div><div class="v">${esc(q.host || "")}</div></div>
        <div class="r"><div class="k">Port</div><div class="v">${esc(q.port || "3306 (default)")}</div></div>
        <div class="r"><div class="k">Database</div><div class="v">${esc(q.database || "")}</div></div>
        <div class="r"><div class="k">Username</div><div class="v">${esc(q.user || "")}</div></div>
        <div class="r"><div class="k">Password</div><div class="v">${"*".repeat(7)}</div></div>
      </div>
      <div class="row">
        <button data-act="changecreds">Change connection</button>
        <button data-act="forgetcreds">Remove connection</button>
      </div>
      <p class="note" style="margin:10px 0 0">The password is kept in Windows Credential
        Manager, never in a file. It is checked against QCH before it is stored.</p>`;
  }

  const field = (id, name, value, type, hint) => `
    <label for="${id}" style="font-size:12.5px;color:var(--ink-2)">${esc(name)}</label>
    <input class="txt" type="${type || "text"}" id="${id}" value="${esc(value || "")}"
           placeholder="${esc(hint || "")}">`;

  return `${devBanner}${error}
    <div style="display:grid;grid-template-columns:140px 1fr;gap:8px 10px;align-items:center;margin-bottom:10px">
      ${field("q-host", "Host", q.host, "text", "qch database host")}
      ${field("q-port", "Port", q.port, "text", "3306 if left blank")}
      ${field("q-db", "Database", q.database, "text", "database name")}
      ${field("q-user", "Username", q.user, "text", "QCH username")}
      ${field("q-pass", "Password", "", "password", "QCH password")}
    </div>
    <div class="row">
      <button class="primary" data-act="savecreds">Save connection</button>
      ${q.user && q.password_stored ? `<button data-act="cancelcreds">Cancel</button>` : ""}
    </div>
    <p class="note" style="margin:10px 0 0">Saving signs in to QCH first: a connection it
      refuses is never stored. Entered once - you will not be asked again. Without a connection
      the import still works; location names, lane configs and count windows are then typed in.</p>`;
}

function wireQchForm() {
  const box = document.getElementById("q-pass");
  if (box) box.addEventListener("keydown", (e) => {
    if (e.key === "Enter") modalBox().querySelector('[data-act="savecreds"]').click();
  });
}

function aboutBody() {
  const app = APP.app || {};
  return `
    <div style="text-align:center;margin-bottom:14px">
      <span class="logotile"><img src="assets/qc_logo_white.png" alt="Quality Counts"></span></div>
    <h3 style="text-align:center;margin-bottom:2px">${esc(app.title || "Intersection Speed tool")}</h3>
    <p style="text-align:center;margin-bottom:18px">Version ${esc(app.version || "")}</p>
    <div class="kv2">
      <div><span>Created for:</span><span>Quality Counts</span></div>
      <div><span>Created By:</span><span>Noah Ramsey-Smith</span></div>
      <div><span>App files:</span><span class="mono">${esc(app.app_home || "")}</span></div>
      <div><span>Documents:</span><span class="mono">${esc(app.documents || "")}</span></div>
    </div>`;
}

function settingsClick(act) {
  if (act === "t-settings") { settingsError = ""; return showSettings("settings"); }
  if (act === "t-about") { settingsError = ""; return drawSettings("about"); }
  if (act === "modify") return modifyDefaultFolder();
  if (act === "savecreds") return saveCredentials();
  if (act === "changecreds") { qchEditing = true; settingsError = ""; return drawSettings("settings"); }
  if (act === "cancelcreds") { qchEditing = false; settingsError = ""; return drawSettings("settings"); }
  if (act === "forgetcreds") return forgetCredentials();
  if (act !== "ok") return;
  settingsError = "";
  closeModal();
}

async function modifyDefaultFolder() {
  const chosen = await pickFolder("Select default project folder",
                                  (APP.app && APP.app.default_folder) || null);
  if (!chosen) return drawSettings("settings");
  try {
    // The engine writes a test file into it before saving, so a folder that cannot be written
    // to is refused here.
    const body = await put("/api/settings/default-folder", {folder: chosen});
    settingsError = body.ok ? "" : body.error.message;
    if (body.ok) toast("Default project folder saved");
  } catch (e) {
    return engineDown(e);
  }
  const keep = settingsError;
  await refreshApp();
  settingsError = keep;
  drawSettings("settings");
}

async function saveCredentials() {
  const value = (id) => (document.getElementById(id).value || "").trim();
  const payload = {
    host: value("q-host"),
    user: value("q-user"),
    database: value("q-db"),
    password: document.getElementById("q-pass").value || "",
    port: value("q-port") ? Number(value("q-port")) : null,
  };
  if (!payload.host || !payload.user || !payload.database || !payload.password) {
    settingsError = "Host, database, username and password are all needed.";
    return drawSettings("settings");
  }
  if (payload.port !== null && !Number.isInteger(payload.port)) {
    settingsError = "The port must be a whole number, or left blank for 3306.";
    return drawSettings("settings");
  }
  modalBox().querySelector('[data-act="savecreds"]').disabled = true;
  try {
    const body = await put("/api/settings/qch-credentials", payload);
    if (!body.ok) {
      settingsError = body.error.message;
      return drawSettings("settings");
    }
    settingsError = "";
    qchState = await get("/api/settings/qch-credentials");
    qchEditing = false;
    drawSettings("settings");
    toast(body.message || "QCH connection saved");
  } catch (e) {
    engineDown(e);
  }
}

async function forgetCredentials() {
  try {
    await del("/api/settings/qch-credentials");
    qchState = await get("/api/settings/qch-credentials");
    qchEditing = true;
    settingsError = "";
    drawSettings("settings");
    toast("QCH connection removed");
  } catch (e) {
    engineDown(e);
  }
}
