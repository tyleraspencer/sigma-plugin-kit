#!/usr/bin/env python3
"""Generate a workbook spec that hosts a registered plugin, bound to data.

Two source modes. **Generated is the default** -- no data flags needed:

    build-plugin-workbook.py --name "My Viz Demo" --plugin-id <uuid>

  generated  Rows are compiled into a `SELECT ... FROM (VALUES ...)` literal
             and published as a `kind: "sql"` table element, so the data lives
             in the workbook spec itself. This is the only API route for
             fabricated rows: input tables cannot be written from code, and
             `/v2/files` has no CSV upload, so a CSV-backed element can't be
             produced either. Supply your own rows with --data, or get the
             built-in sample shape (text / float / int / date / boolean).

  warehouse  `--path DB SCHEMA TABLE` binds a real table instead, grouped by
             --dimension with --measure aggregated over it.

Generated mode needs no GROUP BY: you control the rows, so emit exactly the
rows the plugin should draw, one per category.

The SQL is Snowflake-flavoured (`::varchar`, `::number`, `::timestamp_ntz`).
Another connection type needs the casts adjusted.
"""
import argparse
import csv
import json
import re
import sys

# Verified live 2026-09-11. Any connection works for generated mode -- the
# VALUES literal never touches a real table -- but this one is always present.
DEFAULT_CONNECTION = "bee6615c-7d11-435c-8819-e32207b27fe4"   # Sigma Sample Database

# Warehouse mode's default, used only when --path is given without one.
DEFAULT_PATH = ["RETAIL", "PLUGS_ELECTRONICS", "PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA"]
DEFAULT_DIMENSION = "STORE_REGION"
DEFAULT_MEASURE = "Sum(PRICE * QUANTITY)"
DEFAULT_MEASURE_NAME = "Revenue"

# The built-in generated dataset. Deliberately one text, two numeric, one
# date and one boolean column, so it satisfies whatever `allowedTypes` a
# plugin's editor panel filters on.
SAMPLE_ROWS = [
    {"NAME": "Alice Johnson",  "SCORE": 87.5, "EVENTS": 42, "AS_OF": "2024-01-15", "ACTIVE": True},
    {"NAME": "Bob Smith",      "SCORE": 92.3, "EVENTS": 18, "AS_OF": "2024-02-20", "ACTIVE": False},
    {"NAME": "Carol Williams", "SCORE": 78.9, "EVENTS": 35, "AS_OF": "2024-03-10", "ACTIVE": True},
    {"NAME": "David Brown",    "SCORE": 65.2, "EVENTS": 27, "AS_OF": "2024-04-05", "ACTIVE": True},
    {"NAME": "Emma Davis",     "SCORE": 95.7, "EVENTS": 51, "AS_OF": "2024-05-12", "ACTIVE": False},
    {"NAME": "Frank Miller",   "SCORE": 71.4, "EVENTS": 23, "AS_OF": "2024-06-18", "ACTIVE": True},
]

CAST = {"text": "::varchar", "int": "::number", "float": "::float",
        "boolean": "::boolean", "date": "::date", "datetime": "::timestamp_ntz"}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")

# Bare ALL-CAPS identifiers in a warehouse measure expression are column names
# to qualify. Sigma's functions are CamelCase (Sum, Count), so they don't match.
_BARE_COLUMN = re.compile(r"(?<!\[)\b([A-Z][A-Z0-9_]{1,})\b(?!\])")


def column_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return "col-" + (slug or "unnamed")


