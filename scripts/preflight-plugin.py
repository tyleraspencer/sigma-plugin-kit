#!/usr/bin/env python3
"""Static preflight for a Sigma plugin, before anything irreversible happens.

Every check here is a *silent* failure mode -- the plugin deploys fine,
registers fine, publishes fine, renders its own fallback data, and looks
completely correct in a screenshot. None of them raise an error anywhere in
Sigma, which is why each one has already cost a debugging session.

The rule this file exists to enforce: a fact that cost a session once becomes
a check, not a note in the docs.

    python3 scripts/preflight-plugin.py <plugin-name> [--data FILE]

Exit status is 1 if any check FAILs, so it can gate a pipeline. Warnings never
gate -- they're things that are usually wrong but legitimately intentional
sometimes.

The editor-panel checks deliberately import build-plugin-workbook.py's OWN
parser rather than re-implementing one. Authoring and building must agree
about the binding contract; two parsers is how they silently stop agreeing.
"""

import argparse
import csv
import importlib.util
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

# --- output -------------------------------------------------------------
# Matches doctor.sh so the two read as one tool.

class Report:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def ok(self, name, detail=""):
        print("  [ok]   %-24s %s" % (name, detail))

    def warn(self, name, detail=""):
        self.warned += 1
        print("  [warn] %-24s %s" % (name, detail))

    def fail(self, name, detail=""):
        self.failed += 1
        print("  [FAIL] %-24s %s" % (name, detail))

    def skip(self, name, detail=""):
        print("  [skip] %-24s %s" % (name, detail))


def load_builder():
    """Import build-plugin-workbook.py (hyphens, so no plain import)."""
    path = REPO / "scripts" / "build-plugin-workbook.py"
    spec = importlib.util.spec_from_file_location("_bpw", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def data_headers(path):
    """Column names available in a --data file, in order."""
    p = str(path)
    if p.lower().endswith((".csv", ".tsv")):
        delim = "\t" if p.lower().endswith(".tsv") else ","
        with open(p, newline="", encoding="utf-8-sig") as fh:
            return next(csv.reader(fh, delimiter=delim))
    with open(p, encoding="utf-8") as fh:
        rows = json.load(fh)
    heads = []
    for row in rows:
        for k in row:
            if k not in heads:
                heads.append(k)
    return heads


def strip_comments(src):
    """Drop comments so a check can't match prose *about* an antipattern.

    These files carry long comments explaining the very bugs being checked for
    ("window.sigmaComputing.plugin.client is defined by no published bundle"),
    so scanning raw source flags every correct plugin. The `//` rule skips a
    slash preceded by `:` to leave URLs alone.
    """
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)      # HTML comments
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)       # JS block comments
    src = re.sub(r"(?<!:)//[^\n]*", " ", src)              # JS line comments
    return src


# --- checks -------------------------------------------------------------

SDK_TAG = re.compile(r"""<script[^>]*\bsrc\s*=\s*["'][^"']*sigmacomputing-plugin[^"']*["']""",
                     re.I)
REACT_TAG = re.compile(r"""<script[^>]*\bsrc\s*=\s*["'][^"']*\breact(?:@[\w.\-]+)?/[^"']*["']""",
                       re.I)


