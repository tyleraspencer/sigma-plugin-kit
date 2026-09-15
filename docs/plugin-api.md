# Sigma plugin SDK reference

`@sigmacomputing/plugin`, current **1.3.2**. Everything here is from the SDK
README and the shipped type declarations, which are authoritative — the
help-center pages document roughly a third of the API and contain several
outright errors (listed at the bottom).

Sources worth going back to:

- `https://raw.githubusercontent.com/sigmacomputing/plugin/main/packages/plugin-sdk/README.md`
- `https://unpkg.com/@sigmacomputing/plugin@1.3.2/dist/cjs/index.d.cts` — the types
- `https://github.com/sigmacomputing/sigma-sample-plugins` — official examples

## Getting the SDK

**npm, always.** Every plugin in this kit is a Vite + React project, so the SDK
is a bundled dependency and there is no script tag anywhere.

```bash
npm install @sigmacomputing/plugin react react-dom
```

```js
import { client, useConfig, useElementData } from '@sigmacomputing/plugin';
```

`client` is a **pre-initialized instance** — use it directly. Hooks are
nominally meant to be wrapped in `<SigmaClientProvider client={...}>`, but
because `client` is already initialized the sample plugins call
`client.config.configureEditorPanel([...])` at module scope and use hooks
directly with no provider. That works, and it's what the template does.

Never reach for a window global. `window.sigmaComputing.plugin.client` is a
widely-copied pattern that **no published bundle has ever defined**, and
`window.SigmaPlugin` only exists when the UMD build is loaded from a script
tag — so in a bundled plugin both read `undefined`.

### Why the UMD/script-tag route is not used here

The bundle does define a `SigmaPlugin` global with the same 17 exports, and it
is what a CDN `<script>` gives you. It was removed from this kit because its
failure mode is silent and expensive.

React is an *external* of the UMD build, and the factory calls
`React.createContext` at module top level. With no `window.React` loaded
*before* the SDK script it throws right there, before assigning anything:
`window.SigmaPlugin` is left a bare `{}` with zero keys, `SigmaPlugin.client` is
`undefined`, and a plugin that checks for a client takes its no-client branch
and renders synthetic fallback data forever. The only symptom is one uncaught
`u.createContext is not a function` in the console — nothing looks wrong in a
screenshot. Verified in a browser against 1.3.2, 2026-09-11: with React loaded
first, `window.SigmaPlugin` exposes all 17 exports and `client` is a real
object; without it, zero keys.

Bundling makes that unrepresentable, which is the whole argument for a build
step. If you are reading someone else's single-file plugin, this is the first
thing to check.

## configureEditorPanel — all 14 types

Called once, at module scope. Every entry takes `name: string` and an optional
`label: string` (`url-parameter` takes **no** `label`). The `name` is the key
the value arrives under in `config`, **and** the key a workbook spec's plugin
`config` must use. Renaming one silently unbinds every workbook using it.

| type | additional fields |
|---|---|
| `group` | — |
| `element` | — |
| `column` | `source` **(required)**, `allowMultiple` **(required)**, `allowedTypes?` |
| `text` | `source?`, `secure?`, `multiline?`, `placeholder?`, `defaultValue?` |
| `toggle` | `source?`, `defaultValue?: boolean` |
| `checkbox` | `source?`, `defaultValue?: boolean` |
| `radio` | `values` **(required)**, `source?`, `singleLine?`, `defaultValue?` |
| `dropdown` | `values` **(required)**, `source?`, `width?`, `defaultValue?` |
| `color` | `source?` |
| `variable` | `allowedTypes?: ControlType[]` |
| `interaction` | — |
| `action-trigger` | — |
| `action-effect` | — |
| `url-parameter` | name only |

`allowedTypes` is an **allowlist**, not a blocklist. The help page says
"prevent" and spells it `allowTypes`; both are wrong.

- On `column`: `ValueType[]` — `boolean`, `datetime`, `number`, `integer`,
  `text`, `variant`, `link`, `error`.
- On `variable`: `ControlType[]` — `boolean`, `date`, `number`, `text`,
  `text-list`, `number-list`, `date-list`, `number-range`, `date-range`.

