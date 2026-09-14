// Archetype: KPI tiles.
//
// One tile per dimension member: the value big, the label small, and its share
// of the total as a bar. Column count comes from the measured width, so the
// same element reads at a quarter-width card and at full screen.
// Copied over src/App.jsx by `new-plugin.sh <name> --from kpi`.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  client,
  useConfig,
  useElementData,
  useVariable,
  useLoadingState,
} from '@sigmacomputing/plugin';

// The first two `column` entries are the label and the value IN THIS ORDER --
// build-plugin-workbook.py binds them positionally.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },

  { type: 'group', name: 'Style' },
  { type: 'color', name: 'accent', source: 'Style' },
  { type: 'text', name: 'unit', source: 'Style', defaultValue: '' },
  { type: 'toggle', name: 'showShare', source: 'Style', defaultValue: true },
  { type: 'dropdown', name: 'sort', source: 'Style',
    values: ['Value, high to low', 'Value, low to high', 'Label A-Z'],
    defaultValue: 'Value, high to low' },

  // 'text-list' is a LIST control (what a plugin writes a selection into);
  // plain 'text' is a text INPUT box. ControlType names the control's kind,
  // not its selection mode, so a single-select list is still 'text-list'.
  // Declaring ['text'] here makes Sigma render "Invalid selection" in the
  // panel -- on a binding that otherwise works, because the spec binds by
  // controlId and bypasses the panel's picker.
  { type: 'variable', name: 'selected', allowedTypes: ['text-list'] },
]);

const DEMO = [
  ['Audio', 128400], ['Charging', 96300], ['Cables', 71200],
  ['Wearables', 54800], ['Storage', 38100], ['Accessories', 22600],
].map(([label, value]) => ({ label, value }));

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

const MIN_TILE = 150;

export default function App() {
  const config = useConfig();
  // Column-keyed parallel arrays, NOT row objects. Zip by index.
  const sigmaData = useElementData(config.source);
  const [variable, setVariable] = useVariable(config.selected);
  const [, setLoading] = useLoadingState(true);

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
      if (!Number.isFinite(v)) continue;
      out.push({ label: String(labels[i] ?? '—'), value: v });
    }
    return out.length ? out : null;
  }, [sigmaData, config.label, config.value]);

  useEffect(() => { setLoading(false); }, [rows, setLoading]);

  const isDemo = !rows && !config.source;
  const data = rows ?? (isDemo ? DEMO : []);

  // Only re-render when the box really changed size -- the callback rewrites
  // DOM inside the observed element, so an unguarded observer never settles.
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

  const sorted = useMemo(() => {
    const copy = [...data];
    if (config.sort === 'Label A-Z') copy.sort((a, b) => a.label.localeCompare(b.label));
    else if (config.sort === 'Value, low to high') copy.sort((a, b) => a.value - b.value);
    else copy.sort((a, b) => b.value - a.value);
    return copy;
  }, [data, config.sort]);

  const total = sorted.reduce((s, r) => s + r.value, 0) || 1;
  const accent = config.accent || '#2563eb';
  const unit = config.unit || '';

  // Width is 0 at first paint. Fall back to a single column rather than
  // dividing by zero, and re-render once the observer reports a real width.
  const cols = size.w > 0 ? Math.max(1, Math.floor(size.w / MIN_TILE)) : 1;
  // Shrink the number as tiles get narrow, so a long value never clips.
  const valueSize = cols >= 4 ? 20 : cols === 3 ? 24 : 28;

  const pick = (label) => {
    if (!config.selected) return;
    if (String(label) === String(selected)) setVariable();
    else setVariable(String(label));
  };

  if (!sorted.length) {
    return (
      <div style={S.hint}>
        {client.sigmaEnv === 'author'
          ? 'Bind an element, a label column and a value column in the editor panel.'
          : 'No data to display.'}
      </div>
    );
  }

  return (
    <div style={S.wrap}>
      <div style={S.top}>
        <div style={S.title}>__PLUGIN_TITLE__</div>
        {isDemo && <div style={S.badge}>demo data</div>}
      </div>

      <div style={{ ...S.grid, gridTemplateColumns: `repeat(${cols}, 1fr)` }}>
        {sorted.map((r) => {
          const isSel = selected != null && String(r.label) === String(selected);
          const share = (r.value / total) * 100;
          return (
            <div
              key={r.label}
              onClick={() => pick(r.label)}
              style={{
                ...S.tile,
                borderColor: isSel ? accent : '#e5e7eb',
                opacity: selected != null && !isSel ? 0.45 : 1,
              }}
            >
              <div style={S.label} title={r.label}>{r.label}</div>
              <div style={{ ...S.value, fontSize: valueSize }}>
                {fmt(r.value)}{unit && <span style={S.unit}>{unit}</span>}
              </div>
              {config.showShare !== false && (
                <>
                  <div style={S.track}>
                    <div style={{ ...S.fill, width: `${share}%`, background: accent }} />
                  </div>
                  <div style={S.share}>{share.toFixed(1)}% of total</div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const S = {
  wrap: { height: '100%', display: 'flex', flexDirection: 'column', padding: '12px 16px', gap: 10 },
  top: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: '0 0 auto' },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: '#374151' },
  badge: { fontSize: 9, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
           padding: '2px 6px', borderRadius: 3, background: '#fef3c7', color: '#92400e' },
  // minHeight 0 lets this scroll instead of pushing the plugin past the frame.
  grid: { flex: 1, minHeight: 0, display: 'grid', gap: 10, alignContent: 'start', overflowY: 'auto' },
  tile: { border: '1px solid #e5e7eb', borderRadius: 6, padding: '10px 12px',
          display: 'flex', flexDirection: 'column', gap: 4, cursor: 'pointer',
          transition: 'opacity .2s ease, border-color .2s ease' },
  // Deliberately NOT uppercased: this is data, not chrome. Brand and SKU names
  // carry their own casing ("JBL", "iPhone") and forcing a case throws it away.
  label: { fontSize: 10, color: '#6b7280', whiteSpace: 'nowrap', overflow: 'hidden',
           textOverflow: 'ellipsis', letterSpacing: '.04em' },
  value: { fontWeight: 700, color: '#111827', fontVariantNumeric: 'tabular-nums', lineHeight: 1.1 },
  unit: { fontSize: '.5em', fontWeight: 600, color: '#6b7280', marginLeft: 3 },
  track: { background: '#f3f4f6', borderRadius: 2, height: 4, overflow: 'hidden', marginTop: 2 },
  fill: { height: '100%', borderRadius: 2, transition: 'width .25s ease' },
  share: { fontSize: 9, color: '#9ca3af', fontVariantNumeric: 'tabular-nums' },
  hint: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', fontSize: 11, color: '#9ca3af', padding: '0 24px', lineHeight: 1.6 },
};