def check_react_archetype(rep, plugin_dir, html):
    """Every plugin is a Vite + React project. Nothing else may be deployed.

    The hand-written single-file archetype -- one index.html pulling the SDK's
    UMD bundle off a CDN -- was removed because its worst failure is silent.
    React is an *external* of that bundle and the factory calls
    React.createContext at module top level, so a page that loads the SDK
    without loading React first throws there: window.SigmaPlugin is left a bare
    {} with zero keys, client is undefined, the plugin takes its no-client
    branch, and it renders fallback data forever. The only symptom anywhere is
    one uncaught `u.createContext is not a function` in the iframe console.

    Bundling the SDK as an npm dependency makes that unrepresentable, so the
    rule is enforced here rather than left to a reviewer's eye.
    """
    pkg_path = plugin_dir / "package.json"
    if not pkg_path.is_file():
        rep.fail("react-archetype",
                 "no package.json -- every plugin is a Vite + React project, and "
                 "the single-file archetype is no longer deployable")
        return
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        rep.fail("react-archetype", "package.json does not parse: %s" % exc)
        return
    deps = pkg.get("dependencies") or {}
    missing = [d for d in ("react", "@sigmacomputing/plugin") if d not in deps]
    if missing:
        rep.fail("react-archetype",
                 "package.json is missing dependenc%s: %s"
                 % ("y" if len(missing) == 1 else "ies", ", ".join(missing)))
        return
    # A CDN script tag in a bundled plugin means someone reintroduced the
    # archetype by hand, and reintroduced its failure mode with it.
    if SDK_TAG.search(html):
        rep.fail("react-archetype",
                 "index.html loads the SDK from a CDN as well -- a bundled plugin "
                 "must import the client from @sigmacomputing/plugin, not a script tag")
        return
    rep.ok("react-archetype", "Vite + React, SDK bundled from npm")


def check_sdk_global(rep, src):
    """A bundled plugin must import `client`, not reach for a window global.

    `window.sigmaComputing.plugin.client` is defined by no published bundle,
    and `window.SigmaPlugin` only exists when the UMD build is loaded from a
    script tag -- which a React plugin never does. Either one appearing in a
    bundled plugin means code was pasted from a single-file example and will
    read undefined at runtime.
    """
    if "sigmaComputing" in src:
        rep.fail("sdk-global",
                 "references window.sigmaComputing, which no published bundle "
                 "defines -- import { client } from '@sigmacomputing/plugin'")
        return
    if "SigmaPlugin" in src:
        rep.fail("sdk-global",
                 "references window.SigmaPlugin, which only exists when the UMD "
                 "build is loaded from a script tag -- a bundled plugin must "
                 "import { client } from '@sigmacomputing/plugin'")
        return
    if re.search(r"""from\s+['"]@sigmacomputing/plugin['"]""", src):
        rep.ok("sdk-global", "imports the client from @sigmacomputing/plugin")
    else:
        rep.fail("sdk-global",
                 "never imports from '@sigmacomputing/plugin' -- the plugin has "
                 "no way to talk to Sigma")


def check_resize_observer(rep, src):
    """An unguarded ResizeObserver that re-renders is an infinite loop.

    The callback rebuilds DOM *inside* the observed element, which fires the
    observer again. The page pins a core and stops responding to clicks, with
    nothing in the console -- it just looks like a hang.
    """
    if "ResizeObserver" not in src:
        rep.skip("resize-observer-guard", "no ResizeObserver")
        return
    guards = ("clientWidth", "clientHeight", "contentRect", "offsetWidth", "offsetHeight")
    if any(g in src for g in guards):
        rep.ok("resize-observer-guard", "compares a dimension before re-rendering")
    else:
        rep.fail("resize-observer-guard",
                 "ResizeObserver with no size comparison -- if its callback "
                 "re-renders into the observed element this is an infinite loop")


def check_editor_panel(rep, builder, src_file):
    """Parse the panel with the builder's parser; report what it will bind."""
    src = src_file.read_text(encoding="utf-8", errors="replace")
    entries = builder.parse_editor_panel(src)
    if entries is None:
        rep.fail("editor-panel-parses",
                 "build-plugin-workbook.py cannot parse configureEditorPanel in "
                 "%s -- it will refuse to generate a workbook" % src_file.name)
        return None
    primary, cols, extra = builder.bindings_from_panel(entries)
    if not primary:
        rep.fail("editor-panel-parses", "no `element` binding declared")
        return None
    if len(cols) < 2:
        rep.fail("editor-panel-parses",
                 "only %d column binding(s); the builder needs at least two "
                 "(it uses the first as the label and the second as the value)"
                 % len(cols))
        return None
    rep.ok("editor-panel-parses",
           "%d column binding(s) on element '%s'" % (len(cols), primary))
    rep.ok("label-value-keys",
           "label=%s  value=%s  (panel order decides this)" % (cols[0][0], cols[1][0]))
    if extra:
        rep.warn("extra-elements",
                 "declares element binding(s) %s that the generated workbook "
                 "will not populate -- bind by hand in Sigma" % ", ".join(extra))
    return cols


