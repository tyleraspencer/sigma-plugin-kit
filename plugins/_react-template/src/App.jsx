import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  client,
  useConfig,
  useElementData,
  useVariable,
  useLoadingState,
  usePluginStyle,
} from '@sigmacomputing/plugin';

// Sigma design tokens -- docs/design-system.md. A plugin reads as another
// Sigma element only when it uses these; no loose hex below this line. Brand
// colors replace `accent` and its tints only -- the neutrals never move.
const T = {
  card: '#ffffff',
  ink: '#0f172a', body: '#475569', muted: '#7c8698', hairline: '#e5e8ef',
  accent: '#274690', accentTint: '#eaf0fb', accentEdge: '#d8e0f8',
  good: '#0f8a5f', bad: '#b42318', warn: '#b45309', warnTint: '#fdf1e3',
  context: '#cbd5e1',
};

// Declared once, at module scope -- not inside the component. Each `name` is
// the key the value arrives under in `config` AND the key a workbook spec's
// plugin `config` must use, so renaming one silently unbinds every workbook
// already using it.
//
// All 14 available types are in docs/plugin-api.md. Note `column` requires
// both `source` and `allowMultiple`, and `allowedTypes` is an ALLOWLIST.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },

  { type: 'group', name: 'Style' },
  { type: 'color', name: 'accent', source: 'Style' },
  { type: 'dropdown', name: 'sort', source: 'Style',
    values: ['Value, high to low', 'Value, low to high', 'Label A-Z'],
    defaultValue: 'Value, high to low' },
  { type: 'toggle', name: 'showTotal', source: 'Style', defaultValue: true },

  // Two-way channel to a workbook control: the author binds a real control,
  // clicking a bar writes into it, and other elements can filter on it.
  // 'text-list' is a LIST control (what a plugin writes a selection into);
  // plain 'text' is a text INPUT box. ControlType names the control's kind,
  // not its selection mode, so a single-select list is still 'text-list'.
  // Declaring ['text'] here makes Sigma render "Invalid selection" in the
  // panel -- on a binding that otherwise works, because the spec binds by
  // controlId and bypasses the panel's picker.
  { type: 'variable', name: 'selected', allowedTypes: ['text-list'] },
]);

const DEMO = ['North', 'South', 'East', 'West', 'Central', 'Coastal', 'Mountain', 'Valley']
  .map((label, i) => ({ label, value: Math.round(4200 * Math.exp(-i * 0.28) + 180 * ((i * 7) % 5)) }));

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

