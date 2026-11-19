"""The speed tool's web app: the shell around the speed router (plan 1.4 section 8, SP-6).

    app = main.create_app()

What the shell adds to the router (art_speed/api.py, mounted unchanged at /speed):

    GET    /api/app                              the tool, its settings, and the open project
    PUT    /api/settings/default-folder          where "Open project folder" starts
    GET    /api/settings/qch-credentials         the saved QC Hub connection (never the password)
    PUT    /api/settings/qch-credentials         check, then save (password to Credential Manager)
    DELETE /api/settings/qch-credentials
    POST   /api/dialog/folder                    the native folder picker
    GET    /api/projects/recent                  the Home cards
    GET    /api/projects/recent/{id}/status      one card: is the folder there, has it speed data
    DELETE /api/projects/recent/{id}             "Remove from list" (the folder is never touched)
    POST   /api/projects/open                    {folder, project_number?}
    POST   /api/project/close
    POST   /api/project/reveal                   {report_folder?}: open it in File Explorer

These /api routes answer the way A.R.T.'s do - {ok: true, ...} or {ok: false, error: {kind,
message}} with 200 - because the Home and Settings screens are A.R.T.'s, brought across (see
COPIED_FROM_ART.md). The /speed routes keep their own shapes (the contract).

Settings, the recent projects list and the log live in the tool's own folder
(%LOCALAPPDATA%\\SpeedTool); the QC Hub password in Windows Credential Manager under
"Speed tool QCH". Nothing is shared with, read from or written to A.R.T.'s.
"""

from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from art_speed import api, config, dialogs, fsguard, jsonfile, notes, paths, project as project_mod, session
from art_speed.appstate import recent, settings
from art_speed.log import get_logger, setup_logging
from art_speed.create_project import wizard
from art_speed.qch import credentials, regions
from art_speed.storage import db

log = get_logger(__name__)


def _ok(**fields) -> dict:
    return {"ok": True, **fields}


def _fail(kind: str, message: str, branch=None, **fields) -> dict:
    return {"ok": False, "error": {"kind": kind, "message": message},
            "branch": _branch(branch), **fields}


def _branch(branch) -> Optional[dict]:
    """A wizard branch: which pop-up New project is in, its message and its buttons."""
    if branch is None:
        return None
    return {"step": branch.step, "branch": branch.branch, "message": branch.message,
            "choices": list(branch.choices), "default": branch.default}


def _project(opened: project_mod.Project) -> dict:
    try:
        has_data = db.exists(opened.folder, opened.number)
    except db.SpeedDbError:
        has_data = False
    return {"project_number": opened.number, "project_name": opened.name, "client": opened.client,
            "state": opened.state, "folder": str(opened.folder), "db": str(opened.db_path),
            "is_art_project": opened.is_art_project, "has_speed_data": has_data}


class FolderIn(BaseModel):
    folder: str


class OpenIn(BaseModel):
    folder: str
    project_number: Optional[str] = None


class NumberIn(BaseModel):
    number: str
    fresh: bool = False                 # Retry: ask QCH again instead of reusing the answer


class CheckIn(BaseModel):
    number: str
    folder: str


class CreateIn(BaseModel):
    number: str
    folder: Optional[str] = None


class NotesIn(BaseModel):
    html: Optional[str] = None


class PickIn(BaseModel):
    title: Optional[str] = None
    start: Optional[str] = None


class CredentialsIn(BaseModel):
    host: str
    user: str
    database: str
    password: str
    port: Optional[int] = None


