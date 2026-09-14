// Archetype: donut / part-to-whole.
//
// An SVG arc chart sized from the measured box, with the legend beside it on a
// wide frame and below it on a tall one. Small slices roll into "Other" so the
// ring stays readable.
// Copied over src/App.jsx by `new-plugin.sh <name> --from donut`.
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

// The first two `column` entries are the label and the value IN THIS ORDER --
// build-plugin-workbook.py binds them positionally.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },

  { type: 'group', name: 'Style' },
  // Six is the cap a composition stays readable at (docs/design-system.md ->
  // "Charts"); past it the tail belongs in "Other", which the slice logic
  // below builds. 'All' stays for the author who really does want twenty
  // wedges, but it is not on the recommended side of the list.
  { type: 'dropdown', name: 'maxSlices', source: 'Style',
    values: ['4', '5', '6', 'All'], defaultValue: '6' },
  { type: 'toggle', name: 'showLegend', source: 'Style', defaultValue: true },
  { type: 'toggle', name: 'showTotal', source: 'Style', defaultValue: true },

  // 'text-list' is a LIST control (what a plugin writes a selection into);
  // plain 'text' is a text INPUT box. ControlType names the control's kind,
  // not its selection mode, so a single-select list is still 'text-list'.
  // Declaring ['text'] here makes Sigma render "Invalid selection" in the
  // panel -- on a binding that otherwise works, because the spec binds by
  // controlId and bypasses the panel's picker.
  { type: 'variable', name: 'selected', allowedTypes: ['text-list'] },
]);

// Slices are shares of ONE total and are drawn largest-first, so they take one
// hue stepped deep-to-pale -- not ten unrelated colors, which encode a
// difference in kind that isn't there and stop being distinguishable the
// moment two land next to each other. docs/design-system.md -> "Charts".
//
// Interpolated rather than a fixed array so the ramp fits however many slices
// survive the cap. The pale end stops well short of `accentTint`: a #eaf0fb
// wedge on a white card reads as a hole in the donut, and it has to stay
// clearly deeper than the grey "Other" takes or the two last slices merge.
const RAMP_DEEP = [39, 70, 144];    // T.accent   #274690
const RAMP_PALE = [141, 161, 206];  //            #8da1ce
const sliceColor = (i, n) => {
  const t = n <= 1 ? 0 : i / (n - 1);
  const c = RAMP_DEEP.map((deep, k) => Math.round(deep + (RAMP_PALE[k] - deep) * t));
  return 'rgb(' + c[0] + ', ' + c[1] + ', ' + c[2] + ')';
};
// The tail is not a category -- grey it out of the ranking.
const OTHER_COLOR = T.context;

const DEMO = [
  ['Audio', 128400], ['Charging', 96300], ['Cables', 71200],
  ['Wearables', 54800], ['Storage', 38100], ['Accessories', 22600],
  ['Mounts', 14300], ['Adapters', 9800],
].map(([label, value]) => ({ label, value }));

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

// Arc path from two angles, in degrees clockwise from 12 o'clock.
function arc(cx, cy, rOuter, rInner, from, to) {
  const pt = (r, deg) => {
    const rad = ((deg - 90) * Math.PI) / 180;
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
  };
  // A full circle cannot be drawn as one arc -- the start and end points are
  // identical and the path collapses to nothing. Nudge it just short.
  const sweep = Math.min(to - from, 359.999);
  const large = sweep > 180 ? 1 : 0;
  const [x0, y0] = pt(rOuter, from);
  const [x1, y1] = pt(rOuter, from + sweep);
  const [x2, y2] = pt(rInner, from + sweep);
  const [x3, y3] = pt(rInner, from);
  return `M${x0},${y0} A${rOuter},${rOuter} 0 ${large} 1 ${x1},${y1} ` +
         `L${x2},${y2} A${rInner},${rInner} 0 ${large} 0 ${x3},${y3} Z`;
}

