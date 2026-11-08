"""Tests for the document core: types, DocumentStore, spec/ADR types and registry."""
from __future__ import annotations

import dataclasses
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_takt.adr import AdrStore
from agent_takt.documents import adr_type as adr_type_mod
from agent_takt.documents.adr_type import ADR_TYPE
from agent_takt.documents.frontmatter import SPEC_STYLE, read_frontmatter
from agent_takt.documents.registry import get_store, get_type
from agent_takt.documents.spec_type import (
    SPEC_TYPE,
    find_empty_scenarios,
    merge_requirements,
    parse_requirement_scenarios,
    spec_before_transition,
    spec_validate_document,
    validate_delta_consistency,
    validate_delta_syntax,
    extract_requirements_section,
)
from agent_takt.documents.store import (
    AmbiguousDocument,
    DocumentNotFound,
    DocumentStore,
)
from agent_takt.documents.types import DocumentType, ValidationError
from agent_takt import spec_requirements

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "documents"
FIXED_NOW = "2026-01-02T03:04:05+00:00"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _spec_text(id_: str, name: str = "Spec", status: str = "draft", body: str = "# Spec\n") -> str:
    return (
        f"---\nname: {name}\nid: {id_}\ndescription: null\npriority: null\n"
        f"status: {status}\ntags: []\n---\n{body}"
    )


def _adr_body() -> str:
    return (
        "## Summary\n\nWe decided to use approach A for concrete reasons.\n\n"
        "## Context\n\nThe system required a decision about real constraints.\n\n"
        "## Considered Options\n\n### Option A — Real Option\n\n* Good: Meets goals\n"
        "* Bad: Higher cost\n\n## Decision\n\nWe chose Option A because it is faster.\n\n"
        "## Consequences\n\n### Positive\n\n* Goals are met.\n\n### Negative\n\n* Cost.\n"
    )


REQ_BODY_ADDED = (
    "# Spec\n\n## Requirements\n\n"
    "### ADDED: cap-one / Do the thing\n"
    "The system SHALL do the thing.\n"
    "#### Scenario: It happens\n"
    "- **WHEN** a\n- **THEN** b\n"
)


class TmpRootCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def spec_store(self) -> DocumentStore:
        return get_store(self.root, "spec")

    def add_spec(self, folder: str, filename: str, id_: str, **kw) -> Path:
        return _write(self.root / "specs" / folder / filename, _spec_text(id_, **kw))


# ---------------------------------------------------------------------------
# DocumentType
# ---------------------------------------------------------------------------