def qualify(expr, table):
    return _BARE_COLUMN.sub(lambda m: "[%s/%s]" % (table, m.group(1)), expr)


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
    """Render one cell as a SQL literal. Every string is single-quote escaped."""
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
    """Compile rows into a kind:"sql" table element. Returns (element, colmap)."""
    headers = []
    for row in rows:
        for k in row:
            if k not in headers:
                headers.append(k)

    kinds = [infer_type([r.get(h) for r in rows]) for h in headers]
    taken = set()
    names = [sql_name(h, taken) for h in headers]

    select = ",\n  ".join('v.c%d%s AS %s' % (i + 1, CAST[k], n)
                          for i, (n, k) in enumerate(zip(names, kinds)))
    values = ",\n  ".join(
        "(" + ", ".join(literal(r.get(h), k) for h, k in zip(headers, kinds)) + ")"
        for r in rows)
    cols = ", ".join("c%d" % (i + 1) for i in range(len(headers)))
    statement = "SELECT\n  %s\nFROM (VALUES\n  %s\n) AS v(%s)" % (select, values, cols)

    # Column formulas reference the implicit source element name "Custom SQL".
    # An explicit `name` keeps the header as written -- without it Sigma
    # prettifies UNITS_SOLD into "Units Sold".
    columns = [{"id": column_id(h), "name": str(h), "formula": "[Custom SQL/%s]" % n}
               for h, n in zip(headers, names)]

    element = {
        "id": "tbl-data", "kind": "table", "name": "Generated data",
        "source": {"kind": "sql", "connectionId": connection_id, "statement": statement},
        "columns": columns,
        "order": [c["id"] for c in columns],
    }
    # header -> (column id, inferred kind), for picking label/value columns
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
        # or a calculations entry -- an orphan renders a nonsensical summary
        # value instead of per-row data.
        "groupings": [{"id": "by-dim", "groupBy": [dim_id], "calculations": [mea_id]}],
    }
    return element, dim_id, mea_id


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
                    help="generated mode: rows to compile into the SQL VALUES literal. "
                         "A .csv/.tsv, or a JSON array of objects. Omit for the "
                         "built-in sample shape.")
    ap.add_argument("--label-column", help="generated mode: column the plugin labels by "
                                           "(default: first text column)")
    ap.add_argument("--value-column", help="generated mode: column the plugin measures "
                                           "(default: first numeric column)")

    ap.add_argument("--path", nargs=3, metavar=("DB", "SCHEMA", "TABLE"),
                    help="warehouse mode: bind a real table instead of generating rows. "
                         "Known-good example: %s" % " ".join(DEFAULT_PATH))
    ap.add_argument("--dimension", default=DEFAULT_DIMENSION,
                    help="warehouse mode: column to group by. Default %s" % DEFAULT_DIMENSION)
    ap.add_argument("--measure", default=DEFAULT_MEASURE,
                    help="warehouse mode: aggregate expression; bare column names are "
                         "qualified for you. Default %r" % DEFAULT_MEASURE)
    ap.add_argument("--measure-name", default=DEFAULT_MEASURE_NAME)

    # These must match the `name` values in the plugin's own
    # configureEditorPanel DEFS. The bundled template declares label/value, but
    # a plugin is free to declare anything -- sec-logo-bars uses `team`. Bind
    # the wrong key and the plugin silently renders its synthetic fallback.
    ap.add_argument("--label-key", default="label",
                    help="plugin config key for the label, matching its DEFS. Default 'label'")
    ap.add_argument("--value-key", default="value",
                    help="plugin config key for the value, matching its DEFS. Default 'value'")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args()

    if args.path is not None:
        table, label_id, value_id = build_warehouse(args)
        described = "%s (warehouse)" % ".".join(args.path)
    else:
        rows = load_rows(args.data) if args.data else SAMPLE_ROWS
        table, colmap = build_generated(rows, args.connection_id)

        def pick(explicit, wanted, what):
            if explicit:
                if explicit not in colmap:
                    raise SystemExit(
                        "build-plugin-workbook: --%s-column %r is not in the data.\n"
                        "  Available: %s" % (what, explicit, ", ".join(colmap)))
                return colmap[explicit][0]
            for header, (cid, kind) in colmap.items():
                if kind in wanted:
                    return cid
            raise SystemExit(
                "build-plugin-workbook: no %s column found (need one of %s).\n"
                "  Columns: %s" % (what, "/".join(wanted),
                                   ", ".join("%s:%s" % (h, k) for h, (_, k) in colmap.items())))

        label_id = pick(args.label_column, ("text",), "label")
        value_id = pick(args.value_column, ("int", "float"), "value")
        described = "%d generated row(s) as a SQL VALUES literal" % len(rows)

    plugin = {"id": "plug-viz", "kind": "plugin", "pluginId": args.plugin_id,
              "config": {"source": {"kind": "element", "elementId": "tbl-data"},
                         args.label_key: label_id, args.value_key: value_id}}

    # A text element's content field is `body` and takes markdown. There is no
    # `text` or `variant` field -- supplying those fails POST with
    # `Invalid kind: "text"`. See docs/elements-known-good.md.
    title = {"id": "txt-title", "kind": "text", "body": "**%s**" % args.name}

    page_id = "page-plugin"
    spec = {
        "name": args.name,
        "schemaVersion": 1,
        "pages": [{"id": page_id, "name": "Plugin"}],
        "elements": [title, plugin, table],
        "layout": layout_xml(page_id, ["txt-title", "plug-viz", "tbl-data"],
                             {"txt-title": 3, "plug-viz": 17, "tbl-data": 12}),
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
        print("  bound:  %s=%s, %s=%s"
              % (args.label_key, label_id, args.value_key, value_id), file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