export default function App() {
  const config = useConfig();
  // Column-keyed parallel arrays, NOT row objects. Zip by index.
  const sigmaData = useElementData(config.source);
  const [variable, setVariable] = useVariable(config.selected);
  const [, setLoading] = useLoadingState(true);
  // Fill the root. An element hosting a plugin is NOT given the white surface
  // a table or a chart sits on -- the iframe is transparent over the page
  // canvas, so an unpainted root lets the editor's grid guides show through
  // the chart. `backgroundColor` is the only property client.style carries.
  // Filling is not the same as drawing a card: no border, radius or shadow.
  const surface = usePluginStyle()?.backgroundColor || T.card;

  // The CURRENT value of a variable lives at .defaultValue.value. A list
  // control's value is an ARRAY, so take the first entry -- this highlights
  // one item. Comparing the raw value works by accident for a single
  // selection (String(['A']) === 'A') and matches nothing once there are two.
  const rawSelected = variable?.defaultValue?.value ?? null;
  const selected = Array.isArray(rawSelected) ? (rawSelected[0] ?? null) : rawSelected;

  const rows = useMemo(() => {
    const labels = sigmaData?.[config.label];
    const values = sigmaData?.[config.value];
    if (!labels || !values) return null;
    const out = [];
    for (let i = 0; i < values.length; i++) {
      const v = Number(values[i]);
      if (!Number.isFinite(v) || v < 0) continue;  // negatives have no arc
      out.push({ label: String(labels[i] ?? '—'), value: v });
    }
    return out.length ? out : null;
  }, [sigmaData, config.label, config.value]);

  useEffect(() => { setLoading(false); }, [rows, setLoading]);

  const isDemo = !rows && !config.source;
  const data = rows ?? (isDemo ? DEMO : []);

  // Every dimension of this chart comes from the measured box, so the observer
  // is the layout. Guard it: its callback re-renders into the element it
  // observes, and without the comparison that is an infinite loop.
  const [size, setSize] = useState({ w: 0, h: 0 });
  const lastSize = useRef({ w: 0, h: 0 });
  useEffect(() => {
    if (!window.ResizeObserver) return;
    const ro = new ResizeObserver(() => {
      const w = document.body.clientWidth;
      const h = document.body.clientHeight;
      if (w === lastSize.current.w && h === lastSize.current.h) return;
      lastSize.current = { w, h };
      setSize({ w, h });
    });
    ro.observe(document.body);
    return () => ro.disconnect();
  }, []);

  const slices = useMemo(() => {
    const sorted = [...data].sort((a, b) => b.value - a.value);
    const cap = config.maxSlices === 'All' ? Infinity : Number(config.maxSlices || 6);
    if (!Number.isFinite(cap) || sorted.length <= cap) return sorted;
    const head = sorted.slice(0, cap);
    const rest = sorted.slice(cap).reduce((s, r) => s + r.value, 0);
    // Only worth a slice if it is actually there.
    return rest > 0 ? [...head, { label: 'Other', value: rest, isOther: true }] : head;
  }, [data, config.maxSlices]);

  const total = slices.reduce((s, r) => s + r.value, 0);

  const pick = (slice) => {
    if (!config.selected || slice.isOther) return;  // "Other" is not a member
    if (String(slice.label) === String(selected)) setVariable();
    else setVariable(String(slice.label));
  };

  if (!slices.length || total <= 0) {
    return (
      <div style={{ ...S.hint, background: surface }}>
        {client.sigmaEnv === 'author'
          ? 'Bind an element, a label column and a positive value column in the editor panel.'
          : 'No data to display.'}
      </div>
    );
  }

  // Side-by-side only when there is width to spare; otherwise stack.
  const sideways = size.w > 0 && size.w >= 420 && size.w > size.h;
  const legendOn = config.showLegend !== false;
  // First paint reports 0. Draw at a usable default and re-draw on the real
  // measurement rather than waiting for one.
  const boxW = size.w > 0 ? size.w : 320;
  const boxH = size.h > 0 ? size.h : 240;
  const chartW = legendOn && sideways ? boxW * 0.5 : boxW;
  const chartH = legendOn && !sideways ? boxH * 0.62 : boxH;
  const dim = Math.max(60, Math.min(chartW, chartH) - 44);
  const r = dim / 2;
  const inner = r * 0.58;

  let angle = 0;
  // Ramp across the real categories only -- "Other" takes grey, so counting it
  // in would waste the palest step on a wedge that never uses it.
  const nCategories = slices.filter((s) => !s.isOther).length;
  const wedges = slices.map((s, i) => {
    const sweep = (s.value / total) * 360;
    const w = { ...s, from: angle, to: angle + sweep,
                color: s.isOther ? OTHER_COLOR : sliceColor(i, nCategories) };
    angle += sweep;
    return w;
  });

  return (
    <div style={{ ...S.wrap, background: surface }}>
      <div style={S.top}>
        <div style={S.title}>__PLUGIN_TITLE__</div>
        {isDemo && <div style={S.badge}>demo data</div>}
      </div>

      <div style={{ ...S.body, flexDirection: sideways ? 'row' : 'column' }}>
        <div style={S.chart}>
          <svg width={dim} height={dim} viewBox={`0 0 ${dim} ${dim}`} role="img">
            {wedges.map((w) => {
              const isSel = selected != null && String(w.label) === String(selected);
              return (
                <path
                  key={`${w.label}-${w.from}`}
                  d={arc(r, r, r, inner, w.from, w.to)}
                  fill={w.color}
                  stroke="#fff"
                  strokeWidth="1"
                  opacity={selected != null && !isSel ? 0.3 : 1}
                  style={{ cursor: w.isOther ? 'default' : 'pointer' }}
                  onClick={() => pick(w)}
                />
              );
            })}
            {config.showTotal !== false && (
              <text x={r} y={r} textAnchor="middle" dominantBaseline="central"
                    style={S.centerText}>
                {fmt(total)}
              </text>
            )}
          </svg>
        </div>

        {legendOn && (
          <div style={{ ...S.legend, ...(sideways ? S.legendSide : S.legendBelow) }}>
            {wedges.map((w) => {
              const isSel = selected != null && String(w.label) === String(selected);
              return (
                <div
                  key={`${w.label}-legend`}
                  onClick={() => pick(w)}
                  style={{ ...S.legendRow,
                           opacity: selected != null && !isSel ? 0.4 : 1,
                           cursor: w.isOther ? 'default' : 'pointer' }}
                >
                  <span style={{ ...S.swatch, background: w.color }} />
                  <span style={S.legendLabel} title={w.label}>{w.label}</span>
                  <span style={S.legendValue}>
                    {((w.value / total) * 100).toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

const S = {
  wrap: { height: '100%', display: 'flex', flexDirection: 'column', padding: '12px 16px', gap: 8 },
  top: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: '0 0 auto' },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: T.body },
  badge: { fontSize: 9, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
           padding: '2px 6px', borderRadius: 3, background: T.warnTint, color: T.warn },
  // minHeight 0 on the flex body, or the SVG refuses to shrink and the plugin
  // spills past the iframe instead of fitting it.
  body: { flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', gap: 12 },
  chart: { flex: '0 0 auto', display: 'flex', alignItems: 'center', justifyContent: 'center' },
  centerText: { fontSize: 16, fontWeight: 700, fill: T.ink },
  legend: { display: 'flex', flexDirection: 'column', gap: 3, minHeight: 0, overflowY: 'auto' },
  legendSide: { flex: 1 },
  legendBelow: { flex: '0 0 auto', width: '100%', maxHeight: '38%' },
  legendRow: { display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 6,
               alignItems: 'center', fontSize: 11 },
  swatch: { width: 9, height: 9, borderRadius: 2, flex: '0 0 auto' },
  legendLabel: { color: T.body, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  legendValue: { color: T.ink, fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
  hint: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', fontSize: 11, color: T.muted, padding: '0 24px', lineHeight: 1.6 },
};
