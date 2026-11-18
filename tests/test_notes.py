"""0.1.1: the internal notes box on the Speed tab - what is kept of the text, and the file.
A.R.T.'s tests/test_notes.py (1.2.13), copied, plus the speed tool's own rule for the file name."""

import json

import pytest

from art_speed import notes


@pytest.fixture
def folder(tmp_path):
    project = tmp_path / "177690"
    project.mkdir()
    return project


BOX = '<span class="cb" contenteditable="false"></span>'
TICKED = '<span class="cb" contenteditable="false" data-on></span>'


# ------------------------------------------------------------------ the subset

def test_formatting_colours_and_tick_boxes_survive():
    html = ('<div><b>Delivery</b>: <span style="color:rgb(192, 57, 43);font-weight:bold">Fri</span></div>'
            '<div><i>a</i> <u>b</u> <span style="background-color:#fff176">c</span></div>'
            f'<div>{TICKED}&nbsp;done</div><div>{BOX}&nbsp;to do</div><div><br></div>')
    assert notes.clean(html) == html.replace("&nbsp;", " ")


def test_what_the_browser_writes_for_the_toolbar_is_kept():
    html = ('<div><span style="font-weight: bold; font-style: italic; text-decoration-line: underline;'
            ' color: rgb(31, 95, 174); background-color: rgb(185, 240, 200);">x</span></div>')
    assert notes.clean(html) == ('<div><span style="font-weight:bold;font-style:italic;'
                                 'text-decoration-line:underline;color:rgb(31, 95, 174);'
                                 'background-color:rgb(185, 240, 200)">x</span></div>')


@pytest.mark.parametrize("sent, kept", [
    ('<div>a<script>alert(1)</script>b</div>', '<div>ab</div>'),
    ('<div onclick="x()">a</div>', '<div>a</div>'),
    ('<div><img src=x onerror=alert(1)>a</div>', '<div>a</div>'),
    ('<div><a href="javascript:x">link</a></div>', '<div>link</div>'),
    ('<div><span style="background-image:url(x);color:red">a</span></div>',
     '<div><span style="color:red">a</span></div>'),
    ('<div><span style="color:expression(alert(1))">a</span></div>', '<div><span>a</span></div>'),
    ('<div><span class="cb other" onclick="x" data-on="1"></span></div>', f'<div>{TICKED}</div>'),
    ('<div><span class="big">a</span></div>', '<div><span>a</span></div>'),
    ('<div>1 &lt; 2 &amp; 3 > 2</div>', '<div>1 &lt; 2 &amp; 3 &gt; 2</div>'),
    ('<h1>Title</h1><table><tr><td>cell</td></tr></table>', '<div>Titlecell</div>'),
    ('<div><b>never closed', '<div><b>never closed</b></div>'),
    ('<div>a</b></div></div>', '<div>a</div>'),
])
def test_anything_outside_the_subset_is_dropped_and_the_text_kept(sent, kept):
    assert notes.clean(sent) == kept


@pytest.mark.parametrize("sent, kept", [
    ('first<div>second</div>', '<div>first</div><div>second</div>'),
    ('<b>first</b> line<br>second', '<div><b>first</b> line</div><div>second</div>'),
    (f'{BOX} one<div>two</div>', f'<div>{BOX} one</div><div>two</div>'),
    ('<div>a</div>\n  <div>b</div>', '<div>a</div><div>b</div>'),
    ('a<br><br>b', '<div>a</div><div><br></div><div>b</div>'),
])
def test_every_line_is_its_own_div(sent, kept):
    assert notes.clean(sent) == kept
    assert notes.clean(kept) == kept                     # and cleaning again changes nothing


@pytest.mark.parametrize("sent", ["", None, "<div><br></div>", "<div> &nbsp; </div><div><br></div>",
                                  "   ", "<div><b></b></div>"])
def test_only_empty_lines_is_an_empty_box(sent):
    assert notes.clean(sent) == ""


def test_a_tick_box_alone_is_not_empty():
    assert notes.clean(f"<div>{BOX}</div>") == f"<div>{BOX}</div>"


def test_not_text_or_far_too_long_is_refused():
    with pytest.raises(notes.NotesError):
        notes.clean(["a"])
    with pytest.raises(notes.NotesError, match="too long"):
        notes.clean("a" * (notes.MAX_CHARS + 1))


# ------------------------------------------------------------------ the file

def test_no_file_is_an_empty_box_and_reading_writes_nothing(folder):
    assert notes.read(folder) == notes.Notes()
    assert list(folder.iterdir()) == []


def test_write_then_read(folder):
    saved = notes.write(folder, f"<div>{TICKED}&nbsp;done</div><script>x</script>")
    assert saved.html == f"<div>{TICKED} done</div>" and saved.saved_utc.endswith("Z")
    assert notes.read(folder) == saved
    on_disk = json.loads((folder / "177690_notes.json").read_text(encoding="utf-8"))
    assert on_disk == {"notes_version": 1, "html": saved.html, "saved_utc": saved.saved_utc}
    assert sorted(p.name for p in folder.iterdir()) == ["177690_notes.json"]    # no .bak, no .tmp


def test_clearing_the_box_is_saved_as_empty(folder):
    notes.write(folder, "<div>something</div>")
    assert notes.write(folder, "<div><br></div>").html == ""
    assert notes.read(folder).html == ""


def test_a_hand_edited_file_is_cleaned_on_the_way_in(folder):
    (folder / "177690_notes.json").write_text(json.dumps(
        {"notes_version": 1, "html": "<div onclick='x'>a<script>b</script></div>",
         "saved_utc": "2026-11-17T20:35:00Z"}), encoding="utf-8")
    assert notes.read(folder) == notes.Notes("<div>a</div>", "2026-11-17T20:35:00Z")


def test_a_damaged_file_is_set_aside_and_the_box_starts_empty(folder):
    (folder / "177690_notes.json").write_text("{not json", encoding="utf-8")
    assert notes.read(folder) == notes.Notes()
    assert (folder / "177690_notes.json.bad").exists()
    assert notes.write(folder, "<div>again</div>").html == "<div>again</div>"


def test_the_project_file_is_never_touched(folder):
    project_file = folder / "177690.json"
    project_file.write_text("{}", encoding="utf-8")
    notes.write(folder, "<div>a</div>")
    assert project_file.read_text(encoding="utf-8") == "{}"


def test_the_file_is_named_with_the_project_number_even_in_a_folder_that_is_not(tmp_path):
    """The speed tool opens any folder with a typed number; the notes file follows the number."""
    folder = tmp_path / "some speed job"
    folder.mkdir()
    saved = notes.write(folder, "<div>call back</div>", "174083")
    assert (folder / "174083_notes.json").is_file()
    assert notes.read(folder, "174083").html == saved.html == "<div>call back</div>"
    assert notes.read(folder).html == ""                    # not under the folder's own name