class TestDocumentType(unittest.TestCase):
    def test_folder_for_reserved_status_raises(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            SPEC_TYPE.folder_for_status("current")

    def test_folder_for_unknown_status_without_custom(self):
        with self.assertRaisesRegex(ValueError, "invalid status"):
            ADR_TYPE.folder_for_status("weird")

    def test_folder_for_mapped_and_custom_status(self):
        self.assertEqual(SPEC_TYPE.folder_for_status("draft"), "drafts")
        self.assertEqual(SPEC_TYPE.folder_for_status("on-hold"), "on-hold")

    def test_folder_for_invalid_custom_status(self):
        for bad in ("a b", "a/b", ""):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                SPEC_TYPE.folder_for_status(bad)

    def test_status_for_folder(self):
        self.assertEqual(SPEC_TYPE.status_for_folder("drafts"), "draft")
        self.assertEqual(SPEC_TYPE.status_for_folder("on-hold"), "on-hold")
        self.assertIsNone(SPEC_TYPE.status_for_folder("current"))
        self.assertIsNone(ADR_TYPE.status_for_folder("misc"))

    def test_is_transition_allowed(self):
        self.assertTrue(SPEC_TYPE.is_transition_allowed("done", "draft"))
        self.assertTrue(ADR_TYPE.is_transition_allowed("draft", "approved"))
        self.assertFalse(ADR_TYPE.is_transition_allowed("approved", "draft"))

    def test_default_on_document_changed_is_noop(self):
        dt = DocumentType(name="x", id_prefix="x-", root_dir="x")
        self.assertIsNone(dt.on_document_changed(dt, "x-1", Path("p")))

    def test_invalid_resolution_raises(self):
        with self.assertRaises(ValueError):
            DocumentType(name="x", id_prefix="x-", root_dir="x", resolution="nope")

    def test_immutable(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            SPEC_TYPE.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry(TmpRootCase):
    def test_both_types_share_the_store(self):
        """Scenario: document-management / Single document core / Both types share the store"""
        spec_store = get_store(self.root, "spec")
        adr_store = get_store(self.root, "adr")
        self.assertIs(type(spec_store), DocumentStore)
        self.assertIs(type(adr_store), DocumentStore)
        self.assertEqual(spec_store.doc_type.name, "spec")
        self.assertEqual(adr_store.doc_type.name, "adr")
        # Both lifecycle operations come from the same class.
        for op in ("create", "set_field", "set_status", "remove", "validate", "apply"):
            self.assertIs(getattr(type(spec_store), op), getattr(type(adr_store), op))
        # The ADR wrapper delegates to the same store class.
        self.assertIsInstance(AdrStore(self.root)._store, DocumentStore)

    def test_unknown_type_raises_keyerror(self):
        with self.assertRaises(KeyError):
            get_store(self.root, "nope")
        with self.assertRaises(KeyError):
            get_type("nope")

    def test_adr_type_fields(self):
        self.assertEqual(ADR_TYPE.id_prefix, "ADR-")
        self.assertEqual(ADR_TYPE.root_dir, "adr")
        self.assertEqual(ADR_TYPE.resolution, "id_prefix")
        self.assertFalse(ADR_TYPE.allow_custom_statuses)
        self.assertEqual(
            set(ADR_TYPE.statuses), {"draft", "approved", "superseded", "rejected"}
        )
        self.assertEqual(get_store(self.root, "adr").doc_type.name, "adr")


# ---------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------


class TestListAndLoad(TmpRootCase):
    def test_missing_root_returns_empty(self):
        self.assertEqual(self.spec_store().load_all(), [])

    def test_list_filters(self):
        _write(
            self.root / "specs/drafts/a.md",
            "---\nname: A\nid: spec-aaaaaaaa\nstatus: draft\npriority: high\ntags:\n- x\n- y\n---\n",
        )
        _write(
            self.root / "specs/planned/b.md",
            "---\nname: B\nid: spec-bbbbbbbb\nstatus: planned\npriority: low\ntags:\n- x\n---\n",
        )
        st = self.spec_store()
        self.assertEqual({d.id for d in st.list()}, {"spec-aaaaaaaa", "spec-bbbbbbbb"})
        self.assertEqual([d.id for d in st.list(status="planned")], ["spec-bbbbbbbb"])
        self.assertEqual([d.id for d in st.list(priority="high")], ["spec-aaaaaaaa"])
        self.assertEqual(len(st.list(tags=["x"])), 2)
        self.assertEqual([d.id for d in st.list(tags=["x", "y"])], ["spec-aaaaaaaa"])
        self.assertEqual(st.list(tags=["x", "zzz"]), [])

    def test_reserved_current_folder_excluded(self):
        self.add_spec("drafts", "a.md", "spec-aaaaaaaa")
        _write(self.root / "specs/current/cap.md", "## Requirement: X\n")
        docs = self.spec_store().load_all()
        self.assertEqual([d.id for d in docs], ["spec-aaaaaaaa"])

    def test_malformed_frontmatter_is_legacy_and_recorded(self):
        self.add_spec("drafts", "good.md", "spec-aaaaaaaa")
        bad = _write(self.root / "specs/drafts/bad.md", "---\nkey: [oops\n---\nbody\n")
        st = self.spec_store()
        docs = st.load_all()  # must not raise
        by_stem = {d.path.stem: d for d in docs}
        self.assertTrue(by_stem["bad"].legacy)
        self.assertEqual(by_stem["bad"].status, "legacy")
        self.assertFalse(by_stem["good"].legacy)
        self.assertIn(str(bad), st.errors)

    def test_no_frontmatter_is_legacy_with_stem_id(self):
        _write(self.root / "specs/done/old-spec.md", "# Old\n")
        (doc,) = self.spec_store().load_all()
        self.assertTrue(doc.legacy)
        self.assertEqual(doc.id, "old-spec")


class TestResolve(TmpRootCase):
    def setUp(self):
        super().setUp()
        self.add_spec("drafts", "alpha-feature.md", "spec-1cdb1111")
        self.add_spec("drafts", "beta-feature.md", "spec-1cdb2222")
        self.add_spec("planned", "gamma.md", "spec-9f9f9f9f")

    def test_resolve_by_short_prefix(self):
        """Scenario: document-management / Spec resolution by ID, prefix or filename / Resolve a spec by short prefix"""
        st = self.spec_store()
        self.assertEqual(st.resolve("9f9f").id, "spec-9f9f9f9f")
        self.assertEqual(st.resolve("spec-9f9f").id, "spec-9f9f9f9f")

    def test_exact_id_case_insensitive(self):
        self.assertEqual(self.spec_store().resolve("SPEC-1CDB1111").id, "spec-1cdb1111")

    def test_filename_stem_fallback(self):
        self.assertEqual(self.spec_store().resolve("gamma").id, "spec-9f9f9f9f")
        self.assertEqual(self.spec_store().resolve("alpha").id, "spec-1cdb1111")

    def test_ambiguous_prefix_lists_all_ids(self):
        """Scenario: document-management / Spec resolution by ID, prefix or filename / Ambiguous query"""
        with self.assertRaises(AmbiguousDocument) as ctx:
            self.spec_store().resolve("1cdb")
        self.assertEqual(sorted(ctx.exception.matches), ["spec-1cdb1111", "spec-1cdb2222"])
        self.assertIn("spec-1cdb1111", str(ctx.exception))
        self.assertIn("spec-1cdb2222", str(ctx.exception))

    def test_ambiguous_prefix_does_not_fall_through_to_filename(self):
        # "1cdb" is also not in any filename; use a query that is ambiguous by ID
        # and a unique filename substring elsewhere.
        self.add_spec("planned", "1cdb-in-name.md", "spec-77777777")
        with self.assertRaises(AmbiguousDocument):
            self.spec_store().resolve("1cdb")

    def test_not_found_carries_query(self):
        with self.assertRaises(DocumentNotFound) as ctx:
            self.spec_store().resolve("zzzz")
        self.assertEqual(ctx.exception.query, "zzzz")

    def test_empty_query_not_found(self):
        with self.assertRaises(DocumentNotFound):
            self.spec_store().resolve("  ")

    def test_id_prefix_mode_has_no_filename_matching(self):
        _write(
            self.root / "adr/drafts/adr-abcdef12-some-title.md",
            "---\nid: ADR-abcdef12\ntitle: T\nstatus: draft\n---\n",
        )
        st = get_store(self.root, "adr")
        self.assertEqual(st.resolve("ADR-abcd").id, "ADR-abcdef12")
        self.assertEqual(st.resolve("adr-abcdef12").id, "ADR-abcdef12")
        with self.assertRaises(DocumentNotFound):
            st.resolve("some-title")
        with self.assertRaises(DocumentNotFound):
            st.resolve("abcdef12")  # bare hex has no 'ADR-' prefix added in this mode


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate(TmpRootCase):
    def test_spec_create(self):
        calls = []
        dt = dataclasses.replace(
            SPEC_TYPE, on_document_changed=lambda t, i, p: calls.append((i, p))
        )
        doc = DocumentStore(self.root, dt).create("My Feature Name")
        self.assertRegex(doc.id, r"^spec-[0-9a-f]{8}$")
        self.assertEqual(doc.path, self.root / "specs" / "drafts" / "my-feature-name.md")
        fm, body = read_frontmatter(doc.path.read_text(encoding="utf-8"))
        self.assertEqual(list(fm), list(SPEC_TYPE.default_frontmatter))
        self.assertEqual(fm["name"], "My Feature Name")
        self.assertEqual(fm["id"], doc.id)
        self.assertEqual(fm["status"], "draft")
        self.assertIn("# My Feature Name", body)
        self.assertEqual(calls, [(doc.id, doc.path)])

    def test_create_makes_missing_folders_and_uses_project_template(self):
        _write(self.root / "specs" / "spec-template.md", "# {name}\n\ncustom template\n")
        doc = self.spec_store().create("Tpl")
        self.assertEqual(doc.body, "# Tpl\n\ncustom template\n")
        self.assertTrue(doc.path.is_file())

    def test_create_existing_file_raises(self):
        st = self.spec_store()
        st.create("Same")
        with self.assertRaises(FileExistsError):
            st.create("Same")

    def test_filename_builder_receives_kwargs(self):
        seen = {}

        def builder(**kw):
            seen.update(kw)
            return f"{kw['hex']}-{kw['slug']}.md"

        dt = dataclasses.replace(SPEC_TYPE, filename_builder=builder)
        doc = DocumentStore(self.root, dt).create("Hello World")
        self.assertEqual(set(seen), {"id", "hex", "slug", "title"})
        self.assertEqual(seen["title"], "Hello World")
        self.assertEqual(doc.path.name, f"{seen['hex']}-hello-world.md")

    def test_on_create_hook_runs_before_write(self):
        def hook(store, doc):
            doc.frontmatter["description"] = "from hook"

        dt = dataclasses.replace(SPEC_TYPE, on_create=hook)
        doc = DocumentStore(self.root, dt).create("Hooked")
        self.assertEqual(read_frontmatter(doc.path.read_text())[0]["description"], "from hook")

    def test_adr_create(self):
        _write(
            self.root / "templates/adr/template.md",
            "---\nid: x\n---\n\n## Summary\n\n# {{title}}\n",
        )
        doc = get_store(self.root, "adr").create("Use Postgres")
        self.assertRegex(doc.path.name, r"^adr-[0-9a-f]{8}-use-postgres\.md$")
        self.assertRegex(doc.id, r"^ADR-[0-9a-f]{8}$")
        self.assertEqual(doc.path.parent.name, "drafts")
        self.assertIn("# Use Postgres", doc.path.read_text())
        self.assertEqual(doc.frontmatter["title"], "Use Postgres")

    def test_adr_create_without_template_uses_builtin_body(self):
        doc = get_store(self.root, "adr").create("No template")
        self.assertIn("## Summary", doc.path.read_text())


# ---------------------------------------------------------------------------
# set_field / set_status / remove
# ---------------------------------------------------------------------------


class TestSetFieldAndStatus(TmpRootCase):
    def setUp(self):
        super().setUp()
        self.path = self.add_spec("drafts", "demo.md", "spec-0123abcd", name="Demo")
        self.st = self.spec_store()

    def test_set_field_updates_only_that_line(self):
        before = self.path.read_text()
        self.st.set_field("0123", "priority", "high")
        self.assertEqual(
            self.path.read_text(), before.replace("priority: null", "priority: high")
        )

    def test_set_field_unchanged_does_not_rewrite(self):
        before = self.path.stat().st_mtime_ns
        calls = []
        dt = dataclasses.replace(SPEC_TYPE, on_document_changed=lambda *a: calls.append(a))
        DocumentStore(self.root, dt).set_field("0123", "priority", None)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertEqual(calls, [])

    def test_set_field_status_rejected(self):
        with self.assertRaises(ValueError):
            self.st.set_field("0123", "status", "done")

    def test_set_field_on_legacy_rejected(self):
        _write(self.root / "specs/drafts/legacy.md", "# nothing\n")
        with self.assertRaises(ValueError):
            self.st.set_field("legacy", "priority", "high")

    def test_set_field_adr_style_redumps(self):
        p = _write(
            self.root / "adr/drafts/adr-abcdef12-t.md",
            "---\nid: ADR-abcdef12\ntitle: T\nstatus: draft\n---\n\nbody\n",
        )
        get_store(self.root, "adr").set_field("ADR-abcdef12", "description", "d")
        self.assertEqual(
            p.read_text(),
            "---\nid: ADR-abcdef12\ntitle: T\nstatus: draft\ndescription: d\n---\n\nbody\n",
        )

    def test_spec_moves_to_planned(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Spec moves to planned"""
        body_before = self.path.read_text().split("---\n", 2)[2]
        doc = self.st.set_status("0123", "planned")
        new = self.root / "specs/planned/demo.md"
        self.assertEqual(doc.path, new)
        self.assertFalse(self.path.exists())
        text = new.read_text()
        self.assertEqual(read_frontmatter(text)[0]["status"], "planned")
        self.assertEqual(text.split("---\n", 2)[2], body_before)

    def test_custom_spec_status(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Custom spec status"""
        doc = self.st.set_status("0123", "on-hold")
        self.assertEqual(doc.path, self.root / "specs/on-hold/demo.md")
        self.assertTrue(doc.path.is_file())
        self.assertEqual(read_frontmatter(doc.path.read_text())[0]["status"], "on-hold")
        # and the store lists it with that status
        self.assertEqual(self.st.list(status="on-hold")[0].id, "spec-0123abcd")

    def test_reserved_status_rejected(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Reserved status rejected"""
        before = self.path.read_text()
        with self.assertRaisesRegex(ValueError, "reserved"):
            self.st.set_status("0123", "current")
        self.assertEqual(self.path.read_text(), before)
        self.assertFalse((self.root / "specs/current").exists())

    def test_invalid_custom_status_rejected_without_changes(self):
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            self.st.set_status("0123", "a b")
        self.assertEqual(self.path.read_text(), before)

    def test_same_status_is_noop(self):
        calls = []
        dt = dataclasses.replace(SPEC_TYPE, on_document_changed=lambda *a: calls.append(a))
        DocumentStore(self.root, dt).set_status("0123", "draft")
        self.assertEqual(calls, [])
        self.assertTrue(self.path.exists())

    def test_transition_hooks(self):
        events = []
        dt = dataclasses.replace(
            SPEC_TYPE,
            before_transition=lambda s, d, new: events.append(("before", d.status, new)),
            after_transition=lambda s, d, old: events.append(("after", old, d.status)),
        )
        DocumentStore(self.root, dt).set_status("0123", "planned")
        self.assertEqual(events, [("before", "draft", "planned"), ("after", "draft", "planned")])

    def test_failing_before_transition_changes_nothing(self):
        def boom(store, doc, new):
            raise ValueError("nope")

        dt = dataclasses.replace(SPEC_TYPE, before_transition=boom)
        before = self.path.read_text()
        with self.assertRaises(ValueError):
            DocumentStore(self.root, dt).set_status("0123", "planned")
        self.assertEqual(self.path.read_text(), before)
        self.assertFalse((self.root / "specs/planned").exists())

    def test_invalid_adr_transition(self):
        """Scenario: document-management / Status changes keep folder and frontmatter in sync / Invalid ADR transition"""
        p = _write(
            self.root / "adr/approved/adr-abcdef12-t.md",
            "---\nid: ADR-abcdef12\ntitle: T\nstatus: approved\n---\n\nbody\n",
        )
        before = p.read_text()
        with self.assertRaisesRegex(ValueError, "not allowed"):
            get_store(self.root, "adr").set_status("ADR-abcdef12", "draft")
        self.assertEqual(p.read_text(), before)
        self.assertTrue(p.exists())
        self.assertFalse((self.root / "adr/drafts").exists())

    def test_set_status_target_collision(self):
        self.add_spec("planned", "demo.md", "spec-eeeeeeee")
        with self.assertRaises(FileExistsError):
            self.st.set_status("0123", "planned")
        self.assertTrue(self.path.exists())

    def test_remove_deletes_file_and_notifies(self):
        calls = []
        dt = dataclasses.replace(SPEC_TYPE, on_document_changed=lambda t, i, p: calls.append(i))
        DocumentStore(self.root, dt).remove("0123")
        self.assertFalse(self.path.exists())
        self.assertEqual(calls, ["spec-0123abcd"])


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate(TmpRootCase):
    def test_malformed_file_reported_while_others_validated(self):
        bad = _write(self.root / "specs/drafts/bad.md", "---\nk: [x\n---\n")
        _write(
            self.root / "specs/drafts/dup1.md",
            _spec_text("spec-aaaaaaaa"),
        )
        _write(self.root / "specs/drafts/dup2.md", _spec_text("spec-aaaaaaaa"))
        errs = self.spec_store().validate()
        paths = {e.path for e in errs}
        self.assertIn(str(bad), paths)
        self.assertTrue(any("duplicate id" in e.message for e in errs))

    def test_validate_by_id(self):
        self.add_spec("drafts", "a.md", "spec-aaaaaaaa")
        _write(self.root / "specs/drafts/b.md", "---\nk: [x\n---\n")
        self.assertEqual(self.spec_store().validate("spec-aaaaaaaa"), [])

    def test_validate_document_hook_called(self):
        self.add_spec("drafts", "a.md", "spec-aaaaaaaa")
        dt = dataclasses.replace(
            SPEC_TYPE,
            validate_document=lambda s, d: [ValidationError(str(d.path), "custom")],
        )
        errs = DocumentStore(self.root, dt).validate()
        self.assertEqual([e.message for e in errs], ["custom"])

    def test_nonstandard_id_is_warning(self):
        _write(self.root / "specs/drafts/a.md", _spec_text("s-a0f0124"))
        errs = self.spec_store().validate()
        self.assertEqual([(e.severity, "s-a0f0124" in e.message) for e in errs], [("warning", True)])

    def test_missing_name_is_warning(self):
        _write(
            self.root / "specs/drafts/a.md",
            "---\nid: spec-aaaaaaaa\nstatus: draft\n---\nbody\n",
        )
        errs = self.spec_store().validate()
        self.assertEqual([e.severity for e in errs], ["warning"])
        self.assertIn("name", errs[0].message)

    def test_status_folder_mismatch_is_error(self):
        _write(self.root / "specs/planned/a.md", _spec_text("spec-aaaaaaaa", status="draft"))
        errs = self.spec_store().validate()
        self.assertTrue(any(e.severity == "error" and "does not match" in e.message for e in errs))

    def test_legacy_skipped_by_store_validate_but_warns_directly(self):
        _write(self.root / "specs/drafts/legacy.md", "# x\n")
        st = self.spec_store()
        self.assertEqual(st.validate(), [])
        (doc,) = st.load_all()
        out = spec_validate_document(st, doc)
        self.assertEqual([e.severity for e in out], ["warning"])

    def test_adr_validate_messages(self):
        _write(
            self.root / "adr/drafts/adr-abcdef12-x.md",
            "---\nid: bad-id\ntitle: T\nstatus: weird\nsupersedes:\n- ADR-ffffffff\n---\nbody\n",
        )
        _write(
            self.root / "adr/approved/adr-11111111-x.md",
            "---\nid: ADR-11111111\ntitle: T\nstatus: approved\ncreated_at: x\nauthors: [a]\n"
            "superseded_by: ADR-99999999\n---\n\n## Summary\n\n(placeholder)\n",
        )
        msgs = [e.message for e in get_store(self.root, "adr").validate()]
        joined = "\n".join(msgs)
        self.assertIn("missing required frontmatter field: 'created_at'", joined)
        self.assertIn("invalid ADR ID format: 'bad-id'", joined)
        self.assertIn("invalid status value: 'weird'", joined)
        self.assertIn("missing required section: ## Context", joined)
        self.assertIn("dangling superseded_by reference: 'ADR-99999999'", joined)


# ---------------------------------------------------------------------------
# Notifications + auto-commit
# ---------------------------------------------------------------------------


class TestOnDocumentChangedRecording(TmpRootCase):
    """on_document_changed fires once per operation, never for specs/current writes."""

    def setUp(self):
        super().setUp()
        self.calls: list[tuple[str, str, Path]] = []
        self.dt = dataclasses.replace(
            SPEC_TYPE,
            on_document_changed=lambda t, i, p: self.calls.append((t.name, i, p)),
        )
        self.st = DocumentStore(self.root, self.dt)

    def test_create_notifies_once(self):
        doc = self.st.create("One")
        self.assertEqual(self.calls, [("spec", doc.id, doc.path)])

    def test_set_field_notifies_once_and_not_for_noop(self):
        doc = self.st.create("One")
        self.calls.clear()
        self.st.set_field(doc.id, "priority", "high")
        self.assertEqual(len(self.calls), 1)
        self.st.set_field(doc.id, "priority", "high")
        self.assertEqual(len(self.calls), 1)

    def test_set_status_notifies_once_with_new_path(self):
        doc = self.st.create("One")
        self.calls.clear()
        self.st.set_status(doc.id, "planned")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][2], self.root / "specs/planned/one.md")
        self.st.set_status(doc.id, "planned")  # no-op
        self.assertEqual(len(self.calls), 1)

    def test_remove_notifies_once(self):
        doc = self.st.create("One")
        self.calls.clear()
        self.st.remove(doc.id)
        self.assertEqual(len(self.calls), 1)

    def test_apply_notifies_once_per_changed_document(self):
        a = self.st.create("A")
        b = self.st.create("B")
        c = self.st.create("C")
        self.calls.clear()
        self.st.apply([
            (a.id, "planned", None),
            (b.id, None, {"priority": "low"}),
            (c.id, None, {"priority": None}),  # no-op
        ])
        self.assertEqual(sorted(i for _, i, _ in self.calls), sorted([a.id, b.id]))

    def test_specs_current_writes_do_not_notify(self):
        doc = self.st.create("Merge me")
        self.st.set_field(doc.id, "priority", "high")
        body = REQ_BODY_ADDED
        text = doc.path.read_text()
        doc.path.write_text(text.replace(text.split("---\n", 2)[2], body))
        self.calls.clear()
        self.st.set_status(doc.id, "done")
        cap = self.root / "specs/current/cap-one.md"
        self.assertTrue(cap.is_file())
        # exactly one notification — for the spec itself, none for specs/current
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], doc.id)
        self.assertNotIn(cap, [p for _, _, p in self.calls])
        self.assertFalse(any("current" in p.parts for _, _, p in self.calls))

    def test_merge_requirements_direct_does_not_notify(self):
        # merge_requirements is a plain function; capability files are not documents
        merge_requirements("p.md", "spec-1", REQ_BODY_ADDED, self.root / "specs/current")
        self.assertEqual(self.calls, [])


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


class TestAutoCommit(TmpRootCase):
    def _init_git(self):
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        _git(self.root, "config", "commit.gpgsign", "false")
        _write(self.root / "README.md", "x")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "init")

    def _count(self) -> int:
        return int(_git(self.root, "rev-list", "--count", "HEAD").strip())

    def test_one_commit_per_operation_when_enabled(self):
        self._init_git()
        st = DocumentStore(self.root, dataclasses.replace(SPEC_TYPE, auto_commit=True))
        start = self._count()
        doc = st.create("Committed")
        self.assertEqual(self._count(), start + 1)
        st.set_field(doc.id, "priority", "high")
        self.assertEqual(self._count(), start + 2)
        st.set_status(doc.id, "planned")
        self.assertEqual(self._count(), start + 3)
        st.remove(doc.id)
        self.assertEqual(self._count(), start + 4)
        self.assertIn("SPEC lifecycle", _git(self.root, "log", "-1", "--format=%s"))

    def test_no_commits_when_disabled(self):
        self._init_git()
        st = DocumentStore(self.root, SPEC_TYPE)
        start = self._count()
        doc = st.create("Quiet")
        st.set_status(doc.id, "planned")
        self.assertEqual(self._count(), start)

    def test_apply_makes_single_commit(self):
        self._init_git()
        st = DocumentStore(self.root, dataclasses.replace(SPEC_TYPE, auto_commit=False))
        a, b = st.create("A"), st.create("B")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-q", "-m", "seed")
        st2 = DocumentStore(self.root, dataclasses.replace(SPEC_TYPE, auto_commit=True))
        start = self._count()
        st2.apply([(a.id, "planned", None), (b.id, "done", {"priority": "low"})])
        self.assertEqual(self._count(), start + 1)


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> dict[str, bytes]:
    snap = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    snap.update({f"{p.relative_to(root)}/": b"" for p in root.rglob("*") if p.is_dir()})
    return snap


class TestApply(TmpRootCase):
    def setUp(self):
        super().setUp()
        self.a = self.add_spec("drafts", "a.md", "spec-aaaaaaaa", name="A")
        self.b = self.add_spec("drafts", "b.md", "spec-bbbbbbbb", name="B")

    def test_multi_doc_move_and_update_succeeds(self):
        calls = []
        dt = dataclasses.replace(SPEC_TYPE, on_document_changed=lambda t, i, p: calls.append(i))
        st = DocumentStore(self.root, dt)
        docs = st.apply([
            ("spec-aaaaaaaa", "planned", {"priority": "high"}),
            ("spec-bbbbbbbb", "done", None),
        ])
        self.assertEqual([d.id for d in docs], ["spec-aaaaaaaa", "spec-bbbbbbbb"])
        fa = read_frontmatter((self.root / "specs/planned/a.md").read_text())[0]
        self.assertEqual((fa["status"], fa["priority"]), ("planned", "high"))
        self.assertEqual(
            read_frontmatter((self.root / "specs/done/b.md").read_text())[0]["status"], "done"
        )
        self.assertFalse(self.a.exists() or self.b.exists())
        self.assertEqual(sorted(calls), ["spec-aaaaaaaa", "spec-bbbbbbbb"])

    def test_failing_before_transition_on_second_change_writes_nothing(self):
        def before(store, doc, new):
            if doc.id == "spec-bbbbbbbb":
                raise ValueError("second fails")

        st = DocumentStore(self.root, dataclasses.replace(SPEC_TYPE, before_transition=before))
        snap = _snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "second fails"):
            st.apply([("spec-aaaaaaaa", "planned", None), ("spec-bbbbbbbb", "done", None)])
        self.assertEqual(_snapshot(self.root), snap)

    def test_after_transition_failure_rolls_back_everything(self):
        calls = []

        def after(store, doc, old):
            if doc.id == "spec-bbbbbbbb":
                raise RuntimeError("after boom")

        dt = dataclasses.replace(
            SPEC_TYPE, after_transition=after, on_document_changed=lambda *a: calls.append(a)
        )
        st = DocumentStore(self.root, dt)
        snap = _snapshot(self.root)
        with self.assertRaises(RuntimeError):
            st.apply([("spec-aaaaaaaa", "planned", {"priority": "low"}),
                      ("spec-bbbbbbbb", "brand-new-status", None)])
        self.assertEqual(_snapshot(self.root), snap)  # originals identical, new files+dirs gone
        self.assertFalse((self.root / "specs/planned").exists())
        self.assertFalse((self.root / "specs/brand-new-status").exists())
        self.assertEqual(calls, [])

    def test_write_failure_midway_restores_originals(self):
        st = self.spec_store()
        real = Path.write_text
        count = {"n": 0}

        def flaky(self_, *args, **kw):
            count["n"] += 1
            if count["n"] == 2:
                raise OSError("disk full")
            return real(self_, *args, **kw)

        snap = _snapshot(self.root)
        with patch.object(Path, "write_text", flaky):
            with self.assertRaises(OSError):
                st.apply([("spec-aaaaaaaa", "planned", None), ("spec-bbbbbbbb", "done", None)])
        self.assertEqual(_snapshot(self.root), snap)

    def test_duplicate_ids_rejected(self):
        snap = _snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.spec_store().apply([("spec-aaaaaaaa", "planned", None),
                                     ("spec-aaaaaaaa", "done", None)])
        self.assertEqual(_snapshot(self.root), snap)

    def test_target_path_collision_rejected(self):
        self.add_spec("planned", "a.md", "spec-cccccccc")
        snap = _snapshot(self.root)
        with self.assertRaises(FileExistsError):
            self.spec_store().apply([("spec-aaaaaaaa", "planned", None)])
        self.assertEqual(_snapshot(self.root), snap)

    def test_same_target_path_for_two_changes_rejected(self):
        self.add_spec("planned", "b.md", "spec-dddddddd")  # irrelevant bystander
        c = self.add_spec("done", "a.md", "spec-eeeeeeee")  # same filename as a.md
        snap = _snapshot(self.root)
        with self.assertRaises(FileExistsError):
            self.spec_store().apply([("spec-aaaaaaaa", "done", None)])
        self.assertTrue(c.exists())
        self.assertEqual(_snapshot(self.root), snap)

    def test_same_status_field_only_rewrites_in_place(self):
        st = self.spec_store()
        (doc,) = st.apply([("spec-aaaaaaaa", "draft", {"priority": "high"})])
        self.assertEqual(doc.path, self.a)
        self.assertEqual(read_frontmatter(self.a.read_text())[0]["priority"], "high")

    def test_empty_change_list(self):
        snap = _snapshot(self.root)
        self.assertEqual(self.spec_store().apply([]), [])
        self.assertEqual(_snapshot(self.root), snap)

    def test_status_in_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.spec_store().apply([("spec-aaaaaaaa", None, {"status": "done"})])

    def test_apply_with_done_merges_requirements(self):
        a_text = self.a.read_text().replace("# Spec\n", REQ_BODY_ADDED)
        self.a.write_text(a_text)
        store = self.spec_store()
        store.apply([("spec-aaaaaaaa", "done", None)])
        # spec_before_transition must run for apply() as well
        self.assertTrue((self.root / "specs/current/cap-one.md").is_file())


# ---------------------------------------------------------------------------
# Requirements merge on spec completion
# ---------------------------------------------------------------------------


class TestRequirementsMerge(TmpRootCase):
    def _spec_with_body(self, body: str, folder: str = "drafts") -> Path:
        return _write(
            self.root / f"specs/{folder}/s.md",
            _spec_text("spec-12345678", status={"drafts": "draft", "done": "done"}[folder], body=body),
        )

    def _current(self, cap: str) -> Path:
        return self.root / "specs/current" / f"{cap}.md"

    def test_valid_added_modified_removed_deltas_merged(self):
        """Scenario: document-management / Requirements merge on spec completion / Valid deltas merged"""
        body = (
            "# S\n\n## Requirements\n\n"
            "### ADDED: cap-one / First\nThe system SHALL first.\n#### Scenario: S1\n"
            "- **WHEN** a\n- **THEN** b\n\n"
            "### ADDED: cap-one / Second\nThe system MUST second.\n#### Scenario: S2\n"
            "- **WHEN** c\n- **THEN** d\n"
        )
        self._spec_with_body(body)
        st = self.spec_store()
        st.set_status("spec-12345678", "done")
        self.assertEqual(
            st.last_merged,
            [("ADDED", "cap-one", "First"), ("ADDED", "cap-one", "Second")],
        )
        text = self._current("cap-one").read_text()
        self.assertIn("## Requirement: First", text)
        self.assertIn("## Requirement: Second", text)
        self.assertTrue((self.root / "specs/done/s.md").is_file())

        # MODIFIED replaces in place
        _write(
            self.root / "specs/drafts/m.md",
            _spec_text(
                "spec-22222222",
                body="# M\n\n## Requirements\n\n### MODIFIED: cap-one / First\n"
                "The system SHALL first, differently.\n#### Scenario: S1b\n"
                "- **WHEN** x\n- **THEN** y\n",
            ),
        )
        st.set_status("spec-22222222", "done")
        text = self._current("cap-one").read_text()
        self.assertIn("differently", text)
        self.assertIn("## Requirement: Second", text)
        self.assertLess(text.index("First"), text.index("Second"))

        # REMOVED deletes one block; removing the last empties and deletes the file
        for i, req in enumerate(("First", "Second")):
            _write(
                self.root / f"specs/drafts/r{i}.md",
                _spec_text(
                    f"spec-3333333{i}",
                    body=f"# R\n\n## Requirements\n\n### REMOVED: cap-one / {req}\nReason: gone.\n",
                ),
            )
        st.set_status("spec-33333330", "done")
        self.assertNotIn("## Requirement: First", self._current("cap-one").read_text())
        st.set_status("spec-33333331", "done")
        self.assertFalse(self._current("cap-one").exists())

    def test_invalid_delta_blocks_completion(self):
        """Scenario: document-management / Requirements merge on spec completion / Invalid delta blocks completion"""
        body = (
            "# S\n\n## Requirements\n\n### ADDED: cap-one / No keyword\n"
            "Nothing normative here.\n#### Scenario: S1\n- **WHEN** a\n- **THEN** b\n"
        )
        path = self._spec_with_body(body)
        _write(self._current("existing"), "## Requirement: Keep\nSHALL keep\n")
        snap = _snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "invalid requirement deltas"):
            self.spec_store().set_status("spec-12345678", "done")
        self.assertEqual(_snapshot(self.root), snap)
        self.assertTrue(path.exists())
        self.assertFalse((self.root / "specs/done").exists())

    def test_invalid_deltas_leave_all_files_untouched_in_merge_requirements(self):
        cur = self.root / "specs/current"
        _write(cur / "cap-one.md", "## Requirement: Keep\nSHALL keep\n")
        snap = _snapshot(self.root)
        bad = "## Requirements\n\n### ADDED: cap-one / X\nno keyword\n"
        with self.assertRaises(ValueError):
            merge_requirements("p.md", "spec-1", bad, cur)
        self.assertEqual(_snapshot(self.root), snap)

    def test_done_spec_validates_after_merge(self):
        """Scenario: document-management / Requirements merge on spec completion / Done spec validates after merge"""
        self._spec_with_body(REQ_BODY_ADDED)
        st = self.spec_store()
        st.set_status("spec-12345678", "done")
        self.assertTrue(self._current("cap-one").is_file())
        errors = [e for e in st.validate("spec-12345678") if e.severity == "error"]
        self.assertEqual(errors, [])
        # The same ADDED requirement now exists in current, yet the done spec is OK:
        # consistency checks are skipped for done specs.
        (doc,) = [d for d in st.load_all() if d.id == "spec-12345678"]
        self.assertEqual(doc.status, "done")
        # while an identical draft *would* fail the consistency check
        _write(self.root / "specs/drafts/again.md", _spec_text("spec-44444444", body=REQ_BODY_ADDED))
        msgs = [e.message for e in st.validate("spec-44444444")]
        self.assertTrue(any("already exists" in m for m in msgs), msgs)

    def test_transitioning_done_spec_does_not_merge_again(self):
        self._spec_with_body(REQ_BODY_ADDED)
        st = self.spec_store()
        st.set_status("spec-12345678", "done")
        cap = self._current("cap-one")
        cap.write_text("sentinel\n## Requirement: Do the thing\nSHALL\n", encoding="utf-8")
        st.set_status("spec-12345678", "done")  # same status
        self.assertTrue(cap.read_text().startswith("sentinel"))
        # the hook itself declines to merge for an already-done spec
        (doc,) = [d for d in st.load_all() if d.id == "spec-12345678"]
        self.assertEqual(doc.status, "done")
        self.assertEqual(spec_before_transition(st, doc, "done"), [])
        self.assertTrue(cap.read_text().startswith("sentinel"))

    def test_spec_without_requirements_moves_to_done(self):
        self._spec_with_body("# plain\n")
        st = self.spec_store()
        st.set_status("spec-12345678", "done")
        self.assertEqual(st.last_merged, [])
        self.assertFalse((self.root / "specs/current").exists())

    def test_planned_does_not_touch_current(self):
        self._spec_with_body(REQ_BODY_ADDED)
        self.spec_store().set_status("spec-12345678", "planned")
        self.assertFalse((self.root / "specs/current").exists())


# ---------------------------------------------------------------------------
# Requirement parsing (fenced scenarios etc.)
# ---------------------------------------------------------------------------

FENCED_TEXT = (
    "# Spec\n\n## Requirements\n\n"
    "### ADDED: cap / Real req\nThe system SHALL work.\n#### Scenario: Real one\n"
    "- **WHEN** a\n- **THEN** b\n\n"
    "```markdown\n#### Scenario: Fenced\n### ADDED: fake / Fake\n```\n"
)


class TestRequirementParsing(unittest.TestCase):
    def test_fenced_scenarios_ignored_consistently(self):
        """Scenario: document-management / Single document core / Fenced scenarios are ignored consistently"""
        expected = [("cap", "Real req", "Real one")]
        # The document core and the plan-time helper module share one parser.
        self.assertEqual(parse_requirement_scenarios(FENCED_TEXT), expected)
        self.assertEqual(spec_requirements.parse_requirement_scenarios(FENCED_TEXT), expected)
        self.assertIs(
            spec_requirements.parse_requirement_scenarios, parse_requirement_scenarios
        )
        # and validation (the same code `takt spec validate` runs) agrees
        section = extract_requirements_section(FENCED_TEXT)
        self.assertEqual(validate_delta_syntax("p.md", section or ""), [])

    def test_requirements_only_inside_fence_is_no_section(self):
        text = "# S\n\n```\n## Requirements\n### ADDED: a / b\n```\n"
        self.assertIsNone(extract_requirements_section(text))
        self.assertEqual(parse_requirement_scenarios(text), [])

    def test_repeated_requirements_heading_does_not_restart(self):
        text = (
            "## Requirements\n\n### ADDED: a / one\nSHALL x\n#### Scenario: s1\n"
            "- **WHEN** a\n- **THEN** b\n\n## Requirements\n\n"
            "### ADDED: a / two\nSHALL y\n#### Scenario: s2\n- **WHEN** a\n- **THEN** b\n"
        )
        self.assertEqual(
            parse_requirement_scenarios(text), [("a", "one", "s1"), ("a", "two", "s2")]
        )

    def test_section_ends_at_next_h2(self):
        text = (
            "## Requirements\n\n### ADDED: a / one\nSHALL x\n#### Scenario: s1\n"
            "- **WHEN** a\n- **THEN** b\n\n## Files to Modify\n\n"
            "### ADDED: a / ignored\nSHALL\n#### Scenario: nope\n"
        )
        self.assertEqual(parse_requirement_scenarios(text), [("a", "one", "s1")])

    def test_removed_entries_excluded(self):
        text = "## Requirements\n\n### REMOVED: a / old\nReason: gone.\n"
        self.assertEqual(parse_requirement_scenarios(text), [])

    def test_empty_scenario_name_is_error_and_skipped(self):
        text = (
            "## Requirements\n\n### ADDED: a / one\nSHALL x\n#### Scenario:\n"
            "- **WHEN** a\n- **THEN** b\n"
        )
        self.assertEqual(parse_requirement_scenarios(text), [])
        self.assertEqual(find_empty_scenarios(text), [("a", "one")])
        errs = validate_delta_syntax("p.md", extract_requirements_section(text) or "")
        self.assertTrue(any("empty name" in e.message for e in errs))

    def test_syntax_rules(self):
        def errs(body: str) -> list[str]:
            return [e.message for e in validate_delta_syntax("p.md", body)]

        self.assertTrue(errs("### ADDED: a / x\nno keyword\n#### Scenario: s\n- **WHEN** a\n- **THEN** b\n"))
        self.assertTrue(errs("### ADDED: a / x\nSHALL\n"))  # no scenario
        self.assertTrue(errs("### ADDED: a / x\nSHALL\n#### Scenario: s\n- **THEN** b\n"))  # no WHEN
        self.assertTrue(errs("### ADDED: a / x\nSHALL\n#### Scenario: s\n- **WHEN** a\n"))  # no THEN
        self.assertTrue(errs("### REMOVED: a / x\n"))  # no reason
        self.assertTrue(errs("### REMOVED: a / x\nReason: r\n#### Scenario: s\n"))
        self.assertTrue(errs("### BOGUS a / x\n"))  # malformed heading
        self.assertTrue(errs("### ADDED: Bad_Slug / x\nSHALL\n#### Scenario: s\n- **WHEN** a\n- **THEN** b\n"))
        dup = ("### ADDED: a / x\nSHALL\n#### Scenario: s\n- **WHEN** a\n- **THEN** b\n"
               "### ADDED: a / X\nSHALL\n#### Scenario: s\n- **WHEN** a\n- **THEN** b\n")
        self.assertTrue(any("duplicate" in m.lower() for m in errs(dup)))

    def test_consistency_errors_run_independently_of_syntax(self):
        with tempfile.TemporaryDirectory() as tmp:
            cur = Path(tmp)
            bad_syntax_modified = "### MODIFIED: nofile / x\nno keyword\n"
            msgs = [e.message for e in validate_delta_consistency("p.md", bad_syntax_modified, cur)]
            self.assertTrue(any("capability file not found" in m for m in msgs), msgs)
            _write(cur / "cap.md", "## Requirement: Exists\nSHALL\n")
            added = "### ADDED: cap / Exists\nno keyword\n"
            self.assertTrue(any("already exists" in e.message
                                for e in validate_delta_consistency("p.md", added, cur)))
            mod = "### MODIFIED: cap / Missing\nno keyword\n"
            self.assertTrue(any("requirement not found" in e.message
                                for e in validate_delta_consistency("p.md", mod, cur)))


# ---------------------------------------------------------------------------
# ADR type
# ---------------------------------------------------------------------------


def _adr_fixture_root(tmp: Path) -> Path:
    for src in (FIXTURES / "adr" / "input").glob("*.md"):
        text = src.read_text(encoding="utf-8")
        folder = "drafts" if "status: draft" in text else "approved"
        _write(tmp / "adr" / folder / src.name, text)
    return tmp


class TestAdrType(TmpRootCase):
    def setUp(self):
        super().setUp()
        _adr_fixture_root(self.root)
        p = patch.object(adr_type_mod, "_utc_now", lambda: FIXED_NOW)
        p.start()
        self.addCleanup(p.stop)
        self.store = AdrStore(self.root)

    def _read(self, folder: str, name: str) -> str:
        return (self.root / "adr" / folder / name).read_text(encoding="utf-8")

    def _expected(self, name: str) -> str:
        return (FIXTURES / "adr" / "expected" / name).read_text(encoding="utf-8")

    # --- golden files: byte-identical to the output of the pre-change adr.py ---

    def test_golden_approve(self):
        self.store.approve("ADR-aaaaaaaa")
        self.assertEqual(
            self._read("approved", "adr-aaaaaaaa-new-decision.md"), self._expected("approve.md")
        )

    def test_golden_approve_with_supersedes(self):
        self.store.approve("ADR-aaaaaaaa", supersedes=["ADR-bbbbbbbb"])
        self.assertEqual(
            self._read("approved", "adr-aaaaaaaa-new-decision.md"),
            self._expected("approve_supersedes_main.md"),
        )
        self.assertEqual(
            self._read("superseded", "adr-bbbbbbbb-old-decision.md"),
            self._expected("approve_supersedes_target.md"),
        )

    def test_golden_reject(self):
        self.store.reject("ADR-aaaaaaaa")
        self.assertEqual(
            self._read("rejected", "adr-aaaaaaaa-new-decision.md"), self._expected("reject.md")
        )

    def test_golden_standalone_supersede(self):
        self.store.approve("ADR-aaaaaaaa")
        self.store.supersede("ADR-bbbbbbbb", "ADR-aaaaaaaa")
        self.assertEqual(
            self._read("superseded", "adr-bbbbbbbb-old-decision.md"),
            self._expected("supersede.md"),
        )

    def test_adr_lifecycle_unchanged(self):
        """Scenario: document-management / ADR behaviour preserved / ADR lifecycle unchanged"""
        self.store.approve("ADR-aaaaaaaa")
        fm, _ = read_frontmatter(self._read("approved", "adr-aaaaaaaa-new-decision.md"))
        self.assertEqual(fm["status"], "approved")
        self.assertEqual(fm["accepted_at"], FIXED_NOW)
        self.assertEqual(list(fm), list(yaml.safe_load(self._expected("approve.md").split("---\n")[1])))
        with self.assertRaisesRegex(ValueError, "cannot be approved: status is 'approved'"):
            self.store.approve("ADR-aaaaaaaa")
        with self.assertRaisesRegex(ValueError, "cannot be rejected"):
            self.store.reject("ADR-aaaaaaaa")
        self.store.supersede("ADR-bbbbbbbb", "ADR-aaaaaaaa")
        self.assertTrue((self.root / "adr/superseded/adr-bbbbbbbb-old-decision.md").is_file())
        self.assertEqual(self.store.validate_all(), [])

    def test_approve_placeholder_body_exact_message(self):
        p = self.root / "adr/drafts/adr-aaaaaaaa-new-decision.md"
        text = p.read_text()
        p.write_text(text.split("---\n\n", 1)[0] + "---\n\n" + get_type("adr").template)
        with self.assertRaises(ValueError) as ctx:
            self.store.approve("ADR-aaaaaaaa")
        self.assertTrue(
            str(ctx.exception).startswith(
                "ADR ADR-aaaaaaaa cannot be approved; body section errors:\n  - "
            )
        )
        self.assertTrue(p.exists())

    def test_approve_with_supersedes_is_atomic(self):
        """Scenario: document-management / ADR behaviour preserved / Approve with supersedes is atomic"""
        # target not approved -> nothing changes
        snap = _snapshot(self.root)
        # a second draft is not a valid supersede target
        _write(
            self.root / "adr/drafts/adr-cccccccc-other.md",
            (FIXTURES / "adr/input/adr-aaaaaaaa-new-decision.md").read_text().replace(
                "ADR-aaaaaaaa", "ADR-cccccccc"
            ),
        )
        snap = _snapshot(self.root)
        with self.assertRaisesRegex(ValueError, "Cannot supersede ADR-cccccccc"):
            self.store.approve("ADR-aaaaaaaa", supersedes=["ADR-cccccccc"])
        self.assertEqual(_snapshot(self.root), snap)

        # a write failure while applying the transaction -> all files restored
        real = Path.write_text
        n = {"c": 0}

        def flaky(self_, *a, **kw):
            n["c"] += 1
            if n["c"] == 2:
                raise OSError("boom")
            return real(self_, *a, **kw)

        with patch.object(Path, "write_text", flaky):
            with self.assertRaises(OSError):
                self.store.approve("ADR-aaaaaaaa", supersedes=["ADR-bbbbbbbb"])
        self.assertEqual(_snapshot(self.root), snap)
        self.assertFalse((self.root / "adr/superseded").exists())

        # and the happy path moves both
        self.store.approve("ADR-aaaaaaaa", supersedes=["ADR-bbbbbbbb"])
        new_fm, _ = read_frontmatter(self._read("approved", "adr-aaaaaaaa-new-decision.md"))
        old_fm, _ = read_frontmatter(self._read("superseded", "adr-bbbbbbbb-old-decision.md"))
        self.assertEqual(new_fm["supersedes"], ["ADR-bbbbbbbb"])
        self.assertEqual(old_fm["superseded_by"], "ADR-aaaaaaaa")
        self.assertEqual(old_fm["superseded_at"], FIXED_NOW)

    def test_approve_rolls_back_when_body_check_fails(self):
        p = self.root / "adr/drafts/adr-aaaaaaaa-new-decision.md"
        p.write_text(p.read_text().split("## Summary")[0], encoding="utf-8")
        snap = _snapshot(self.root)
        with self.assertRaises(ValueError):
            self.store.approve("ADR-aaaaaaaa", supersedes=["ADR-bbbbbbbb"])
        self.assertEqual(_snapshot(self.root), snap)


if __name__ == "__main__":
    unittest.main()
