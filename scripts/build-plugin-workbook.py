#!/usr/bin/env python3
"""Generate a workbook spec that hosts a registered plugin, bound to data.

Data comes from one of three places, in priority order:

  --data FILE    Rows you supply: a .csv/.tsv, or a JSON array of objects.
                 **Prefer this.** Invent rows that mean something for the
                 plugin at hand -- team names for a standings chart, funnel
                 stages for a funnel. Types are inferred per column.

  --plugin-src   Rows synthesized from the plugin's own editor panel. The
                 script reads its `configureEditorPanel` declaration, takes the
                 column bindings and their `allowedTypes`, and generates
                 correctly-typed columns named to match -- so the plugin binds
                 with no guesswork. Values are obvious placeholders ("Team A",
                 "Team B"): the *shape* is right, the meaning is not.
                 pipeline.sh passes this automatically.

  --path         A real warehouse table instead, grouped by --dimension with
                 --measure aggregated over it.

There is no built-in row set on purpose. A generic default ("Alice Johnson",
"SCORE") is the thing everyone falls into and nobody notices is meaningless.

Generated rows are compiled into a `SELECT ... FROM (VALUES ...)` literal and
published as a `kind: "sql"` table element, so the data lives in the workbook
spec. That is the only API route for fabricated rows: input tables cannot be
written from code, and /v2/files has no CSV upload. See docs/plugins.md.

Generated mode needs no GROUP BY -- you control the rows, so emit exactly the
rows the plugin should draw, one per category.

--variable-control additionally emits a list control and binds it to one of the
plugin's `variable` editor-panel entries, which is the only channel a plugin
has for writing a selection back into a workbook. The control's choices come
from a data column, so the values the plugin writes are always values the
control accepts.

The SQL is Snowflake-flavoured (`::varchar`, `::number`, `::timestamp_ntz`).
Another connection type needs the casts adjusted.
"""
import argparse
import csv
import json
import pathlib
import re
import sys

# Verified live 2026-09-11. Any connection works for generated mode -- the
# VALUES literal never touches a real table -- but this one is always present.
DEFAULT_CONNECTION = "bee6615c-7d11-435c-8819-e32207b27fe4"   # Sigma Sample Database

# Warehouse mode's known-good target.
DEFAULT_PATH = ["RETAIL", "PLUGS_ELECTRONICS", "PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA"]
DEFAULT_DIMENSION = "STORE_REGION"
DEFAULT_MEASURE = "Sum(PRICE * QUANTITY)"
DEFAULT_MEASURE_NAME = "Revenue"

CAST = {"text": "::varchar", "int": "::number", "float": "::float",
        "boolean": "::boolean", "date": "::date", "datetime": "::timestamp_ntz"}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_BARE_COLUMN = re.compile(r"(?<!\[)\b([A-Z][A-Z0-9_]{1,})\b(?!\])")

# Sigma ValueType -> our internal kind, for synthesizing from allowedTypes.
_VALUE_TYPE = {"text": "text", "link": "text", "variant": "text",
               "number": "float", "integer": "int",
               "boolean": "boolean", "datetime": "datetime"}


def column_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return "col-" + (slug or "unnamed")


def qualify(expr, table):
    return _BARE_COLUMN.sub(lambda m: "[%s/%s]" % (table, m.group(1)), expr)


# --- reading a plugin's editor panel --------------------------------------

def _balanced_array(src, start):
    depth = 0
    for j in range(start, len(src)):
        if src[j] == "[":
            depth += 1
        elif src[j] == "]":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return None


def parse_editor_panel(src):
    """Pull the configureEditorPanel entries out of plugin source.

    The panel must be declared inline -- `configureEditorPanel([...])`. The
    indirect `var DEFS = [...]` form the old single-file template used is no
    longer accepted: every plugin is React and writes the array at the call
    site. Regex rather than a JS parser: these are flat object literals, and
    the alternative is shipping a JS runtime.
    """
    m = re.search(r"configureEditorPanel\s*\(\s*\[", src)
    if not m:
        return None
    block = _balanced_array(src, m.end() - 1)
    if not block:
        return None

    entries = []
    for obj in re.findall(r"\{[^{}]*\}", block):
        def field(key):
            f = re.search(key + r"\s*:\s*['\"]([^'\"]+)['\"]", obj)
            return f.group(1) if f else None
        types = re.search(r"allowedTypes\s*:\s*\[([^\]]*)\]", obj)
        entries.append({
            "type": field("type"),
            "name": field("name"),
            "source": field("source"),
            "allowedTypes": [t.strip().strip("'\"") for t in types.group(1).split(",")
                             if t.strip()] if types else None,
        })
    return entries


