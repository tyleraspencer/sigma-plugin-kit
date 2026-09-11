#!/usr/bin/env python3
"""Prove a plugin renders BOUND data, not its own fallback -- with no Sigma.

    python3 scripts/verify-plugin-binding.py <plugin-name> [--data FILE]

Writes a self-contained harness page to plugins/_harness/<name>.html and
prints the URL to open. Needs no Sigma login, no deploy, no registration and
no network: it runs entirely against the plugin's own built bundle.

Build the plugin first -- the harness drives plugins/<name>/dist/, because the
bundle is what Sigma actually loads and the only thing worth proving.

WHY THIS EXISTS
    The kit's own rule is "if the plugin shows its demo data in Sigma, the
    binding is wrong" -- but checking that needs eyes on the rendered iframe,
    and the in-app browser is not signed in to Sigma. So the usual proof was
    an HTTP 200 from publish, which is exactly the thing the kit warns is
    meaningless. A plugin whose client never initialises publishes cleanly,
    renders its fallback, and screenshots perfectly.

    This runs the plugin's real code against the real data shape Sigma sends
    (column-keyed parallel arrays, NOT row objects) and compares the bound
    render against the fallback render. Run it BEFORE deploy: deploy and
    register are the irreversible steps, since PATCH /v2/plugins/{id} cannot
    change a plugin's url.

HOW IT DECIDES
    It renders the plugin twice, in two real iframes:
      A. with no element bound   -> whatever the plugin falls back to
      B. with the real rows      -> what a workbook would show
    If A and B are identical, or B never shows the bound values, the plugin is
    ignoring its bindings. That test needs no knowledge of the plugin's markup.

    A built plugin imports `client` from @sigmacomputing/plugin, so there is no
    window global to stub -- the SDK talks to its host over postMessage and
    nothing else. So the harness page *is* the host. Protocol, read off the
    1.3.2 bundle:

        plugin -> host   {type, args, elementId}  via window.parent.postMessage
        host   -> plugin {type, result, error}    via frame.contentWindow

    Replying with the request's own `type` resolves the plugin's pending
    promise; element data arrives on its own channel,
    "wb:plugin:element:<id>:data". Driving the real SDK this way means the
    handshake itself is evidence: a plugin whose client never initialises never
    sends wb:plugin:init, and the harness says so.

READING THE RESULT
    The page shows a report, and also sets:
      document.title      "HARNESS PASS" / "HARNESS FAIL"
      window.__HARNESS__  { pass, checks: [{name, ok, detail}], ... }
    so one javascript_tool call or a glance at the tab title is enough.
"""

import argparse
import importlib.util
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
HARNESS_DIR = REPO / "plugins" / "_harness"


