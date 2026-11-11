"""SP-1 (plan 1.4): an A.R.T. project folder is recognised and only ever read."""

from __future__ import annotations

import json

import pytest

from art_speed import project


def art_folder(tmp_path, number="174083", **fields):
    folder = tmp_path / number
    folder.mkdir()
    data = {"manifest_version": 1, "project_number": number, "project_name": "DC Vision Zero speed study",
            "client": "DDOT", "state": "DC", "nm_region": "midatlantic_northeast", "qch_lookup": "found",
            "created_utc": "2026-09-16T17:05:00Z", "created_app_version": "1.2.12", "datasets": []}
    data.update(fields)
    (folder / f"{number}.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


def test_art_project_folder_supplies_number_name_state_client(tmp_path):
    folder = art_folder(tmp_path)
    before = (folder / "174083.json").read_bytes()
    p = project.open_folder(folder)
    assert (p.number, p.name, p.state, p.client, p.is_art_project) == (
        "174083", "DC Vision Zero speed study", "DC", "DDOT", True)
    assert p.db_path == folder / "speed_174083.db"
    assert p.as_dict()["db"].endswith("speed_174083.db")
    assert (folder / "174083.json").read_bytes() == before
    assert [x.name for x in folder.iterdir()] == ["174083.json"]          # nothing was written


def test_any_other_folder_needs_the_number_and_uses_its_name(tmp_path):
    folder = tmp_path / "speed study March"
    folder.mkdir()
    with pytest.raises(project.ProjectError, match="Enter the six-digit project number"):
        project.open_folder(folder)
    p = project.open_folder(folder, "174083")
    assert (p.number, p.name, p.state, p.client, p.is_art_project) == (
        "174083", "speed study March", None, None, False)
    with pytest.raises(project.ProjectError, match="not six digits"):
        project.open_folder(folder, "1740")


def test_a_project_file_for_another_project_is_refused(tmp_path):
    folder = art_folder(tmp_path, project_number="999999")
    with pytest.raises(project.ProjectError, match="is for project '999999', not 174083"):
        project.open_folder(folder)


def test_an_art_folder_cannot_be_opened_under_another_number(tmp_path):
    with pytest.raises(project.ProjectError, match="can't be opened as 111111"):
        project.open_folder(art_folder(tmp_path), "111111")


def test_missing_folder(tmp_path):
    with pytest.raises(project.ProjectError, match="is not a folder"):
        project.open_folder(tmp_path / "nope")
