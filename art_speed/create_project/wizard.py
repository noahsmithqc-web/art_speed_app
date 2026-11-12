"""Create, open and edit a project.

Everything the wizard screens do happens here, so every branch can be run from the command
line before a screen is wired to it. The wizard keeps nothing on disk while someone clicks
through it: the answers live in the page, and one call to `create` does all the writing.

The write sequence (plan 4):

  1  build the project file in memory and validate it   nothing has touched disk
  2  check the parent folder: there, a folder, answers within ~10 s, path <= 240 chars
  3  create the one ###### folder - a single create-or-fail step, parents are never created -
     and the empty bike_ped_paths inside it, where the path photos go (R4-4)
  4  write ######.json.tmp, flush, close, rename
  5  read it back and validate
  6  add the project to the recent projects list        failure here is a warning only
  7  hand back the result for the Summary screen

Undo removes only what this attempt created - at most the one folder, the empty bike_ped_paths
inside it and the file. It never deletes recursively and never touches a folder that was already there (Q11).

The second half of the file (C5) is the wizard itself: one step function per screen, each
reporting which branch it landed in and which buttons the mockup offers there. The command
line and C7's screens both go through these, so the two cannot drift apart - which is why the
branching moved out of `create_project/cli.py` and into here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from art_speed import config, fsguard, paths
from art_speed.appstate import recent, settings
from art_speed.log import get_logger
from art_speed import manifest
from art_speed.qch import lookup as qch_lookup
from art_speed.qch import regions
PATHS_FOLDER = "bike_ped_paths"      # A.R.T. makes this folder in every new project; so does this copy

log = get_logger(__name__)

# Sentinel for edit(): leaving a field out is not the same as clearing it.
KEEP = object()


class WizardError(Exception):
    """Something the user needs to be told about, with a message written for them."""


class ProjectExists(WizardError):
    """A ###### folder is already there (Q11). The app never writes into a folder it didn't create."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        super().__init__(f"Project {self.folder.name} already exists in "
                         f"{self.folder.parent}. Choose another folder or another number.")


class FolderProblem(WizardError):
    """The parent folder is missing, unreachable, or can't be written to."""


class OpenRefused(WizardError):
    """The folder is not an A.R.T. project folder, or its file is unreadable (Q12)."""


@dataclass
class Created:
    manifest: manifest.Manifest
    folder: Path                     # the ###### folder
    file: Path
    in_recent: bool = True
    warnings: list = field(default_factory=list)


# ------------------------------------------------------------------ helpers

def full_path(folder: str | os.PathLike) -> Path:
    """A folder as the app stores it - see paths.full_path, which is the one copy of the rule."""
    return paths.full_path(folder)


def project_folder(parent_folder: str | os.PathLike, project_number: str) -> Path:
    """<save folder>\\######. Nothing is created here."""
    if not paths.is_valid_project_number(project_number):
        raise WizardError(f"{project_number!r} is not a six-digit project number.")
    return full_path(parent_folder) / project_number


def default_parent() -> str:
    """The saved default project folder, or Documents when there isn't one (Q14)."""
    folder, status = settings.default_folder()
    if status == settings.OK and folder:
        return folder
    return str(paths.documents_folder())


def already_exists(parent_folder: str | os.PathLike, project_number: str) -> bool:
    """The early "already exists" check. Re-run it after any folder change (Q15).

    Any existing ###### entry counts, folder or file: the app will not write into something
    it did not create.
    """
    folder = project_folder(parent_folder, project_number)
    try:
        return fsguard.exists(folder)
    except fsguard.FsTimeout:
        raise FolderProblem(f"{folder.parent} did not respond. If it is a network drive, "
                            "check that you are connected.") from None
    except OSError as exc:
        raise FolderProblem(
            f"{folder.parent} could not be checked ({exc.strerror or exc}).") from exc


