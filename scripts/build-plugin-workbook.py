#!/usr/bin/env python3
"""Generate a workbook spec that hosts a registered plugin, bound to real data.

Defaults to an aggregated view of the org's sample retail table, so the common
case needs no data flags at all:

    build-plugin-workbook.py --name "My Viz Demo" --plugin-id <uuid>

That produces a grouped `table` element -- Store Region by Sum(PRICE*QUANTITY)
-- with the plugin bound to it. Override any of --connection-id, --path,
--dimension, --measure, --measure-name to point somewhere else.

Why no input-table/fake-data mode: Sigma cannot populate an input table from a
spec. `insert-rows` is a runtime action effect (one row per effect) and is
currently rejected outright by the spec API, and no source kind accepts
literal rows -- `sql`, `custom-sql`, `customSql`, `warehouse-sql`, `manual`
and `inline` were all probed and refused. Binding to a real warehouse table is
both faster and the only path that actually shows data. See docs/plugins.md.

Measure and dimension expressions take bare warehouse column names; this
script qualifies them to `[TABLE/COLUMN]` for you. That matters: a bare
`[PRICE]` against a warehouse source **publishes with HTTP 200** and then
compiles to `'Unknown column "[PRICE]"'` in the SQL -- a silent failure with
no error anywhere.
"""
import argparse
import json
import re
import sys

# Verified live 2026-09-11: connection "Sigma Sample Database", warehouse path
# RETAIL.PLUGS_ELECTRONICS.PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA (note the
# _DATA suffix). Columns include ORDER_NUMBER, DATE, SKU_NUMBER, QUANTITY,
# COST, PRICE, PRODUCT_TYPE, PRODUCT_FAMILY, PRODUCT_LINE, BRAND,
# PRODUCT_NAME, STORE_NAME, STORE_REGION, STORE_STATE, STORE_CITY,
# STORE_ZIP_CODE, CUSTOMER_NAME.
DEFAULT_CONNECTION = "bee6615c-7d11-435c-8819-e32207b27fe4"
DEFAULT_PATH = ["RETAIL", "PLUGS_ELECTRONICS", "PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA"]
DEFAULT_DIMENSION = "STORE_REGION"
DEFAULT_MEASURE = "Sum(PRICE * QUANTITY)"
DEFAULT_MEASURE_NAME = "Revenue"

# Bare ALL-CAPS identifiers in an expression are warehouse column names to
# qualify. Sigma's own functions are CamelCase (Sum, Count, DateTrunc), so
# they are not matched. A token already inside [brackets] is left alone.
_BARE_COLUMN = re.compile(r"(?<!\[)\b([A-Z][A-Z0-9_]{1,})\b(?!\])")


def column_id(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return "col-" + (slug or "unnamed")


def qualify(expr, table):
    """Rewrite bare ALL_CAPS column names to [TABLE/COLUMN] references."""
    return _BARE_COLUMN.sub(lambda m: "[%s/%s]" % (table, m.group(1)), expr)


def layout_xml(page_id, ordered_ids, spans):
    rows = []
    cursor = 1
    for eid in ordered_ids:
        span = spans[eid]
        rows.append('<Element elementId="%s" gridColumn="1 / 25" gridRow="%d / %d"/>'
                    % (eid, cursor, cursor + span))
        cursor += span
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" '
            'gridTemplateRows="auto" id="%s">%s</Page>' % (page_id, "".join(rows)))


def main():
    ap = argparse.ArgumentParser(
        description="Generate a workbook spec hosting a registered Sigma plugin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--name", required=True, help="workbook name")
    ap.add_argument("--plugin-id", required=True, help="pluginId from register-plugin.sh")
    ap.add_argument("--folder-id", help="destination folder id")

    ap.add_argument("--connection-id", default=DEFAULT_CONNECTION)
    ap.add_argument("--path", nargs=3, metavar=("DB", "SCHEMA", "TABLE"), default=DEFAULT_PATH)
    ap.add_argument("--dimension", default=DEFAULT_DIMENSION,
                    help="column to group by (the plugin's label). Default %s" % DEFAULT_DIMENSION)
    ap.add_argument("--measure", default=DEFAULT_MEASURE,
                    help="aggregate expression, bare column names OK. Default %r" % DEFAULT_MEASURE)
    ap.add_argument("--measure-name", default=DEFAULT_MEASURE_NAME)
    # These must match the `name` values in the plugin's own
    # configureEditorPanel DEFS. The bundled template declares label/value,
    # but a plugin is free to declare anything -- sec-logo-bars uses `team`
    # for its dimension. Bind the wrong key and the plugin silently renders
    # its synthetic fallback, because nothing it looks for resolves.
    ap.add_argument("--label-key", default="label",
                    help="plugin config key for the dimension, matching the plugin's "
                         "configureEditorPanel DEFS. Default 'label'")
    ap.add_argument("--value-key", default="value",
                    help="plugin config key for the measure, matching the plugin's "
                         "configureEditorPanel DEFS. Default 'value'")
    ap.add_argument("--out", help="write here instead of stdout")
    args = ap.parse_args()

    table_name = args.path[-1]
    dim_name = args.dimension.replace("_", " ").title()

    dim_id = column_id(args.dimension)
    mea_id = column_id(args.measure_name)

    table = {
        "id": "tbl-data",
        "kind": "table",
        "name": "%s by %s" % (args.measure_name, dim_name),
        "source": {
            "kind": "warehouse-table",
            "connectionId": args.connection_id,
            "path": list(args.path),
        },
        "columns": [
            {"id": dim_id, "name": dim_name,
             "formula": "[%s/%s]" % (table_name, args.dimension)},
            {"id": mea_id, "name": args.measure_name, "formula": qualify(args.measure, table_name)},
        ],
        # Once a table has groupings, every column must be a groupBy dimension
        # or a calculations entry -- an orphaned column renders a nonsensical
        # summary value instead of per-row data.
        "groupings": [
            {"id": "by-dim", "groupBy": [dim_id], "calculations": [mea_id]}
        ],
    }

    config = {
        "source": {"kind": "element", "elementId": "tbl-data"},
        args.label_key: dim_id,
        args.value_key: mea_id,
    }
    plugin = {"id": "plug-viz", "kind": "plugin", "pluginId": args.plugin_id, "config": config}

    # A text element's content field is `body` and takes markdown. There is no
    # `text` or `variant` field -- supplying those fails POST with
    # `Invalid kind: "text"`, which blames the element kind rather than the
    # field. See docs/elements-known-good.md.
    title = {"id": "txt-title", "kind": "text", "body": "**%s**" % args.name}

    elements = [title, plugin, table]
    ordered = ["txt-title", "plug-viz", "tbl-data"]
    spans = {"txt-title": 3, "plug-viz": 17, "tbl-data": 12}

    page_id = "page-plugin"
    spec = {
        "name": args.name,
        "schemaVersion": 1,
        "pages": [{"id": page_id, "name": "Plugin"}],
        "elements": elements,
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
        print("  source: %s  (%s)" % (".".join(args.path), args.connection_id), file=sys.stderr)
        print("  group:  %s -> %s = %s"
              % (args.dimension, args.measure_name, qualify(args.measure, table_name)),
              file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
