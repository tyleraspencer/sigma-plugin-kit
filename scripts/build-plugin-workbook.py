#!/usr/bin/env python3
"""Generate a workbook spec that hosts a registered plugin, bound to its data.

Two data modes, because they need genuinely different specs:

  --data-mode fake   Creates an `input-table` element seeded with your rows,
                     and binds the plugin to it. The data lands in Sigma as a
                     real, editable table -- the plugin exercises the same
                     code path it will use in production, and a reviewer can
                     see and change the numbers.

                     Sigma has no way to pre-populate an input table from a
                     spec: `insert-rows` is a runtime action effect, and one
                     effect inserts exactly one row. So the generated workbook
                     carries a "Seed demo data" button holding one insert-rows
                     effect per row. Publish, open the workbook, click it once.

  --data-mode real   Creates a `table` element on a warehouse table and binds
                     the plugin to that. No seed button, no input table.

Usage:
  build-plugin-workbook.py --name "Regional Ranking" --plugin-id <uuid> \\
      --data-mode fake --connection-id <conn> --data rows.json \\
      --bind label=Region --bind value=Revenue

  build-plugin-workbook.py --name "Regional Ranking" --plugin-id <uuid> \\
      --data-mode real --connection-id <conn> \\
      --path SALES_DB PUBLIC REGIONS --columns REGION REVENUE \\
      --bind label=REGION --bind value=REVENUE

`--bind <plugin-key>=<column>` maps a key you declared in the plugin's
configureEditorPanel to a column. Bindings are emitted as bare column-ID
strings, which is what Sigma's plugin config expects.

Writes the flat spec shape (schemaVersion/pages/elements/layout as top-level
siblings). publish-workbook.sh wraps it into the `document` envelope on POST.
"""
import argparse
import json
import re
import sys

SYSTEM_COLUMNS = ("ID", "CREATED_AT", "CREATED_BY", "UPDATED_AT", "UPDATED_BY")