def _check_parent(parent: Path, target: Path) -> None:
    """Step 2: the parent is there, it is a folder, and the path will fit."""
    paths.check_path_length(manifest.file_in(target))      # raises PathTooLong
    try:
        if not fsguard.is_dir(parent):
            if fsguard.exists(parent):
                raise FolderProblem(f"{parent} is a file, not a folder.")
            raise FolderProblem(f"{parent} is not a folder on this computer. "
                                "Choose an existing folder.")
    except fsguard.FsTimeout:
        raise FolderProblem(f"{parent} did not respond within "
                            f"{config.FS_TIMEOUT_S:g} seconds. If it is a network drive, "
                            "check that you are connected.") from None
    except OSError as exc:
        raise FolderProblem(f"{parent} could not be checked ({exc.strerror or exc}).") from exc


def _undo(folder: Path, file: Path | None) -> None:
    """Remove only what this attempt created: the file, then the one folder."""
    if file is not None:
        for leftover in (file, file.with_suffix(file.suffix + ".tmp")):
            try:
                if leftover.exists():
                    leftover.unlink()
                    log.info("undo: removed %s", leftover)
            except OSError as exc:
                log.warning("undo: could not remove %s (%s)", leftover, exc)
    try:
        if (folder / PATHS_FOLDER).is_dir():
            os.rmdir(folder / PATHS_FOLDER)  # the empty photos folder this attempt made
        os.rmdir(folder)                     # never recursive: fails if anything is left
        log.info("undo: removed %s", folder)
    except OSError as exc:
        log.warning("undo: could not remove %s (%s)", folder, exc)


def _cleared(value):
    """Edit rule for a text field: trimmed, and blank (or None) clears it."""
    if value is None:
        return None
    return str(value).strip() or None


# ------------------------------------------------------------------ create

def create(project_number: str, parent_folder: str | os.PathLike | None = None, *,
           project_name: str | None = None, client: str | None = None,
           state: str | None = None, qch_lookup: str = manifest.SKIPPED,
           created_utc: str | None = None, lookup: Lookup | None = None) -> Created:
    """Create <parent>\\######\\######.json. See the write sequence at the top of the file.

    `lookup` is the remembered QCH answer for this number (C5). It fills the three fields QCH
    knows about and decides what goes in `qch_lookup`; anything passed in explicitly wins over
    it, which is how the command line's --name / --client / --state work.
    """
    if lookup is not None:
        project_name = project_name or lookup.project_name
        client = client or lookup.client
        state = state or lookup.state
        qch_lookup = lookup.recorded
    parent = full_path(parent_folder or default_parent())

    # 1 - in memory, before anything touches disk
    try:
        record = manifest.Manifest(project_number=project_number, project_name=project_name,
                                   client=client, state=state, qch_lookup=qch_lookup,
                                   created_utc=created_utc or manifest.now_utc(),
                                   created_app_version=config.VERSION)
    except manifest.ManifestError as exc:
        raise WizardError(str(exc)) from exc
    folder = parent / record.project_number

    # 2 - the parent folder
    try:
        _check_parent(parent, folder)
    except paths.PathTooLong as exc:
        raise FolderProblem(str(exc)) from exc

    # 3 - one folder, create-or-fail, parents are never created
    try:
        fsguard.make_dir(folder)
    except FileExistsError as exc:
        raise ProjectExists(folder) from exc
    except fsguard.FsTimeout as exc:
        raise FolderProblem(f"{parent} did not respond while creating {record.project_number}. "
                            "Nothing was created.") from exc
    except OSError as exc:
        raise FolderProblem(f"Cannot create {folder} ({exc.strerror or exc}).") from exc
    file = manifest.file_in(folder)
    try:                                     # the photos folder (R4-4): empty, the user fills it
        fsguard.make_dir(folder / PATHS_FOLDER)
    except (OSError, fsguard.FsTimeout) as exc:
        log.error("create %s failed making %s (%s); undoing", record.project_number, PATHS_FOLDER, exc)
        _undo(folder, None)
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise FolderProblem(f"Cannot create {folder / PATHS_FOLDER} ({reason}). Nothing was left behind.") from exc

    # 4 and 5 - write it, then read it back
    try:
        manifest.write(folder, record)
        checked = manifest.read(folder, record.project_number)
    except (OSError, fsguard.FsTimeout, manifest.ManifestError, paths.PathTooLong) as exc:
        log.error("create %s failed while writing (%s); undoing", record.project_number, exc)
        _undo(folder, file)
        if isinstance(exc, paths.PathTooLong):
            raise FolderProblem(str(exc)) from exc
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise FolderProblem(f"Cannot write to {folder} ({reason}). Nothing was left behind.") \
            from exc

    log.info("created project %s in %s", checked.project_number, folder)

    # 6 - the recent projects list. A failure here never undoes the project (Q13).
    result = Created(manifest=checked, folder=folder, file=file)
    try:
        recent.touch(checked.project_number, folder, checked.project_name)
    except (OSError, fsguard.FsTimeout, ValueError) as exc:
        log.warning("project %s was created but not added to the recent list (%s)",
                    checked.project_number, exc)
        result.in_recent = False
        result.warnings.append(f"Project {checked.project_number} created, but it couldn't be "
                               "added to Recent projects.")
    return result


