from pathlib import Path

import pytest

from peek.memory.store import MemoryStore, slugify


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path / "memory")


def test_slugify_basic():
    assert slugify("Hello World!") == "hello_world"
    assert slugify("  --- foo bar ---  ") == "foo_bar"
    assert slugify("") == "entry"


def test_write_and_read_round_trip(store: MemoryStore):
    e = store.write_entry(
        name="No glazing",
        description="User dislikes praise for routine work",
        type_="feedback",
        body="**Why:** style preference\n**How to apply:** keep responses dry",
    )
    assert e.filename == "feedback_no_glazing.md"

    loaded = store.read_entry(e.filename)
    assert loaded is not None
    assert loaded.name == "No glazing"
    assert loaded.type == "feedback"
    assert "Why:" in loaded.body


def test_index_line_added_and_updated(store: MemoryStore):
    store.write_entry("Role", "User is an engineer", "user", "Senior engineer.")
    text = (store.root / "MEMORY.md").read_text()
    assert "[Role](user_role.md)" in text
    assert "User is an engineer" in text

    # Update by writing same filename: hook should change, no duplicate line.
    store.write_entry("Role", "User is a senior engineer at MyUnisoft", "user",
                      "Updated body.", filename="user_role.md")
    text = (store.root / "MEMORY.md").read_text()
    assert text.count("[Role](user_role.md)") == 1
    assert "MyUnisoft" in text


def test_disambiguates_filename_on_collision(store: MemoryStore):
    a = store.write_entry("Setup", "first", "project", "body a")
    b = store.write_entry("Setup", "second", "project", "body b")
    assert a.filename == "project_setup.md"
    assert b.filename == "project_setup_2.md"


def test_delete_removes_file_and_index_line(store: MemoryStore):
    e = store.write_entry("Doomed", "to be deleted", "reference", "body")
    assert (store.root / e.filename).exists()
    assert "Doomed" in (store.root / "MEMORY.md").read_text()

    assert store.delete_entry(e.filename) is True
    assert not (store.root / e.filename).exists()
    assert "Doomed" not in (store.root / "MEMORY.md").read_text()


def test_invalid_type_rejected(store: MemoryStore):
    with pytest.raises(ValueError):
        store.write_entry("X", "y", "garbage", "body")


def test_list_entries_skips_index(store: MemoryStore):
    store.write_entry("A", "a-hook", "user", "body a")
    store.write_entry("B", "b-hook", "feedback", "body b")
    entries = store.list_entries()
    assert {e.name for e in entries} == {"A", "B"}


def test_assemble_for_prompt_includes_all_lines(store: MemoryStore):
    store.write_entry("A", "hook a", "user", "body a")
    store.write_entry("B", "hook b", "feedback", "body b")
    out = store.assemble_for_prompt()
    assert "hook a" in out and "hook b" in out


def test_read_entry_refuses_path_traversal(store: MemoryStore, tmp_path):
    # Plant a sensitive file outside the memory dir.
    secret = tmp_path / "outside.md"
    secret.write_text("---\nname: x\ndescription: x\ntype: user\n---\nSECRET",
                      encoding="utf-8")
    assert store.read_entry("../outside.md") is None
    assert store.read_entry("/etc/passwd") is None
    assert store.read_entry("..") is None


def test_delete_entry_refuses_path_traversal(store: MemoryStore, tmp_path):
    secret = tmp_path / "outside.md"
    secret.write_text("dont delete me", encoding="utf-8")
    assert store.delete_entry("../outside.md") is False
    assert secret.exists()


def test_write_entry_refuses_unsafe_explicit_filename(store: MemoryStore):
    import pytest
    with pytest.raises(ValueError):
        store.write_entry("X", "y", "user", "z", filename="../escaped.md")
    with pytest.raises(ValueError):
        store.write_entry("X", "y", "user", "z", filename="MEMORY.md")


def test_write_entry_strips_newlines_in_frontmatter_fields(store: MemoryStore):
    e = store.write_entry(
        "Multi\nline\nname",
        "Description with\n---\nfake: frontmatter",
        "user",
        "body",
    )
    assert "\n" not in e.name
    assert "\n" not in e.description
    # Round-trip: still parseable, type still 'user' (no forged frontmatter).
    loaded = store.read_entry(e.filename)
    assert loaded is not None
    assert loaded.type == "user"
