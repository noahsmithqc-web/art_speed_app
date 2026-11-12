/* New project: A.R.T.'s create-project wizard (its wizard.js, see COPIED_FROM_ART.md), wired to
 * the same engine routes. What it creates is an A.R.T. project folder, so A.R.T. opens it too.
 *
 * The page keeps the answers (number, folder, what QCH said) and nothing else - closing the app
 * half way through leaves nothing on disk. The engine keeps one thing, the last QCH answer for
 * that number, which is why stepping back and forth does not ask QCH again (Q35).
 *
 * Every pop-up here is drawn from the `branch` the engine sent back, so the buttons a failure
 * offers are decided in one place and the command line reads the same table (C5). Nothing in
 * this file decides that, say, "already exists" offers three buttons - it just renders them.
 */
"use strict";

const WSTEP = {
  number: "Create project &middot; step 1 of 3",
  folder: "Create project &middot; step 2 of 3",
  confirm: "Create project &middot; step 3 of 3",
};

function startWizard() {
  APP.wiz = {number: "", lookup: null, folder: null, source: null, warning: null};
  stepNumber();
}

function cancelWizard() {
  APP.wiz = null;
  closeModal();
  APP.tab = "home";
  render();
}

/* ---------------------------------------------------------------- [e2] the project number */

function stepNumber() {
  const w = APP.wiz;
  showModal("", `
    <div class="wstep"><span>${WSTEP.number}</span><span class="cmd">[e2]</span></div>
    <h3>Enter the project number</h3>
    <p>Six-digit QCH project number. Everything else is looked up for you.</p>
    <div class="field">
      <input type="text" id="pnum" maxlength="6" inputmode="numeric" placeholder="&ndash;&ndash;&ndash;&ndash;&ndash;&ndash;"
             value="${esc(w.number)}">
      <div class="numhint" id="phint"></div>
    </div>
    <div class="actions"><div class="sp"></div>
      <button data-act="cancel">Cancel</button>
      <button class="primary" id="pgo" data-act="continue" disabled>Continue</button>
    </div>`, (act) => {
      if (act === "cancel") return cancelWizard();
      if (act === "continue") {
        const v = (document.getElementById("pnum").value || "").trim();
        if (!/^\d{6}$/.test(v)) return;
        w.number = v;
        stepLookup();
      }
    });

  // Six digits and nothing else - the same rule the engine applies, said early so nobody
  // waits on QCH to be told about a typo. The engine checks it again regardless.
  const box = document.getElementById("pnum");
  const go = document.getElementById("pgo");
  const hint = document.getElementById("phint");
  const check = () => {
    box.value = box.value.replace(/\D/g, "").slice(0, 6);
    const ok = /^\d{6}$/.test(box.value);
    go.disabled = !ok;
    hint.textContent = ok ? "✓" : `${box.value.length} of 6 digits`;
    hint.className = "numhint" + (ok ? " ok" : "");
  };
  box.addEventListener("input", check);
  box.addEventListener("keydown", e => { if (e.key === "Enter" && !go.disabled) go.click(); });
  check();
}

/* ---------------------------------------------------------------- [e6] the QCH lookup */

async function stepLookup(fresh) {
  const w = APP.wiz;
  spinnerModal(WSTEP.number, "[e6]", `Looking up ${w.number} in QCH`,
               "Querying project name, client, state, and NM region…");
  let body;
  try {
    body = await post("/api/qch/lookup", {number: w.number, fresh: !!fresh});
  } catch (e) {
    return engineDown(e);
  }
  if (!body.ok) return errorModal("That project number will not do", body.error.message,
                                  () => stepNumber());
  w.lookup = body;

  if (!body.branch) return stepFolder();

  // not_found {Cancel, Re-enter, Create project} or connection_failed {Cancel, Retry, Continue}.
  branchModal(body.branch, (choice) => {
    if (choice === "cancel") return cancelWizard();
    if (choice === "re_enter") { w.number = ""; return stepNumber(); }
    if (choice === "retry") return stepLookup(true);
    stepFolder();                                   // continue: carry on with what we have
  }, {
    title: body.branch.branch === "not_found"
      ? `Project ${w.number} not found in QCH`
      : "Connection to QCH failed",
    bad: body.branch.branch === "connection_failed",
    note: body.branch.branch === "not_found"
      ? "The project will be created with only the project number. Name, client, state and "
        + "NM region will be blank."
      : "You can still create the project with just the project number - name, client, state "
        + "and NM region will be left blank. Check the QCH connection in Settings.",
  });
}

/* ---------------------------------------------------------------- the folder the app offers */

async function stepFolder() {
  const w = APP.wiz;
  if (w.folder) return stepCheck();                 // already chosen once this run
  let body;
  try {
    body = await get("/api/create/default-folder");
  } catch (e) {
    return engineDown(e);
  }
  w.folder = body.folder;
  w.source = body.source;
  // A saved folder that has gone is not silently swapped: the engine offers Documents and says
  // what happened, and that sentence rides along on the confirm step (Q14).
  w.warning = body.warning || null;
  stepConfirmFolder();
}

/* ---------------------------------------------------------------- [e5] confirm the folder */