def check_action_trigger_wired(rep, builder, src_file, src):
    """An `action-trigger` and a `triggerAction()` call are two halves of one thing.

    Standing rule: when a plugin causes a workbook action, the action fires from
    the PLUGIN, not from a button or a control's on-change standing in for it
    (`validate-spec.py` -> `plugin-owns-its-actions` enforces the workbook half).
    That only works if the plugin declares the trigger and actually calls it,
    and each half is silent without the other:

    - `triggerAction()` with no `action-trigger` panel entry passes `undefined`,
      and the SDK's only validation is a console warning nobody reads -- the
      click does nothing.
    - an `action-trigger` entry that is never called is a trigger the workbook
      author can wire to an action that can never fire. Worse than useless: it
      looks wired from the workbook side.
    """
    entries = builder.parse_editor_panel(src_file.read_text(encoding="utf-8",
                                                            errors="replace"))
    declared = [e.get("name") for e in (entries or [])
                if e.get("type") == "action-trigger"]
    calls = re.search(r"\btriggerAction\s*\(", src)

    if not declared and not calls:
        rep.skip("action-trigger-wired", "plugin triggers no workbook actions")
        return
    if calls and not declared:
        rep.fail("action-trigger-wired",
                 "calls triggerAction() but declares no `action-trigger` panel "
                 "entry, so the argument is undefined -- the SDK only warns to "
                 "the console and the action never fires")
        return
    if declared and not calls:
        rep.fail("action-trigger-wired",
                 "declares action-trigger %s but never calls triggerAction() -- "
                 "a workbook can bind an action to it that nothing will ever fire"
                 % ", ".join(repr(d) for d in declared))
        return
    rep.ok("action-trigger-wired",
           "declares and fires action-trigger %s" % ", ".join(repr(d) for d in declared))


def check_bindings_resolve(rep, cols, data_path):
    """Every declared column binding must find a column in the data.

    build-plugin-workbook.py binds by matching the panel entry name against the raw
    data header. A typo doesn't error -- the key is simply absent from the
    plugin's config, and the plugin quietly renders without that field.
    """
    if cols is None:
        rep.skip("bindings-resolve", "editor panel did not parse")
        return
    if not data_path:
        rep.skip("bindings-resolve", "no --data (pass one to check binding names)")
        return
    heads = data_headers(data_path)
    names = [n for n, _ in cols]
    missing = [n for n in names if n not in heads]
    if missing:
        rep.fail("bindings-resolve",
                 "no column named %s in %s -- these bindings will be absent "
                 "from the plugin's config. Headers: %s"
                 % (", ".join(repr(m) for m in missing),
                    pathlib.Path(data_path).name, ", ".join(heads)))
        return
    unused = [h for h in heads if h not in names]
    detail = "all %d binding(s) matched" % len(names)
    if unused:
        detail += "; %d extra data column(s) carried but unbound" % len(unused)
    rep.ok("bindings-resolve", detail)


def check_vite_base(rep, plugin_dir):
    """Vite must set base: './' or built assets 404 under the Pages subpath.

    Symptom is a blank iframe with nothing in any log.
    """
    configs = list(plugin_dir.glob("vite.config.*"))
    if not configs:
        rep.skip("vite-base", "not a Vite plugin")
        return
    text = configs[0].read_text(encoding="utf-8", errors="replace")
    if re.search(r"""base\s*:\s*['"]\./['"]""", text):
        rep.ok("vite-base", "base: './'")
    else:
        rep.fail("vite-base",
                 "%s does not set base: './' -- built assets will 404 under the "
                 "GitHub Pages subpath and Sigma shows a blank iframe"
                 % configs[0].name)


