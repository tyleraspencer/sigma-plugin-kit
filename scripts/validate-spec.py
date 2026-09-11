#!/usr/bin/env python3
"""Pre-POST static validation for the workbook specs this kit generates.

Sigma's POST/PUT endpoints accept structurally broken specs and silently
rewrite or drop things, so these checks catch the failure signatures that
have actually cost a debugging session:

- per-page `pages[].layout` is discarded (layout must be one top-level string)
- an element absent from the layout XML renders at the page bottom or not at all
- `<Element>` is a leaf; children nested in it are silently dropped
- `format` on a column needs `{kind, formatString}`, not the UI's shape
- a bare `[COLUMN]` against a warehouse source publishes with HTTP 200 and
  compiles to literal `Unknown column "[COLUMN]"` in the SQL
- a plugin element with a bad pluginId or a binding naming a nonexistent
  column publishes clean and renders an empty iframe

Checks for charts, pivots, KPIs, controls, containers and input tables were
removed: this kit emits only text, plugin and grouped-table elements, so they
could never fire. If you hand-author a richer workbook, the upstream
ryan-workbook-skill still has them.

Run before every POST/PUT (publish-workbook.sh does this for you):

    python3 scripts/validate-spec.py path/to/spec.json

Exits 0 on success, non-zero on any fail-level issue. Warn-level issues print
to stderr but do not change the exit code.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET


CHECKS = [
    "schema-version",
    "no-per-page-layout",
    "elements-placed-in-layout",
    "layoutelement-has-children",
    "column-format-shape",
    "bare-ref-resolution",
    "plugin-refs-resolve",
    "warehouse-refs-qualified",
]


def issues_schema_version(spec: dict) -> list[tuple[str, str]]:
    """Warn when `schemaVersion` isn't the currently-known-good value.

    Verified 2026-08-03 via a live POST: `schemaVersion: 2` is rejected
    outright with `"schemaVersion: Invalid 1: number"` — every canonical
    exemplar and every successful POST this skill has made uses `1`.
    WARN, not FAIL: upstream `reference/workflows/crud.md` already documents that
    this value isn't guaranteed stable long-term and recommends reading
    it from a reference GET rather than hardcoding — this check just
    catches the common case of an unverified non-1 value before POST.
    """
    issues = []
    sv = spec.get("schemaVersion")
    if sv is not None and sv != 1:
        issues.append((
            "warn",
            f"top-level `schemaVersion` is {sv!r}, not the currently-verified "
            "value `1`. Every canonical exemplar and every successful POST "
            "this skill has made uses `schemaVersion: 1`; `2` was live-tested "
            "2026-08-03 and rejected outright. If you have a specific reason "
            "to believe the value has changed, confirm via "
            "`scripts/api/publish-workbook.sh get-spec <reference-workbook-id>` "
            "first — see upstream reference/workflows/crud.md → 'schemaVersion — don't hardcode'."
        ))
    return issues


def issues_per_page_layout(spec: dict) -> list[tuple[str, str]]:
    issues = []
    for i, p in enumerate(spec.get("pages", [])):
        if p.get("layout"):
            issues.append((
                "fail",
                f"pages[{i}] ({p.get('id')}): has a per-page `layout` field. "
                "Sigma silently discards it — move to the top-level `layout` "
                "string with all <Page> elements as siblings."
            ))
    return issues


def _parse_layout(layout: str) -> ET.Element | None:
    if not layout:
        return None
    cleaned = re.sub(r"<\?xml[^?]*\?>", "", layout).strip()
    wrapped = f"<root>{cleaned}</root>"
    try:
        return ET.fromstring(wrapped)
    except ET.ParseError as e:
        sys.stderr.write(f"validate-spec: layout XML failed to parse: {e}\n")
        return None


def issues_elements_placed(spec: dict, root: ET.Element | None) -> list[tuple[str, str]]:
    if root is None:
        return [("fail", "no top-level `layout` field — workbook will have an auto-generated layout")]
    # The layout XML grammar has 5 tags, not 2 — TabbedContainer is a valid
    # elementId-bearing placement tag alongside LayoutElement/GridContainer.
    # (Its child <Tab> tags carry no elementId — tabs bind positionally to
    # the element's own `tabs[]` array order, not by XML attribute.)
    # Verified 2026-08-03 against 2 harvested workbooks (Claims Command
    # Center, Bergey's Unified Insights) — both produced exactly one false
    # FAIL per tabbed-container element before this fix.
    placed_ids = {
        el.get("elementId")
        for el in root.iter()
        if el.tag in ("Element", "Container", "TabbedContainer", "LayoutElement", "GridContainer")
    }
    issues = []
    for ei, el in enumerate(spec.get("elements", [])):
        eid = el.get("id")
        if eid and eid not in placed_ids:
            issues.append((
                "fail",
                f"elements[{ei}] ({eid}, kind={el.get('kind')}): "
                "not placed in the layout XML — will render at the page bottom or not at all."
            ))
    return issues


def issues_layoutelement_has_children(root: ET.Element | None) -> list[tuple[str, str]]:
    """Forward case of the containers-have-children check.

    `<Element>` (formerly `<LayoutElement>`) is a leaf tag — it positions
    exactly one element and takes no children. `<Element type="grid">` with
    nested tags parses without error but the children are silently dropped
    (they never render). Use `<Container>` (formerly `<GridContainer>`)
    instead when a tag needs to wrap children.

    Ported 2026-08-03 from the real upstream `sigma-workbooks` skill's manual
    checklist (upstream `reference/workflows/validate.md`) — the local skill's
    `containers-have-children` only caught the inverse (a container element
    with no matching nested children), not this direction. Updated
    2026-08-10 to accept both the current (`Element`/`Container`) and legacy
    (`LayoutElement`/`GridContainer`) tag names — see `issues_elements_placed`
    for the same accommodation.
    """
    if root is None:
        return []
    issues = []
    for el in root.iter():
        if el.tag not in ("Element", "LayoutElement"):
            continue
        children = list(el)
        if children:
            child_tags = ", ".join(c.tag for c in children)
            issues.append((
                "fail",
                f"<{el.tag} elementId=\"{el.get('elementId')}\"> has nested "
                f"child tag(s) ({child_tags}) — {el.tag} is a leaf; children "
                "nested inside it are silently dropped and never render. Use "
                "<Container> instead if this element needs to wrap children."
            ))
    return issues


def issues_column_format_shape(spec: dict) -> list[tuple[str, str]]:
    """Per Phase 6b: `format` IS spec-able, but only with `kind` + `formatString`.

    The UI-emitted shape `{type: "number", format: "currency"}` is rejected
    with "Missing 'kind' field". The verified shape is
    `{kind: "number", formatString: "$,.2f"}`.
    """
    issues = []
    for ei, el in enumerate(spec.get("elements", [])):
        for ci, col in enumerate(el.get("columns", []) or []):
            fmt = col.get("format")
            if fmt is None:
                continue
            if not isinstance(fmt, dict):
                issues.append((
                    "fail",
                    f"elements[{ei}].columns[{ci}] ({col.get('id')}): "
                    f"`format` must be an object, got {type(fmt).__name__}."
                ))
                continue
            if "kind" not in fmt:
                issues.append((
                    "fail",
                    f"elements[{ei}].columns[{ci}] ({col.get('id')}): "
                    "`format` is missing required `kind` field. "
                    "Verified shape: {kind: \"number\", formatString: \"$,.2f\"}. "
                    "If this came from a UI export ({type: ..., format: ...}), strip and re-spec."
                ))
    return issues


def _all_elements(spec: dict) -> list[tuple[int, dict]]:
    """Yield (index, element) for every top-level element.

    Elements moved from a per-page pages[].elements nesting to a single
    flat document.elements array 2026-08-10 — page membership now lives
    entirely in the layout XML, not in the JSON. The int this returns is
    the flat array index (for error-message locators), not a page index;
    nothing here ever used it for cross-element same-page comparisons —
    confirmed by reading every call site before this change.
    """
    return list(enumerate(spec.get("elements", [])))


def _extract_channel_column_ids(value) -> list[str]:
    """Return the column-id string(s) a single channel's value references.

    Channel value shapes observed across element kinds are inconsistent
    (`{"id": ...}` for donut value/holeValue and map region/lat/lon/size,
    `{"columnId": ...}` for KPI value and chart xAxis, `{"columnIds": [...]}`
    for chart yAxis, `{"by", "column": ...}` for color, and arrays of
    `{"id": ...}` for map label/tooltip) — this walks all of them generically
    rather than special-casing per element kind.
    """
    ids: list[str] = []
    if value is None:
        return ids
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        col_ids = value.get("columnIds")
        if isinstance(col_ids, list):
            ids.extend(x for x in col_ids if isinstance(x, str))
        for key in ("id", "columnId", "column"):
            v = value.get(key)
            if isinstance(v, str):
                ids.append(v)
        return ids
    if isinstance(value, list):
        for item in value:
            ids.extend(_extract_channel_column_ids(item))
        return ids
    return ids


def _inferred_column_name(col: dict) -> str | None:
    """Return the display name Sigma's resolver uses for a column.

    Explicit `name` wins. Otherwise, when the formula is a single qualified
    ref `[<Source>/<Column>]`, Sigma auto-infers `<Column>` as the display
    name. (upstream `reference/conventions.md` → "Explicit-`name` rule" recommends
    setting `name` explicitly to avoid resolver lookups failing for
    downstream sibling references — but most exemplars omit it on
    passthrough columns and Sigma's auto-inference fills the gap.)
    """
    if col.get("name"):
        return col["name"]
    formula = (col.get("formula") or "").strip()
    m = re.fullmatch(r"\[([^/\]]+)/([^/\]]+)\]", formula)
    if m:
        return m.group(2)
    return None


def _collect_control_ids(spec: dict) -> set[str]:
    """Every `controlId` on the spec — valid bare-ref targets for formulas."""
    ids: set[str] = set()
    for el in spec.get("elements", []):
        cid = el.get("controlId")
        if cid:
            ids.add(cid)
    return ids


def issues_bare_ref_resolution(spec: dict) -> list[tuple[str, str]]:
    """Flag bare bracketed refs that don't resolve to a sibling column or control.

    Catches the #1 Sigma spec error: `[column_name]` without a `/` inside a
    formula when the referenced column actually lives on the source element,
    not the current one, and therefore needs the source prefix (e.g.
    `[<SourceName>/column_name]`).

    A bare `[X]` is valid when `X` matches one of:
    - The explicit `name` of a sibling column in this element's `columns[]`.
    - The column auto-inferred from a sibling's single qualified
      `[<Source>/<Column>]` formula.
    - A `controlId` anywhere on the spec.

    Limitations:
    - Regex-based; can false-positive on bracketed text inside string
      literals (e.g. `DateFormat([Date], "[MM] %Y")` — the `[MM]` is a
      strftime token, not a column ref).
    - Sigma's auto-disambiguator can create phantom column names like
      `Store Region (1)` for cross-element references; bare refs to those
      will false-positive too. Inspect flagged cases before fixing.

    Ported from the upstream sigma-workbooks skill's `validate-spec.sh`
    2026-05-21.
    """
    control_ids = _collect_control_ids(spec)
    issues = []
    for element in spec.get("elements", []):
        cols = element.get("columns") or []
        sibling_names = {n for n in (_inferred_column_name(c) for c in cols) if n}
        valid_targets = sibling_names | control_ids
        for col in cols:
            formula = col.get("formula") or ""
            if not formula:
                continue
            # Find all bare [name] refs (no slash inside the brackets).
            bare_refs = re.findall(r"\[([^/\]]+)\]", formula)
            unresolved = [r for r in bare_refs if r not in valid_targets]
            if unresolved:
                el_label = element.get("name") or element.get("id") or "(unnamed)"
                col_label = col.get("name") or col.get("id") or "(unnamed)"
                refs_str = ", ".join(repr(r) for r in unresolved)
                # WARN-level (not fail) because Sigma auto-infers some
                # column names this check can't predict — e.g.
                # `DateTrunc("week", [Date])` becomes "Week of Date",
                # and cross-element references can produce phantom
                # `(N)`-suffix names. Inspect each flagged case; if it's
                # a real bare ref to a non-sibling, add the source
                # prefix. If it's an auto-inferred name, the flag is
                # noise.
                issues.append((
                    "warn",
                    f"element '{el_label}' / column '{col_label}': "
                    f"bare bracketed refs don't match any sibling column or controlId: {refs_str}. "
                    f"Add the source prefix (e.g. [<source-name>/{unresolved[0]}]) "
                    f"or rename a sibling. Formula: {formula}"
                ))
    return issues


_AGG_FN_PATTERN = re.compile(
    r"\b("
    r"Sum|SumIf|Avg|AvgIf|Count|CountIf|CountDistinct|CountDistinctIf|"
    r"CountNonNull|Min|MinIf|Max|MaxIf|Median|"
    r"PercentileCont|PercentileDisc|"
    r"StdDev|StdDevP|Variance|VarianceP|Mode|First|Last|"
    r"Any"
    r")\s*\(",
    re.IGNORECASE,
)


_CONDITIONAL_AGG_NATIVE_FORM = {
    "sum": (
        "SumIf(<value>, <condition>)",
        "returns NULL (not 0) on an empty match — if the original used "
        "a `0` else-branch, wrap the rewrite: Zn(SumIf(<value>, <condition>))",
    ),
    "count": (
        "CountIf(<condition>)",
        "returns 0 on an empty match, same as the composed form — no "
        "null-behavior change",
    ),
    "countdistinct": (
        "CountDistinctIf(<value>, <condition>)",
        "empty-match behavior isn't explicitly documented by Sigma; "
        "verify before assuming parity, and wrap in Coalesce(..., 0) "
        "defensively if a non-null result is required",
    ),
    "avg": (
        "AvgIf(<value>, <condition>)",
        "returns NULL on an empty match, same as the composed form "
        "(assuming a Null, not 0, else-branch) — no null-behavior change",
    ),
    "min": (
        "MinIf(<value>, <condition>)",
        "returns NULL on an empty match, same as the composed form "
        "(assuming a Null, not 0, else-branch) — no null-behavior change",
    ),
    "max": (
        "MaxIf(<value>, <condition>)",
        "returns NULL on an empty match, same as the composed form "
        "(assuming a Null, not 0, else-branch) — no null-behavior change",
    ),
}

_CONDITIONAL_AGG_ANTIPATTERN = re.compile(
    r"\b(Sum|CountDistinct|Count|Avg|Min|Max)\s*\(\s*If\s*\(",
    re.IGNORECASE,
)


def _load_spec(path: str) -> dict:
    """Load a spec from JSON or YAML.

    YAML support ported 2026-08-03 from the real upstream `sigma-workbooks`
    skill's `validate-spec.sh`, which handles `.yaml`/`.yml` via a
    PyYAML-or-`yq` fallback chain. The upstream SKILL.md already documents that YAML
    specs arrive from users; the validator was JSON-only until now.
    """
    if not path.endswith((".yaml", ".yml")):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    try:
        import yaml  # type: ignore
    except ImportError:
        pass
    else:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)
    import subprocess
    for cmd in (["yq", "-o=json", path], ["yq", ".", path]):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return json.loads(result.stdout)
        except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
            continue
    sys.stderr.write(
        "validate-spec: YAML input requires PyYAML (`pip install pyyaml`) "
        "or `yq` on PATH (either mikefarah/yq or the Python yq wrapper — "
        "both read the same via `yq .`).\n"
    )
    sys.exit(2)


_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def issues_plugin_refs_resolve(spec: dict) -> list[tuple[str, str]]:
    """Verify `kind: "plugin"` elements are wired to something real.

    A plugin element that publishes cleanly and renders blank is the default
    failure mode here, because Sigma validates neither the pluginId nor the
    config bindings at POST time: an unregistered UUID, or a config key
    pointing at a column that does not exist on the bound element, both
    return 200 and then show an empty iframe.

    `kind: "plugin"` is undocumented in the spec API (the spec endpoints are
    private Beta) but verified working against a live org. Every check here is
    on shapes observed in specs that actually render.
    """
    out: list[tuple[str, str]] = []
    all_elements = _all_elements(spec)
    by_id = {el.get("id"): el for _, el in all_elements if isinstance(el, dict)}

    for idx, el in all_elements:
        if not isinstance(el, dict) or el.get("kind") != "plugin":
            continue
        loc = f"elements[{idx}] (id={el.get('id')!r})"

        plugin_id = el.get("pluginId")
        if not plugin_id:
            out.append(("fail", f"{loc}: plugin element has no `pluginId`. Register the "
                                "plugin first: scripts/api/register-plugin.sh create ..."))
        elif not _UUID_RE.match(str(plugin_id)):
            out.append(("fail", f"{loc}: `pluginId` {plugin_id!r} is not a UUID. It must be "
                                "the id returned by POST /v2/plugins, not the plugin's name "
                                "or its hosted URL."))

        config = el.get("config")
        if not isinstance(config, dict):
            out.append(("fail", f"{loc}: plugin element has no `config` object, so nothing "
                                "is bound and it will render empty."))
            continue

        source = config.get("source")
        if not isinstance(source, dict):
            out.append(("warn", f"{loc}: `config.source` is missing. The plugin will fall "
                                "back to whatever it renders with no data (a good plugin "
                                "shows demo data); bind an element to show real rows."))
            continue

        src_id = source.get("elementId")
        if source.get("kind") != "element" or not src_id:
            out.append(("fail", f"{loc}: `config.source` must be "
                                '{"kind": "element", "elementId": "<id>"}, got '
                                f"{json.dumps(source)}."))
            continue

        src_el = by_id.get(src_id)
        if src_el is None:
            out.append(("fail", f"{loc}: `config.source.elementId` {src_id!r} does not match "
                                "any element in this spec."))
            continue

        # Column bindings are bare column-id strings keyed by the names the
        # plugin declared in configureEditorPanel. Anything else that is a
        # plain string is treated as a column binding too -- that is exactly
        # what it is.
        src_columns = {
            c.get("id")
            for c in (src_el.get("columns") or [])
            if isinstance(c, dict) and c.get("id")
        }
        if not src_columns:
            continue

        for key, value in config.items():
            if key == "source" or not isinstance(value, str):
                continue
            if value not in src_columns:
                out.append((
                    "fail",
                    f"{loc}: config binding {key!r} -> {value!r} is not a column on "
                    f"source element {src_id!r} (kind={src_el.get('kind')!r}). "
                    f"Available: {', '.join(sorted(src_columns))}",
                ))

    return out



def issues_warehouse_refs_qualified(spec: dict) -> list[tuple[str, str]]:
    """Catch bare [COLUMN] refs on a warehouse-table source.

    This is the one that publishes successfully and lies: a bare `[PRICE]`
    against `{kind: "warehouse-table"}` returns HTTP 200 and then compiles to
    literal `'Unknown column "[PRICE]"'` strings in the SQL, with no error
    anywhere. Warehouse columns must be qualified with the last path segment:
    `[PLUGS_ELECTRONICS_HANDS_ON_LAB_DATA/PRICE]`.

    Verified live 2026-09-11 by publishing all three forms and reading the
    compiled SQL back.
    """
    issues: list[tuple[str, str]] = []
    for ei, el in enumerate(spec.get("elements", [])):
        src_obj = el.get("source") or {}
        if src_obj.get("kind") != "warehouse-table":
            continue
        path = src_obj.get("path") or []
        if not path:
            continue
        table = path[-1]
        for ci, col in enumerate(el.get("columns", []) or []):
            formula = col.get("formula")
            if not isinstance(formula, str):
                continue
            for ref in re.findall(r"\[([^\]]+)\]", formula):
                if "/" in ref:
                    continue
                issues.append((
                    "fail",
                    f"elements[{ei}].columns[{ci}] ({col.get('id')}): formula "
                    f"references [{ref}] unqualified on a warehouse-table source. "
                    f"This POSTs with HTTP 200 and then compiles to "
                    f'\'Unknown column "[{ref}]"\' in the SQL. '
                    f"Use [{table}/{ref}]."
                ))
    return issues

def main() -> None:
    if len(sys.argv) != 2:
        sys.stderr.write("usage: validate-spec.py <spec.json|spec.yaml>\n")
        sys.exit(2)
    spec = _load_spec(sys.argv[1])

    root = _parse_layout(spec.get("layout", ""))

    all_issues: list[tuple[str, str, str]] = []
    for tag, fn in [
        ("schema-version",            lambda: issues_schema_version(spec)),
        ("no-per-page-layout",        lambda: issues_per_page_layout(spec)),
        ("elements-placed-in-layout", lambda: issues_elements_placed(spec, root)),
        ("layoutelement-has-children", lambda: issues_layoutelement_has_children(root)),
        ("column-format-shape",       lambda: issues_column_format_shape(spec)),
        ("bare-ref-resolution",       lambda: issues_bare_ref_resolution(spec)),
        ("plugin-refs-resolve",       lambda: issues_plugin_refs_resolve(spec)),
        ("warehouse-refs-qualified", lambda: issues_warehouse_refs_qualified(spec)),
    ]:
        for level, msg in fn():
            all_issues.append((level, tag, msg))

    fail_count = sum(1 for level, _, _ in all_issues if level == "fail")
    warn_count = sum(1 for level, _, _ in all_issues if level == "warn")

    limitations = (
        "Note: these checks catch known failure signatures from past sessions "
        "— a clean run does not guarantee the spec renders correctly. "
        "`bare-ref-resolution` only catches bare (unqualified) refs; qualified "
        "refs are not verified here (the server checks those on publish). "
        "`action-refs-resolve` verifies overlayId/control/table/tabbedContainer/"
        "agentId references, including inside agents[].tools[].steps[] — always "
        "visually verify after publish. `plugin-refs-resolve` checks pluginId "
        "shape and that config bindings name real columns on the bound element, "
        "but cannot confirm the pluginId is registered in your org — check that "
        "with scripts/api/register-plugin.sh list."
    )

    if not all_issues:
        print(f"validate-spec: {sys.argv[1]} — all {len(CHECKS)} checks passed")
        print(limitations)
        sys.exit(0)

    for level, tag, msg in all_issues:
        prefix = "FAIL" if level == "fail" else "WARN"
        sys.stderr.write(f"[{prefix}][{tag}] {msg}\n")

    summary = f"validate-spec: {fail_count} fail, {warn_count} warn in {sys.argv[1]}"
    sys.stderr.write(f"\n{summary}\n{limitations}\n")
    sys.exit(1 if fail_count else 0)


if __name__ == "__main__":
    main()
