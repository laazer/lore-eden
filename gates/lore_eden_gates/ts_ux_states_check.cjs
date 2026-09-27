#!/usr/bin/env node
/**
 * Keep the app usable by the person in front of it.
 *
 * These four shapes are the mechanical half of policy/ux-definition-of-done.md;
 * the rest of that checklist is a reading, not something a gate can decide.
 *
 * The silent-failure gate covers the failure path: an error the user never
 * sees. This one covers the states next to it that are just as invisible in
 * review and just as loud in use — a control nothing can name, a control a
 * keyboard cannot reach, a backdrop only a mouse can dismiss, and a list that
 * renders nothing and explains nothing.
 *
 * None of these are caught by anything else lore-eden ships. policy/oxlintrc.json
 * enables no jsx-a11y rules, tsc has no opinion about an accessible name, and a
 * screenshot review sees an icon button and reads it as fine because the
 * reviewer already knows what it does.
 *
 * Checks (diff-scoped, on .ts/.tsx under the detected TypeScript root):
 *   1. <button>/<a> whose only children are icons — no text, no aria-label,
 *      no title. Nothing announces it, and nothing explains it on hover.
 *   2. onClick on a non-interactive element without role + tabIndex + a key
 *      handler. Reachable with a mouse, unreachable without one.
 *   3. A dismiss backdrop — role="presentation" with onClick — in a file that
 *      never handles Escape. The mouse can close it and the keyboard cannot.
 *   4. A fetched list rendered with .map() in a file that never handles the
 *      empty case. Zero rows and a blank pane are the same picture, and the
 *      user cannot tell "none yet" from "it broke".
 *
 * A case that is genuinely fine says so, in a comment, with a reason:
 *
 *     <div onClick={close} /> {/* ux-ok: backdrop; Esc closes and the dialog traps focus *\/}
 *
 * The marker alone is not enough — the reason must be substantive, the same
 * contract `silent-ok:` carries, from the same module (ts_waivers.cjs), and it
 * waives the one construct it annotates, not the control around it.
 *
 * Extracted from loregarden's `.lefthook/scripts/`. Parser resolution, scope
 * and file reading come from ts_gate_harness.cjs. Where this differs from the
 * source it is a defect fixed, and each has a test in
 * gates/tests/test_ts_ux_states_gate.py.
 */

const {
  isTestFile,
  parseGradedFile,
  readSource,
  runGate,
  walk,
} = require("./ts_gate_harness.cjs");
const { spanTouched, waiverContract } = require("./ts_waivers.cjs");

const ALLOW_MARKER = "ux-ok:";

// Elements the browser already makes focusable, clickable and announceable.
// `label` and `option` are here because a click on them is handled by the
// control they belong to, not by them.
const NATIVELY_INTERACTIVE = new Set([
  "a",
  "button",
  "input",
  "label",
  "option",
  "select",
  "summary",
  "textarea",
]);

// Attributes that give an element an accessible name.
const NAMING_ATTRS = new Set(["aria-label", "aria-labelledby", "title"]);

// Attributes by which a *child* names the control it sits in: accessible-name
// computation takes a child's own label, and an image's `alt`, as the text it
// contributes. `alt=""` is the explicit "decorative" and contributes nothing.
const CHILD_NAMING_ATTRS = new Set(["aria-label", "aria-labelledby", "title", "alt"]);

// An element hidden from the accessibility tree on purpose. A modal backdrop is
// the case here: it is `role="presentation"` precisely so it is not announced or
// tabbed to, so demanding tabIndex on it would be asking for the opposite of
// what it is for. Its keyboard obligation is Escape, checked separately.
const PRESENTATIONAL_ROLES = new Set(["presentation", "none"]);

// Structure, not a control. A `role="dialog"` panel carrying
// `onClick={(e) => e.stopPropagation()}` is stopping the backdrop from closing
// underneath it, and asking that container for a key handler is asking it to be
// something it is not. The widget roles — button, link, tab, menuitem — are
// deliberately absent: those genuinely owe the keyboard an answer.
const CONTAINER_ROLES = new Set([
  "alertdialog",
  "dialog",
  "document",
  "group",
  "list",
  "listitem",
  "region",
  "toolbar",
]);

