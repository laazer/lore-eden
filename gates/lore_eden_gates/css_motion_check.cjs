#!/usr/bin/env node
/**
 * Keep motion on the tokens, off the layout, and behind the reader's preference.
 *
 * Ported from loregarden's ts_motion_check.cjs (the rules) onto this package's
 * harness (scope, report, exit code) and waiver contract, so repos that run
 * lore-eden's gates (lore-eden, loremaker) hold the same line loregarden does.
 *
 * Checks (diff-scoped, on .css):
 *   1. A transition or keyframe that animates a layout property (width, height,
 *      top, margin, …), or `transition: all`, which includes them. Animate
 *      transform and opacity instead.
 *   2. A hardcoded UI duration (up to 400ms) in a transition or animation, where
 *      the repo defines the `--t-*` motion tokens (in `css_token_source` from
 *      .lore-eden-gates.json, or the root stylesheet). Longer ambient loops are
 *      left alone: no token describes them.
 *   3. Motion in a file with no reduced-motion policy: neither a global
 *      `@media (prefers-reduced-motion: reduce)` rule over `*` in the root
 *      stylesheet, nor one in the file itself.
 *
 * A case that is genuinely right says so, with a reason:
 *
 *     transition: width var(--t-fast); /* motion-ok: the rail's width is the layout change itself *\/
 */

const fs = require("fs");
const path = require("path");

const { isTestFile, readSource, runGate, tsSourceRoot } = require("./ts_gate_harness.cjs");
const { waiverContract } = require("./ts_waivers.cjs");

const ALLOW_MARKER = "motion-ok:";
const CONFIG_FILE = ".lore-eden-gates.json";

const LAYOUT_PROPERTY =
  /^(?:width|height|(?:min|max)-(?:width|height)|top|right|bottom|left|inset(?:-.*)?|margin(?:-.*)?|padding(?:-.*)?|border(?:-(?:top|right|bottom|left))?-width|font-size|line-height|flex-basis|gap|row-gap|column-gap|grid-template-(?:rows|columns))$/;
// Transitions on these move nothing on screen; a reader who asked for less
// motion is not served by suppressing a colour fade.
const STILL_PROPERTY =
  /^(?:opacity|visibility|color|background(?:-color)?|border(?:-(?:top|right|bottom|left))?-color|outline-color|fill|stroke|box-shadow|text-decoration-color|filter)$/;