# ------------------------------------------------------------------ open

def open_project(folder: str | os.PathLike, project_number: str | None = None) -> manifest.Manifest:
    """Open an existing project folder. Refuses anything that isn't one (Q12); no repair."""
    path = full_path(folder)
    try:
        if not fsguard.is_dir(path):
            raise OpenRefused(f"{path} is not a folder on this computer.")
    except fsguard.FsTimeout as exc:
        raise OpenRefused(f"{path} did not respond. If it is a network drive, check that "
                          "you are connected.") from exc
    except OSError as exc:
        raise OpenRefused(f"{path} could not be checked ({exc.strerror or exc}).") from exc

    try:
        record = manifest.read(path, project_number)
    except manifest.ManifestError as exc:
        raise OpenRefused(str(exc)) from exc

    try:
        recent.touch(record.project_number, path, record.project_name)
    except (OSError, fsguard.FsTimeout, ValueError) as exc:
        log.warning("opened %s but could not update the recent list (%s)",
                    record.project_number, exc)
    log.info("opened project %s in %s", record.project_number, path)
    return record


# ------------------------------------------------------------------ edit

def edit(folder: str | os.PathLike, *, project_name=KEEP, client=KEEP,
         state=KEEP) -> manifest.Manifest:
    """Summary's Edit button (Q22).

    Only name, client and state can change; each can be cleared by passing None. The number,
    created date, folder and the internal fields are read-only, and nm_region follows state.
    Leaving an argument out leaves that field alone.
    """
    path = full_path(folder)
    try:
        record = manifest.read(path)
    except manifest.ManifestError as exc:
        raise OpenRefused(str(exc)) from exc

    if project_name is not KEEP:
        record.project_name = _cleared(project_name)
    if client is not KEEP:
        record.client = _cleared(client)
    if state is not KEEP:
        try:
            record.state = regions.clean_state(state)
        except regions.InvalidState as exc:
            raise WizardError(str(exc)) from exc

    try:
        manifest.write(path, record)
    except paths.PathTooLong as exc:
        raise FolderProblem(str(exc)) from exc
    except (OSError, fsguard.FsTimeout) as exc:
        reason = exc.strerror if isinstance(exc, OSError) and exc.strerror else exc
        raise FolderProblem(f"Cannot save {manifest.file_in(path).name} ({reason}). "
                            "The project file is unchanged.") from exc

    saved = manifest.read(path, record.project_number)
    # The Home-screen card shows the name, so it follows the edit.
    try:
        recent.touch(saved.project_number, path, saved.project_name)
    except (OSError, fsguard.FsTimeout, ValueError) as exc:
        log.warning("edited %s but could not update the recent list (%s)",
                    saved.project_number, exc)
    log.info("edited project %s in %s", saved.project_number, path)
    return saved


