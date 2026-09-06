#!/usr/bin/env python3
"""Stylesheets, under the same rules as the code beside them.

CSS was the hole. Every gate in this library grades ``.py``, ``.ts`` or
``.tsx``, and lefthook's globs match those extensions, so a commit touching only
stylesheets ran **no gate at all** — the hook stage printed "no files for
inspection" and passed. That is not a small omission: the defect that prompted
this gate was 81 declarations naming a custom property nothing defines, each one
silently dropped by the browser, accumulated over the whole life of the files
because nothing was watching.

Five rules, and each exists because the failure it names had already happened
rather than because it sounded prudent:

``undefined-token``
    ``var(--fsBody)`` where the token is ``--fs-body``. CSS has no error for
    this: an unknown custom property with no fallback makes the declaration
    invalid at computed-value time, and it is dropped. No warning, no console
    message — a control simply renders unstyled. Needs ``css_token_source``;
    without it the rule is off and says so, because a rule that cannot name the
    tokens cannot tell a typo from a token the consumer supplies.

``token-duplicate-colour``
    A colour literal equal to a token's value, in either spelling —
    ``rgba(111, 174, 143, .28)`` is ``#6fae8f`` is ``--ac2``. The same colour in
    three notations moves in one place when the theme changes and stays put in
    the other two. Same rule as ``py_string_vocab``'s enum check, one language
    over.

``file-length``
    Only on net growth, like every other size cap here. A cap that fires on any
    touch to an already-long file blocks the cleanup that would shorten it.

``important``
    ``!important`` starts a specificity war with the next author. The one
    honest use is overriding a stylesheet you do not control, so the waiver has
    to *name* that package and the gate checks the claim: the package must
    appear in a ``package.json`` dependency list and must not be one of ours.
    A waiver that cannot be checked is a comment.

``orphan``
    A stylesheet nothing imports. Dead CSS reads as live CSS — it is the file
    people edit for an hour before discovering the page never loaded it.

Waivers, on the offending line or the line above it::

    color: #6fae8f; /* css-org: allow-colour (matching a screenshot exactly) */
    z-index: 9 !important; /* css-org: allow-important (react-datepicker) */

Usage:
    css_organization_check.py [staged files...]
    css_organization_check.py --repo PATH --scope worktree|staged|branch
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

_GATE_SCRIPTS = Path(__file__).resolve().parent
if str(_GATE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_GATE_SCRIPTS))

from interpreter import require_python  # noqa: E402 - sys.path is set up just above

# Before the imports below, not inside main(): the version this gate needs is
# needed to *import* them, so a check that ran later would never run at all.
require_python()

from gate_cli import guarded  # noqa: E402 - sys.path is set up above
from house_rules import (  # noqa: E402 - deliberately after require_python()
    HouseRules,
    HouseRulesError,
    load_house_rules,
)
from precommit_git_diff import (  # noqa: E402 - same
    DEFAULT_BASE_REF,
    STAGED,
    git_repo_root,
    read_source_text,
    resolve_gate_scope,
)

#: Stylesheets are mostly one declaration per line, so this is deliberately
#: tighter than the 1500 a Python module gets and close to the 1200 for a `.tsx`.
MAX_FILE_LINES = 600

WAIVER_COLOUR = "css-org: allow-colour"
WAIVER_IMPORTANT = "css-org: allow-important"
WAIVER_ORPHAN = "css-org: allow-orphan"
WAIVER_LENGTH = "css-org: allow-length"

#: `@lore-eden/…` is ours; so is anything the repo publishes itself. A waiver
#: naming one of these is not "a library we do not control".
_OUR_SCOPES = ("@lore-eden/", "@loregarden/")

_VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?")
_DECL_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
_HEX_RE = re.compile(r"#([0-9a-fA-F]{3,8})\b")
_RGB_RE = re.compile(r"\brgba?\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*[,)]")
_IMPORTANT_RE = re.compile(r"!\s*important\b")
_WAIVER_ARG_RE = re.compile(r"css-org: allow-important\s*\(\s*([^)]+?)\s*\)")
#: `import './x.css'`, `@import "./x.css"`, `from "./x.css"` — any mention of the
#: file's name in a source file counts. Deliberately loose: this rule is about a
#: stylesheet nothing knows about, and a false negative beats accusing a live
#: file of being dead.
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)

#: The suffix this gate grades.
CSS_SUFFIX = ".css"

#: Suffixes that can carry a reference to a stylesheet. Named rather than
#: inlined at the one call site: it is a vocabulary, and the orphan rule's
#: accuracy is exactly the question of what is in it.
REFERENCING_SUFFIXES = frozenset(
    {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".html"}
)


@dataclass(frozen=True)
class Finding:
    lineno: int
    message: str


def _strip_comments(text: str) -> str:
    """Comments blanked, newlines kept, so line numbers still line up."""
    return _COMMENT_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def normalised_colour(value: str) -> str | None:
    """One spelling for a colour, so two notations of it compare equal.

    ``#6faE8f``, ``#6fae8f`` and ``rgb(111, 174, 143)`` all become ``6fae8f``.
    Alpha is deliberately dropped: ``rgba(111, 174, 143, .28)`` is the token's
    colour at an opacity, which is still the token's colour hard-coded.
    """
    value = value.strip()
    m = _HEX_RE.fullmatch(value)
    if m:
        digits = m.group(1).lower()
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits)
        return digits[:6]
    m = _RGB_RE.match(value)
    if m:
        try:
            parts = [int(p) for p in m.groups()[:3]]
        except ValueError:
            return None
        if any(p > 255 for p in parts):
            return None
        return "".join(f"{p:02x}" for p in parts)
    return None


def load_token_names_and_colours(
    repo: Path | None, rules: HouseRules
) -> tuple[frozenset[str], dict[str, str]]:
    """Custom properties the repo's token source defines, and their colours.

    Two source shapes, because a repo defines its tokens in whichever language
    owns them: a ``.css`` file with ``--name: value`` declarations, or a
    TypeScript/JavaScript module carrying ``css``/``value`` pairs (which is what
    a repo that derives its CSS variables from a typed table has). Anything
    else, and the rule is off rather than guessing.
    """
    if repo is None or not rules.css_token_source:
        return frozenset(), {}
    source = repo / rules.css_token_source
    if not source.is_file():
        raise HouseRulesError(
            f"`css_token_source` names {rules.css_token_source}, which is not a file "
            f"under {repo}; the undefined-token rule cannot run without it"
        )
    text = source.read_text(encoding="utf-8", errors="replace")
    names: set[str] = set()
    colours: dict[str, str] = {}
    if source.suffix == CSS_SUFFIX:
        for m in re.finditer(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", text):
            names.add(m.group(1))
            norm = normalised_colour(m.group(2))
            if norm:
                colours.setdefault(norm, m.group(1))
    else:
        for m in re.finditer(
            r"css:\s*[\"'](--[A-Za-z0-9_-]+)[\"']\s*,\s*value:\s*[\"']([^\"']*)[\"']", text
        ):
            names.add(m.group(1))
            norm = normalised_colour(m.group(2))
            if norm:
                colours.setdefault(norm, m.group(1))
    if not names:
        raise HouseRulesError(
            f"`css_token_source` {rules.css_token_source} defines no custom properties this "
            "gate could read; a token source it cannot parse is not a token source"
        )
    return frozenset(names), colours


def _waived(lines: list[str], lineno: int, marker: str) -> bool:
    """The waiver on the offending line, or on the line above it.

    Both, because a long declaration is routinely wrapped and the trailing
    comment then sits on a different physical line from the property.
    """
    for index in (lineno - 1, lineno - 2):
        if 0 <= index < len(lines) and marker in lines[index]:
            return True
    return False


def undefined_token_findings(
    lines: list[str], code: list[str], token_names: frozenset[str]
) -> list[Finding]:
    """``var(--x)`` naming a property nothing defines, with no fallback.

    A property the same file declares is fine — component-local variables are a
    normal thing to have. A ``var(--x, fallback)`` is fine too: the fallback is
    what renders, so the declaration is not dropped. It is the bare reference to
    a name nothing defines that disappears.
    """
    local = {m.group(1) for line in code for m in _DECL_RE.finditer(line)}
    findings = []
    for lineno, line in enumerate(code, start=1):
        for m in _VAR_RE.finditer(line):
            name, has_fallback = m.group(1), m.group(2)
            if has_fallback or name in token_names or name in local:
                continue
            findings.append(
                Finding(
                    lineno,
                    f"var({name}) names a custom property nothing defines, so this "
                    f"declaration is dropped at computed-value time",
                )
            )
    return findings


def duplicate_colour_findings(
    lines: list[str], code: list[str], token_colours: dict[str, str]
) -> list[Finding]:
    """A colour literal that is already a token, in any notation."""
    findings = []
    for lineno, line in enumerate(code, start=1):
        if _waived(lines, lineno, WAIVER_COLOUR):
            continue
        seen: set[str] = set()
        for m in (*_HEX_RE.finditer(line), *_RGB_RE.finditer(line)):
            norm = normalised_colour(m.group(0))
            if norm is None or norm in seen:
                continue
            token = token_colours.get(norm)
            if token is None:
                continue
            seen.add(norm)
            findings.append(
                Finding(
                    lineno,
                    f"`{m.group(0)}` is the value of `{token}`; use var({token}) so it "
                    f"follows the theme instead of being frozen here",
                )
            )
    return findings


def important_findings(lines: list[str], code: list[str], third_party: frozenset[str]) -> list[Finding]:
    """``!important``, unless the waiver names a package we do not control.

    The claim is checked rather than taken: an unwaived one is a finding, and so
    is a waiver naming something that is not a dependency, or that is ours. A
    waiver nobody verifies is how ``!important`` becomes the house style.
    """
    findings = []
    for lineno, line in enumerate(code, start=1):
        if not _IMPORTANT_RE.search(line):
            continue
        waiver = None
        for index in (lineno - 1, lineno - 2):
            if 0 <= index < len(lines):
                m = _WAIVER_ARG_RE.search(lines[index])
                if m:
                    waiver = m.group(1)
                    break
        if waiver is None:
            findings.append(
                Finding(
                    lineno,
                    "`!important` starts a specificity war; raise the selector's "
                    f"specificity instead, or waive it as `{WAIVER_IMPORTANT} (<package>)` "
                    "naming the third-party stylesheet being overridden",
                )
            )
            continue
        if any(waiver.startswith(scope) for scope in _OUR_SCOPES):
            findings.append(
                Finding(
                    lineno,
                    f"the `!important` waiver names `{waiver}`, which is ours — fix the "
                    "specificity there rather than overriding it from here",
                )
            )
        elif third_party and waiver not in third_party:
            findings.append(
                Finding(
                    lineno,
                    f"the `!important` waiver names `{waiver}`, which is not a dependency "
                    "of this repo; the exception is for overriding a stylesheet you do "
                    "not control",
                )
            )
    return findings


def third_party_packages(repo: Path | None) -> frozenset[str]:
    """Every dependency named by any ``package.json`` the repo tracks.

    Empty when there is none to read, which turns the waiver's package check
    off rather than failing every waiver in a repo that has no manifest.
    """
    if repo is None:
        return frozenset()
    names: set[str] = set()
    for manifest in repo.rglob("package.json"):
        if "node_modules" in manifest.parts:
            continue
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(raw, dict):  # py-org: allow-isinstance (no Pydantic here by design)
            continue
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            block = raw.get(section)
            if isinstance(block, dict):  # py-org: allow-isinstance — see above
                names.update(block)
    return frozenset(names)


def orphan_finding(path: Path, repo: Path | None, lines: list[str]) -> Finding | None:
    """A stylesheet no source file mentions.

    Matched on the file's name rather than a resolved import path, because
    bundlers, aliases and CSS ``@import`` all spell the reference differently
    and this rule is about a file nothing knows about at all. Loose on purpose:
    calling a live stylesheet dead is worse than missing one.
    """
    if repo is None or _waived(lines, 1, WAIVER_ORPHAN):
        return None
    needle = path.name
    for candidate in repo.rglob("*"):
        if not candidate.is_file() or candidate == path:
            continue
        if "node_modules" in candidate.parts or ".git" in candidate.parts:
            continue
        if candidate.suffix not in REFERENCING_SUFFIXES:
            continue
        try:
            if needle in candidate.read_text(encoding="utf-8", errors="replace"):
                return None
        except OSError:
            continue
    return Finding(
        1,
        f"nothing imports {needle}; a stylesheet nothing loads reads exactly like one "
        "that works",
    )


def check_file(
    path: Path,
    *,
    content: str,
    touched_lines: set[int] | None,
    net_growing: bool,
    repo: Path | None,
    token_names: frozenset[str],
    token_colours: dict[str, str],
    third_party: frozenset[str],
) -> list[str]:
    """Every rule against one stylesheet, scoped to the lines this change touched."""
    lines = content.splitlines()
    code = _strip_comments(content).splitlines()

    findings: list[Finding] = []
    if token_names:
        findings.extend(undefined_token_findings(lines, code, token_names))
    if token_colours:
        findings.extend(duplicate_colour_findings(lines, code, token_colours))
    findings.extend(important_findings(lines, code, third_party))

    scoped = [
        f for f in findings if touched_lines is None or f.lineno in touched_lines
    ]

    # Two whole-file properties. Neither belongs to a line the change touched,
    # so both are reported for any file in scope — but the length cap still
    # waits for net growth, or a commit that deletes 40 lines from a long file
    # is blocked by the length it just reduced.
    if len(lines) > MAX_FILE_LINES and net_growing and not _waived(lines, 1, WAIVER_LENGTH):
        scoped.append(
            Finding(
                len(lines),
                f"{len(lines)} lines (max {MAX_FILE_LINES}); split it by component "
                "rather than growing one sheet the whole app edits",
            )
        )
    orphan = orphan_finding(path, repo, lines)
    if orphan is not None:
        scoped.append(orphan)

    return [f"{path}:{f.lineno}: {f.message}" for f in sorted(scoped, key=lambda f: f.lineno)]


def css_files_in_scope(
    repo: Path | None, candidates: Sequence[Path], discovered: bool
) -> list[Path]:
    """``.css`` this gate grades. A discovered list is not narrowed further: a
    stylesheet is a stylesheet wherever it sits, and there is no equivalent of a
    source root that would exclude one honestly."""
    return [path for path in candidates if path.suffix == CSS_SUFFIX]


@dataclass(frozen=True)
class Invocation:
    files: list[Path]
    repo: Path | None
    diff_scope: str
    base_ref: str
    label: str


def parse_argv(argv: list[str]) -> Invocation:
    files: list[Path] = []
    repo_arg: str | None = None
    diff_scope = STAGED
    base_ref = DEFAULT_BASE_REF
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--repo" and index + 1 < len(argv):
            repo_arg, index = argv[index + 1], index + 2
        elif arg == "--scope" and index + 1 < len(argv):
            diff_scope, index = argv[index + 1], index + 2
        elif arg == "--base" and index + 1 < len(argv):
            base_ref, index = argv[index + 1], index + 2
        else:
            if arg.endswith(CSS_SUFFIX):
                files.append(Path(arg))
            index += 1
    repo = Path(repo_arg).resolve() if repo_arg else git_repo_root()
    label = "pre-commit" if diff_scope == STAGED and repo_arg is None else "gate"
    return Invocation(files, repo, diff_scope, base_ref, label)


def main(argv: list[str]) -> int:
    invocation = parse_argv(argv)
    return guarded(invocation.label, lambda: _check(invocation))


def _check(invocation: Invocation) -> int:
    run = resolve_gate_scope(
        label=invocation.label,
        repo=invocation.repo,
        diff_scope=invocation.diff_scope,
        base_ref=invocation.base_ref,
        explicit_files=invocation.files,
        select=css_files_in_scope,
    )
    rules = load_house_rules(run.repo)
    token_names, token_colours = load_token_names_and_colours(run.repo, rules)
    if not token_names:
        # Said on every run, in both invocation forms. A rule that is off and
        # silent is indistinguishable from a rule that found nothing — which is
        # the defect this library exists to refuse.
        print(
            f"{invocation.label}: token rules are off — set `css_token_source` in "
            ".lore-eden-gates.json to the file defining this repo's custom properties."
        )
    if not run.files:
        return 0

    third_party = third_party_packages(run.repo)
    failures: list[str] = []
    for path in run.files:
        failures.extend(
            check_file(
                path,
                content=read_source_text(path, repo=run.repo),
                touched_lines=run.touched_lines(path),
                net_growing=run.net_growing(path),
                repo=run.repo,
                token_names=token_names,
                token_colours=token_colours,
                third_party=third_party,
            )
        )

    if not failures:
        print(f"{invocation.label}: CSS organization checks passed.")
        return 0

    print(f"{invocation.label}: CSS organization check failed:")
    for failure in failures:
        print(f" - {failure}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
