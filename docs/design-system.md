# Designing a plugin: Sigma's design system in React

A plugin is an iframe inside a workbook page. It should read as **another Sigma
element**, not as a web app someone embedded. This file is the visual half of
that: the token set and chart conventions Sigma's own workbook surfaces use,
plus the five product design principles restated as things you do in `App.jsx`.

The mechanical rules — fill the iframe, guard the `ResizeObserver`, never touch
a window global — are in `docs/plugin-api.md` → "Loading, sizing, errors". This
file assumes those and only covers what the plugin *looks* like.

**The constraint that shapes everything below: a plugin cannot read the
workbook theme.** `client.style.get()` returns exactly one property,
`backgroundColor` — no palette, no fonts, no dark-mode signal
(`docs/plugin-api.md` → "client.style"). So the tokens here are hardcoded in
the plugin, and anything the author must be able to change is an editor-panel
`color` field instead. Don't go looking for a theme API; there isn't one.

## Tokens

The same set Sigma's workbook design system uses. `plugins/_react-template` and
every archetype already declare it at module scope; keep it there, reference
`T.*` everywhere, and leave no loose hex in JSX.

```js
const T = {
  canvas:   '#f4f5f8',  // page behind the card — a plugin rarely paints this
  card:     '#ffffff',  // the surface the plugin sits on
  ink:      '#0f172a',  // primary values, headings
  body:     '#475569',  // labels, secondary text
  muted:    '#7c8698',  // metadata, captions, counts
  hairline: '#e5e8ef',  // borders, gridlines, empty track fill
  accent:      '#274690',  // the single measure, the primary action
  accentTint:  '#eaf0fb',  // callout fill, palest tier of a ramp
  accentEdge:  '#d8e0f8',  // border on a tint fill; the dot color the tint can't be
  good:     '#0f8a5f',  goodTint: '#edf5ea',
  bad:      '#b42318',  badTint:  '#fdecec',
  warn:     '#b45309',  warnTint: '#fdf1e3',  // spare — at-risk, and the demo badge
  context:  '#cbd5e1',  // comparison / prior-period series
  dark:     '#0f172a',  // the one loud surface, if there is one
  onDark:   '#ffffff',  mutedOnDark: '#9aa4bf',
};
```

- **If the user names brand colors, replace `accent` and its tint pair only.**
  The neutrals and the semantic pair stay. A plugin wearing someone's full brand
  palette stops reading as part of the workbook — and the neutrals are what make
  dense data legible, not brand expression.
- **Don't paint a card.** The workbook already drew one: the element sits in a
  white surface with its own border and radius. A plugin that renders its own
  white rounded panel produces a card inside a card. Default the root to
  transparent, and honor `client.style.backgroundColor` when it's set.
- `warn` is genuinely spare. Three semantic colors in one small chart is noise.

## Type

One family (the host's default sans is correct — don't load a webfont into an
iframe for chrome). Hierarchy comes from size, weight, and color. Sigma's page
type scale governs *workbook text elements*; inside a plugin, everything is
chart furniture and runs smaller:

| Role | Size / weight | Color |
|---|---|---|
| Card label — CAPS, short, at most one per plugin | 13 / 700, `letterSpacing: .04em` | `body` |
| The value the user came to read (KPI figure) | 28–40 / 700, `tabular-nums` | `ink` |
| Data value in a row or table | 11–12 / 600, `tabular-nums` | `ink` |
| Axis and row labels | 11 / 400 | `body` |
| Caption, count, footnote | 9–10 / 400 | `muted` |

- **`fontVariantNumeric: 'tabular-nums'` on every column of numbers.** Not
  optional — proportional digits make a column of figures unscannable.
- **Left-align text, right-align numbers.** No exceptions in anything tabular.
- CAPS is for short labels only. A capitalized 30-character category name is
  slower to read, not more official.
- The value dominates its label. If the label is as loud as the number, the
  label is wrong — drop its weight, its size, or its color, in that order.

## Charts

These come from how Sigma builds charts in-app. A plugin that follows them
sits next to a native chart without looking foreign.

- **No chart title inside the plugin.** The workbook draws one above the
  element already — `scripts/build-plugin-workbook.py` emits a bold `txt-title`
  text element directly above every generated plugin element, and a hand-built
  page puts the plugin in a card whose label does the same job.
  Keep at most one label inside, in the card-label role above, and only when it
  says something the page title doesn't ("Revenue, last 12 months" above a bare
  "Brand Bars").
- **One measure = one color: `accent`.** Use `good` or `bad` instead when the
  measure *is itself* a gain or a loss — retention, uptime, churn, attrition.
  Never color the bars of a single series by category "for variety"; that
  encodes nothing and reads as five meanings.
- **A comparison series is `context` grey.** Prior period, target, benchmark —
  grey behind, accent in front.
- **A categorical split only when the categories are the subject, and ≤6 of
  them.** Past six, group the tail into "Other" or switch to a ranked bar. Check
  the real cardinality before you build — a synthetic sample of four categories
  hides the twenty the warehouse column actually has.
- **Ordered tiers get one hue stepped, never unrelated colors.** Pale to deep:
  `accentTint` → `#7f95c5` → `accent`. On white, use `accentEdge` for the palest
  tier's legend dot — `accentTint` reads as nothing. For n slices or tiers,
  interpolate between `accent` `rgb(39,70,144)` and `rgb(141,161,206)` rather
  than hardcoding an array; `plugins/_archetypes/donut.jsx` has the four-line
  `sliceColor(i, n)`. Stop short of `accentTint`: as a *fill* on white it reads
  as a hole, and it has to stay clearly deeper than the `context` grey an
  "Other" bucket takes.
