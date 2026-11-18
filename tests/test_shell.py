"""SP-6: the shell - the tool's own settings and QC Hub login, recent projects, one project open."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from art_speed import config, dialogs, main, paths, session
from art_speed.appstate import recent, settings
from art_speed.qch import connection, credentials
from tests import helpers
from tests.test_project import art_folder


@pytest.fixture
def client():
    return TestClient(main.create_app())


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        del self.store[(service, user)]


@pytest.fixture
def keyring(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: kr)
    return kr


def test_the_tool_keeps_its_own_state_apart_from_art(client, speed_home, monkeypatch):
    info = client.get("/api/app").json()
    assert (info["name"], info["version"], info["project"]) == ("Speed tool", config.VERSION, None)
    assert info["app_home"] == str(speed_home) and info["default_folder_status"] == "unset"
    monkeypatch.delenv(config.ENV_HOME)
    assert paths.app_home().name == "SpeedTool"                      # never ...\\ART
    assert credentials.KEYRING_SERVICE == "Speed tool QCH"


def test_qch_connection_is_checked_then_saved_password_in_the_credential_manager(client, keyring, monkeypatch, speed_home):
    assert client.get("/api/settings/qch-credentials").json() == {
        "ok": True, "host": None, "port": None, "user": None, "database": None,
        "password_stored": False, "dev_file_in_use": False, "dev_file": None}
    body = {"host": "qch.example", "user": "nsmith", "database": "qcdb", "password": "s3cret", "port": None}
    monkeypatch.setattr(connection, "check", lambda creds=None: (False, "refused", connection.REFUSED))
    refused = client.put("/api/settings/qch-credentials", json=body).json()
    assert refused["ok"] is False and refused["error"]["kind"] == "credentials_refused"
    assert keyring.store == {} and settings.qch_connection() == {}            # nothing was saved
    monkeypatch.setattr(connection, "check", lambda creds=None: (True, "connected", None))
    saved = client.put("/api/settings/qch-credentials", json=body).json()
    assert saved["ok"] and saved["checked"] and saved["outcome"] == "connected"
    assert keyring.store == {("Speed tool QCH", "nsmith"): "s3cret"}
    on_disk = (speed_home / "settings.json").read_text()
    assert "s3cret" not in on_disk and json.loads(on_disk)["qch"] == {
        "host": "qch.example", "user": "nsmith", "database": "qcdb", "port": 3306}
    shown = client.get("/api/settings/qch-credentials").json()
    assert shown["password_stored"] is True and "password" not in shown and shown["user"] == "nsmith"
    assert credentials.load().safe == "nsmith@qch.example:3306/qcdb"         # what the import will use
    monkeypatch.setattr(connection, "check", lambda creds=None: (False, "no VPN", connection.UNREACHABLE))
    offline = client.put("/api/settings/qch-credentials", json=dict(body, password="other")).json()
    assert offline["ok"] and offline["outcome"] == "saved_unchecked"          # saved, to be used on the network
    assert client.delete("/api/settings/qch-credentials").json() == {"ok": True}
    assert keyring.store == {} and settings.qch_connection() == {}


def test_default_folder(client, tmp_path):
    out = client.put("/api/settings/default-folder", json={"folder": str(tmp_path)}).json()
    assert out == {"ok": True, "folder": str(tmp_path)}
    assert client.get("/api/app").json()["default_folder_status"] == "ok"
    bad = client.put("/api/settings/default-folder", json={"folder": str(tmp_path / "nope")}).json()
    assert bad["ok"] is False and bad["error"]["kind"] == "folder_rejected"


def test_open_an_art_project_folder_and_it_is_saved_to_recent(client, tmp_path):
    folder = art_folder(tmp_path)
    out = client.post("/api/projects/open", json={"folder": str(folder)}).json()
    assert out["ok"] and out["project"] == {
        "project_number": "174083", "project_name": "DC Vision Zero speed study", "client": "DDOT",
        "state": "DC", "folder": str(folder), "db": str(folder / "speed_174083.db"),
        "is_art_project": True, "has_speed_data": False}
    assert client.get("/api/app").json()["project"]["project_number"] == "174083"
    assert client.get("/speed/project").json()["number"] == "174083"          # the router sees the same project
    cards = client.get("/api/projects/recent").json()["projects"]
    assert [(c["project_number"], c["project_name"], c["folder"]) for c in cards] == [
        ("174083", "DC Vision Zero speed study", str(folder))]
    status = client.get(f"/api/projects/recent/{cards[0]['id']}/status").json()
    assert status["found"] is True and status["has_speed_data"] is False
    assert sorted(p.name for p in folder.iterdir()) == ["174083.json"]         # opening writes nothing there


def test_any_other_folder_asks_for_the_project_number(client, tmp_path):
    folder = tmp_path / "speed study March"
    folder.mkdir()
    asked = client.post("/api/projects/open", json={"folder": str(folder)}).json()
    assert asked["ok"] is False and asked["error"]["kind"] == "needs_number"
    assert client.get("/api/app").json()["project"] is None
    bad = client.post("/api/projects/open", json={"folder": str(folder), "project_number": "17"}).json()
    assert bad["error"]["kind"] == "open_refused" and "not six digits" in bad["error"]["message"]
    out = client.post("/api/projects/open", json={"folder": str(folder), "project_number": "174083"}).json()
    assert out["project"]["project_name"] == "speed study March" and out["project"]["is_art_project"] is False
    card = client.get("/api/projects/recent").json()["projects"][0]
    assert card["project_number"] == "174083"                                  # reopening needs no retyping
    assert client.post("/api/projects/open", json={"folder": card["folder"],
                                                   "project_number": card["project_number"]}).json()["ok"]


def test_one_project_open_at_a_time_and_a_refused_open_leaves_it_alone(client, tmp_path):
    a = art_folder(tmp_path)
    b = tmp_path / "other"
    b.mkdir()
    client.post("/api/projects/open", json={"folder": str(a)})
    client.post("/api/projects/open", json={"folder": str(tmp_path / "missing")})
    assert session.peek().number == "174083"
    client.post("/api/projects/open", json={"folder": str(b), "project_number": "111111"})
    assert session.peek().number == "111111"
    assert [c["project_number"] for c in client.get("/api/projects/recent").json()["projects"]] == ["111111", "174083"]
    assert client.post("/api/project/close").json() == {"ok": True}
    assert client.get("/api/app").json()["project"] is None
    assert client.get("/speed/locations").status_code == 409


def test_a_card_whose_folder_has_gone_and_remove_from_list(client, tmp_path):
    folder = tmp_path / "gone"
    folder.mkdir()
    client.post("/api/projects/open", json={"folder": str(folder), "project_number": "222222"})
    card = client.get("/api/projects/recent").json()["projects"][0]
    folder.rmdir()
    assert client.get(f"/api/projects/recent/{card['id']}/status").json()["found"] is False
    note = client.get("/api/app").json()                                       # the open project is closed, with a note
    assert note["project"] is None and "was closed because its folder is no longer" in note["notes"][0]
    out = client.delete(f"/api/projects/recent/{card['id']}").json()
    assert out["ok"] and out["removed"] and recent.entries() == []
    assert client.delete(f"/api/projects/recent/{card['id']}").json()["error"]["kind"] == "no_such_card"


def test_the_folder_picker_and_reveal(client, tmp_path, monkeypatch):
    monkeypatch.setattr(dialogs, "pick_folder", lambda title, start: str(tmp_path))
    assert client.post("/api/dialog/folder", json={"title": "x"}).json() == {
        "ok": True, "folder": str(tmp_path), "cancelled": False}
    monkeypatch.setattr(dialogs, "pick_folder", lambda title, start: None)
    assert client.post("/api/dialog/folder", json={}).json()["cancelled"] is True
    assert client.post("/api/project/reveal", json={}).json()["error"]["kind"] == "no_project"
    shown = []
    monkeypatch.setattr(dialogs, "reveal", lambda target: shown.append(str(target)))
    folder = art_folder(tmp_path)
    (folder / "reports_2026_11_12__0800").mkdir()
    client.post("/api/projects/open", json={"folder": str(folder)})
    assert client.post("/api/project/reveal", json={"report_folder": "reports_2026_11_12__0800"}).json()["ok"]
    assert client.post("/api/project/reveal", json={"report_folder": "..\\\\elsewhere"}).json()["error"]["kind"] == "no_such_folder"
    assert shown == [str(folder / "reports_2026_11_12__0800")]


def test_the_page_is_served_and_the_whole_flow_runs_through_it(client, tmp_path):
    assert "<title>Speed tool</title>" in client.get("/").text
    for name in ("speed.css", "shell.js", "speed.js"):
        assert client.get(f"/{name}").status_code == 200
    folder = tmp_path / "p"
    folder.mkdir()
    csvs = tmp_path / "csv"
    csvs.mkdir()
    helpers.write_csv(csvs, 17408304, 1, helpers.speed_rows("NB", [20, 30]))
    client.post("/api/projects/open", json={"folder": str(folder), "project_number": "174083"})
    assert client.post("/speed/import", json={"folder": str(csvs), "use_qch": False}).json()["status"] == "imported"
    assert client.get("/api/app").json()["project"]["has_speed_data"] is True
    assert len(client.get("/speed/locations").json()["rows"]) == 1


def test_new_project_is_arts_wizard_and_writes_an_art_project_folder(tmp_path):
    """New project: number -> QCH lookup -> folder -> create. With no QCH connection the lookup
    lands in the connection_failed branch and the project is created from the number alone."""
    from fastapi.testclient import TestClient

    from art_speed import main
    client = TestClient(main.create_app())
    assert client.post("/api/qch/lookup", json={"number": "12"}).json()["error"]["kind"] == "bad_number"
    looked = client.post("/api/qch/lookup", json={"number": "174083"}).json()
    assert looked["ok"] and looked["branch"]["branch"] == "connection_failed"
    assert "continue" in looked["branch"]["choices"]
    assert client.get("/api/create/default-folder").json()["source"] == "fallback"       # none set yet
    assert client.post("/api/create/check", json={"number": "174083", "folder": str(tmp_path)}).json()["free"]

    made = client.post("/api/create", json={"number": "174083", "folder": str(tmp_path)}).json()
    assert made["ok"] and made["project"]["project_number"] == "174083"
    assert made["project"]["is_art_project"] and not made["project"]["has_speed_data"]
    folder = tmp_path / "174083"
    assert (folder / "174083.json").is_file()                       # A.R.T.'s project file
    assert client.get("/api/app").json()["project"]["folder"] == str(folder)      # and it is the open one
    assert [c["project_number"] for c in client.get("/api/projects/recent").json()["projects"]] == ["174083"]
    assert client.get("/speed/locations").json()["rows"] == []      # ready for Add speed data

    again = client.post("/api/create", json={"number": "174083", "folder": str(tmp_path)}).json()
    assert not again["ok"] and again["error"]["kind"] == "exists" and again["branch"]["branch"] == "exists"


def test_the_file_picker_route_answers_like_the_folder_picker(monkeypatch):
    from fastapi.testclient import TestClient

    from art_speed import dialogs, main
    client = TestClient(main.create_app())
    monkeypatch.setattr(dialogs, "pick_files", lambda title, start, types: ["C:\\x\\17408301_cam-1_speed.csv"])
    out = client.post("/api/dialog/files", json={"title": "t"}).json()
    assert out["ok"] and out["files"] == ["C:\\x\\17408301_cam-1_speed.csv"] and out["cancelled"] is False
    monkeypatch.setattr(dialogs, "pick_files", lambda title, start, types: None)
    assert client.post("/api/dialog/files", json={}).json() == {"ok": True, "files": [], "cancelled": True}


def test_internal_notes_start_empty_save_cleaned_and_touch_nothing_else(tmp_path):
    """0.1.1: the Speed tab's notes box. What comes back is what was stored, cleaned."""
    from fastapi.testclient import TestClient

    from art_speed import main
    client = TestClient(main.create_app())
    assert client.get("/api/project/notes").json()["error"]["kind"] == "no_project"
    assert client.put("/api/project/notes", json={"html": "x"}).json()["error"]["kind"] == "no_project"
    folder = tmp_path / "a speed job"
    folder.mkdir()
    assert client.post("/api/projects/open", json={"folder": str(folder), "project_number": "174083"}).json()["ok"]
    assert client.get("/api/project/notes").json() == {"ok": True, "notes": {"html": "", "saved_utc": ""}}

    sent = '<div onclick="x"><b>Refilm</b> site 04<script>x</script></div>'
    body = client.put("/api/project/notes", json={"html": sent}).json()
    assert body["ok"] and body["notes"]["html"] == "<div><b>Refilm</b> site 04</div>" and body["notes"]["saved_utc"]
    assert client.get("/api/project/notes").json()["notes"] == body["notes"]
    assert sorted(p.name for p in folder.iterdir()) == ["174083_notes.json"]       # its own file, nothing else made
    too_long = client.put("/api/project/notes", json={"html": "a" * 200_001})
    assert too_long.status_code == 200 and too_long.json()["error"]["kind"] == "not_saved"
