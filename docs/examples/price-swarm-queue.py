#!/usr/bin/env python3
"""Add the queue half of the Price Swarm workbook to a generated plugin spec.

build-plugin-workbook.py emits the read half -- the grouped warehouse table and
the plugin bound to it. This adds the write half, which it has no flags for:

  seven scratch controls  <- the plugin writes these on every dot click
  an on-change action     <- fires when the LAST of those writes lands
  an input table          <- the row the action inserts
  a button                <- the same effect, by hand, if the auto path is off

Shapes are not invented: every field here was read back off a workbook the
Sigma UI authored (Loan Calculator 74bb3c81, Bulk Update Examples 39c35d8e).
`insert-rows` wants `tableElementId` and a `values` map keyed by input-table
column id -- not `table`/`elementId`, and not a `rows` array.
"""
import json
import sys

base_path, out_path = sys.argv[1], sys.argv[2]
spec = json.load(open(base_path))

QUEUE = "tbl-queue"
CONNECTION = "bee6615c-7d11-435c-8819-e32207b27fe4"  # proven to host input tables

# (control id, element id, kind, panel label)
CONTROLS = [
    ("cPickProduct", "ctl-pick-product", "text", "Picked product"),
    ("cPickFamily", "ctl-pick-family", "text", "Family"),
    ("cPickBrand", "ctl-pick-brand", "text", "Brand"),
    ("cPickPrice", "ctl-pick-price", "number", "Avg price"),
    ("cPickMargin", "ctl-pick-margin", "number", "Margin"),
    ("cPickUnits", "ctl-pick-units", "number", "Units"),
    ("cPickToken", "ctl-pick-token", "text", "Click token"),
]

# The effect both the action and the button fire. Six control reads, two
# formulas, one constant -- the same map Sigma's own UI produces.
INSERT = {
    "effect": "insert-rows",
    "tableElementId": QUEUE,
    "values": {
        "q-product": {"type": "control", "control": "cPickProduct"},
        "q-family": {"type": "control", "control": "cPickFamily"},
        "q-brand": {"type": "control", "control": "cPickBrand"},
        "q-price": {"type": "control", "control": "cPickPrice"},
        "q-margin": {"type": "control", "control": "cPickMargin"},
        "q-units": {"type": "control", "control": "cPickUnits"},
        "q-verdict": {"type": "constant",
                      "value": {"type": "text", "value": "Needs review"}},
        "q-at": {"type": "formula", "formula": "Now()"},
        "q-by": {"type": "formula", "formula": "CurrentUserEmail()"},
    },
}

controls = []
for cid, eid, kind, label in CONTROLS:
    el = {"id": eid, "kind": "control", "controlId": cid, "name": label,
          "controlType": kind}
    if kind == "text":
        el.update({"mode": "equals", "showOperators": False})
    else:
        el.update({"mode": "="})
    # The token control is the trigger. Its on-change is what turns a click in
    # an iframe into a row in a warehouse table, and the guard keeps a cleared
    # control or a half-written pick from inserting a blank row.
    if cid == "cPickToken":
        el["actions"] = [{
            "id": "act-queue-pick",
            "trigger": {"on": "on-change",
                        "condition": {"type": "formula",
                                      "formula": "IsNotNull([cPickProduct])"}},
            "effects": [INSERT],
        }]
    controls.append(el)

queue = {
    "id": QUEUE,
    "kind": "input-table",
    "source": {"kind": "empty", "connectionId": CONNECTION},
    "inputMode": "explore",
    "name": {"text": "Pricing review queue", "fontWeight": "bold"},
    "columns": [
        {"id": "q-product", "type": "text", "name": "Product"},
        {"id": "q-family", "type": "text", "name": "Family"},
        {"id": "q-brand", "type": "text", "name": "Brand"},
        {"id": "q-price", "type": "number", "name": "Avg price",
         "format": {"kind": "number", "formatString": "$,.2f"}},
        {"id": "q-margin", "type": "number", "name": "Margin",
         "format": {"kind": "number", "formatString": ",.1%"}},
        {"id": "q-units", "type": "number", "name": "Units"},
        {"id": "q-verdict", "type": "text", "name": "Verdict",
         "values": ["Needs review", "Raise price", "Cut price",
                    "Discontinue", "Keep as-is"],
         "pills": "color-by-option"},
        {"id": "q-at", "type": "datetime", "name": "Queued at"},
        {"id": "q-by", "type": "text", "name": "Queued by"},
    ],
    "sort": [{"columnId": "q-at", "direction": "descending", "nulls": "last"}],
}

button = {
    "id": "btn-queue", "kind": "button", "text": "Add current pick",
    "appearance": "outline",
    "actions": [{
        "id": "act-queue-button",
        "trigger": {"on": "on-click",
                    "condition": {"type": "formula",
                                  "formula": "IsNotNull([cPickProduct])"}},
        "effects": [INSERT],
    }],
}

note = {
    "id": "txt-how", "kind": "text",
    "body": ("Click any dot in the swarm. The plugin writes the product, family, "
             "brand, price, margin and units into the six controls below, then "
             "writes the click token **last** — and that token's on-change "
             "action inserts the row into the queue. The button is the same "
             "effect, fired by hand."),
}

spec["elements"] = spec["elements"] + [note] + controls + [button, queue]

# The plugin's half of the binding. A `variable` panel entry is bound through
# the plugin's own config, keyed by the entry's name, and unlike a column
# binding the value is an OBJECT. Without this the plugin's setVariable calls
# go nowhere and every click is silently inert.
plugin = next(e for e in spec["elements"] if e["kind"] == "plugin")
for cid, _eid, _kind, _label in CONTROLS:
    plugin["config"][cid[1].lower() + cid[2:]] = {"kind": "control", "controlId": cid}

# Layout: the swarm dominates, the controls read as a "last pick" strip under
# it, and the queue sits directly below so a click and its row are on screen
# together. Every element needs a slot or it does not appear at all.
rows = [
    ("txt-title", 1, 4, 1, 25),
    ("plug-viz", 4, 30, 1, 25),
    ("txt-how", 30, 34, 1, 25),
    ("ctl-pick-product", 34, 37, 1, 9),
    ("ctl-pick-family", 34, 37, 9, 15),
    ("ctl-pick-brand", 34, 37, 15, 21),
    ("ctl-pick-token", 34, 37, 21, 25),
    ("ctl-pick-price", 37, 40, 1, 7),
    ("ctl-pick-margin", 37, 40, 7, 13),
    ("ctl-pick-units", 37, 40, 13, 19),
    ("btn-queue", 37, 40, 19, 25),
    (QUEUE, 40, 54, 1, 25),
    ("tbl-data", 54, 66, 1, 25),
]
slots = "".join(
    '<Element elementId="%s" gridColumn="%d / %d" gridRow="%d / %d"/>'
    % (eid, c0, c1, r0, r1) for eid, r0, r1, c0, c1 in rows)
spec["layout"] = ('<?xml version="1.0" encoding="utf-8"?>'
                  '<Page type="grid" gridTemplateColumns="repeat(24, 1fr)" '
                  'gridTemplateRows="auto" id="page-plugin">%s</Page>' % slots)

ids = {e["id"] for e in spec["elements"]}
missing = [eid for eid, *_ in rows if eid not in ids]
extra = sorted(ids - {eid for eid, *_ in rows})
if missing or extra:
    sys.exit("layout/elements mismatch: missing=%s unplaced=%s" % (missing, extra))

json.dump(spec, open(out_path, "w"), indent=2)
print("wrote %s -- %d elements" % (out_path, len(spec["elements"])))
