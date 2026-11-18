/* Internal notes (0.1.1) - A.R.T.'s notes box (the notes part of its summary.js, 1.2.13; see
 * COPIED_FROM_ART.md), drawn under the locations table on the Speed tab.
 *
 * The box at the bottom of the Speed tab: text for the team that never reaches a report. Edit opens
 * it with a toolbar; Save and Cancel close it. A tick box can be ticked without Edit, and that
 * tick is saved on the spot.
 *
 * APP.inotes is what the engine last stored for the open project; APP.inEdit is the draft while the
 * box is open, kept up to date as the user types so a trip to another tab finds it still there
 * (A.R.T.'s rule for its Edit form). The engine rebuilds whatever is sent from a small fixed
 * subset of HTML (project/notes.py), and that is the only thing ever drawn from APP.inotes.
 */
"use strict";

const stamp = (utc) => utc ? utc.replace("T", " ").replace(/:\d\dZ$/, " UTC") : "";

/** Draw the box. Called by the shell every time the Speed tab is drawn, and by the box itself. */
function renderNotes() {
  const wrap = document.getElementById("noteswrap");
  if (!wrap) return;
  if (!APP.project) { wrap.innerHTML = ""; return; }
  wrap.innerHTML = notesSection(notesFor(APP.project));
  wireNotes(wrap);
  if (!wrap.onclick) wrap.onclick = (e) => {
    const act = e.target.dataset.act;
    if (act === "notes-edit") return notesEdit();
    if (act === "notes-cancel") { APP.inEdit = null; return renderNotes(); }
    if (act === "notes-save") return notesSave();
  };
}


const NOTE_COLOURS = [["#1c2128", "Black (default)"], ["#c0392b", "Red"], ["#d9730d", "Orange"],
                      ["#2f6f4e", "Green"], ["#1f5fae", "Blue"], ["#7b3fa0", "Purple"]];
const NOTE_HIGHLIGHTS = [["#fff176", "Yellow"], ["#b9f0c8", "Green"], ["#bfe0ff", "Blue"],
                         ["#ffc9d6", "Pink"]];

/** The open project's notes, or null while they are still being read. */
function notesFor(p) {
  if (APP.inEdit && APP.inEdit.number !== p.project_number) APP.inEdit = null;
  if (APP.inotes && APP.inotes.number === p.project_number) return APP.inotes;
  loadNotes(p.project_number);
  return null;
}

async function loadNotes(number) {
  if (loadNotes.busy === number) return;
  loadNotes.busy = number;
  try {
    const body = await get("/api/project/notes");
    if (!APP.project || APP.project.project_number !== number) return;   // another project by now
    const n = body.ok ? body.notes : {html: "", saved_utc: ""};
    APP.inotes = {number: number, html: n.html, saved_utc: n.saved_utc};
    if (APP.tab === "speed") renderNotes();
  } catch (e) {
    console.error(e);
  } finally {
    loadNotes.busy = null;
  }
}

function notesSection(n) {
  const editing = !!APP.inEdit;
  const html = editing ? APP.inEdit.html : (n ? n.html : "");
  const bar = !editing ? "" : `<div class="inbar">
      <button class="fb b" data-cmd="bold" title="Bold (Ctrl+B)">B</button>
      <button class="fb i" data-cmd="italic" title="Italic (Ctrl+I)">I</button>
      <button class="fb u" data-cmd="underline" title="Underline (Ctrl+U)">U</button>
      <span class="div"></span><span class="lbl">Text</span>
      ${NOTE_COLOURS.map(([c, name]) =>
        `<button class="sw" data-fg="${c}" style="background:${c}" title="${name}"></button>`).join("")}
      <span class="div"></span><span class="lbl">Highlight</span>
      <button class="sw hl none" data-bg="transparent" title="No highlight"></button>
      ${NOTE_HIGHLIGHTS.map(([c, name]) =>
        `<button class="sw hl" data-bg="${c}" style="background:${c}" title="${name}"></button>`).join("")}
      <span class="div"></span>
      <button class="txt" data-cb="1" title="Insert a checkbox where the cursor is"><span class="cb" data-on></span>Checkbox</button>
      <button class="txt clr" data-cmd="removeFormat" title="Remove formatting from the selection">Clear formatting</button>
    </div>`;
  return `<section class="inotes">
    <div class="hd">
      <div><h3>Internal notes</h3>
        <div class="hint">For the team only. Never printed on the workbook or any export.</div></div>
      <div class="sp"></div>
      <span class="saved" id="in-saved">${n && n.saved_utc ? "Last saved " + esc(stamp(n.saved_utc)) : ""}</span>
      ${editing
        ? `<button class="sm" data-act="notes-cancel">Cancel</button>
           <button class="sm primary" data-act="notes-save">Save</button>`
        : `<button class="sm" data-act="notes-edit" ${n ? "" : "disabled"}>Edit</button>`}
    </div>
    <div class="inbox ${editing ? "editing" : ""}">
      ${bar}
      <div class="intext" id="in-text" contenteditable="${editing}" spellcheck="true"
           data-ph="No internal notes yet. Click Edit to add some.">${html}</div>
    </div>
  </section>`;
}

const noteCaret = (node, atEnd) => {
  const r = document.createRange();
  r.selectNodeContents(node);
  r.collapse(!atEnd);
  const s = getSelection();
  s.removeAllRanges();
  s.addRange(r);
};

