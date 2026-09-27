"""The TypeScript UX-states gate: the four shapes, the look-alikes, the waiver.

Extracted from loregarden's ``.lefthook/scripts/ts_ux_states_check.cjs``. Most
of that gate's value is in the cases that look like violations and are not, so
those are pinned alongside the shapes. Every repo here has no ``node_modules``
of its own; the parser and the scope come from ``ts_gate_harness.cjs``, shared
with the organization and silent-failure gates.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

GATE = "ts_ux_states_check.cjs"


def ts_repo(repo, relpath: str, content: str):
    """TypeScript under src/, uncommitted so every line is an addition."""
    repo.write("package.json", '{"name":"graded","private":true}\n')
    repo.commit("layout")
    repo.write(relpath, content)
    return repo


def output(result) -> str:
    return f"{result.stdout}\n{result.stderr}"


def run_gate(repo, *files: str):
    return repo.gate(GATE, "--repo", str(repo.root), "--scope", "worktree", *files)


def assert_fails(result, needle: str) -> None:
    assert result.returncode == 1, output(result)
    assert needle in output(result), output(result)


def assert_passes(result) -> None:
    assert result.returncode == 0, output(result)
    assert "examined 1 file(s)" in output(result), output(result)
    assert "user-experience checks passed" in output(result), output(result)


def component(jsx: str, preamble: str = "") -> str:
    return (
        f"{preamble}"
        "export function Row({ close }: { close: () => void }) {\n"
        f"  return (\n    {jsx}\n  );\n"
        "}\n"
    )


class TestUnnamedControls:
    def test_an_icon_only_button_fails(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<button onClick={close}><TrashIcon /></button>"))
        assert_fails(run_gate(repo), "<button> has no text and no aria-label/title")

    def test_an_icon_only_anchor_fails(self, repo):
        ts_repo(repo, "src/Row.tsx", component('<a href="/x"><LinkIcon /></a>'))
        assert_fails(run_gate(repo), "<a> has no text")

    @pytest.mark.parametrize(
        "jsx",
        [
            '<button aria-label="Delete row" onClick={close}><TrashIcon /></button>',
            '<button title="Delete row" onClick={close}><TrashIcon /></button>',
            "<button onClick={close}>Delete</button>",
            "<button onClick={close}>{label}</button>",
        ],
    )
    def test_a_named_button_passes(self, repo, jsx):
        ts_repo(repo, "src/Row.tsx", component(jsx, "const label = 'x';\n"))
        assert_passes(run_gate(repo))

    def test_the_failure_names_the_policy_it_is_half_of(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<button onClick={close}><TrashIcon /></button>"))
        assert_fails(run_gate(repo), "policy/ux-definition-of-done.md")


class TestKeyboardReach:
    def test_onclick_on_a_div_fails(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<div onClick={close}>row</div>"))
        assert_fails(run_gate(repo), "<div onClick> is missing role, tabIndex, a key handler")

    def test_a_div_with_role_tabindex_and_a_key_handler_passes(self, repo):
        jsx = '<div role="button" tabIndex={0} onClick={close} onKeyDown={close}>row</div>'
        ts_repo(repo, "src/Row.tsx", component(jsx))
        assert_passes(run_gate(repo))

    @pytest.mark.parametrize(
        "jsx",
        [
            "<button onClick={close}>Close</button>",
            "<label onClick={close}>Name</label>",
            '<div role="dialog" onClick={(e) => e.stopPropagation()}>panel</div>',
            "<Card onClick={close}>row</Card>",
        ],
    )
    def test_native_controls_containers_and_components_pass(self, repo, jsx):
        # label is clicked through its control; a dialog panel stopping
        # propagation is structure, not a control; a component may well render
        # a button inside, and the gate cannot see in.
        ts_repo(repo, "src/Row.tsx", component(jsx))
        assert_passes(run_gate(repo))


class TestBackdropEscape:
    BACKDROP = '<div role="presentation" onClick={close}>backdrop</div>'

    def test_a_backdrop_in_a_file_that_never_handles_escape_fails(self, repo):
        ts_repo(repo, "src/Modal.tsx", component(self.BACKDROP))
        assert_fails(run_gate(repo), "nothing in this file handles Escape")

    def test_the_same_backdrop_with_an_escape_handler_passes(self, repo):
        escape = (
            "const onKey = (e: KeyboardEvent) => {\n"
            '  if (e.key === "Escape") dismiss();\n'
            "};\n"
        )
        ts_repo(repo, "src/Modal.tsx", component(self.BACKDROP, escape))
        assert_passes(run_gate(repo))

    def test_a_backdrop_is_not_asked_for_tabindex(self, repo):
        # role="presentation" is deliberate: its keyboard obligation is Escape,
        # and demanding tabIndex would ask it to be announced.
        escape = 'const ESC = "Escape";\n'
        ts_repo(repo, "src/Modal.tsx", component(self.BACKDROP, escape))
        result = run_gate(repo)
        assert "is missing" not in output(result), output(result)
        assert_passes(result)


class TestEmptyState:
    FETCHED = (
        "export function Rows() {\n"
        "  const [rows, setRows] = useState<string[]>([]);\n"
        '  useEffect(() => { void fetch("/rows").then((r) => r.json()).then(setRows); }, []);\n'
        "  return <ul>{rows.map((row) => <li key={row}>{row}</li>)}</ul>;\n"
        "}\n"
    )

    def test_a_fetched_list_with_no_empty_case_fails(self, repo):
        ts_repo(repo, "src/Rows.tsx", self.FETCHED)
        assert_fails(run_gate(repo), "rows.map() renders rows")

    def test_the_same_list_with_an_empty_case_passes(self, repo):
        handled = self.FETCHED.replace(
            "  return <ul>", "  if (rows.length === 0) return <p>No rows yet</p>;\n  return <ul>"
        )
        ts_repo(repo, "src/Rows.tsx", handled)
        assert_passes(run_gate(repo))

    def test_a_handwritten_list_is_not_a_fetched_one(self, repo):
        source = self.FETCHED.replace("rows.map", "TABS.map")
        ts_repo(repo, "src/Rows.tsx", source)
        assert_passes(run_gate(repo))

    def test_options_in_a_select_are_not_a_blank_pane(self, repo):
        source = self.FETCHED.replace(
            "<ul>{rows.map((row) => <li key={row}>{row}</li>)}</ul>",
            "<select>{rows.map((row) => <option key={row}>{row}</option>)}</select>",
        )
        ts_repo(repo, "src/Rows.tsx", source)
        assert_passes(run_gate(repo))


class TestTheWaiverContract:
    def test_a_substantive_reason_waives_the_line(self, repo):
        jsx = (
            "<div>\n"
            "      {/* ux-ok: backdrop; Esc closes and the dialog traps focus */}\n"
            "      <div onClick={close}>x</div>\n"
            "    </div>"
        )
        ts_repo(repo, "src/Row.tsx", component(jsx))
        assert_passes(run_gate(repo))

    def test_the_bare_marker_still_fails(self, repo):
        jsx = "<div>\n      {/* ux-ok: */}\n      <div onClick={close}>x</div>\n    </div>"
        ts_repo(repo, "src/Row.tsx", component(jsx))
        result = run_gate(repo)
        assert_fails(result, "'ux-ok:' with no substantive reason")
        assert "is missing role" in output(result)

    def test_a_short_reason_is_reported_once(self, repo):
        # The source walked up from every touched line beneath a comment block
        # and reported the same marker once per line — three findings for one.
        source = (
            "export function V({ close }: { close: () => void }) {\n"
            "  // ux-ok: meh\n"
            "  // more words about this that are long enough\n"
            "  return <div onClick={close}>x</div>;\n"
            "}\n"
        )
        ts_repo(repo, "src/V.tsx", source)
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert output(result).count("'ux-ok:' with no substantive reason") == 1, output(result)


class TestTheSeams:
    def test_it_runs_without_a_parser_in_the_graded_repo(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<div onClick={close}>row</div>"))
        assert not (repo.root / "node_modules").exists()
        assert_fails(run_gate(repo), "is missing role")

    def test_staged_files_as_lefthook_passes_them(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<div onClick={close}>row</div>"))
        repo.stage("src/Row.tsx")
        result = repo.gate(GATE, "src/Row.tsx")
        assert result.returncode == 1, output(result)
        assert "pre-commit: user-experience check failed" in output(result)

    def test_an_unknown_scope_is_refused(self, repo):
        ts_repo(repo, "src/Row.tsx", component("<div onClick={close}>row</div>"))
        result = repo.gate(GATE, "--repo", str(repo.root), "--scope", "wrokTree")
        assert result.returncode == 1, output(result)
        assert "unknown scope" in output(result)

    def test_an_unparseable_file_is_refused_not_skipped(self, repo):
        ts_repo(repo, "src/Broken.tsx", "export function ( { { {\n")
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "could not parse" in output(result)


class TestDefectsFixedInExtraction:
    """Each fails against the loregarden source's behaviour."""

    def test_an_image_with_alt_text_names_its_button(self, repo):
        # Accessible-name computation takes an image's alt as the text it
        # contributes. The source reported this — the canonical accessible
        # icon button — as having no name.
        jsx = '<button onClick={close}><img src="/trash.svg" alt="Delete row" /></button>'
        ts_repo(repo, "src/Row.tsx", component(jsx))
        assert_passes(run_gate(repo))

    def test_an_empty_alt_is_decorative_and_names_nothing(self, repo):
        # The control for the above: alt="" is the explicit "decorative".
        jsx = '<button onClick={close}><img src="/trash.svg" alt="" /></button>'
        ts_repo(repo, "src/Row.tsx", component(jsx))
        assert_fails(run_gate(repo), "<button> has no text")

    def test_a_generic_arrow_in_a_ts_file_is_graded(self, repo):
        # jsx: true for every file made `<T>(...) =>` unparseable in a `.ts`
        # file, and the source skipped what it could not parse — along with
        # the bare waiver in it.
        source = (
            "// ux-ok:\n"
            "export const firstOf = <T>(items: T[]): T | undefined => items[0];\n"
        )
        ts_repo(repo, "src/first.ts", source)
        result = run_gate(repo)
        assert_fails(result, "'ux-ok:' with no substantive reason")
        assert "could not parse" not in output(result)