# ================================================================== the wizard, step by step
#
# One function per stop in the mockup, each reporting the branch it landed in and the buttons
# that branch offers. `run` walks them in order, which is what the command line calls and what
# C6's endpoints will mirror one at a time.

# The buttons, named once so a screen and the command line can't invent two sets.
CANCEL = "cancel"                  # Cancel
CONTINUE = "continue"              # "Create project" / "Continue without details"
RE_ENTER = "re_enter"              # Re-enter project number
RETRY = "retry"                    # Retry
CHANGE_FOLDER = "change_folder"    # Change folder…

# Where the folder the wizard offers came from (plan §4, Q14).
FROM_SETTINGS = "settings"
FROM_FALLBACK = "fallback"

# A fourth lookup outcome the three in qch/lookup.py don't need: --skip-qch, and the C7
# screens' equivalent, never ask QCH at all.
NOT_RUN = "not_run"


class Cancelled(WizardError):
    """The user backed out of the wizard. Nothing was created."""

    def __init__(self, message: str = "Cancelled - nothing was created."):
        super().__init__(message)


@dataclass
class Branch:
    """A fork in the wizard: what happened, what to say, and what the user may do about it."""
    step: str                  # lookup | folder | write
    branch: str                # the row in the plan's branch table
    message: str
    choices: tuple             # in the order the mockup shows the buttons
    default: str               # what happens when nobody is at the keyboard

    def __str__(self) -> str:
        return self.message


@dataclass
class Lookup:
    """What QCH said about a number - the one thing the engine remembers between steps.

    The wizard is a minute's work, so the answer is held until the number changes (Q35): going
    back and forth between the folder step and the confirmation doesn't ask QCH again, and
    Retry asks it afresh.
    """
    number: str
    outcome: str                                  # found | not_found | connection_failed | not_run
    project_name: str | None = None
    client: str | None = None
    state: str | None = None
    notes: list = field(default_factory=list)
    message: str = ""

    @property
    def found(self) -> bool:
        return self.outcome == qch_lookup.FOUND

    @property
    def nm_region(self) -> str | None:
        return regions.region_for(self.state)

    @property
    def recorded(self) -> str:
        """What goes in the project file's `qch_lookup`. A failed connection is `skipped`, not
        a fourth value (Q30): the file records what the lookup found, and it found nothing."""
        if self.outcome in (qch_lookup.FOUND, manifest.NOT_FOUND):
            return self.outcome
        return manifest.SKIPPED

    @property
    def branch(self) -> Branch | None:
        """The pop-up this outcome puts on the screen, or None when there is nothing to ask."""
        if self.outcome == manifest.NOT_FOUND:
            return Branch("lookup", manifest.NOT_FOUND, self.message,
                          (CANCEL, RE_ENTER, CONTINUE), default=CONTINUE)
        if self.outcome == qch_lookup.CONNECTION_FAILED:
            # Cancel added in C7 (Q49): the mockup's pop-up had only Retry and Continue, which
            # left no way out of the wizard but forward while QCH was down. The default stays
            # Continue, so an unattended run still creates with the number only.
            return Branch("lookup", qch_lookup.CONNECTION_FAILED, self.message,
                          (CANCEL, RETRY, CONTINUE), default=CONTINUE)
        return None


@dataclass
class FolderStep:
    """The folder the wizard offers before anyone changes it."""
    folder: str
    source: str                    # FROM_SETTINGS | FROM_FALLBACK
    warning: str | None = None


@dataclass
class Check:
    """The early "already exists" check (Q15), re-run after every folder change."""
    folder: Path                   # the ###### folder, not its parent
    exists: bool
    path_ok: bool
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.exists and self.path_ok

    @property
    def branch(self) -> Branch | None:
        """The pop-up this check puts on the screen, or None when the folder is free.

        The mockup's "already exists" offers three: {Cancel, Pick a different folder, Re-enter
        project number}. Path-too-long is ours, and a different number would not help there -
        only a shorter folder will.
        """
        if self.exists:
            return Branch("folder", "exists", self.message,
                          (CANCEL, CHANGE_FOLDER, RE_ENTER), default=CANCEL)
        if not self.path_ok:
            return Branch("folder", "path_too_long", self.message,
                          (CHANGE_FOLDER, CANCEL), default=CANCEL)
        return None