- **A ranking is a horizontal bar chart with a top-n cutoff**, never a sorted
  table pretending to be a chart, and never a vertical bar chart with rotated
  labels.
- **Never rotate axis labels.** If they don't fit horizontally, the chart is
  horizontal, the labels are truncated with an ellipsis and a `title` attribute,
  or the grain is wrong.
- **Every date axis is truncated to a grain and formatted** (`Feb 2026`, not
  `2026-02-01T00:00:00Z`, and not 400 daily ticks on a 300px axis).
- **Every KPI is label + value + context.** A naked number is not a KPI — give
  it a comparison, a delta, or a sparkline. At most one KPI in a plugin is a
  focus tile (reversed on `dark`); more than one and nothing is focused.
- **Gridlines are `hairline`, one axis, behind the marks.** No chart borders, no
  drop shadows, no gradients on data, no 3D anything. Ink goes to data.
- A legend earns its place only when a color encodes something the labels don't.
  One series = no legend.

## The five principles, as plugin decisions

Sigma's five product design principles, restated as what they mean inside an
iframe a few hundred pixels tall.

**1 · Focus on what matters.** The plugin exists to answer one question. Every
pixel that isn't the answer, the labels that identify it, or the control that
changes it comes out. No decorative borders or shadows, no logo, no "Powered
by", no duplicate title. Watch for: chrome that grows one prop at a time over a
few iterations.

**2 · Balance flexibility with excellent outcomes.** Author-facing options go in
`configureEditorPanel` with a **defaultValue that is already the right answer**
— the plugin must look finished before anyone opens the panel. Constrain rather
than expose: a `dropdown` of three sort orders, not a free-text sort expression;
a single `color` field, not six. Guard the inputs — clamp a top-n, `Number` and
`isFinite` every value, degrade to a readable empty state instead of rendering
`NaN`.

**3 · Unfold complexity gradually.** The first paint shows the headline. Detail
comes on hover or click — a tooltip, an expanded row, a drill panel — not
stacked into the default view. There is no room in an iframe for a settings UI:
options belong in the editor panel, where Sigma already put them.

**4 · Encourage direct data manipulation.** This is the one plugins most often
skip. If clicking a bar could filter the page, wire it: a `variable` panel entry
bound to a workbook control, `setVariable(...)` on click, `setVariable()` with
no arguments to clear (`docs/plugin-api.md` → "Variables"). Then **show the
state** — the selected mark stays at full opacity and goes bold, the others drop
to ~0.35, and the footer says what's filtered. Feedback is immediate and
reversible: clicking the selected mark again clears it.

**5 · Optimize for information density.** Compact by default. Padding is
`8–16px`, not `32px`; rows are `6px` apart, not `16px`. Whitespace creates
hierarchy, it isn't decoration. A plugin 12 rows tall that shows six bars and a
lot of air is wasting the author's grid. Support scanning: consistent alignment,
one subtle grid, a clear type hierarchy — and always give a number something to
be compared against.

## States

Every one of these renders; none of them is a blank iframe.

| State | What to render |
|---|---|
| Unbound, in the author's editor (`client.sigmaEnv === 'author'`) | one line in `muted` naming the exact fields to bind: "Bind an element, a label column and a value column." |
| Unbound, published | "No data to display." — never the author instructions |
| Bound, zero rows | the chart frame with an empty-state line, not a collapsed div |
| Loading | `useLoadingState(true)` and let Sigma draw the loading bar; don't build a spinner |
| Demo/synthetic data | a small `warn`-tinted badge, so nobody screenshots fabricated numbers thinking they're real |
| A value that isn't finite | skip the row; never render `NaN`, `Infinity`, or `undefined` |

## Accessibility

- WCAG AA contrast on every piece of text. `muted` (`#7c8698`) on white is the
  floor — it passes at 9px+ for captions and nothing lighter does.
- **Never encode meaning in color alone.** Good/bad also needs a sign, an arrow,
  or a word. Red/green as the sole differentiator fails for ~8% of male readers.
- Anything clickable gets `cursor: pointer`, a visible hover change, and a
  focus outline — don't `outline: none` a focusable element.
- Truncated text carries the full string in a `title` attribute.

## The checklist

Before a plugin ships, walk it:

1. **Clutter** — what can come out? Is there a title the page already shows?
2. **Color** — does every color encode something? Is there more than one accent?
3. **Density** — would the author fit a third more data in this frame?
4. **Direct manipulation** — can they click it? Do they see what happened?
5. **Numbers** — tabular figures, right-aligned, formatted (`4.2M`, not
   `4183920.43`)?
6. **States** — unbound, empty, loading, and a non-finite value all render.
7. **Sizes** — it still reads at 6 rows tall and at 20, narrow and full width.

## Where this comes from

- The token set, the chart conventions, and "one measure = accent" are Sigma's
  workbook design system — the same one the `sigma-workbook-quickstart` skill
  applies when building native Sigma pages. Matching it is why a plugin looks
  native.
- The five principles and the color/typography guidance are Sigma's product
  design principles (`sigma-design-principles`).
- Neither one knows about iframes; the plugin-specific consequences above — no
  card inside a card, no duplicate title, no theme API, panel fields instead of
  in-plugin settings — are this kit's.
