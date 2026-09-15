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
    "action-refs-resolve",
    "plugin-refs-resolve",
    "plugin-owns-its-actions",
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


def issues_action_refs_resolve(spec: dict) -> list[tuple[str, str]]:
    """Verify every action/effect reference resolves to something real.

    Restored 2026-09-15. This check shipped upstream 2026-08-03, was dropped
    with the other 12 in the extraction (see NOTICE), and the drop was never
    reflected in the closing note -- every run since has claimed to verify
    overlayId/control/table/tabbedContainer/agentId references while doing
    nothing of the kind. Ported back from 60ff083 rather than rewritten.

    Why it matters: a dangling `overlayId`, `control`, `table`,
    `tabbedContainer`, or `navigate.target.page` fails SILENTLY. The POST
    succeeds and the button renders; nothing happens when it is clicked.
    Same family as `plugin-refs-resolve` -- the API validates none of these.

    Checks, per effect:
    - `set-control-value`: `control`, and `value.control` when the value is
      itself sourced from a control.
    - `clear-control`: `scope.control`.
    - `open-overlay`: `overlayId` matches an `overlays[].id` (modals moved out
      of `pages[]` into a top-level `overlays` array 2026-08-10 --
      `pages[].type:"modal"` is no longer valid).
    - `navigate`: `target.page` matches a page `id`, `target.element` an
      element `id`.
    - `select-tab`: `tabbedContainer` names a `kind:"tabbed-container"`
      element, and `selectedTab.index` is in range of its `tabs[]`.
    - `insert-rows`/`update-rows`/`delete-rows`: `table` names a
      `kind:"input-table"` element; `values` keys match that table's column
      `id`s, and any nested `{type:"control"}` value resolves.
    - `refresh-element`: `target.element` names an element.
    - `chat.agentId` matches an `agents[].id`.

    Also walks `agents[].tools[].steps[]`, which reuses this same effect
    vocabulary with an added `kind:"effect"` sibling key per step.

    Two effects are deliberately NOT checked. `open-document`'s `document`
    and optional `target` name a page/element in a DIFFERENT workbook, whose
    ids are not in this spec and cannot be resolved from here (upstream
    `reference/specification/actions.md` notes a typo there is a silent no-op
    with nothing to validate against). `close-overlay` takes no reference.

    Note this check says nothing about whether an effect is ACCEPTED. A clean
    run here means the references are internally consistent, not that the spec
    publishes. (`insert-rows` was long recorded here as rejected outright;
    that was a wrong field name, retracted 2026-09-15 -- see
    docs/elements-known-good.md -> "input-table, and insert-rows".)
    """
    issues = []
    all_elements = _all_elements(spec)
    control_ids = _collect_control_ids(spec)
    elements_by_id = {el.get("id"): el for _, el in all_elements if el.get("id")}
    overlay_ids = {o.get("id") for o in (spec.get("overlays") or []) if o.get("id")}
    all_page_ids = {p.get("id") for p in spec.get("pages", [])}
    agent_ids = {a.get("id") for a in (spec.get("agents") or []) if a.get("id")}

    def _check_control(label: str, cid, loc: str):
        if cid and cid not in control_ids:
            issues.append((
                "fail",
                f"{loc}: {label} `{cid}` does not match any `controlId` in the spec. "
                "The effect will silently no-op."
            ))

    def _check_element(label: str, eid, loc: str):
        if eid and eid not in elements_by_id:
            issues.append((
                "fail",
                f"{loc}: {label} `{eid}` does not match any element `id` in the spec. "
                "The effect will silently no-op."
            ))

    def _check_effect(fx: dict, loc: str):
        if not isinstance(fx, dict):
            return
        effect = fx.get("effect")
        loc = f"{loc} ({effect})"

        if effect == "set-control-value":
            _check_control("target control", fx.get("control"), loc)
            value = fx.get("value") or {}
            if isinstance(value, dict) and value.get("type") == "control":
                _check_control("source control", value.get("control"), loc)

        elif effect == "clear-control":
            scope = fx.get("scope") or {}
            if isinstance(scope, dict) and scope.get("type") == "control":
                _check_control("scope control", scope.get("control"), loc)

        elif effect == "open-overlay":
            overlay_id = fx.get("overlayId")
            if overlay_id and overlay_id not in overlay_ids:
                issues.append((
                    "fail",
                    f"{loc}: overlayId `{overlay_id}` does not match any "
                    "`overlays[].id`. The overlay will silently fail to open."
                ))

        elif effect == "navigate":
            target = fx.get("target") or {}
            page_id = target.get("page")
            if page_id and page_id not in all_page_ids:
                issues.append((
                    "fail",
                    f"{loc}: target.page `{page_id}` does not match any page `id`. "
                    "The navigation will silently no-op."
                ))
            _check_element("target.element", target.get("element"), loc)

        # `refresh-element`'s target is element-only -- it does NOT accept
        # `navigate`'s {type:page} variant, despite the shared field name.
        elif effect == "refresh-element":
            _check_element("target.element", (fx.get("target") or {}).get("element"), loc)

        elif effect == "select-tab":
            tc_id = fx.get("tabbedContainer")
            tc_el = elements_by_id.get(tc_id)
            if tc_id and (tc_el is None or tc_el.get("kind") != "tabbed-container"):
                issues.append((
                    "fail",
                    f"{loc}: tabbedContainer `{tc_id}` does not match any "
                    "`kind:\"tabbed-container\"` element. The tab switch will silently no-op."
                ))
            elif tc_el is not None:
                idx = (fx.get("selectedTab") or {}).get("index")
                n_tabs = len(tc_el.get("tabs") or [])
                if isinstance(idx, int) and not (0 <= idx < n_tabs):
                    issues.append((
                        "fail",
                        f"{loc}: selectedTab.index {idx} is out of range for "
                        f"`{tc_id}`, which has {n_tabs} tab(s) (valid: 0-{n_tabs - 1})."
                    ))

        # `update-rows` was not in the original check; actions.md lists it
        # beside the other two with the same `table` field and the same
        # silent-no-op failure, so it is checked identically here.
        elif effect in ("insert-rows", "update-rows", "delete-rows"):
            # The field is `tableElementId`. This check asked for `table` from
            # the day it was written, so `table_id` was always None and the
            # whole branch below never ran -- it reported clean on every
            # row-mutating effect ever validated. `table` is not a legacy
            # alias either: Sigma silently drops field names it does not know,
            # so an effect naming `table` has no target at all and no-ops.
            table_id = fx.get("tableElementId")
            if table_id is None and fx.get("table"):
                issues.append((
                    "fail",
                    f"{loc}: the target field is `tableElementId`, not `table`. "
                    "Sigma drops unknown field names silently, so this effect "
                    "publishes clean and then writes nothing."
                ))
                table_id = fx.get("table")
            table_el = elements_by_id.get(table_id)
            if table_id and (table_el is None or table_el.get("kind") != "input-table"):
                issues.append((
                    "fail",
                    f"{loc}: table `{table_id}` does not match any "
                    "`kind:\"input-table\"` element. The write will silently no-op."
                ))
            elif table_el is not None and effect in ("insert-rows", "update-rows"):
                table_col_ids = {
                    c.get("id") for c in (table_el.get("columns") or []) if c.get("id")
                }
                for col_id, val in (fx.get("values") or {}).items():
                    if col_id not in table_col_ids:
                        issues.append((
                            "fail",
                            f"{loc}: values key `{col_id}` does not match any column "
                            f"`id` on input-table `{table_id}`."
                        ))
                    if isinstance(val, dict) and val.get("type") == "control":
                        _check_control(
                            f"values[{col_id!r}] control", val.get("control"), loc
                        )

    for pi, el in all_elements:
        if not isinstance(el, dict):
            continue
        el_label = el.get("id") or "(unnamed)"
        for ai, action in enumerate(el.get("actions", []) or []):
            for fi, fx in enumerate((action or {}).get("effects", []) or []):
                loc = f"elements[{pi}] ({el_label}).actions[{ai}].effects[{fi}]"
                _check_effect(fx, loc)

        if el.get("kind") == "chat":
            agent_id = el.get("agentId")
            if agent_id and agent_id not in agent_ids:
                issues.append((
                    "fail",
                    f"elements[{pi}] ({el_label}): agentId `{agent_id}` does not "
                    "match any `agents[].id` in the spec. The chat element will render "
                    "with no agent attached."
                ))

    for gi, agent in enumerate(spec.get("agents") or []):
        agent_label = agent.get("id") or "(unnamed)"
        for ti, tool in enumerate((agent or {}).get("tools", []) or []):
            tool_label = (tool or {}).get("toolId") or "(unnamed)"
            for si, step in enumerate((tool or {}).get("steps", []) or []):
                loc = f"agents[{gi}] ({agent_label}).tools[{ti}] ({tool_label}).steps[{si}]"
                _check_effect(step, loc)

    return issues


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

        # A grouped element has two readable levels, and naming the element
        # alone silently picks the wrong one. Without `groupingId` the plugin
        # reads "All source columns" -- the ungrouped warehouse rows, capped at
        # the SDK's 25,000 -- each repeating its group's aggregate. It renders
        # 25,000 rows of one member while the same element draws a correct
        # five-row table beside it.
        #
        # Nothing else catches it: the spec is valid, the element's own SQL
        # keeps its GROUP BY, publish returns 200, and the bind harness feeds
        # the plugin rows directly so it never touches this path. Found live
        # 2026-09-12 on a plugin that had passed every other gate.
        groupings = src_el.get("groupings") or []
        if groupings and not source.get("groupingId"):
            ids = ", ".join(repr(g.get("id")) for g in groupings if isinstance(g, dict))
            out.append((
                "fail",
                f"{loc}: `config.source` names a GROUPED element ({src_id!r}) but sets no "
                f"`groupingId`, so the plugin reads that element's ungrouped source rows "
                f"instead of its groups -- every row repeating its group's aggregate, "
                f"capped at 25,000. Add \"groupingId\": <one of {ids}>."))

        # A `variable` binding carries a controlId, not a column id: it is how
        # a plugin reads and writes a workbook control. Collect them so they
        # are not mistaken for broken column bindings below.
        control_ids = {
            e.get("controlId")
            for _, e in all_elements
            if isinstance(e, dict) and e.get("kind") == "control" and e.get("controlId")
        }

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
            if key == "source":
                continue
            # Sigma's own canonical form for a control binding, which is what
            # a workbook it has normalized round-trips as.
            if isinstance(value, dict) and value.get("kind") == "control":
                cid = value.get("controlId")
                if cid not in control_ids:
                    out.append((
                        "fail",
                        f"{loc}: config binding {key!r} names control {cid!r}, which is "
                        "not the controlId of any control in this spec.",
                    ))
                continue
            if not isinstance(value, str):
                continue
            if value in control_ids:
                continue
            if value not in src_columns:
                out.append((
                    "fail",
                    f"{loc}: config binding {key!r} -> {value!r} is neither a column on "
                    f"source element {src_id!r} (kind={src_el.get('kind')!r}) nor the "
                    f"controlId of a control in this spec. "
                    f"Columns: {', '.join(sorted(src_columns))}"
                    + (f"; controls: {', '.join(sorted(c for c in control_ids if c))}"
                       if control_ids else ""),
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

def _controls_read_by_effects(effects) -> set[str]:
    """Every control an effect READS, as opposed to writes.

    A read is `{"type": "control", "control": X}` -- a value sourced from a
    control -- or a `[X]` reference inside a formula. `set-control-value`'s own
    `control` field is a bare string naming its target, which is a WRITE and
    deliberately not collected: a native button that pushes a value into a
    control the plugin also writes is not the anti-pattern this check is for.
    """
    found: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "control" and isinstance(node.get("control"), str):
                found.add(node["control"])
            if node.get("type") == "formula" and isinstance(node.get("formula"), str):
                found.update(re.findall(r"\[([^\]/]+)\]", node["formula"]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(effects or [])
    return found


def issues_plugin_owns_its_actions(spec: dict) -> list[tuple[str, str]]:
    """An action driven by a plugin must be TRIGGERED BY that plugin.

    Standing rule, set 2026-09-15 after it was got wrong on `price-swarm`:
    when a plugin is the thing the user interacts with, the action it causes
    fires from the plugin's own `action-trigger`, not from a native element
    standing in for it. A button the user has to press afterwards is not the
    interaction they asked for, and a control's `on-change` is worse -- it is
    unverified that Sigma treats a plugin's `setVariable` as the kind of change
    that fires a control action, so that wiring can publish clean, validate
    clean, and simply never fire.

    The tell is mechanical: an action whose effects READ controls the plugin
    writes is an action the plugin is driving. If its trigger is anything other
    than that plugin's `action-trigger`, the plugin is not in charge of it.

    Also checks both halves of the trigger binding, which is the failure this
    rule replaces one silent mode with another if left unvalidated:

    - a `{kind: "action-trigger"}` in a plugin's `config` with no action on that
      element naming the same `actionTriggerId` -- the plugin fires into
      nothing;
    - an action on a plugin element whose `actionTriggerId` appears in no
      config value -- nothing can ever fire it.

    Both publish with HTTP 200 and look, from the workbook, exactly like a
    plugin whose clicks do not work.
    """
    issues = []
    all_elements = _all_elements(spec)
    control_ids = _collect_control_ids(spec)

    # controlId -> plugin element id, for every control a plugin can write
    plugin_controls: dict[str, str] = {}
    # actionTriggerId -> (plugin element id, config key)
    declared_triggers: dict[str, tuple[str, str]] = {}

    for _, el in all_elements:
        if not isinstance(el, dict) or el.get("kind") != "plugin":
            continue
        pid = el.get("id") or "(unnamed)"
        for key, val in (el.get("config") or {}).items():
            if isinstance(val, dict):
                if val.get("kind") == "control" and val.get("controlId"):
                    plugin_controls[val["controlId"]] = pid
                elif val.get("kind") == "action-trigger" and val.get("actionTriggerId"):
                    declared_triggers[val["actionTriggerId"]] = (pid, key)
            # The bare form is accepted for controls too, and is what this
            # kit emitted before the object form.
            elif isinstance(val, str) and val in control_ids:
                plugin_controls[val] = pid

    if not plugin_controls and not declared_triggers:
        return issues

    wired_triggers: set[str] = set()

    for pi, el in all_elements:
        if not isinstance(el, dict):
            continue
        el_label = el.get("id") or "(unnamed)"
        is_plugin = el.get("kind") == "plugin"
        for ai, action in enumerate(el.get("actions", []) or []):
            if not isinstance(action, dict):
                continue
            trigger = action.get("trigger")
            trig_id = (trigger or {}).get("actionTriggerId") if isinstance(trigger, dict) else None
            fires_from_plugin = bool(trig_id)
            if trig_id:
                wired_triggers.add(trig_id)
                if trig_id not in declared_triggers:
                    issues.append((
                        "fail",
                        f"elements[{pi}] ({el_label}).actions[{ai}]: trigger "
                        f"`{trig_id}` appears in no plugin `config` value. Nothing "
                        "can fire this action -- bind it as "
                        f'`config.<panel name> = {{"kind": "action-trigger", '
                        f'"actionTriggerId": "{trig_id}"}}` on the plugin element.'
                    ))
                elif declared_triggers[trig_id][0] != el_label:
                    owner = declared_triggers[trig_id][0]
                    issues.append((
                        "fail",
                        f"elements[{pi}] ({el_label}).actions[{ai}]: trigger "
                        f"`{trig_id}` is declared by plugin `{owner}`, but this "
                        "action is on a different element. Sigma fires a plugin "
                        "trigger against actions on the plugin's OWN element."
                    ))

            read = _controls_read_by_effects(action.get("effects"))
            driven = sorted(c for c in read if c in plugin_controls)
            if driven and not (is_plugin and fires_from_plugin):
                owner = plugin_controls[driven[0]]
                on = trigger.get("on") if isinstance(trigger, dict) else trigger
                issues.append((
                    "fail",
                    f"elements[{pi}] ({el_label}).actions[{ai}] reads control(s) "
                    f"{', '.join('`%s`' % c for c in driven)}, which plugin "
                    f"`{owner}` writes -- so the plugin is driving this action, "
                    f"but it is triggered by `{on or 'this element'}` instead. "
                    "Move the action onto the plugin element and trigger it from "
                    "the plugin's own `action-trigger` "
                    '(`trigger: {"kind": "action-trigger", "actionTriggerId": ...}`), '
                    "so a click in the plugin is the whole interaction. "
                    "docs/plugin-api.md -> 'A plugin owns its own actions'."
                ))

    for trig_id, (pid, key) in sorted(declared_triggers.items()):
        if trig_id not in wired_triggers:
            issues.append((
                "fail",
                f"plugin `{pid}` binds an action-trigger at `config.{key}` "
                f"(`{trig_id}`) but no action on it declares that trigger. The "
                "plugin will call triggerAction() and nothing will happen."
            ))

    return issues


def main() -> None:
    args = [a for a in sys.argv[1:] if a not in ("-v", "--verbose")]
    verbose = len(args) != len(sys.argv) - 1
    if len(args) != 1:
        sys.stderr.write("usage: validate-spec.py [-v] <spec.json|spec.yaml>\n")
        sys.exit(2)
    sys.argv = [sys.argv[0], args[0]]
    spec = _load_spec(args[0])

    root = _parse_layout(spec.get("layout", ""))

    all_issues: list[tuple[str, str, str]] = []
    for tag, fn in [
        ("schema-version",            lambda: issues_schema_version(spec)),
        ("no-per-page-layout",        lambda: issues_per_page_layout(spec)),
        ("elements-placed-in-layout", lambda: issues_elements_placed(spec, root)),
        ("layoutelement-has-children", lambda: issues_layoutelement_has_children(root)),
        ("column-format-shape",       lambda: issues_column_format_shape(spec)),
        ("bare-ref-resolution",       lambda: issues_bare_ref_resolution(spec)),
        ("action-refs-resolve",       lambda: issues_action_refs_resolve(spec)),
        ("plugin-refs-resolve",       lambda: issues_plugin_refs_resolve(spec)),
        ("plugin-owns-its-actions",   lambda: issues_plugin_owns_its_actions(spec)),
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
        # One line. The limitations paragraph below is real and worth reading
        # once, but printed on every clean run it is five lines of prose in
        # front of the next command -- so it waits for -v, or for a failure,
        # where it is actually load-bearing.
        print(f"validate-spec: all {len(CHECKS)} checks passed — {args[0]}")
        if verbose:
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