def find_plugin_source(path):
    """Locate the file declaring the editor panel, given a file or plugin dir."""
    p = pathlib.Path(path)
    if p.is_file():
        return p
    for candidate in ("index.html", "src/App.jsx", "src/App.js",
                      "src/App.tsx", "src/main.jsx"):
        f = p / candidate
        if f.is_file() and "configureEditorPanel" in f.read_text(encoding="utf-8",
                                                                 errors="replace"):
            return f
    for f in sorted(p.rglob("*")):
        if not f.is_file() or f.suffix not in (".html", ".js", ".jsx", ".ts", ".tsx"):
            continue
        if "node_modules" in f.parts or "dist" in f.parts:
            continue
        if "configureEditorPanel" in f.read_text(encoding="utf-8", errors="replace"):
            return f
    return None


def bindings_from_panel(entries):
    """-> (primary element name, [(column binding, kind)], extra element names)."""
    elements = [e["name"] for e in entries if e["type"] == "element" and e["name"]]
    if not elements:
        return None, [], []
    primary = elements[0]
    cols = []
    for e in entries:
        if e["type"] != "column" or not e["name"]:
            continue
        if e.get("source") and e["source"] != primary:
            continue
        allowed = e.get("allowedTypes") or []
        kind = next((_VALUE_TYPE[t] for t in allowed if t in _VALUE_TYPE), "text")
        cols.append((e["name"], kind))
    return primary, cols, elements[1:]


def synthesize_rows(cols, n):
    """Placeholder rows whose columns match the plugin's bindings.

    Named after the bindings so the plugin binds cleanly, typed to satisfy its
    `allowedTypes`, and deterministic so a screenshot is reproducible. The
    values are visibly placeholders -- use --data for rows that mean something.
    """
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rows = []
    for i in range(n):
        row = {}
        for name, kind in cols:
            title = re.sub(r"[_-]+", " ", str(name)).strip().title() or "Item"
            if kind == "int":
                row[name] = int(round(4200 * (0.78 ** i))) + (i * 7) % 40
            elif kind == "float":
                row[name] = round(4200 * (0.78 ** i) + ((i * 7) % 40), 1)
            elif kind == "boolean":
                row[name] = (i % 2 == 0)
            elif kind == "datetime":
                row[name] = "2024-%02d-15" % ((i % 12) + 1)
            else:
                row[name] = "%s %s" % (title, letters[i % len(letters)])
        rows.append(row)
    return rows


# --- generated mode -------------------------------------------------------

def infer_type(values):
    seen = [v for v in values if v is not None and str(v).strip() != ""]
    if not seen:
        return "text"
    if all(isinstance(v, bool) for v in seen):
        return "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in seen):
        return "int"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return "float"
    strs = [str(v).strip() for v in seen]
    if all(s.lower() in ("true", "false") for s in strs):
        return "boolean"
    if all(_DATE.match(s) for s in strs):
        return "date"
    if all(_DATETIME.match(s) for s in strs):
        return "datetime"
    try:
        nums = [float(s) for s in strs]
    except ValueError:
        return "text"
    return "int" if all(n.is_integer() for n in nums) else "float"


def literal(value, kind):
    """One cell as a SQL literal. Strings are single-quote escaped."""
    if value is None or str(value).strip() == "":
        return "NULL"
    if kind == "boolean":
        truthy = value is True or str(value).strip().lower() in ("true", "yes", "1")
        return "TRUE" if truthy else "FALSE"
    if kind in ("int", "float"):
        return str(value)
    escaped = "'" + str(value).replace("'", "''") + "'"
    if kind == "date":
        return escaped + "::date"
    if kind == "datetime":
        return escaped + "::timestamp_ntz"
    return escaped


def sql_name(header, taken):
    name = re.sub(r"[^A-Za-z0-9_]", "_", str(header).strip()).upper().strip("_") or "COL"
    if name[0].isdigit():
        name = "C_" + name
    base, n = name, 2
    while name in taken:
        name, n = "%s_%d" % (base, n), n + 1
    taken.add(name)
    return name


