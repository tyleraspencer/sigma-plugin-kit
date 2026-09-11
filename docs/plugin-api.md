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

**Single-file / UMD.** The bundle defines one global, `SigmaPlugin`, exporting
`client`, `initialize()`, `SigmaClientProvider`, every hook, and
`polyfillRequestAnimationFrame`. `SigmaPlugin.client` is a **pre-initialized
instance** — use it directly.

```html
<script src="https://unpkg.com/@sigmacomputing/plugin@1.3.2/dist/umd/sigmacomputing-plugin.umd.js"></script>
```

Pin the version in anything you register. `window.sigmaComputing.plugin.client`
is a widely-copied pattern that **no published bundle has ever defined**.

**React is an external in the UMD build.** `window.React` must exist *before*
the SDK script for any hook to work. The imperative `client` needs no React,
which is why the single-file template uses only `client`.

**npm / bundler.** `npm install @sigmacomputing/plugin`, then import `client`
and the hooks. Hooks must be wrapped in `<SigmaClientProvider client={...}>`
— except that `client` is pre-initialized, so in practice the sample plugins
call `client.config.configureEditorPanel([...])` at module scope and use hooks
directly with no provider.

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

**Plugin as trigger.** Declare `{type: 'action-trigger', name: 'onBarClick'}`,
then call `client.config.triggerAction(config.onBarClick)` from your UI. The
callback takes **no payload** — the workbook author wires up what happens. To
convey *what* was clicked, write a `variable` first, then trigger.

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
- **Sizing: not covered by any documentation.** No resize event, no auto-height.
  Use a `ResizeObserver` inside the iframe and re-layout yourself.
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