def check_fallback_visible(rep, src):
    """A fallback render that isn't labelled is indistinguishable from success."""
    has_fallback = re.search(r"\bsynth\b|demoData|isDemo|fallback", src)
    if not has_fallback:
        rep.skip("fallback-labelled", "no obvious fallback path")
        return
    if re.search(r"badge|demo data", src, re.I):
        rep.ok("fallback-labelled", "fallback path has a visible badge")
    else:
        rep.warn("fallback-labelled",
                 "renders fallback data with no visible badge -- a broken "
                 "binding will look exactly like success")


# --- filling the frame --------------------------------------------------
# The workbook author sizes the element and resizes it freely -- dragging it,
# toggling the editor panel, switching to a phone layout, going full screen. A
# plugin that renders at a size it chose itself is correct at exactly one of
# those and wrong at every other, and NOTHING downstream notices: it publishes
# clean, and a screenshot taken at the size you happened to test looks perfect.
# Until these checks existed the only way to catch it was a human dragging the
# harness frame and remembering to.

# A CSS length in viewport units: a number followed by the unit. Matching the
# bare letters would flag any identifier containing "vh".
VIEWPORT_UNITS = re.compile(r"\b\d+(?:\.\d+)?(?:vh|vw|dvh|dvw|svh|svw|lvh|lvw)\b",
                            re.I)
FLEX_GROW = re.compile(r"flex\s*:\s*1\b|flexGrow\s*:\s*1\b|flex\s*:\s*'1\b")


def check_viewport_units(rep, src):
    """vh/vw size against the viewport, and the iframe is not the viewport.

    It only coincides with the element box when the plugin happens to be the
    whole screen. Everywhere else -- which is everywhere -- the plugin renders
    at a size unrelated to the box it was given, and overflows or letterboxes.
    """
    hits = sorted(set(m.group(0) for m in VIEWPORT_UNITS.finditer(src)))
    if not hits:
        rep.ok("viewport-units", "no vh/vw -- sized from its own box")
        return
    rep.fail("viewport-units",
             "uses viewport units (%s). The iframe is not the viewport, so "
             "these are right at one size and wrong at every other. Size from "
             "the parent box: 100%% down the chain, plus a ResizeObserver."
             % ", ".join(hits[:4]))


def check_root_height(rep, plugin_dir):
    """A percentage height resolves to `auto` unless every ancestor declares one.

    Miss any link in html -> body -> #root and the plugin collapses to its
    content height: a short strip of chart at the top of a tall empty element.
    """
    html_file = plugin_dir / "index.html"
    if not html_file.is_file():
        rep.skip("root-height", "no index.html")
        return
    html = html_file.read_text(encoding="utf-8", errors="replace")
    # One rule may cover all three selectors, or each may be styled separately.
    missing = []
    for sel in ("html", "body", "#root"):
        pat = re.compile(r"(^|[,{\s])%s\s*[,{][^}]*height\s*:\s*100%%" % re.escape(sel),
                         re.I | re.M | re.S)
        if not pat.search(html):
            missing.append(sel)
    if missing:
        rep.fail("root-height",
                 "index.html does not give %s a height -- a percentage height "
                 "below an auto-height ancestor resolves to auto, and the "
                 "plugin collapses to its content instead of filling the frame"
                 % ", ".join(missing))
    else:
        rep.ok("root-height", "html, body, #root all declare height")


def _enclosing_object(src, pos):
    """The `{...}` literal containing pos, for inspecting one style object."""
    depth = 0
    start = None
    for i in range(pos, -1, -1):
        if src[i] == "}":
            depth += 1
        elif src[i] == "{":
            if depth == 0:
                start = i
                break
            depth -= 1
    if start is None:
        return ""
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return src[start:]