def write_branch(message: str) -> Branch:
    """The write-error pop-up. Three buttons by Q39, where the mockup had one OK: Try again is
    what you want when a network drive comes back."""
    return Branch("write", "write_failed", message, (RETRY, CHANGE_FOLDER, CANCEL),
                  default=CANCEL)


# The remembered lookup: one number, one answer.
_remembered: Lookup | None = None


def check_number(number) -> str:
    """Six digits, as text. This runs again at create time, never only on the way in."""
    text = str(number).strip()
    if not paths.is_valid_project_number(text):
        raise WizardError(f"Project number {number!r} is not six digits. "
                          "It is six digits and nothing else, for example 177690.")
    return text


def forget_lookup() -> None:
    """Drop the remembered QCH answer (a new number, or a wizard that was closed)."""
    global _remembered
    _remembered = None


def remembered_lookup(number: str | None = None) -> Lookup | None:
    """The remembered answer, if it is for this number."""
    if _remembered is None:
        return None
    if number is not None and _remembered.number != check_number(number):
        return None
    return _remembered


def look_up(number: str, *, fresh: bool = False, qch=None) -> Lookup:
    """The QCH step. Never raises: the outcome carries the answer (C4).

    The remembered result is used when it is for this number, unless `fresh` is asked for -
    which is what Retry does.
    """
    global _remembered
    number = check_number(number)
    if not fresh:
        held = remembered_lookup(number)
        if held is not None:
            log.info("QCH lookup for %s: using the answer from a moment ago (%s)",
                     number, held.outcome)
            return held

    result = qch_lookup.lookup(number, qch)
    _remembered = Lookup(number=number, outcome=result.outcome,
                         project_name=result.project_name, client=result.client,
                         state=result.state, notes=list(result.notes),
                         message=result.message)
    return _remembered


def skip_lookup(number: str) -> Lookup:
    """--skip-qch, and whatever C7 calls it on the screen. Records `skipped`, asks nothing."""
    global _remembered
    _remembered = Lookup(number=check_number(number), outcome=NOT_RUN,
                         message="QCH was not asked about this project.")
    return _remembered


def default_folder_step() -> FolderStep:
    """The folder the wizard offers, and why (Q14).

    A saved folder that has since gone is not silently swapped: the wizard offers Documents
    and says what happened, so nobody creates a project somewhere they didn't mean to.
    """
    folder, status = settings.default_folder()
    if status == settings.OK and folder:
        return FolderStep(folder=folder, source=FROM_SETTINGS)
    documents = str(paths.documents_folder())
    if status == settings.MISSING and folder:
        log.warning("default project folder %s is no longer there; offering %s", folder,
                    documents)
        return FolderStep(documents, FROM_FALLBACK,
                          f"The default project folder ({folder}) is no longer there, so "
                          f"{documents} is offered instead. Set a new one in Settings.")
    return FolderStep(documents, FROM_FALLBACK,
                      f"No default project folder is set, so {documents} is offered. "
                      "Set one in Settings to skip this step next time.")


def check(number: str, parent_folder: str | os.PathLike) -> Check:
    """Is this number free in this folder, and will the path fit? (plan §4, Q15)"""
    folder = project_folder(parent_folder, check_number(number))
    exists = already_exists(folder.parent, folder.name)     # raises FolderProblem
    path_ok, message = True, ""
    try:
        paths.check_path_length(manifest.file_in(folder))
    except paths.PathTooLong as exc:
        path_ok, message = False, str(exc)
    if exists:
        message = str(ProjectExists(folder))
    return Check(folder=folder, exists=exists, path_ok=path_ok, message=message)