class RevealIn(BaseModel):
    report_folder: Optional[str] = None      # a reports_yyyy_mm_dd__hhmm name inside the project


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title=config.APP_TITLE, version=config.VERSION,
                  docs_url=None, redoc_url=None, openapi_url=None)

    # -------------------------------------------------------------- the app itself

    @app.get("/api/app")
    def app_info() -> dict:
        loaded = settings.load()
        folder, status = settings.default_folder()
        gone = session.still_there()
        opened = session.peek()
        return {"name": config.APP_NAME, "title": config.APP_TITLE, "version": config.VERSION,
                "app_home": str(paths.app_home()), "app_home_source": paths.app_home_source(),
                "documents": str(paths.documents_folder()),
                "default_folder": folder, "default_folder_status": status,
                "can_pick_folder": dialogs.can_pick(),
                "notes": list(loaded.notes) + ([gone] if gone else []),
                "project": _project(opened) if opened else None}

    # -------------------------------------------------------------- settings

    @app.put("/api/settings/default-folder")
    def set_default_folder(body: FolderIn) -> dict:
        try:
            saved = settings.set_default_folder(body.folder)
        except settings.FolderRejected as exc:
            return _fail("folder_rejected", str(exc))
        return _ok(folder=saved)

    @app.get("/api/settings/qch-credentials")
    def get_qch_credentials() -> dict:
        saved = settings.qch_connection()
        dev = credentials.dev_file()
        user = saved.get("user")
        return _ok(host=saved.get("host"), port=saved.get("port"), user=user,
                   database=saved.get("database"),
                   # Never the password itself - only whether one is there to be used.
                   password_stored=bool(user and credentials.stored_password(user)),
                   dev_file_in_use=dev is not None, dev_file=str(dev) if dev else None)

    @app.put("/api/settings/qch-credentials")
    def put_qch_credentials(body: CredentialsIn) -> dict:
        # The login is checked before it is stored, so a password QC Hub refuses is never written.
        saved = credentials.save_connection(host=body.host, user=body.user, database=body.database,
                                            password=body.password, port=body.port)
        if saved.outcome == credentials.REJECTED:
            return _fail("credentials_refused", saved.message)
        return _ok(outcome=saved.outcome, message=saved.message, checked=saved.ok)

    @app.delete("/api/settings/qch-credentials")
    def delete_qch_credentials() -> dict:
        saved = settings.qch_connection()
        if saved.get("user"):
            credentials.forget_password(saved["user"])
        settings.clear_qch_connection()
        return _ok()

    # -------------------------------------------------------------- the native folder picker

    @app.post("/api/dialog/folder")
    async def pick_folder(body: PickIn) -> dict:
        """On a worker thread, so the app keeps answering while the picker is open."""
        try:
            chosen = await run_in_threadpool(dialogs.pick_folder, body.title or "Select folder", body.start)
        except dialogs.DialogBusy as exc:
            return _fail("dialog_busy", str(exc))
        except dialogs.DialogUnavailable as exc:
            return _fail("no_dialog", str(exc))
        return _ok(folder=chosen, cancelled=chosen is None)

    @app.post("/api/dialog/files")
    async def pick_files(body: PickIn) -> dict:
        """Add speed data: several speed CSVs at once, from one folder."""
        try:
            chosen = await run_in_threadpool(
                dialogs.pick_files, body.title or "Select files", body.start,
                (("Speed CSV", "*_speed*.csv"), ("CSV", "*.csv"), ("All files", "*.*")))
        except dialogs.DialogBusy as exc:
            return _fail("dialog_busy", str(exc))
        except dialogs.DialogUnavailable as exc:
            return _fail("no_dialog", str(exc))
        return _ok(files=chosen or [], cancelled=chosen is None)

    # -------------------------------------------------------------- recent projects

    def _entry(card_id: str):
        return next((e for e in recent.entries() if e.card_id == card_id), None)

    @app.get("/api/projects/recent")
    def recent_projects() -> dict:
        loaded = recent.load()
        cards = [{"id": e.card_id, "project_number": e.project_number, "project_name": e.project_name,
                  "folder": e.folder, "last_opened_utc": e.last_opened_utc} for e in loaded.data]
        return _ok(projects=cards, notes=list(loaded.notes), recovered=loaded.recovered)

    @app.get("/api/projects/recent/{card_id}/status")
    def recent_status(card_id: str) -> dict:
        """One card's pill, read from that folder. Called per card after the cards are on the
        screen, so a folder on a sleeping drive delays its own pill and nothing else."""
        entry = _entry(card_id)
        if entry is None:
            return _fail("no_such_card", "That project is no longer in the recent list.")
        try:
            there = fsguard.is_dir(entry.folder)
            has_data = there and db.exists(entry.folder, entry.project_number)
        except (OSError, fsguard.FsTimeout, db.SpeedDbError) as exc:
            log.info("recent card %s could not be read (%s)", entry.project_number, exc)
            there, has_data = False, False
        if not there:
            return _ok(id=card_id, found=False, has_speed_data=False,
                       message=f"Folder not found: {entry.folder}")
        return _ok(id=card_id, found=True, has_speed_data=has_data, project_name=entry.project_name)

    @app.delete("/api/projects/recent/{card_id}")
    def forget_recent(card_id: str) -> dict:
        """Only the list entry goes; the project folder, wherever it is, is never touched."""
        entry = _entry(card_id)
        if entry is None:
            return _fail("no_such_card", "That project is no longer in the recent list.")
        try:
            removed = recent.remove(entry.folder)
        except (OSError, jsonfile.JsonFileError, fsguard.FsTimeout) as exc:
            log.warning("could not remove %s from the recent list (%s)", entry.folder, exc)
            return _fail("not_removed", f"Could not update the recent projects list ({exc}).")
        return _ok(removed=removed, project_number=entry.project_number, folder=entry.folder)

    @app.post("/api/projects/open")
    def open_project(body: OpenIn) -> dict:
        try:
            opened = session.open_folder(body.folder, body.project_number)
        except session.NeedsNumber as exc:
            return _fail("needs_number", str(exc))
        except project_mod.ProjectError as exc:
            return _fail("open_refused", str(exc))
        return _ok(project=_project(opened))

    # -------------------------------------------------------------- New project (A.R.T.'s wizard)

    @app.post("/api/qch/lookup")
    async def qch_lookup_route(body: NumberIn) -> dict:
        try:
            number = wizard.check_number(body.number)
        except wizard.WizardError as exc:
            return _fail("bad_number", str(exc))
        # The lookup waits on a database, so it goes off the request thread.
        record = await run_in_threadpool(wizard.look_up, number, fresh=body.fresh)
        return _ok(outcome=record.outcome, project_name=record.project_name,
                   client=record.client, state=record.state, nm_region=record.nm_region,
                   nm_region_label=regions.label_for(record.nm_region),
                   notes=list(record.notes), message=record.message,
                   qch_lookup=record.recorded, branch=_branch(record.branch))

    @app.get("/api/create/default-folder")
    def create_default_folder() -> dict:
        step = wizard.default_folder_step()
        return _ok(folder=step.folder, source=step.source, warning=step.warning)

    @app.post("/api/create/check")
    def create_check(body: CheckIn) -> dict:
        try:
            result = wizard.check(body.number, body.folder)
        except wizard.WizardError as exc:
            return _fail("bad_number" if "six digits" in str(exc) else "folder_problem", str(exc))
        return _ok(folder=str(result.folder), exists=result.exists, path_ok=result.path_ok,
                   free=result.ok, message=result.message, branch=_branch(result.branch))

    @app.post("/api/create")
    async def create_project(body: CreateIn) -> dict:
        """The last step. It re-checks the number, the folder and the clash, and fills name,
        client and state from the QCH answer the engine remembers for this number. What it
        writes is an A.R.T. project folder: ######\\######.json, so A.R.T. opens it too."""
        try:
            number = wizard.check_number(body.number)
        except wizard.WizardError as exc:
            return _fail("bad_number", str(exc))
        folder = body.folder or wizard.default_folder_step().folder
        try:
            result = await run_in_threadpool(wizard.create, number, folder,
                                             lookup=wizard.remembered_lookup(number))
        except wizard.ProjectExists as exc:
            return _fail("exists", str(exc), wizard.check(number, folder).branch)
        except wizard.FolderProblem as exc:
            return _fail("write_failed", str(exc), wizard.write_branch(str(exc)))
        except wizard.WizardError as exc:
            return _fail("not_created", str(exc))
        try:
            opened = session.open_folder(result.folder)
        except project_mod.ProjectError as exc:
            return _fail("open_refused", str(exc))
        return _ok(project=_project(opened), warnings=list(result.warnings), in_recent=result.in_recent)

    # -------------------------------------------------------------- internal notes (0.1.1)

    @app.get("/api/project/notes")
    def get_notes() -> dict:
        """The Speed tab's internal notes box. No file yet is an empty box."""
        opened = session.peek()
        if opened is None:
            return _fail("no_project", str(session.NoProjectOpen()))
        return _ok(notes=notes.read(opened.folder, opened.number).to_dict())

    @app.put("/api/project/notes")
    def save_notes(body: NotesIn) -> dict:
        """Save, and a tick made while the box is not being edited. What comes back is what
        was stored, which is what the page shows from then on."""
        opened = session.peek()
        if opened is None:
            return _fail("no_project", str(session.NoProjectOpen()))
        try:
            saved = notes.write(opened.folder, body.html, opened.number)
        except notes.NotesError as exc:
            return _fail("not_saved", str(exc))
        return _ok(notes=saved.to_dict())

    @app.post("/api/project/close")
    def close_project() -> dict:
        session.close()
        return _ok()

    @app.post("/api/project/reveal")
    def reveal(body: RevealIn) -> dict:
        opened = session.peek()
        if opened is None:
            return _fail("no_project", str(session.NoProjectOpen()))
        target = opened.folder
        if body.report_folder:
            name = body.report_folder
            if not name.startswith("reports_") or "/" in name or "\\" in name or not (target / name).is_dir():
                return _fail("no_such_folder", f"{name} is not a report folder of this project.")
            target = target / name
        try:
            dialogs.reveal(target)
        except dialogs.DialogUnavailable as exc:
            return _fail("no_dialog", str(exc))
        return _ok(folder=str(target))

    # -------------------------------------------------------------- the speed router
    app.include_router(api.create_router(session.peek))

    # Web pages last, so /api/* and /speed/* are never shadowed.
    @app.middleware("http")
    async def no_stale_pages(request: Request, call_next):
        """The page files are small and change with every version: make the browser re-check."""
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/speed/")):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.mount("/", StaticFiles(directory=paths.resource_path("web"), html=True), name="web")
    log.info("app created (version %s)", config.VERSION)
    return app