class TestDefectsFixedInReview:
    """Each fails against the gate as first extracted (PR #59 review)."""

    def test_a_component_child_may_be_the_label(self, repo):
        # A childless component was read as "no text", so a translated label
        # was reported as an unnamed control.
        source = component(
            '<button onClick={close}><FormattedMessage id="save" /></button>',
            preamble='import { FormattedMessage } from "react-intl";\n',
        )
        ts_repo(repo, "src/Row.tsx", source)
        assert_passes(run_gate(repo))

    @pytest.mark.parametrize(
        ("preamble", "jsx"),
        [
            ('import { Trash } from "lucide-react";\n', "<Trash />"),
            ('import { FaTrash } from "react-icons/fa";\n', "<FaTrash />"),
            ("", "<IconTrash />"),
            ("", "<Icons.Trash />"),
        ],
    )
    def test_an_icon_component_still_names_nothing(self, repo, preamble, jsx):
        source = component(f"<button onClick={{close}}>{jsx}</button>", preamble=preamble)
        ts_repo(repo, "src/Row.tsx", source)
        assert_fails(run_gate(repo), "<button> has no text and no aria-label/title")

    def test_a_waiver_on_a_nested_element_does_not_waive_the_control(self, repo):
        # A marker anywhere in the button's span waived it, so the reason
        # written for the inner element excused the unnamed button too.
        jsx = (
            "<button onClick={close}>\n"
            "      <span onClick={close /* ux-ok: the button handles the key path */} />\n"
            "      <TrashIcon />\n"
            "    </button>"
        )
        ts_repo(repo, "src/Row.tsx", component(jsx))
        result = run_gate(repo)
        assert_fails(result, "<button> has no text and no aria-label/title")
        assert "<span onClick>" not in output(result)