def load_builder():
    path = REPO / "scripts" / "build-plugin-workbook.py"
    spec = importlib.util.spec_from_file_location("_bpw", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>HARNESS RUNNING</title>
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,Inter,"Segoe UI",Roboto,Arial,sans-serif;
       margin:0;padding:20px;background:#0b1220;color:#e2e8f0}
  h1{font-size:15px;letter-spacing:.04em;text-transform:uppercase;margin:0 0 4px}
  .sub{font-size:11px;color:#94a3b8;margin-bottom:14px}
  .verdict{display:inline-block;font-weight:800;font-size:12px;letter-spacing:.06em;
           padding:4px 10px;border-radius:4px;margin-bottom:14px}
  .pass{background:#064e3b;color:#6ee7b7}
  .fail{background:#7f1d1d;color:#fca5a5}
  table{border-collapse:collapse;width:100%;font-size:12px;margin-bottom:18px}
  td{padding:5px 8px;border-bottom:1px solid #1e293b;vertical-align:top}
  td.s{width:26px}
  td.n{width:190px;color:#cbd5e1;font-weight:600}
  td.d{color:#94a3b8}
  .frames{display:flex;gap:14px}
  .frame{flex:1}
  .frame h2{font-size:10px;letter-spacing:.08em;text-transform:uppercase;
            color:#64748b;margin:0 0 6px}
  iframe{width:100%;height:300px;border:1px solid #1e293b;border-radius:6px;background:#fff}
</style>
</head>
<body>
<h1>__NAME__</h1>
<div class="sub">A = nothing bound (fallback) &nbsp;·&nbsp; B = rows bound through the real SDK</div>
<div id="verdict" class="verdict">running…</div>
<table id="checks"></table>

<div class="frames">
  <div class="frame"><h2>A — unbound</h2><iframe id="fa"></iframe></div>
  <div class="frame"><h2>B — bound</h2><iframe id="fb"></iframe></div>
</div>

<script>
(function(){
  var P = __PAYLOAD__;
  var checks = [];
  function add(name, ok, detail){ checks.push({name:name, ok:!!ok, detail:detail||''}); }

  // --- Sigma host emulation -------------------------------------------------
  // A built plugin imports `client` from @sigmacomputing/plugin, so there is no
  // window global to stub -- the SDK talks to its host over postMessage and
  // nothing else. So this page *is* the host.
  //
  // Protocol, read off the 1.3.2 bundle:
  //   plugin -> host   {type, args, elementId}   via window.parent.postMessage
  //   host   -> plugin {type, result, error}     via frame.contentWindow
  // A reply reusing the request's `type` resolves the plugin's pending promise;
  // element data arrives on its own channel, "wb:plugin:element:<id>:data".
  var ELEMENT_ID = 'tbl-data';

  var frames = [
    { key:'A', el:document.getElementById('fa'), cfg:P.cfgUnbound, sawInit:false, sawSub:false },
    { key:'B', el:document.getElementById('fb'), cfg:P.cfg,        sawInit:false, sawSub:false }
  ];

  function send(f, type, result){
    try { f.el.contentWindow.postMessage({ type:type, result:result }, '*'); } catch(e){}
  }

  window.addEventListener('message', function(ev){
    var f = null;
    for (var i=0;i<frames.length;i++){
      if (frames[i].el.contentWindow === ev.source){ f = frames[i]; break; }
    }
    if (!f || !ev.data || !ev.data.type) return;
    var msg = ev.data, t = msg.type;

    if (t === 'wb:plugin:init'){
      f.sawInit = true;
      // Merged into the plugin's state, then re-broadcast as `config`.
      send(f, t, { id:'plugin-1', sigmaEnv:'author', screenshot:false, config:f.cfg });
      return;
    }
    if (t === 'wb:plugin:element:subscribe:data'){
      f.sawSub = true;
      var id = (msg.args && msg.args[0]) || ELEMENT_ID;
      send(f, 'wb:plugin:element:' + id + ':data', P.data);
      return;
    }
    if (t === 'wb:plugin:element:subscribe:columns'){
      var cid = (msg.args && msg.args[0]) || ELEMENT_ID;
      send(f, 'wb:plugin:element:' + cid + ':columns', P.columns);
      return;
    }
    if (t === 'wb:plugin:variable:set'){ f.lastVariable = msg.args; return; }
    // Everything else (config:inspector, loading-state, focus, style:get) just
    // needs its promise settled so the plugin doesn't stall waiting.
    send(f, t, null);
  }, false);

  function textOf(f){
    try { return (f.el.contentDocument.body.innerText || '').trim(); }
    catch(e){ return ''; }
  }

  // Settle on a stable render rather than a fixed sleep: a bundle parses,
  // mounts, subscribes and re-renders, and the last step is the one we need.
  var lastA = null, lastB = null, stable = 0;
  var ticks = 0;
  var timer = setInterval(function(){
    ticks++;
    var a = textOf(frames[0]), b = textOf(frames[1]);
    if (a === lastA && b === lastB && (a || b)) stable++; else stable = 0;
    lastA = a; lastB = b;
    if (stable >= 4 || ticks > 60) { clearInterval(timer); finish(a, b); }
  }, 120);

  function finish(a, b){
    add('frames-loaded', !!(a || b),
        a || b ? 'A ' + a.length + ' chars, B ' + b.length + ' chars'
               : 'neither frame rendered any text -- is the plugin built?');

    // The handshake is the single best signal that the SDK actually came up.
    add('sdk-handshake', frames[0].sawInit && frames[1].sawInit,
        frames[0].sawInit && frames[1].sawInit
          ? 'both frames sent wb:plugin:init'
          : 'no wb:plugin:init from ' +
            (!frames[0].sawInit && !frames[1].sawInit ? 'either frame'
              : (!frames[0].sawInit ? 'frame A' : 'frame B')) +
            ' -- the SDK never initialized');

    add('data-subscribed', frames[1].sawSub,
        frames[1].sawSub ? 'bound frame subscribed to element data'
                         : 'bound frame never called subscribeToElementData, so its '
                           + 'config.source binding is not reaching the SDK');

    var differs = !!a && !!b && a !== b;
    add('binds-vs-fallback', differs,
        differs ? 'bound render differs from the fallback render'
                : 'bound and unbound renders are IDENTICAL -- the plugin is '
                  + 'ignoring its bindings and showing fallback data');

    var hits = P.labels.filter(function(l){ return l && b.indexOf(l) !== -1; });
    var enough = hits.length >= Math.min(2, P.labels.length);
    add('bound-values-visible', enough,
        enough ? hits.length + '/' + P.labels.length + ' bound label(s) appear in B'
               : 'none of the bound labels appear in B (' +
                 P.labels.slice(0,3).join(', ') + '…)');

    var pass = checks.every(function(c){ return c.ok; });
    document.title = pass ? 'HARNESS PASS' : 'HARNESS FAIL';
    var v = document.getElementById('verdict');
    v.className = 'verdict ' + (pass ? 'pass' : 'fail');
    v.textContent = pass ? 'PASS — renders bound data' : 'FAIL — see below';
    document.getElementById('checks').innerHTML = checks.map(function(c){
      return '<tr><td class="s">' + (c.ok ? '✅' : '❌') + '</td>' +
             '<td class="n">' + c.name + '</td>' +
             '<td class="d">' + c.detail + '</td></tr>';
    }).join('');
    window.__HARNESS__ = { pass:pass, checks:checks, rowCount:P.rowCount, name:P.name };
  }

  frames.forEach(function(f){ f.el.src = P.pluginUrl; });
})();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser(
        description="Generate a local harness proving a plugin renders bound "
                    "data rather than its own fallback. No Sigma needed.")
    ap.add_argument("name", help="plugin directory name under plugins/")
    ap.add_argument("--data", help="rows to bind (.csv/.tsv or JSON array). "
                                   "Defaults to rows synthesized from the panel.")
    ap.add_argument("--rows", type=int, default=8,
                    help="how many rows to synthesize when --data is absent")
    ap.add_argument("--plugin-url", default=None,
                    help="override the served path to the plugin HTML")
    ap.add_argument("--port", type=int, default=7824, help="port you will serve on")
    args = ap.parse_args()

    plugin_dir = REPO / "plugins" / args.name
    if not plugin_dir.is_dir():
        print("verify-plugin-binding: no such plugin: %s" % plugin_dir, file=sys.stderr)
        return 2

    # Every plugin is a Vite + React project, so what Sigma loads is the built
    # bundle -- and the bundle is what has to be exercised. Verifying src/ would
    # prove nothing about what gets deployed.
    if not args.plugin_url:
        dist_index = plugin_dir / "dist" / "index.html"
        if not dist_index.is_file():
            print("verify-plugin-binding: plugins/%s/dist/index.html not found.\n"
                  "  Build it first:  (cd plugins/%s && npm install && npm run build)\n"
                  "  The harness drives the built bundle, which is what Sigma loads."
                  % (args.name, args.name), file=sys.stderr)
            return 2

    builder = load_builder()
    src_file = builder.find_plugin_source(str(plugin_dir))
    if not src_file:
        print("verify-plugin-binding: no configureEditorPanel under %s" % plugin_dir,
              file=sys.stderr)
        return 2

    entries = builder.parse_editor_panel(
        src_file.read_text(encoding="utf-8", errors="replace"))
    if entries is None:
        print("verify-plugin-binding: could not parse configureEditorPanel; run "
              "preflight-plugin.py first", file=sys.stderr)
        return 2
    primary, cols, _extra = builder.bindings_from_panel(entries)
    if not primary or len(cols) < 2:
        print("verify-plugin-binding: need an element binding and >=2 column "
              "bindings; run preflight-plugin.py first", file=sys.stderr)
        return 2

    # Rows, then the exact reshaping the real workbook produces: column ids
    # come from the builder's own column_id(), so the harness and production
    # agree on the keys.
    if args.data:
        rows = builder.load_rows(args.data)
        described = "%d row(s) from %s" % (len(rows), args.data)
    else:
        rows = builder.synthesize_rows(cols, max(1, args.rows))
        described = "%d synthesized row(s) (pass --data for real ones)" % len(rows)

    headers = []
    for row in rows:
        for k in row:
            if k not in headers:
                headers.append(k)

    binding_names = [n for n, _ in cols]
    missing = [n for n in binding_names if n not in headers]
    if missing:
        print("verify-plugin-binding: the data has no column named %s.\n"
              "  Bindings must match data headers exactly. Headers: %s"
              % (", ".join(repr(m) for m in missing), ", ".join(headers)),
              file=sys.stderr)
        return 2

    data = {}
    for h in headers:
        cid = builder.column_id(h)
        kind = builder.infer_type([r.get(h) for r in rows])
        vals = []
        for r in rows:
            v = r.get(h)
            if kind in ("int", "float"):
                try:
                    v = float(v) if kind == "float" else int(float(v))
                except (TypeError, ValueError):
                    v = None
            vals.append(v)
        data[cid] = vals

    cfg = {"source": "tbl-data"}
    for n in binding_names:
        cfg[n] = builder.column_id(n)

    label_col = binding_names[0]
    labels = [str(r.get(label_col)) for r in rows if r.get(label_col) not in (None, "")]

    # Frame A gets every style option but no element/column bindings, so any
    # difference between the two renders is attributable to the bindings alone.
    cfg_unbound = {k: v for k, v in cfg.items()
                   if k != "source" and k not in binding_names}

    payload = {
        "name": args.name,
        # Trailing slash matters: a static server that rewrites
        # ".../dist/index.html" to ".../dist" makes the bundle's relative
        # "./assets/..." resolve one directory too high and 404. Deployed, the
        # dist *contents* sit directly in plugins/<name>/, so this mismatch is
        # the harness's alone -- but it looks exactly like a broken build.
        "pluginUrl": args.plugin_url or ("../%s/dist/" % args.name),
        "cfg": cfg,
        "cfgUnbound": cfg_unbound,
        "bindings": binding_names,
        "data": data,
        "columns": {builder.column_id(h): {"name": h} for h in headers},
        "labels": labels,
        "rowCount": len(rows),
    }

    HARNESS_DIR.mkdir(parents=True, exist_ok=True)
    out = HARNESS_DIR / ("%s.html" % args.name)
    blob = json.dumps(payload).replace("</", "<\\/")
    out.write_text(PAGE.replace("__NAME__", args.name).replace("__PAYLOAD__", blob),
                   encoding="utf-8")

    url = "http://localhost:%d/_harness/%s.html" % (args.port, args.name)
    print("harness: %s" % out.relative_to(REPO))
    print("  data:  %s" % described)
    print("  binds: %s" % ", ".join("%s=%s" % (n, cfg[n]) for n in binding_names))
    print()
    print("Serve the plugins directory, then open the harness:")
    print("  npx serve -l %d %s" % (args.port, (REPO / "plugins")))
    print("  %s" % url)
    print()
    print("PASS/FAIL lands in the page, in document.title, and in window.__HARNESS__.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