export default function App() {
  const config = useConfig();

  // Data arrives KEYED BY COLUMN ID -- an object of parallel arrays, not row
  // objects. Delivers up to 25,000 values; past that use
  // useIncrementalElementData.
  const sigmaData = useElementData(config.source);

  const [variable, setVariable] = useVariable(config.selected);
  const [, setLoading] = useLoadingState(true);
  // Fill the root. An element hosting a plugin is NOT given the white surface
  // a table or a chart sits on -- the iframe is transparent over the page
  // canvas, so an unpainted root lets the editor's grid guides show through
  // the chart. `backgroundColor` is the only property client.style carries.
  // Filling is not the same as drawing a card: no border, radius or shadow.
  const surface = usePluginStyle()?.backgroundColor || T.card;

  // The CURRENT value of a variable lives at .defaultValue.value, despite the
  // name. Reading `.value` gets you undefined.
  //
  // A list control's value is an ARRAY. Take the first entry: this plugin
  // highlights one bar. Comparing the raw value used to work by accident --
  // String(['Samsung']) === 'Samsung' -- and then quietly matched nothing the
  // moment a second value arrived, since String(['A','B']) is 'A,B'.
  const rawSelected = variable?.defaultValue?.value ?? null;
  const selected = Array.isArray(rawSelected) ? (rawSelected[0] ?? null) : rawSelected;

  const rows = useMemo(() => {
    const labels = sigmaData?.[config.label];
    const values = sigmaData?.[config.value];
    if (!labels || !values) return null;
    const out = [];
    for (let i = 0; i < values.length; i++) {
      const v = Number(values[i]);
      if (!Number.isFinite(v)) continue;
      out.push({ label: String(labels[i] ?? '—'), value: v });
    }
    return out.length ? out : null;
  }, [sigmaData, config.label, config.value]);

  useEffect(() => { setLoading(false); }, [rows, setLoading]);

  const isDemo = !rows && !config.source;
  const data = rows ?? (isDemo ? DEMO : []);

  const sorted = useMemo(() => {
    const copy = [...data];
    if (config.sort === 'Label A-Z') copy.sort((a, b) => a.label.localeCompare(b.label));
    else if (config.sort === 'Value, low to high') copy.sort((a, b) => a.value - b.value);
    else copy.sort((a, b) => b.value - a.value);
    return copy.slice(0, 12);
  }, [data, config.sort]);

  // Sigma documents no resize event, no auto-height and no size in config --
  // yet the author resizes this element whenever they like. So the plugin
  // fills 100% of the iframe and re-lays-out off an observed size. Anything
  // sized in px/vh/vw, or laid out from the size at first paint (often 0),
  // is correct at exactly one size.
  //
  // The size comparison is load-bearing, not defensive noise. Bumping state
  // re-renders this component, which rewrites DOM *inside* the observed
  // element -- so an unguarded `new ResizeObserver(() => bump(...))` can feed
  // itself forever, pinning a CPU core with nothing in the console. Only
  // re-render when the box actually changed size.
  const wrapRef = useRef(null);
  const lastSize = useRef({ w: 0, h: 0 });
  const [, bump] = useState(0);
  useEffect(() => {
    if (!window.ResizeObserver) return;
    const ro = new ResizeObserver(() => {
      const w = document.body.clientWidth;
      const h = document.body.clientHeight;
      if (w === lastSize.current.w && h === lastSize.current.h) return;
      lastSize.current = { w, h };
      bump((n) => n + 1);
    });
    ro.observe(document.body);
    return () => ro.disconnect();
  }, []);

  const accent = config.accent || T.accent;
  const max = Math.max(...sorted.map((r) => r.value), 0) || 1;

  // setVariable is variadic -- a list control takes several values. Calling it
  // with none clears the control.
  const pick = (label) => {
    if (!config.selected) return;
    if (String(label) === String(selected)) setVariable();
    else setVariable(String(label));
  };

  if (!sorted.length) {
    return (
      <div style={{ ...S.hint, background: surface }}>
        {client.sigmaEnv === 'author'
          ? 'Bind an element, a label column and a value column in the editor panel.'
          : 'No data to display.'}
      </div>
    );
  }

  return (
    <div ref={wrapRef} style={{ ...S.wrap, background: surface }}>
      <div style={S.top}>
        <div style={S.title}>__PLUGIN_TITLE__</div>
        {isDemo && <div style={S.badge}>demo data</div>}
      </div>

      <div style={S.rows}>
        {sorted.map((r) => {
          const isSel = selected != null && String(r.label) === String(selected);
          return (
            <div
              key={r.label}
              onClick={() => pick(r.label)}
              style={{ ...S.row, opacity: selected != null && !isSel ? 0.35 : 1 }}
            >
              <div style={{ ...S.name, fontWeight: isSel ? 700 : 400 }} title={r.label}>
                {r.label}
              </div>
              <div style={S.track}>
                <div style={{ ...S.fill, width: `${(r.value / max) * 100}%`, background: accent }} />
              </div>
              <div style={S.val}>{fmt(r.value)}</div>
            </div>
          );
        })}
      </div>

      {config.showTotal !== false && (
        <div style={S.legend}>
          {sorted.length} of {data.length} · total {fmt(sorted.reduce((s, r) => s + r.value, 0))}
        </div>
      )}
    </div>
  );
}

const S = {
  wrap: { height: '100%', display: 'flex', flexDirection: 'column', padding: '12px 16px', gap: 10 },
  top: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: '0 0 auto' },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: T.body },
  badge: { fontSize: 9, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
           padding: '2px 6px', borderRadius: 3, background: T.warnTint, color: T.warn },
  rows: { flex: 1, display: 'flex', flexDirection: 'column', gap: 6, minHeight: 0, overflow: 'hidden' },
  row: { display: 'grid', gridTemplateColumns: 'minmax(60px,22%) 1fr auto', gap: 8,
         alignItems: 'center', fontSize: 11, cursor: 'pointer' },
  name: { color: T.body, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  track: { background: T.hairline, borderRadius: 3, height: 14, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 3, transition: 'width .25s ease' },
  val: { color: T.ink, fontVariantNumeric: 'tabular-nums', fontWeight: 600 },
  legend: { flex: '0 0 auto', fontSize: 9, color: T.muted },
  hint: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', fontSize: 11, color: T.muted, padding: '0 24px', lineHeight: 1.6 },
};