def column_id(name):
    """Stable, URL/formula-safe column id derived from a display name."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return "col-" + (slug or "unnamed")


def sigma_type(value):
    """Map a Python value to an input-table column type.

    bool before int/float: bool is a subclass of int in Python, so the naive
    order silently types every checkbox column as a number.
    """
    if isinstance(value, bool):
        return "checkbox"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def constant(value):
    """Wrap a Python value as an insert-rows dynamic-value object."""
    t = sigma_type(value)
    if t == "checkbox":
        return {"type": "constant", "value": {"type": "boolean", "value": bool(value)}}
    if t == "number":
        return {"type": "constant", "value": {"type": "number", "value": value}}
    return {"type": "constant", "value": {"type": "text", "value": str(value)}}


def infer_columns(rows):
    """Column (name, type) pairs in first-seen order across all rows."""
    names = []
    for row in rows:
        for key in row:
            if key not in names:
                names.append(key)
    if not names:
        raise SystemExit("build-plugin-workbook: --data rows have no columns.")

    columns = []
    for name in names:
        # First non-null value decides the type; an all-null column falls back
        # to text rather than crashing.
        chosen = "text"
        for row in rows:
            if row.get(name) is not None:
                chosen = sigma_type(row[name])
                break
        columns.append((name, chosen))
    return columns


def build_fake(args, rows):
    columns = infer_columns(rows)
    by_name = {name: column_id(name) for name, _ in columns}

    table = {
        "id": "tbl-data",
        "kind": "input-table",
        "inputMode": "view",
        "source": {"kind": "empty", "connectionId": args.connection_id},
        # System columns must stay bare {id} objects. Sigma adds a `formula`
        # field to them on GET-back, and re-submitting that fails PUT with
        # "system column `ID` cannot set `type` or `formula`" -- so strip them
        # back to bare {id} before any PUT of a harvested spec.
        "columns": [{"id": c} for c in SYSTEM_COLUMNS]
        + [{"id": by_name[name], "name": name, "type": ctype} for name, ctype in columns],
    }

    effects = []
    for row in rows:
        values = {
            by_name[name]: constant(row[name])
            for name in row
            if row.get(name) is not None
        }
        if values:
            effects.append({"effect": "insert-rows", "table": "tbl-data", "values": values})

    if args.no_seed:
        return [table], by_name, "tbl-data"

    seed_button = {
        "id": "btn-seed",
        "kind": "button",
        "text": "Seed demo data (%d rows)" % len(effects),
        "appearance": "outline",
        "actions": [{"id": "a-seed", "trigger": "on-click", "effects": effects}],
    }

    return [table, seed_button], by_name, "tbl-data"


def build_real(args):
    if not args.path or len(args.path) != 3:
        raise SystemExit(
            "build-plugin-workbook: --data-mode real needs --path DATABASE SCHEMA TABLE."
        )
    if not args.columns:
        raise SystemExit("build-plugin-workbook: --data-mode real needs --columns.")

    # Warehouse column formulas reference the LAST path segment, e.g.
    # path ["SALES_DB","PUBLIC","ORDERS"] -> [ORDERS/revenue].
    table_name = args.path[-1]
    by_name = {c: column_id(c) for c in args.columns}

    table = {
        "id": "tbl-data",
        "kind": "table",
        "source": {
            "kind": "warehouse-table",
            "connectionId": args.connection_id,
            "path": list(args.path),
        },
        "columns": [
            {"id": by_name[c], "name": c, "formula": "[%s/%s]" % (table_name, c)}
            for c in args.columns
        ],
    }
    return [table], by_name, "tbl-data"


def layout_xml(page_id, element_ids):
    """A 24-column grid: title, optional seed button, source table, plugin.

    Sigma's `layout` is an XML string, not a nested object. Every element in
    `elements` needs a slot here or it does not appear in the workbook.
    """
    rows = []
    cursor = 1

    def place(eid, span, col="1 / 25"):
        nonlocal cursor
        rows.append(
            '<Element elementId="%s" gridColumn="%s" gridRow="%d / %d"/>'
            % (eid, col, cursor, cursor + span)
        )
        cursor += span

    place("txt-title", 3)
    if "btn-seed" in element_ids:
        place("btn-seed", 3)
    place("plug-viz", 16)
    place("tbl-data", 12)

    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" '
        'gridTemplateRows="auto" id="%s">%s</Page>' % (page_id, "".join(rows))
    )


def main():
    ap = argparse.ArgumentParser(
        description="Generate a workbook spec hosting a registered Sigma plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--name", required=True, help="workbook name")
    ap.add_argument("--plugin-id", required=True, help="pluginId from register-plugin.sh")
    ap.add_argument("--folder-id", help="destination folder id")
    ap.add_argument("--data-mode", choices=("fake", "real"), default="fake")
    ap.add_argument("--connection-id", help="required in both modes")
    ap.add_argument("--data", help="fake mode: JSON file of row objects")
    ap.add_argument("--path", nargs=3, metavar=("DB", "SCHEMA", "TABLE"),
                    help="real mode: warehouse table path")
    ap.add_argument("--columns", nargs="+", help="real mode: columns to project")
    ap.add_argument("--bind", action="append", default=[], metavar="KEY=COLUMN",
                    help="plugin config binding; repeatable")
    ap.add_argument("--title", help="on-canvas title (defaults to --name)")
    ap.add_argument("--no-seed", action="store_true",
                    help="fake mode: emit the input table with no seed button. Use where "
                         "the org's spec schema rejects the insert-rows effect (verified "
                         "live on papercrane 2026-09-11 -- see docs/plugins.md); paste the "
                         "rows into the input table by hand instead. --data is still "
                         "required, to define the columns and their types.")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args()

    if not args.connection_id:
        raise SystemExit(
            "build-plugin-workbook: --connection-id is required.\n"
            "  Even a fake-data input table is warehouse-backed. List options with:\n"
            "    bash scripts/api/list-connections.sh"
        )

    if args.data_mode == "fake":
        if not args.data:
            raise SystemExit("build-plugin-workbook: --data-mode fake needs --data rows.json")
        with open(args.data, encoding="utf-8") as fh:
            rows = json.load(fh)
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise SystemExit("build-plugin-workbook: --data must be a non-empty JSON array of objects.")
        data_elements, by_name, source_id = build_fake(args, rows)
    else:
        data_elements, by_name, source_id = build_real(args)

    # --- Plugin bindings --------------------------------------------------
    # Resolve each --bind target against the columns we just generated, so a
    # typo fails here rather than rendering an empty plugin in Sigma.
    config = {"source": {"kind": "element", "elementId": source_id}}
    for pair in args.bind:
        if "=" not in pair:
            raise SystemExit("build-plugin-workbook: --bind must be KEY=COLUMN, got %r" % pair)
        key, target = pair.split("=", 1)
        key, target = key.strip(), target.strip()
        if target in by_name:
            config[key] = by_name[target]
        elif target in by_name.values():
            config[key] = target
        else:
            raise SystemExit(
                "build-plugin-workbook: --bind %s=%s -- no such column.\n"
                "  Available: %s" % (key, target, ", ".join(sorted(by_name)))
            )

    plugin = {
        "id": "plug-viz",
        "kind": "plugin",
        "pluginId": args.plugin_id,
        "config": config,
    }

    # A text element's content field is `body`, and it takes markdown (inline
    # HTML/`<span style=...>` works too). There is no `text` or `variant`
    # field: supplying them fails POST with `Invalid kind: "text"`, which reads
    # like the element kind is unsupported rather than "you used the wrong
    # field name". Sigma rejects a known field with a bad value shape and
    # silently drops unknown field names, so that message is what a bad
    # content field looks like.
    title = {
        "id": "txt-title",
        "kind": "text",
        "body": "**%s**" % (args.title or args.name),
    }

    elements = [title] + data_elements + [plugin]
    element_ids = {e["id"] for e in elements}
    page_id = "page-plugin"

    spec = {
        "name": args.name,
        "schemaVersion": 1,
        "pages": [{"id": page_id, "name": "Plugin"}],
        "elements": elements,
        "layout": layout_xml(page_id, element_ids),
    }
    if args.folder_id:
        spec["folderId"] = args.folder_id

    text = json.dumps(spec, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("wrote %s" % args.out, file=sys.stderr)
        if args.data_mode == "fake":
            print(
                "Fake-data mode: publish, open the workbook, then click "
                "\"Seed demo data\" once to populate the input table.",
                file=sys.stderr,
            )
    else:
        print(text)


if __name__ == "__main__":
    main()