def build_generated(rows, connection_id):
    """Compile rows into a kind:"sql" table element. -> (element, colmap)."""
    headers = []
    for row in rows:
        for k in row:
            if k not in headers:
                headers.append(k)

    kinds = [infer_type([r.get(h) for r in rows]) for h in headers]
    taken = set()
    names = [sql_name(h, taken) for h in headers]

    select = ",\n  ".join("v.c%d%s AS %s" % (i + 1, CAST[k], n)
                          for i, (n, k) in enumerate(zip(names, kinds)))
    values = ",\n  ".join(
        "(" + ", ".join(literal(r.get(h), k) for h, k in zip(headers, kinds)) + ")"
        for r in rows)
    cols = ", ".join("c%d" % (i + 1) for i in range(len(headers)))
    statement = "SELECT\n  %s\nFROM (VALUES\n  %s\n) AS v(%s)" % (select, values, cols)

    # Formulas reference the implicit source element name "Custom SQL". An
    # explicit `name` keeps the header as written -- without it Sigma
    # prettifies UNITS_SOLD into "Units Sold".
    columns = [{"id": column_id(h), "name": str(h), "formula": "[Custom SQL/%s]" % n}
               for h, n in zip(headers, names)]

    element = {
        "id": "tbl-data", "kind": "table", "name": "Generated data",
        "source": {"kind": "sql", "connectionId": connection_id, "statement": statement},
        "columns": columns,
        "order": [c["id"] for c in columns],
    }
    return element, {h: (column_id(h), k) for h, k in zip(headers, kinds)}


def load_rows(path):
    if path.lower().endswith((".csv", ".tsv")):
        delim = "\t" if path.lower().endswith(".tsv") else ","
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = [dict(r) for r in csv.DictReader(fh, delimiter=delim)]
    else:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh)
    if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
        raise SystemExit("build-plugin-workbook: --data must be a non-empty "
                         "CSV/TSV, or a JSON array of objects.")
    return rows


# --- warehouse mode -------------------------------------------------------

def build_warehouse(args):
    table_name = args.path[-1]
    dim_name = args.dimension.replace("_", " ").title()
    dim_id, mea_id = column_id(args.dimension), column_id(args.measure_name)
    element = {
        "id": "tbl-data", "kind": "table",
        "name": "%s by %s" % (args.measure_name, dim_name),
        "source": {"kind": "warehouse-table", "connectionId": args.connection_id,
                   "path": list(args.path)},
        "columns": [
            {"id": dim_id, "name": dim_name,
             "formula": "[%s/%s]" % (table_name, args.dimension)},
            {"id": mea_id, "name": args.measure_name,
             "formula": qualify(args.measure, table_name)},
        ],
        # Once a table has groupings, every column must be a groupBy dimension
        # or a calculations entry -- an orphan renders a nonsensical summary.
        "groupings": [{"id": "by-dim", "groupBy": [dim_id], "calculations": [mea_id]}],
    }
    return element, dim_id, mea_id


def build_variable_control(binding, rows, header, name=None, multiple=True):
    """A list control the plugin writes into, plus the config key that binds it.

    The plugin side of this is a `variable` editor-panel entry:
    setVariable(<binding>, ...values) pushes a selection into the control, and
    subscribeToWorkbookVariable reads changes back -- so binding it here is
    what turns a plugin click into something the rest of the workbook can
    filter on.

    Choices are the distinct values of `header`, so anything the plugin can
    write is a value the control already accepts. A control element's `id` and
    its `controlId` must DIFFER, or the publish fails with
    `elements[N].controlId: Duplicate id`.
    """
    seen, values = set(), []
    for r in rows:
        v = r.get(header)
        if v is None or str(v).strip() == "" or str(v) in seen:
            continue
        seen.add(str(v))
        values.append(str(v))

    slug = re.sub(r"[^a-z0-9]+", "-", str(binding).strip().lower()).strip("-") or "var"
    # Keep the binding's own casing -- .title() would turn selectedSeats into
    # "Selectedseats", and a controlId is referenced verbatim elsewhere.
    stem = re.sub(r"[^A-Za-z0-9]", "", str(binding)) or "var"
    control_id = "c" + stem[0].upper() + stem[1:]
    element = {
        "id": "ctl-" + slug,
        "kind": "control",
        "controlId": control_id,
        "name": name or re.sub(r"(?<!^)(?=[A-Z])", " ", str(binding)).title(),
        "controlType": "list",
        "selectionMode": "multiple" if multiple else "single",
        "source": {"kind": "manual", "valueType": "text", "values": values},
    }
    return element, control_id, len(values)


def layout_xml(page_id, ordered_ids, spans):
    rows, cursor = [], 1
    for eid in ordered_ids:
        rows.append('<Element elementId="%s" gridColumn="1 / 25" gridRow="%d / %d"/>'
                    % (eid, cursor, cursor + spans[eid]))
        cursor += spans[eid]
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" '
            'gridTemplateRows="auto" id="%s">%s</Page>' % (page_id, "".join(rows)))