// Handling a key press by name. Any of these means the file thought about the
// keyboard path out; which key object it reads from is not this gate's business.
// `useDialogDismiss` is loregarden's hook for it, kept because that is where
// the gate was written.
const ESCAPE_PATTERNS = [/["'`]Escape["'`]/, /\bkeyCode\s*===?\s*27\b/, /\buseDialogDismiss\b/];

// Any of these appearing in a file is a handled empty case. Deliberately broad:
// the gate's job is to notice a list nobody thought about, not to dictate how
// the empty state is written.
const EMPTY_STATE_PATTERNS = [
  // `?.` is written as often as `.`, and a check the gate cannot see is a
  // finding it invents: `if (!findings?.length) return null` is a handled empty
  // case that the un-optional form of this pattern read as an unhandled one.
  /\??\.length\s*===?\s*0/,
  /\??\.length\s*(?:\?|>|&&)/,
  /!\w[\w.?]*\??\.length/,
  /\blength\s*<\s*1\b/,
  /\bisEmpty\b/,
  /\bEmpty(?:State|List|Message|Placeholder)\b/,
  /\bemptyLabel\b/,
  /\bnoResults\b/i,
];

// A file with none of these is not rendering fetched data, so a .map() in it is
// over something the author wrote by hand and cannot surprise anyone by being
// empty.
const ASYNC_SOURCE_PATTERNS = [
  /\bawait\b/,
  /\bfetch\(/,
  /\buseQuery\b/,
  /\buseSWR\b/,
  /\buseEffect\b/,
  /\buse[A-Z]\w*Store\b/,
  /\brequest\(/,
];

/** The tag as written: "div", "Dialog.Panel", or "" for something dynamic. */
function jsxName(node) {
  if (!node) return "";
  if (node.type === "JSXIdentifier") return node.name;
  if (node.type === "JSXMemberExpression") return `${jsxName(node.object)}.${node.property.name}`;
  return "";
}

const elementName = (opening) => jsxName(opening && opening.name);

/** An intrinsic element is lowercase; a component is not, and we cannot see inside it. */
function isIntrinsic(name) {
  return name !== "" && name[0] === name[0].toLowerCase() && !name.includes(".");
}

/** The attribute node named `wanted`, or null. Spreads are skipped. */
function findAttribute(opening, wanted) {
  for (const attr of opening.attributes || []) {
    if (attr.type !== "JSXSpreadAttribute" && attr.name && attr.name.name === wanted) return attr;
  }
  return null;
}

/** The literal string value of one attribute, or "" when it is not a literal. */
function attributeValue(opening, wanted) {
  const attr = findAttribute(opening, wanted);
  const value = attr && attr.value;
  return value && value.type === "Literal" && typeof value.value === "string" ? value.value : "";
}

function attributeNames(opening) {
  const names = new Set();
  let hasSpread = false;
  for (const attr of opening.attributes || []) {
    if (attr.type === "JSXSpreadAttribute") {
      hasSpread = true;
      continue;
    }
    const name = attr.name;
    if (!name) continue;
    if (name.type === "JSXIdentifier") names.add(name.name);
    else if (name.type === "JSXNamespacedName") {
      names.add(`${name.namespace.name}:${name.name.name}`);
    }
  }
  return { names, hasSpread };
}

/**
 * Whether a child element names its parent control: true, false, or null when
 * the name is an expression this gate cannot read.
 *
 * The source did not ask, so `<button><img alt="Delete row" /></button>` — the
 * canonical accessible icon button — was reported as having no name.
 */
function childNames(opening) {
  let unknown = false;
  for (const wanted of CHILD_NAMING_ATTRS) {
    const attr = findAttribute(opening, wanted);
    if (!attr) continue;
    const value = attr.value;
    if (value && value.type === "Literal") {
      if (String(value.value).trim() !== "") return true;
    } else {
      unknown = true;
    }
  }
  return unknown ? null : false;
}

// Where a component is only a glyph. A component the gate cannot see inside —
// `<FormattedMessage id="save" />`, `<Trans>`, `<Label />` — may render the
// control's text, so only one that is recognisably an icon is known to render
// none: by its name (`TrashIcon`, `IconTrash`, `Icon`), or by being imported
// from a package that ships icons.
const ICON_NAME = /(?:^Icons?(?:[A-Z0-9]|$)|Icon$)/;
const ICON_PACKAGE = new RegExp(
  "^(?:lucide-react|react-feather|react-icons(?:/.*)?|@heroicons/.+|@radix-ui/react-icons|" +
    "@mui/icons-material(?:/.*)?|@material-ui/icons(?:/.*)?|@tabler/icons-react|" +
    "@phosphor-icons/react|phosphor-react|@fortawesome/.+|@ant-design/icons|" +
    "@primer/octicons-react|@carbon/icons-react|@iconify/react|react-bootstrap-icons)$",
);

/** Local names imported from an icon package: `import { Trash } from "lucide-react"`. */
function iconImports(ast) {
  const names = new Set();
  for (const statement of ast.body || []) {
    if (statement.type !== "ImportDeclaration") continue;
    if (!ICON_PACKAGE.test(String(statement.source.value))) continue;
    for (const specifier of statement.specifiers || []) names.add(specifier.local.name);
  }
  return names;
}

/** Whether a component tag is an icon: `TrashIcon`, `Icons.Trash`, an icon import. */
function isIconComponent(name, icons) {
  const root = name.split(".")[0];
  const last = name.split(".").pop();
  return ICON_NAME.test(last) || ICON_NAME.test(root) || icons.has(root);
}

/**
 * Whether the element carries its own visible or announced text.
 *
 * A `{expr}` child returns null, not false: the expression may well be a label,
 * and a gate that cannot tell must not accuse. A component child is the same
 * unknown unless it is an icon (`isIconComponent`): the source read every
 * childless component as "no text", so `<button><FormattedMessage id="save" />
 * </button>` — a translated label — was reported as an unnamed control.
 */
function hasOwnText(element, icons) {
  let sawExpression = false;
  let sawText = false;
  for (const child of element.children || []) {
    if (child.type === "JSXText" && child.value.trim() !== "") sawText = true;
    else if (child.type === "JSXExpressionContainer") {
      // `{/* comment */}` renders nothing, so it cannot name the control.
      if (child.expression.type !== "JSXEmptyExpression") sawExpression = true;
    }
    else if (child.type === "JSXElement" || child.type === "JSXFragment") {
      const named = child.type === "JSXElement" ? childNames(child.openingElement) : false;
      const nested = named === true ? true : hasOwnText(child, icons);
      const tag = child.type === "JSXElement" ? elementName(child.openingElement) : "";
      const opaque = tag !== "" && !isIntrinsic(tag) && !isIconComponent(tag, icons);
      if (nested === true) sawText = true;
      else if (nested === null || named === null || opaque) sawExpression = true;
    }
  }
  if (sawText) return true;
  return sawExpression ? null : false;
}

/** `<button>` / `<a>` — the controls check 1 asks for a name. */
function isNameableControl(node) {
  if (node.type !== "JSXElement") return false;
  const name = elementName(node.openingElement);
  return name === "button" || name === "a";
}

/** An intrinsic, not natively interactive element with its own onClick — check 2. */
function isClickableNonControl(node) {
  if (node.type !== "JSXOpeningElement") return false;
  const name = elementName(node);
  if (!isIntrinsic(name) || NATIVELY_INTERACTIVE.has(name)) return false;
  const { names, hasSpread } = attributeNames(node);
  return names.has("onClick") && !hasSpread;
}

/** `role="presentation"` (or "none") with an onClick — the backdrop check 3 grades. */
function isClickBackdrop(node) {
  return (
    node.type === "JSXOpeningElement" &&
    PRESENTATIONAL_ROLES.has(attributeValue(node, "role")) &&
    attributeNames(node).names.has("onClick")
  );
}

/** 1. A control with no name at all — not spoken, not hoverable, not guessable. */
function unnamedControlErrors(filePath, ast, added, waivers) {
  const icons = iconImports(ast);
  const found = [];
  walk(ast, (node) => {
    if (!isNameableControl(node)) return true;
    const name = elementName(node.openingElement);

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(node)) return true;

    const { names, hasSpread } = attributeNames(node.openingElement);
    // Props may arrive wholesale, and dangerouslySetInnerHTML has children we
    // cannot read. Either way the name may be there; do not guess.
    if (hasSpread || names.has("dangerouslySetInnerHTML")) return true;
    if ([...NAMING_ATTRS].some((attr) => names.has(attr))) return true;

    const text = hasOwnText(node, icons);
    if (text === true || text === null) return true;

    found.push(
      `${filePath}:${start}: <${name}> has no text and no aria-label/title — a screen ` +
        `reader announces it as "button", and a hover explains nothing; add an aria-label ` +
        `naming the action`,
    );
    return true;
  });
  return found;
}

/** 2. Clickable to a mouse, invisible to a keyboard. */
function keyboardUnreachableErrors(filePath, ast, added, waivers) {
  const found = [];
  walk(ast, (node) => {
    if (!isClickableNonControl(node)) return true;
    const name = elementName(node);
    const { names } = attributeNames(node);

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(node)) return true;

    const role = attributeValue(node, "role");
    // A backdrop is presentational by design; its obligation is Escape, below.
    if (PRESENTATIONAL_ROLES.has(role) || CONTAINER_ROLES.has(role)) return true;

    const missing = [];
    if (!names.has("role")) missing.push("role");
    if (!names.has("tabIndex")) missing.push("tabIndex");
    if (!["onKeyDown", "onKeyUp", "onKeyPress"].some((k) => names.has(k))) {
      missing.push("a key handler");
    }
    if (missing.length === 0) return true;

    found.push(
      `${filePath}:${start}: <${name} onClick> is missing ${missing.join(", ")} — a keyboard ` +
        `user cannot reach or fire it; use <button> for an action, or add role, tabIndex ` +
        `and onKeyDown`,
    );
    return true;
  });
  return found;
}

/**
 * 3. Dismissable by mouse, not by keyboard.
 *
 * A backdrop wired to `onClick={onClose}` is the app saying this thing is
 * dismissable. Without an Escape handler that promise holds for a pointer and
 * breaks for everything else — and once a focus trap is in place, a keyboard
 * operator is not merely inconvenienced, they are held inside a dialog with no
 * way out that does not involve a mouse.
 */
function backdropWithoutEscapeErrors(filePath, ast, added, waivers, content) {
  if (ESCAPE_PATTERNS.some((re) => re.test(content))) return [];

  const found = [];
  walk(ast, (node) => {
    if (!isClickBackdrop(node)) return true;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(node)) return true;

    found.push(
      `${filePath}:${start}: this backdrop closes on click, but nothing in this file ` +
        `handles Escape — a keyboard operator inside the focus trap has no way out; ` +
        `close on Escape as well`,
    );
    return true;
  });
  return found;
}

/** Identifiers bound to an array literal in this file — handwritten, never surprising. */
function literalArrayNames(ast) {
  const names = new Set();
  walk(ast, (node) => {
    if (
      node.type === "VariableDeclarator" &&
      node.id &&
      node.id.type === "Identifier" &&
      node.init &&
      node.init.type === "ArrayExpression"
    ) {
      names.add(node.id.name);
    }
    return true;
  });
  return names;
}

/** The identifier a `.map()` is called on: `rows` in `rows.map`, `a` in `a.b.map`. */
function mappedBaseName(callee) {
  let object = callee.object;
  while (object && object.type === "MemberExpression") object = object.object;
  return object && object.type === "Identifier" ? object.name : "";
}

/**
 * Whether a `.map()` callback renders elements, and whether they are options.
 * A `<select>` with no options is a disabled picker, not a blank pane — the
 * control still renders and still says what it is.
 */
function rendersRows(callback) {
  let rendersJsx = false;
  let rendersOptions = false;
  walk(callback, (inner) => {
    if (inner.type === "JSXElement" || inner.type === "JSXFragment") rendersJsx = true;
    if (inner.type === "JSXOpeningElement" && elementName(inner) === "option") {
      rendersOptions = true;
    }
    return true;
  });
  return rendersJsx && !rendersOptions;
}

/** A `.map()` whose callback renders rows — the call check 4 grades. */
function isRenderingMap(node) {
  if (node.type !== "CallExpression") return false;
  const callee = node.callee;
  if (!callee || callee.type !== "MemberExpression") return false;
  if (!callee.property || callee.property.name !== "map") return false;
  // Only a map that renders. A map producing values is not an empty state.
  const callback = node.arguments[0];
  if (
    !callback ||
    (callback.type !== "ArrowFunctionExpression" && callback.type !== "FunctionExpression")
  ) {
    return false;
  }
  return rendersRows(callback);
}

/** Every construct this gate grades — what a `ux-ok:` waiver can belong to. */
function gradedConstructs(ast) {
  const found = [];
  walk(ast, (node) => {
    if (
      isNameableControl(node) ||
      isClickableNonControl(node) ||
      isClickBackdrop(node) ||
      isRenderingMap(node)
    ) {
      found.push(node);
    }
    return true;
  });
  return found;
}

/** 4. A fetched list whose empty case nothing renders. */
function missingEmptyStateErrors(filePath, ast, added, waivers, content) {
  if (!ASYNC_SOURCE_PATTERNS.some((re) => re.test(content))) return [];
  if (EMPTY_STATE_PATTERNS.some((re) => re.test(content))) return [];

  const literals = literalArrayNames(ast);
  const found = [];
  const reported = new Set();
  walk(ast, (node) => {
    if (!isRenderingMap(node)) return true;
    const callee = node.callee;

    const base = mappedBaseName(callee);
    // A literal array in this file, or an imported SCREAMING_SNAKE constant:
    // both are lists the author wrote out, and neither can arrive empty.
    if (base && (literals.has(base) || /^[A-Z0-9_]+$/.test(base))) return true;

    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(node)) return true;
    if (reported.has(start)) return true;
    reported.add(start);

    found.push(
      `${filePath}:${start}: ${base || "this list"}.map() renders rows, but nothing in this ` +
        `file handles the empty case — zero rows and a failed load look identical to the ` +
        `user; render an empty state, or waive with '${ALLOW_MARKER}' if a parent renders it`,
    );
    return true;
  });
  return found;
}

runGate({
  title: "user-experience",
  suffixes: [".ts", ".tsx"],
  footer: [
    "These four are the mechanical half of policy/ux-definition-of-done.md; " +
      "the rest of that checklist is a reading.",
  ],
  grade: ({ repoRoot }, { files, touched }) => {
    const errors = [];
    for (const filePath of files) {
      if (isTestFile(filePath)) continue;
      const content = readSource(filePath, repoRoot);
      const ast = parseGradedFile(filePath, content);
      const lines = content.split("\n");
      const { added } = touched(filePath, lines.length);
      const waivers = waiverContract(ALLOW_MARKER, lines, ast.comments, gradedConstructs(ast));
      errors.push(...unnamedControlErrors(filePath, ast, added, waivers));
      errors.push(...keyboardUnreachableErrors(filePath, ast, added, waivers));
      errors.push(...backdropWithoutEscapeErrors(filePath, ast, added, waivers, content));
      errors.push(...missingEmptyStateErrors(filePath, ast, added, waivers, content));
      errors.push(
        ...waivers.shortWaiverFindings(added, filePath, "say why this is right for the person using it"),
      );
    }
    return errors;
  },
});