def check_flex_min_height(rep, src):
    """A flex child will not shrink below its content without min-height: 0.

    The default `min-height: auto` means a growing child refuses to get
    smaller than what is inside it, so instead of the content scrolling the
    whole plugin pushes past the bottom of the iframe. Warn, not fail: the
    check reads inline style objects, and a plugin styled from a .css file or
    a CSS-in-JS library is invisible to it.
    """
    if not FLEX_GROW.search(src):
        rep.skip("flex-min-height", "no growing flex child")
        return
    bad = 0
    total = 0
    for m in FLEX_GROW.finditer(src):
        obj = _enclosing_object(src, m.start())
        if not obj:
            continue
        total += 1
        # Only matters for a child that has to contain something scrollable or
        # stack further -- otherwise there is nothing to overflow.
        risky = re.search(r"overflow|flexDirection|display\s*:\s*'flex'", obj)
        if risky and not re.search(r"minHeight|min-height", obj):
            bad += 1
    if bad:
        rep.warn("flex-min-height",
                 "%d of %d growing flex child(ren) set no minHeight -- without "
                 "it the child cannot shrink below its content and the plugin "
                 "overflows the iframe instead of scrolling inside it"
                 % (bad, total))
    else:
        rep.ok("flex-min-height", "%d growing flex child(ren), all guarded" % total)


def main():
    ap = argparse.ArgumentParser(
        description="Static preflight for a Sigma plugin. Catches the silent "
                    "failure modes before deploy/register, which are the "
                    "irreversible steps.")
    ap.add_argument("name", help="plugin directory name under plugins/")
    ap.add_argument("--data", help="the rows the workbook will be built from; "
                                   "enables the binding-name check")
    args = ap.parse_args()

    plugin_dir = REPO / "plugins" / args.name
    if not plugin_dir.is_dir():
        print("preflight: no such plugin directory: %s" % plugin_dir, file=sys.stderr)
        return 2

    builder = load_builder()
    src_file = builder.find_plugin_source(str(plugin_dir))
    if not src_file:
        print("preflight: no file declaring configureEditorPanel under %s"
              % plugin_dir, file=sys.stderr)
        return 2

    print("== preflight: %s ==" % args.name)
    print("  source: %s" % src_file.relative_to(REPO))
    print()

    raw = src_file.read_text(encoding="utf-8", errors="replace")
    # The script-tag checks only make sense against the served HTML, which for
    # the React archetype is index.html, not the component the panel lives in.
    html_file = plugin_dir / "index.html"
    html = html_file.read_text(encoding="utf-8", errors="replace") \
        if html_file.is_file() else raw
    # Code checks run on comment-stripped source; the tag checks need the raw
    # markup, and a <script src> can't hide in a comment that matters.
    src = strip_comments(raw)

    rep = Report()
    check_react_archetype(rep, plugin_dir, strip_comments(html))
    check_sdk_global(rep, src)
    check_resize_observer(rep, src)
    cols = check_editor_panel(rep, builder, src_file)
    check_action_trigger_wired(rep, builder, src_file, src)
    check_bindings_resolve(rep, cols, args.data)
    check_vite_base(rep, plugin_dir)
    check_fallback_visible(rep, src)
    check_root_height(rep, plugin_dir)
    check_viewport_units(rep, src)
    check_flex_min_height(rep, src)

    print()
    if rep.failed:
        print("  %d check(s) FAILED -- fix before deploying. Deploy and register "
              "are the\n  irreversible steps: PATCH /v2/plugins/{id} cannot change "
              "a plugin's url." % rep.failed)
        return 1
    print("  all checks passed%s" % (" (%d warning(s))" % rep.warned if rep.warned else ""))
    print("  Static checks cannot prove the plugin renders bound data. For that:")
    print("    python3 scripts/verify-plugin-binding.py %s%s"
          % (args.name, " --data %s" % args.data if args.data else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