def main():
    ap = argparse.ArgumentParser(
        description="Generate a workbook spec hosting a registered Sigma plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="workbook name")
    ap.add_argument("--plugin-id", required=True, help="pluginId from register-plugin.sh")
    ap.add_argument("--folder-id", help="destination folder id")
    ap.add_argument("--connection-id", default=DEFAULT_CONNECTION)

    ap.add_argument("--data", metavar="FILE",
                    help="rows to compile into the SQL VALUES literal: a .csv/.tsv, or "
                         "a JSON array of objects. Prefer this -- make the rows mean "
                         "something for this plugin.")
    ap.add_argument("--plugin-src", metavar="PATH",
                    help="plugin file or directory. Its configureEditorPanel column "
                         "bindings decide the generated columns and the plugin config, "
                         "so the data matches the plugin with no guesswork.")
    ap.add_argument("--rows", type=int, default=8,
                    help="how many rows to synthesize from --plugin-src. Default 8.")
    ap.add_argument("--label-column", help="column the plugin labels by "
                                           "(default: first text column)")
    ap.add_argument("--value-column", help="column the plugin measures "
                                           "(default: first numeric column)")

    ap.add_argument("--path", nargs=3, metavar=("DB", "SCHEMA", "TABLE"),
                    help="bind a real warehouse table instead of generating rows. "
                         "Known-good: %s" % " ".join(DEFAULT_PATH))
    ap.add_argument("--dimension", default=DEFAULT_DIMENSION)
    ap.add_argument("--measure", default=DEFAULT_MEASURE)
    ap.add_argument("--measure-name", default=DEFAULT_MEASURE_NAME)

    # Only needed when the plugin's panel can't be read. With --plugin-src the
    # binding keys come straight from the plugin's own editor panel.
    ap.add_argument("--label-key", help="plugin config key for the label, matching its "
                                        "editor panel. Inferred from --plugin-src, else 'label'")
    ap.add_argument("--value-key", help="plugin config key for the value, matching its "
                                        "editor panel. Inferred from --plugin-src, else 'value'")
    ap.add_argument("--variable-control", metavar="BINDING[:COLUMN]",
                    help="emit a list control and bind it to the plugin's `variable` "
                         "editor-panel entry BINDING, so the plugin can write a "
                         "selection back into the workbook. Its choices are the "
                         "distinct values of data column COLUMN (default: the "
                         "plugin's first column binding).")
    ap.add_argument("--control-name", help="display name for --variable-control's "
                                           "control (it always renders its own label)")
    ap.add_argument("--control-single", action="store_true",
                    help="make --variable-control single-select instead of multi")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args()

    notes = []
    panel_cols = []          # [(binding name, kind)] read off the plugin's panel
    panel_entries = []       # every panel entry, for checking a --variable-control

    if args.plugin_src:
        src_file = find_plugin_source(args.plugin_src)
        if not src_file:
            raise SystemExit("build-plugin-workbook: no file declaring "
                             "configureEditorPanel under %s" % args.plugin_src)
        entries = parse_editor_panel(src_file.read_text(encoding="utf-8", errors="replace"))
        if entries is None:
            raise SystemExit("build-plugin-workbook: could not parse "
                             "configureEditorPanel in %s" % src_file)
        panel_entries = entries
        _, panel_cols, extra_elements = bindings_from_panel(entries)
        if extra_elements:
            notes.append("plugin declares extra element bindings (%s) that this workbook "
                         "does not populate -- bind them by hand in Sigma"
                         % ", ".join(extra_elements))

    rows = None
    if args.path is not None:
        table, label_id, value_id = build_warehouse(args)
        label_key = args.label_key or "label"
        value_key = args.value_key or "value"
        described = "%s (warehouse, grouped)" % ".".join(args.path)
        config_extra = {}
    else:
        if args.data:
            rows = load_rows(args.data)
            described = "%d row(s) from %s" % (len(rows), args.data)
        elif panel_cols:
            rows = synthesize_rows(panel_cols, max(1, args.rows))
            described = ("%d placeholder row(s) synthesized from the plugin's panel (%s)"
                         % (len(rows), ", ".join("%s:%s" % (n, k) for n, k in panel_cols)))
            notes.append("values are placeholders -- pass --data for rows that mean "
                         "something for this plugin")
        else:
            raise SystemExit(
                "build-plugin-workbook: nothing to build data from.\n"
                "  Pass --data FILE with rows that suit the plugin (preferred),\n"
                "  or --plugin-src plugins/<name> to synthesize columns from its\n"
                "  editor panel, or --path DB SCHEMA TABLE for a real table.")

        table, colmap = build_generated(rows, args.connection_id)

        def pick(explicit, wanted, what):
            if explicit:
                if explicit not in colmap:
                    raise SystemExit("build-plugin-workbook: --%s-column %r is not in the "
                                     "data.\n  Available: %s"
                                     % (what, explicit, ", ".join(colmap)))
                return colmap[explicit][0]
            for header, (cid, kind) in colmap.items():
                if kind in wanted:
                    return cid
            raise SystemExit("build-plugin-workbook: no %s column found (need one of %s).\n"
                             "  Columns: %s"
                             % (what, "/".join(wanted),
                                ", ".join("%s:%s" % (h, k) for h, (_, k) in colmap.items())))

        label_id = pick(args.label_column, ("text",), "label")
        value_id = pick(args.value_column, ("int", "float"), "value")

        # Bind every column the plugin's panel asked for, not just label/value --
        # a plugin with lat/long/tooltip bindings needs all of them.
        config_extra = {}
        if panel_cols:
            for bname, _kind in panel_cols:
                if bname in colmap:
                    config_extra[bname] = colmap[bname][0]
        label_key = args.label_key or (panel_cols[0][0] if panel_cols else "label")
        value_key = args.value_key or (panel_cols[1][0] if len(panel_cols) > 1 else "value")

    config = {"source": {"kind": "element", "elementId": "tbl-data"}}
    config.update(config_extra)
    config.setdefault(label_key, label_id)
    config.setdefault(value_key, value_id)

    # A control the plugin writes into. This is the whole point of a `variable`
    # binding: a plugin can only reach the rest of a workbook through one.
    controls = []
    if args.variable_control:
        binding, _, col = args.variable_control.partition(":")
        binding = binding.strip()
        col = col.strip()
        if rows is None:
            raise SystemExit("build-plugin-workbook: --variable-control needs generated "
                             "rows to take the control's choices from; it is not "
                             "supported with --path.")
        if panel_entries:
            declared = [e["name"] for e in panel_entries if e["type"] == "variable"]
            if binding not in declared:
                raise SystemExit(
                    "build-plugin-workbook: the plugin declares no `variable` entry "
                    "named %r.\n  Declared: %s\n  The name must match the plugin's "
                    "configureEditorPanel exactly, or the binding is silently absent."
                    % (binding, ", ".join(declared) or "(none)"))
        if not col:
            col = panel_cols[0][0] if panel_cols else None
        if not col or col not in colmap:
            raise SystemExit("build-plugin-workbook: --variable-control column %r is "
                             "not in the data.\n  Available: %s"
                             % (col, ", ".join(colmap)))
        control, control_id, n_values = build_variable_control(
            binding, rows, col, name=args.control_name,
            multiple=not args.control_single)
        controls.append(control)
        # The canonical shape Sigma itself stores, read back off a workbook it
        # had normalized: a control binding is an object, unlike a column
        # binding, which is a bare column-id string.
        config[binding] = {"kind": "control", "controlId": control_id}
        notes.append("control %s (%s, %d choice(s) from %r) bound to the plugin's "
                     "`%s` variable" % (control_id, control["selectionMode"],
                                        n_values, col, binding))

    plugin = {"id": "plug-viz", "kind": "plugin", "pluginId": args.plugin_id,
              "config": config}

    # A text element's content field is `body` and takes markdown. There is no
    # `text` or `variant` field -- supplying those fails POST with
    # `Invalid kind: "text"`. See docs/elements-known-good.md.
    title = {"id": "txt-title", "kind": "text", "body": "**%s**" % args.name}

    page_id = "page-plugin"
    ordered = ["txt-title"] + [c["id"] for c in controls] + ["plug-viz", "tbl-data"]
    spans = {"txt-title": 3, "plug-viz": 17, "tbl-data": 12}
    for c in controls:
        spans[c["id"]] = 3
    spec = {
        "name": args.name,
        "schemaVersion": 1,
        "pages": [{"id": page_id, "name": "Plugin"}],
        "elements": [title] + controls + [plugin, table],
        "layout": layout_xml(page_id, ordered, spans),
    }
    # folderId is effectively required on POST; omitting it surfaces as
    # `Expecting UUID at 0.folderId` inside a large union-type error.
    if args.folder_id:
        spec["folderId"] = args.folder_id

    text = json.dumps(spec, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("wrote %s" % args.out, file=sys.stderr)
        print("  source: %s" % described, file=sys.stderr)
        print("  bound:  %s" % ", ".join("%s=%s" % (k, v) for k, v in config.items()
                                          if k != "source"), file=sys.stderr)
        for n in notes:
            print("  note:   %s" % n, file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