const TIME = /^(\d*\.?\d+)(ms|s)$/i;
const TIME_EXPRESSION = /^(?:var\(--t-|calc\()/i;
const UI_DURATION_CEILING_MS = 400;

/** postcss, from this package's own dependencies; absent, the gate cannot examine CSS. */
function postcss() {
  try {
    return require("postcss");
  } catch (err) {
    console.error(
      `css-motion: cannot load postcss (${err.message}); run \`npm ci\` in lore-eden's gates/ ` +
        "— this gate examined nothing and cannot report anything clean",
    );
    process.exit(1);
  }
}

function splitTopLevel(value, separator) {
  const out = [];
  let depth = 0;
  let current = "";
  for (const ch of value) {
    if (ch === "(") depth++;
    if (ch === ")") depth--;
    if (separator.test(ch) && depth === 0) {
      if (current.trim()) out.push(current.trim());
      current = "";
    } else {
      current += ch;
    }
  }
  if (current.trim()) out.push(current.trim());
  return out;
}

const layers = (value) => splitTopLevel(value, /,/);
const words = (layer) => splitTopLevel(layer, /\s/);
const isTimeSlot = (word) => TIME.test(word) || TIME_EXPRESSION.test(word);
const isNone = (value) => /^\s*none\s*$/i.test(value);

function transitionedProperties(decl) {
  if (decl.prop === "transition-property") return layers(decl.value).map((p) => p.toLowerCase());
  return layers(decl.value).map((layer) => {
    const named = words(layer).find(
      (w) => !isTimeSlot(w) && !w.includes("(") && !/^(?:ease|linear|step)/i.test(w) && w !== "none",
    );
    return (named || "all").toLowerCase();
  });
}

function nearestToken(ms) {
  if (ms <= 160) return "var(--t-fast)";
  if (ms <= 250) return "var(--t-med)";
  return "var(--t-slow)";
}

/** Durations in a transition/animation value: the first time in each layer (the second is a delay). */
function durationsIn(decl) {
  if (/-delay$/.test(decl.prop)) return [];
  const everySlot = /-duration$/.test(decl.prop);
  const found = [];
  for (const layer of layers(decl.value)) {
    const slots = words(layer).filter(isTimeSlot);
    for (const slot of everySlot ? slots : slots.slice(0, 1)) {
      const match = TIME.exec(slot);
      if (match) {
        const ms = match[2].toLowerCase() === "s" ? Number(match[1]) * 1000 : Number(match[1]);
        found.push({ text: slot, ms });
      }
    }
  }
  return found;
}

function within(node, test) {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (parent.type === "atrule" && test(parent)) return true;
  }
  return false;
}
const inKeyframes = (node) => within(node, (rule) => /keyframes$/i.test(rule.name));
const inReducedMotionBlock = (node) => within(node, (rule) => /prefers-reduced-motion/.test(rule.params));

function hasReducedMotionRule(root, { global }) {
  let found = false;
  root.walkAtRules((rule) => {
    if (!/prefers-reduced-motion\s*:\s*reduce/.test(rule.params)) return;
    if (!global) {
      found = true;
      return;
    }
    rule.walkRules((inner) => {
      if (inner.selectors.some((s) => s.trim() === "*")) found = true;
    });
  });
  return found;
}

function moves(decl) {
  if (/^animation(?:-name)?$/.test(decl.prop)) return !isNone(decl.value);
  if (decl.prop === "transition" || decl.prop === "transition-property") {
    if (isNone(decl.value)) return false;
    return transitionedProperties(decl).some((p) => !STILL_PROPERTY.test(p));
  }
  return false;
}

/** The root stylesheet: index.css at the TypeScript root or its src/. */
function rootStylesheet(repoRoot) {
  const base = tsSourceRoot(repoRoot);
  for (const candidate of [path.join(base, "src", "index.css"), path.join(base, "index.css")]) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return null;
}

/** Whether the repo defines the --t-* tokens, so a literal has one to become. */
function definesMotionTokens(repoRoot, rootCss) {
  const sources = [];
  try {
    const config = JSON.parse(fs.readFileSync(path.join(repoRoot, CONFIG_FILE), "utf8"));
    if (config.css_token_source) sources.push(path.join(repoRoot, config.css_token_source));
  } catch (err) {
    // silent-ok: no config, or none naming a token source, means the root stylesheet is the only place to look
    if (err.code !== "ENOENT" && !(err instanceof SyntaxError)) throw err;
  }
  if (rootCss) sources.push(rootCss);
  return sources.some((file) => fs.existsSync(file) && /--t-fast\b/.test(fs.readFileSync(file, "utf8")));
}

function parseCss(filePath, content) {
  return postcss().parse(content, { from: filePath });
}

/** postcss nodes in the shape the waiver contract reads (ESTree-like `loc`). */
const loc = (node) => ({
  start: { line: node.source.start.line, column: node.source.start.column },
  end: {
    line: (node.source.end || node.source.start).line,
    column: (node.source.end || node.source.start).column,
  },
});

function gradeFile(filePath, content, added, facts) {
  const root = parseCss(filePath, content);
  const lines = content.split("\n");
  const policy = facts.globalPolicy || hasReducedMotionRule(root, { global: false });

  const comments = [];
  root.walkComments((c) => comments.push({ value: c.text, loc: loc(c) }));
  const constructs = new Map();
  root.walkDecls((d) => constructs.set(d, { loc: loc(d) }));
  const waivers = waiverContract(ALLOW_MARKER, lines, comments, [...constructs.values()]);

  const found = [];
  const flag = (decl, message) => {
    const { start, end } = constructs.get(decl).loc;
    let touched = false;
    for (let i = start.line; i <= end.line; i += 1) if (added.has(i)) touched = true;
    if (!touched || waivers.waived(constructs.get(decl))) return;
    found.push(`${filePath}:${start.line}: ${message}`);
  };

  root.walkDecls((decl) => {
    if (inReducedMotionBlock(decl)) return;

    if (inKeyframes(decl) && LAYOUT_PROPERTY.test(decl.prop)) {
      flag(decl, `a keyframe animates '${decl.prop}', which relayouts the page every frame; animate transform (translate/scale) or opacity instead`);
      return;
    }
    if ((decl.prop === "transition" || decl.prop === "transition-property") && !isNone(decl.value)) {
      for (const prop of transitionedProperties(decl)) {
        if (prop === "all") {
          flag(decl, "'transition: all' animates whatever changes, layout included; name the properties (transform, opacity, …)");
        } else if (LAYOUT_PROPERTY.test(prop)) {
          flag(decl, `transitions '${prop}', which relayouts the page every frame; animate transform (translate/scale) or opacity instead`);
        }
      }
    }

    if (facts.tokens && /^(?:transition|animation)(?:-duration)?$/.test(decl.prop)) {
      for (const { text, ms } of durationsIn(decl)) {
        if (ms > 0 && ms <= UI_DURATION_CEILING_MS) {
          flag(decl, `hardcodes '${text}' — use ${nearestToken(ms)} (fast: hover/press, med: toggle/dropdown/toast, slow: modal/panel; an exit runs one step faster)`);
        }
      }
    }

    if (!policy && moves(decl)) {
      flag(decl, `'${decl.prop}' moves, and neither the root stylesheet nor this file has a '@media (prefers-reduced-motion: reduce)' rule; add one so a reader who asked for less motion gets a fade or a held frame`);
    }
  });

  found.push(...waivers.shortWaiverFindings(added, filePath, "say why this motion is right"));
  return found;
}

runGate({
  title: "motion",
  suffixes: [".css"],
  footer: ["Motion shows a change of state: tokens for timing, transform and opacity for movement, and the reader's reduced-motion setting honoured."],
  grade: ({ repoRoot }, { files, touched }) => {
    const rootCss = rootStylesheet(repoRoot);
    let globalPolicy = false;
    if (rootCss) globalPolicy = hasReducedMotionRule(parseCss(rootCss, fs.readFileSync(rootCss, "utf8")), { global: true });
    const facts = { tokens: definesMotionTokens(repoRoot, rootCss), globalPolicy };

    const findings = [];
    for (const filePath of files) {
      if (isTestFile(filePath)) continue;
      const content = readSource(filePath, repoRoot);
      const { added } = touched(filePath, content.split("\n").length);
      try {
        findings.push(...gradeFile(filePath, content, added, facts));
      } catch (err) {
        if (err.name !== "CssSyntaxError") throw err;
        // Not a silent skip: a stylesheet this gate cannot read is one it did not check.
        findings.push(`${filePath}: could not parse this stylesheet, so its motion was not checked: ${err.reason}`);
      }
    }
    return findings;
  },
});