**Grouping.** Declare `{type: 'group', name: 'Style'}`, then put children in
it with `source: 'Style'`. `source` on a scalar type points at a group; on a
`column` it points at an `element`. The help page's claim that groups only
hold `text` is contradicted by the types — toggle, checkbox, radio, dropdown
and color all accept `source` too.

**Multiple elements per plugin is normal** — declare several `element` entries
and bind columns to each by `source`.

```js
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },
  { type: 'group', name: 'Style' },
  { type: 'dropdown', name: 'palette', source: 'Style',
    values: ['Warm', 'Cool'], defaultValue: 'Warm' },
  { type: 'toggle', name: 'showLegend', source: 'Style', defaultValue: true },
  { type: 'color', name: 'accent', source: 'Style' },
  { type: 'text', name: 'apiToken', secure: true },
  { type: 'variable', name: 'selected', allowedTypes: ['text-list'] },
]);
```

`secure: true` renders a password input and withholds the value from the
pre-hydrated query string. Use it for any API token.

## The client

`client.sigmaEnv` is `'author' | 'viewer' | 'explorer'` — useful for showing
editor-only affordances. `client.destroy()` tears down.

### client.config

| method | notes |
|---|---|
| `get()` | `Partial<T> \| undefined` |
| `set(config)` | **shallow merge** (the help page's `[{key,value}]` signature is wrong) |
| `getKey(k)` / `setKey(k, v)` | |
| `subscribe(cb)` | → `Unsubscriber` |
| `configureEditorPanel(opts)` | |
| `getVariable(configId)` | a **snapshot** |
| `setVariable(configId, ...values)` | variadic — that's how list/range controls are fed |
| `subscribeToWorkbookVariable(configId, cb)` | → `Unsubscriber` |
| `getInteraction(configId)` | `WorkbookSelection[]` |
| `setInteraction(configId, elementId, selection)` | |
| `triggerAction(configId)` | |
| `registerEffect(configId, fn)` | returns an **unregister** fn |
| `setLoadingState(bool)` | |
| `getUrlParameter` / `setUrlParameter` / `subscribeToUrlParameter` | |
| `subscribeToWorkbookInteraction` | **deprecated** — use the Action API |

### client.elements

Only four, and they key by **config id** (the `name` you declared), not a raw
element id:

- `getElementColumns(configId)` → `Promise<WorkbookElementColumns>` (the help
  page files this under `client.config` — wrong)
- `subscribeToElementColumns(configId, cb)`
- `subscribeToElementData(configId, cb)` → `{[columnId]: any[]}`
- `subscribeToIncrementalElementData(configId, cb)`

**Data arrives column-keyed** — an object of parallel arrays, one per column
id, *not* an array of row objects. Zip by index.

**`useElementData` delivers up to 25,000 values.** Past that, use the
incremental API.

### client.style

`get(): Promise<PluginStyle>` and `subscribe(cb)`. `PluginStyle` has **exactly
one property: `backgroundColor`.** No fonts, no palette, no dark-mode signal.

## Hooks, and their imperative equivalents

| hook | imperative |
|---|---|
| `useConfig(key?)` | `config.get()` / `getKey` / `subscribe` |
| `useEditorPanelConfig(opts)` | `config.configureEditorPanel` |
| `useElementData(id)` | `elements.subscribeToElementData` |
| `useElementColumns(id)` | `elements.subscribeToElementColumns` |
| `useIncrementalElementData(id)` → `[data, loadMore, info]` | `elements.subscribeToIncrementalElementData` |
| `usePaginatedElementData(id)` → `[data, loadMore]` | **no direct equivalent** |
| `useVariable(id)` → `[v, set]` | `getVariable` / `setVariable` / `subscribeToWorkbookVariable` |
| `useUrlParameter(id)` → `[p, set]` | `getUrlParameter` / `setUrlParameter` / `subscribeToUrlParameter` |
| `useInteraction(id, elementId)` → `[sel, set]` | `getInteraction` / `setInteraction` |
| `useActionTrigger(id)` → `() => void` | `config.triggerAction(id)` |
| `useActionEffect(id, fn)` | `config.registerEffect(id, fn)` |
| `useLoadingState(init)` → `[bool, set]` | `config.setLoadingState` |
| `usePluginStyle()` | `client.style.get()` / `subscribe` |
| `usePlugin()` | `client` / `initialize()` |

## Variables — reading and writing workbook controls

This is the real channel between a plugin and the rest of a workbook, and it
goes **both ways**. Declare a `variable`, and the workbook author binds a real
control to it in the editor panel.

```js
// read
const v = client.config.getVariable('selected');
// write -- variadic, so a list control takes several values
client.config.setVariable('selected', 'Texas', 'Alabama');
// react to author/viewer changes
const un = client.config.subscribeToWorkbookVariable('selected', v => { … });
```

**The gotcha:** `WorkbookVariable` is
`{ name: string, defaultValue: { type: string, value: any } }`. The **current**
value lives at `.defaultValue.value` despite the name.

```js
const current = client.config.getVariable('selected')?.defaultValue?.value;
```

This is how a plugin cross-filters a workbook: write the user's selection into
a control that other elements filter on. The lasso-map pattern —
`setVariable` on a lasso selection — is exactly this.

> **Retracted.** Earlier versions of these docs said controls could not reach
> a plugin and told you to project a constant column whose formula was a bare
> `[<controlId>]` reference, then bind that column. That workaround is
> obsolete; `variable` bindings do it properly, in both directions. Don't use
> the column trick.

## Actions

### A plugin owns its own actions

**Standing rule, 2026-09-15: an action a plugin causes is triggered BY the
plugin.** Never by a button the user presses afterwards, never by the
`on-change` of a control the plugin just wrote, never by any other native
element standing in for it. "Clicking the plugin does X" means one click.

Enforced by both gates — `preflight-plugin.py` → `action-trigger-wired` and
`validate-spec.py` → `plugin-owns-its-actions` (the latter runs on every
`post`/`put`, so a delegated action will not publish).

The two alternatives, and why they are not options:

- **A control's `on-change`.** It is unverified that Sigma treats a plugin's
  `setVariable` as the kind of change that fires a control action, and the
  evidence from `price-swarm` is that it does **not**. It publishes clean,
  validates clean, and silently never fires — indistinguishable from a broken
  plugin, which is how it cost a whole debugging round.
- **A button.** It works, and it answers a question nobody asked. A click that
  needs a second click somewhere else is a different feature.

Both halves of the binding live in the spec and must name the same id, which is
yours to invent (22 chars, base62, round-trips unchanged):

```json
"config": { "onPick": {"kind": "action-trigger", "actionTriggerId": "<ID>"} },
"actions": [{ "id": "act-pick",
              "trigger": {"kind": "action-trigger", "actionTriggerId": "<ID>"},
              "effects": [ … ] }]
```

The action goes on the **plugin element** — Sigma fires a plugin trigger
against actions on the plugin's own element. Harvested from the "OnLoad" plugin
in papercrane (workbooks `43642ae4`, `57ba996f`), not guessed. Worked example:
`docs/examples/price-swarm-queue.py`.

**Plugin as trigger.** Declare `{type: 'action-trigger', name: 'onBarClick'}`,
then call `client.config.triggerAction(config.onBarClick)` from your UI. The
callback takes **no payload** — the workbook author wires up what happens. To
convey *what* was clicked, write a `variable` first, then trigger: the writes
and the trigger are separate postMessages, a host applies them in the order
posted, so fields-then-trigger is what lets the effects read the click.

`triggerAction` takes the **config value**, not the panel name, and the host
delivers that value as the bare `actionTriggerId` string — `validateConfigId`
only `console.warn`s on `undefined`, so a wrong argument fails silently.

**Plugin as target.** Declare `{type: 'action-effect', name: 'refresh'}` and
register a handler:

```js
const unregister = client.config.registerEffect(config.refresh, () => { … });
```

The effect receives **no arguments** and returns nothing, so pair it with a
`variable` or `interaction` binding to learn the context. In the workbook:
Actions tab → **Trigger plugin** → pick the target plugin → **Select effect**.

Declaration is the whole registration — there's no manifest.

**Don't make one plugin both the trigger and the target of the same action.**
The docs warn it loops.

## Interactions / selection

`{type: 'interaction', name: 'sel'}` plus
`getInteraction(configId)` / `setInteraction(configId, elementId, selection)`.
`WorkbookSelection` is `Record<string, {type: string, val?: unknown}>` — a
column-keyed selection map. `subscribeToWorkbookInteraction` is deprecated in
favour of the Action API.

## Loading, sizing, errors

- **Loading:** `useLoadingState(true)`, or `config.setLoadingState(false)` when
  your first render completes.
- **Sizing: not covered by any documentation.** No resize event, no auto-height,
  no size in `config`. The host sizes the iframe from the workbook element and
  tells you nothing about it.

  The rule this kit holds plugins to: **fill the whole iframe at any size, and
  re-lay-out when it changes.** The author sizes and resizes the element at
  will, so the only sizes a plugin can be correct at are *all of them*.

  - `html, body, #root { height: 100% }`; root container `width:100%;height:100%`.
  - No fixed `px` on the root/chart/canvas, and no `vh`/`vw` — the iframe is the
    viewport only by accident. Size from the parent box.
  - Flex or grid, with `min-height: 0` on children that must shrink or scroll;
    without it a flex child won't go below its content height and the plugin
    overflows rather than fits.
  - Overflow goes in an internal scroll region, never an iframe scrollbar.
  - The first measurement is often `0` — the iframe mounts before the element
    settles. Render from whatever you measure now; re-render on change.

  Measure with a `ResizeObserver` on `document.body` and **compare the
  dimensions before re-rendering** — the callback rewrites DOM inside the
  observed node, so an unguarded one loops forever (`preflight-plugin.py`'s
  `resize-observer-guard` fails it; the template has the shape to copy).

  **Attach the observer from a callback ref, not a mount effect** — the trap
  that shipped in `price-swarm` and was found by the author, not by a gate. A
  plugin that renders an empty state until its rows arrive has no chart node
  when a `useEffect(..., [])` runs, so the effect measures nothing and observes
  only `document.body`. The chart node then mounts when the data lands, `body`
  never changes size, no callback ever fires, and the plugin sits **blank until
  the author resizes the window** — the one event that does resize `body`.
  Re-measure whenever the node appears, and read again on the next frame and
  once more after a beat, since the first sample inside a new iframe is `0`.

  `verify-plugin-binding.py` **hides this**: its `fills-frame` probe resizes the
  frame, which is exactly the event the broken version needs, so the harness
  renders and passes. To reproduce Sigma's conditions, stub out the `probeFill`
  call in a copy of the generated harness and check an `<svg>` appears with no
  resize at all. Worth making the harness assert that on its own.
  Library-side: Recharts `<ResponsiveContainer>`, Plotly `responsive: true` +
  `layout.autosize` + `useResizeHandler`, ECharts `chart.resize()` on the tick,
  D3/canvas re-read `clientWidth`/`clientHeight` and re-scale every tick.
- **Errors and empty data: not covered either.** There's no error channel, so
  render your own empty state. The one documented error semantic is in
  incremental delivery: a failed eval arrives as a `null` payload that
  terminates the stream, and the host must restart at offset 0.
- **Incremental caveat:** on hosts without incremental support `isComplete`
  stays `false` forever — never drive an auto-load loop off it; watch
  `rowCount` for progress instead. Don't mix `subscribeToElementData` and
  `subscribeToIncrementalElementData` on the same config element.
- `PluginConfig<T>` carries `screenshot: boolean` in the types with no prose
  anywhere. It looks like a render-for-export flag; **that's an inference**,
  not documented.

## Local development

Register a `devUrl` (defaults to **`http://localhost:5173`**, Vite's port) or
use the pre-registered dev playground. In a workbook: the element's **•••**
menu → **Point to Development URL** → enter it → Confirm. Changes hot-reload
without a page refresh, but changing editor-panel *options* means re-entering
the panel values.

The SDK README still says the playground uses port 3000; the help docs and the
REST API both say 5173. Trust 5173.

## Known help-center errors

Worth knowing so you don't "fix" correct code to match a bad doc:

| Page says | Actually |
|---|---|
| `allowTypes`, and it *prevents* types | `allowedTypes`, and it *allows* them |
| Groups contain only `text` children | toggle/checkbox/radio/dropdown/color take `source` too |
| `config.set([{key, value}])` | `set(partialConfig)`, shallow-merged |
| `getElementColumns` is on `client.config` | it's on `client.elements` |
| 5-ish editor panel types | 14 |
| Dev playground on port 3000 (SDK README) | 5173 |