def _choose(branch: Branch, decide) -> str:
    """Ask whoever is driving - a person at a prompt, a flag, or nobody at all."""
    if decide is None:
        return branch.default
    choice = decide(branch)
    if choice is None:
        return branch.default
    if choice not in branch.choices:
        raise WizardError(f"{choice!r} is not one of {', '.join(branch.choices)}.")
    return choice


def run(number: str, parent_folder: str | os.PathLike | None = None, *,
        skip_qch: bool = False, project_name: str | None = None, client: str | None = None,
        state: str | None = None, decide=None, ask_number=None, ask_folder=None,
        qch=None) -> Created:
    """The whole wizard, from the number to a created project.

    Every branch in the plan's branch table is reachable from this one call. `decide(branch)`
    picks a button - a prompt, a command-line flag, or nothing at all, in which case each
    branch takes its own default and the wizard runs straight through. `ask_number` and
    `ask_folder` supply the two answers a button alone can't carry.

    Nothing is written until the last step, and that step re-checks the number, the folder and
    whether the project already exists rather than trusting what an earlier step saw.
    """
    number = check_number(number)
    folder = str(parent_folder) if parent_folder else None

    while True:
        outcome = _one_pass(number, folder, skip_qch=skip_qch, project_name=project_name,
                            client=client, state=state, decide=decide, ask_number=ask_number,
                            ask_folder=ask_folder, qch=qch)
        if isinstance(outcome, Created):
            return outcome
        # A Re-enter from the "already exists" pop-up: a new number, the same folder. The
        # folder is kept on purpose - what clashed was the number, and the project almost
        # certainly still belongs where they put it. (The mockup resets it; this doesn't.)
        number, folder = outcome


def _one_pass(number: str, folder: str | None, *, skip_qch, project_name, client, state,
              decide, ask_number, ask_folder, qch):
    """One run through the wizard. Returns the created project, or (number, folder) to start
    over because the user asked to re-enter the number."""

    def _new_number() -> str:
        if ask_number is None:
            raise WizardError("Re-enter needs a new project number.")
        fresh_number = check_number(ask_number())
        forget_lookup()
        return fresh_number

    # -- the QCH step, which Re-enter and Retry both loop back to
    fresh = False
    while True:
        record = skip_lookup(number) if skip_qch else look_up(number, fresh=fresh, qch=qch)
        fresh = False
        branch = record.branch
        if branch is None:
            break
        choice = _choose(branch, decide)
        if choice == CANCEL:
            raise Cancelled()
        if choice == RETRY:
            fresh = True
            continue
        if choice == RE_ENTER:
            number = _new_number()
            continue
        break                                   # Continue / Create project

    # -- the folder step
    if folder is None:
        step = default_folder_step()
        folder = step.folder
        if ask_folder is not None:
            folder = str(ask_folder(step) or step.folder)

    # -- the confirmation step: check, create, and stay here until it works or is given up on
    while True:
        result = check(number, folder)
        if not result.ok:
            branch = result.branch
            choice = _choose(branch, decide)
            if choice == RE_ENTER:
                return _new_number(), folder
            if choice == CHANGE_FOLDER and ask_folder is not None:
                folder = str(ask_folder(FolderStep(folder, FROM_SETTINGS, result.message))
                             or folder)
                continue
            if result.exists:
                raise ProjectExists(result.folder)
            raise FolderProblem(result.message)

        try:
            return create(number, folder, lookup=remembered_lookup(number),
                          project_name=project_name, client=client, state=state)
        except ProjectExists:
            continue                            # someone else got there between the two steps
        except FolderProblem as exc:
            choice = _choose(write_branch(str(exc)), decide)
            if choice == RETRY:
                continue
            if choice == CHANGE_FOLDER and ask_folder is not None:
                folder = str(ask_folder(FolderStep(folder, FROM_SETTINGS, str(exc))) or folder)
                continue
            raise