function wireNotes(screen) {
  const text = screen.querySelector("#in-text");
  if (!text) return;
  const editing = !!APP.inEdit;
  text.classList.toggle("blank", !editing && !!APP.inotes
                                 && !text.textContent.trim() && !text.querySelector(".cb"));
  const keep = () => { if (APP.inEdit) APP.inEdit.html = text.innerHTML; };

  /* A tick works whether or not the box is open. Closed, it is saved on the spot; open, it
     waits for Save (and goes back with Cancel) like everything else. */
  text.addEventListener("click", (e) => {
    const box = e.target.closest(".cb");
    if (!box) return;
    box.toggleAttribute("data-on");
    if (APP.inEdit) return keep();
    notesTick(text, box);
  });
  if (!editing) return;

  /* The toolbar never takes the focus, so the selection in the notes survives the click. */
  const bar = screen.querySelector(".inbar");
  bar.addEventListener("mousedown", (e) => e.preventDefault());
  bar.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    document.execCommand("styleWithCSS", false, true);
    if (b.dataset.cmd) document.execCommand(b.dataset.cmd);
    if (b.dataset.fg) document.execCommand("foreColor", false, b.dataset.fg);
    if (b.dataset.bg) document.execCommand("hiliteColor", false, b.dataset.bg);
    if (b.dataset.cb) noteInsertBox(text);
    keep();
    noteMarks();
  });

  text.addEventListener("input", (e) => {
    if (!text.firstChild) {                    // everything deleted: every line is its own block
      text.innerHTML = "<div><br></div>";
      noteCaret(text.firstChild, false);
    }
    if (e.inputType === "insertParagraph") noteNextLine(text);
    keep();
  });
  /* Paste comes in as plain text, so nothing from Word or Outlook drags its own fonts in. */
  text.addEventListener("paste", (e) => {
    e.preventDefault();
    document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
  });
}

/** B / I / U light up for what the cursor is in. */
function noteMarks() {
  const bar = document.querySelector(".inotes .inbar");
  if (!bar) return;
  ["bold", "italic", "underline"].forEach(c =>
    bar.querySelector(`[data-cmd="${c}"]`).classList.toggle("on", document.queryCommandState(c)));
}
document.addEventListener("selectionchange", noteMarks);

function noteInsertBox(text) {
  const s = getSelection();
  if (!s.rangeCount || !text.contains(s.anchorNode)) return;
  const r = s.getRangeAt(0);
  r.deleteContents();
  const box = document.createElement("span");
  box.className = "cb";
  box.contentEditable = "false";
  const gap = document.createTextNode(" ");
  r.insertNode(gap);
  r.insertNode(box);
  if (box.previousSibling && box.previousSibling.nodeName === "BR") box.previousSibling.remove();   // the empty line's placeholder
  r.setStartAfter(gap);
  r.collapse(true);
  s.removeAllRanges();
  s.addRange(r);
}

/** Whether a line opens with a tick box (it may sit inside a coloured or bold span). */
function noteStartsWithBox(line) {
  const box = line.querySelector(".cb");
  if (!box) return false;
  const r = document.createRange();
  r.setStart(line, 0);
  r.setEndBefore(box);
  return !r.toString().trim();
}

/** Enter on a tick-box line starts the next line with a tick box; Enter on an empty one ends
 *  the list. Called once the browser has made the new line. */
function noteNextLine(text) {
  const s = getSelection();
  let line = s.anchorNode;
  while (line && line.parentNode !== text) line = line.parentNode;
  const prev = line && line.previousSibling;
  if (!(prev && prev.nodeType === 1 && noteStartsWithBox(prev))) return;
  if (prev.textContent.trim()) return noteInsertBox(text);
  prev.innerHTML = "<br>";
  line.remove();
  noteCaret(prev, false);
}

function notesEdit() {
  const n = APP.inotes;
  if (!n || !APP.project || n.number !== APP.project.project_number) return;
  const html = n.html || "<div><br></div>";
  APP.inEdit = {number: n.number, html: html, was: html};
  renderNotes();
  const text = document.getElementById("in-text");
  if (!text) return;
  text.focus();
  noteCaret(text.lastElementChild || text, true);
}

/** Whether the open notes box holds something Save has not stored (app.js asks before leaving). */
function notesAreDirty() {
  return !!APP.inEdit && APP.inEdit.html !== APP.inEdit.was;
}

function notesStored(saved) {
  APP.inotes = {number: APP.project.project_number, html: saved.html, saved_utc: saved.saved_utc};
}

async function notesSave() {
  const text = document.getElementById("in-text");
  if (!APP.inEdit) return;
  if (text) APP.inEdit.html = text.innerHTML;
  try {
    const body = await put("/api/project/notes", {html: APP.inEdit.html});
    if (!body.ok) return errorModal("Could not save the internal notes", body.error.message);
    notesStored(body.notes);
    APP.inEdit = null;
    renderNotes();
    toast("Internal notes saved");
  } catch (e) {
    engineDown(e);
  }
}

/** A tick made while the box is closed: stored straight away, and only the stamp is redrawn. */
async function notesTick(text, box) {
  try {
    const body = await put("/api/project/notes", {html: text.innerHTML});
    if (!body.ok) {
      box.toggleAttribute("data-on");
      return errorModal("Could not save the internal notes", body.error.message);
    }
    notesStored(body.notes);
    const at = document.getElementById("in-saved");
    if (at) at.textContent = "Last saved " + stamp(body.notes.saved_utc);
  } catch (e) {
    box.toggleAttribute("data-on");
    engineDown(e);
  }
}