function stepConfirmFolder() {
  const w = APP.wiz;
  showModal("", `
    <div class="wstep"><span>${WSTEP.folder}</span><span class="cmd">[e5]</span></div>
    <h3>Confirm save folder</h3>
    <p>Confirm or modify the save folder. A new subfolder will be created using the project
      number. Folder and file names cannot be modified.</p>
    ${w.warning ? `<div class="banner warn">${esc(w.warning)}</div>` : ""}
    <div class="pathbox">${esc(w.folder)}\\<span class="new">${esc(w.number)}</span></div>
    <div style="height:12px"></div>
    <div class="actions">
      <button data-act="cancel">Cancel</button><div class="sp"></div>
      <button data-act="modify">Modify folder</button>
      <button class="primary" data-act="confirm">Confirm</button>
    </div>`, (act) => {
      if (act === "cancel") return cancelWizard();
      if (act === "modify") return changeFolder(stepCheck, stepConfirmFolder);
      stepCheck();
    });
}

/** The picker, then whatever comes next. Cancelling the picker goes back where it came from. */
async function changeFolder(onPicked, onCancel) {
  const chosen = await pickFolder("Select save folder", APP.wiz.folder);
  if (!chosen) return onCancel();
  APP.wiz.folder = chosen;
  APP.wiz.warning = null;                             // they have chosen; the old note is spent
  onPicked();
}

/* ---------------------------------------------------------------- does it already exist? */

async function stepCheck() {
  const w = APP.wiz;
  let body;
  try {
    body = await post("/api/create/check", {number: w.number, folder: w.folder});
  } catch (e) {
    return engineDown(e);
  }
  if (!body.ok) {
    // A folder that cannot even be looked at: no branch, so one message and back a step.
    return errorModal("That folder cannot be used", body.error.message,
                      () => stepConfirmFolder());
  }
  if (!body.branch) return stepConfirm();

  // exists {Cancel, Pick a different folder, Re-enter} or path_too_long {Pick..., Cancel}.
  branchModal(body.branch, (choice) => {
    if (choice === "cancel") return cancelWizard();
    // Q47: a clash is about the number, so the folder they picked is kept.
    if (choice === "re_enter") { w.number = ""; return stepNumber(); }
    if (choice === "change_folder") return changeFolder(stepCheck, stepConfirmFolder);
  }, {
    title: body.branch.branch === "exists"
      ? `Project ${w.number} already exists in this folder`
      : "That path is too long",
  });
}

/* ---------------------------------------------------------------- [e4] the last look */

function stepConfirm() {
  const w = APP.wiz;
  const q = w.lookup || {};
  const known = q.outcome === "found";
  const val = (v) => v ? `<div class="v">${esc(v)}</div>` : `<div class="v blank">(blank)</div>`;
  showModal("", `
    <div class="wstep"><span>${WSTEP.confirm}</span><span class="cmd">[e4]</span></div>
    <h3>Create project ${esc(w.number)}?</h3>
    <p>These values come from QCH and cannot be edited here.</p>
    <div class="pathbox" style="margin-bottom:14px">${esc(w.folder)}\\<span class="new">${esc(w.number)}</span></div>
    <div class="kvalign">
      <div class="r"><div class="k">Project number</div>${val(w.number)}</div>
      <div class="r"><div class="k">Project name</div>${val(q.project_name)}</div>
      <div class="r"><div class="k">Client</div>${val(q.client)}</div>
      <div class="r"><div class="k">State</div>${val(q.state)}</div>
      <div class="r"><div class="k">NM region</div>${val(q.nm_region_label)}</div>
    </div>
    <div class="banner ${known ? "info" : "warn"}" style="margin-bottom:18px">
      ${known
        ? "Pulled from QCH. Blank fields are OK."
        : "QCH had nothing for this number, so only the project number is filled in. Blank "
          + "fields are OK."}
    </div>
    <div class="actions">
      <button data-act="cancel">Cancel</button><div class="sp"></div>
      <button data-act="modify">Modify folder</button>
      <button class="primary" data-act="create">Create project</button>
    </div>`, (act) => {
      if (act === "cancel") return cancelWizard();
      if (act === "modify") return changeFolder(stepCheck, stepConfirm);
      stepWrite();
    });
}

/* ---------------------------------------------------------------- [w] the write */

async function stepWrite() {
  const w = APP.wiz;
  spinnerModal(WSTEP.confirm, "[w]", `Creating project ${w.number}`,
               `Creating the folder and writing ${w.number}.json…`);
  let body;
  try {
    // Only the number and the folder: the engine fills the rest from the QCH answer it
    // remembers, so a page that sat open cannot create from something staler.
    body = await post("/api/create", {number: w.number, folder: w.folder});
  } catch (e) {
    return engineDown(e);
  }

  if (!body.ok) {
    if (!body.branch) {
      return errorModal("The project was not created", body.error.message,
                        () => stepConfirm());
    }
    // write_failed {Retry, Pick a different folder, Cancel} (Q39), or a late "already exists".
    return branchModal(body.branch, (choice) => {
      if (choice === "cancel") return cancelWizard();
      if (choice === "retry") return stepWrite();
      if (choice === "change_folder") return changeFolder(stepCheck, stepConfirm);
      if (choice === "re_enter") { w.number = ""; return stepNumber(); }
    }, {
      title: body.branch.branch === "write_failed"
        ? "Cannot write to the selected folder"
        : `Project ${w.number} already exists in this folder`,
      bad: body.branch.branch === "write_failed",
      note: body.branch.branch === "write_failed"
        ? "Anything this attempt created has been removed. Nothing was left behind."
        : null,
    });
  }

  APP.wiz = null;
  APP.project = body.project;
  closeModal();
  APP.tab = "speed";
  speedReset();
  render();
  loadRecent();
  const warnings = body.warnings || [];
  toast(warnings.length ? warnings[0] : `Project ${body.project.project_number} created`);
}
