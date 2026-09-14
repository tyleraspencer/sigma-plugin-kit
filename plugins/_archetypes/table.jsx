// Archetype: ranked table.
//
// One dimension, one measure, sorted, with an inline heat bar per row and a
// rank number. Drops rows that do not fit rather than growing past the frame.
// Copied over src/App.jsx by `new-plugin.sh <name> --from table`.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  client,
  useConfig,
  useElementData,
  useVariable,
  useLoadingState,
} from '@sigmacomputing/plugin';

// Module scope, not inside the component. The first two `column` entries are
// the label and the value IN THIS ORDER -- build-plugin-workbook.py binds them
// positionally, so swapping them silently swaps the axes of every workbook.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'label', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },

  { type: 'group', name: 'Style' },
  { type: 'color', name: 'accent', source: 'Style' },
  { type: 'toggle', name: 'showRank', source: 'Style', defaultValue: true },
  { type: 'toggle', name: 'showBar', source: 'Style', defaultValue: true },
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
  ['Anker', 48200], ['Belkin', 39100], ['JBL', 33750], ['Logitech', 28400],
  ['Sony', 24900], ['Bose', 19850], ['Jabra', 15200], ['Razer', 11400],
].map(([label, value]) => ({ label, value }));

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

const ROW_H = 26;   // keep in sync with S.row's height
const CHROME_H = 58; // header + footer + padding

export default function App() {
  const config = useConfig();
  // Column-keyed parallel arrays, NOT row objects. Zip by index.
  const sigmaData = useElementData(config.source);
  const [variable, setVariable] = useVariable(config.selected);
  const [, setLoading] = useLoadingState(true);

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

  // Re-render on resize, but only when the box actually changed size. The
  // callback rewrites DOM inside the observed element, so an unguarded
  // observer feeds itself forever: a pinned core and an empty console.
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

  // Height is often 0 at first paint -- render from what we have and
  // re-render when the observer reports a real number.
  const fits = size.h > 0
    ? Math.max(1, Math.floor((size.h - CHROME_H) / ROW_H))
    : sorted.length;
  const shown = sorted.slice(0, fits);

  const accent = config.accent || '#2563eb';
  const max = Math.max(...shown.map((r) => r.value), 0) || 1;

  // setVariable is variadic; calling it with no arguments clears the control.
  const pick = (label) => {
    if (!config.selected) return;
    if (String(label) === String(selected)) setVariable();
    else setVariable(String(label));
  };

  if (!shown.length) {
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

      <div style={S.rows}>
        {shown.map((r, i) => {
          const isSel = selected != null && String(r.label) === String(selected);
          return (
            <div
              key={r.label}
              onClick={() => pick(r.label)}
              style={{ ...S.row, opacity: selected != null && !isSel ? 0.35 : 1 }}
            >
              {config.showRank !== false && <div style={S.rank}>{i + 1}</div>}
              <div style={{ ...S.name, fontWeight: isSel ? 700 : 400 }} title={r.label}>
                {r.label}
              </div>
              {config.showBar !== false && (
                <div style={S.track}>
                  <div style={{ ...S.fill, width: `${(r.value / max) * 100}%`, background: accent }} />
                </div>
              )}
              <div style={S.val}>{fmt(r.value)}</div>
            </div>
          );
        })}
      </div>

      <div style={S.legend}>
        {shown.length} of {sorted.length} · total {fmt(sorted.reduce((s, r) => s + r.value, 0))}
      </div>
    </div>
  );
}

const S = {
  wrap: { height: '100%', display: 'flex', flexDirection: 'column', padding: '12px 16px', gap: 8 },
  top: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', flex: '0 0 auto' },
  title: { fontSize: 13, fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: '#374151' },
  badge: { fontSize: 9, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
           padding: '2px 6px', borderRadius: 3, background: '#fef3c7', color: '#92400e' },
  // minHeight 0 is load-bearing: without it this flex child refuses to shrink
  // below its content height and the plugin overflows the iframe.
  rows: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
  row: { height: 26, display: 'grid', gridTemplateColumns: 'auto minmax(60px,26%) 1fr auto',
         gap: 8, alignItems: 'center', fontSize: 11, cursor: 'pointer' },
  rank: { width: 18, color: '#9ca3af', fontVariantNumeric: 'tabular-nums', fontSize: 10, textAlign: 'right' },
  name: { color: '#4b5563', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' },
  track: { background: '#f3f4f6', borderRadius: 3, height: 12, overflow: 'hidden' },
  fill: { height: '100%', borderRadius: 3, transition: 'width .25s ease' },
  val: { color: '#111827', fontVariantNumeric: 'tabular-nums', fontWeight: 600, minWidth: 44, textAlign: 'right' },
  legend: { flex: '0 0 auto', fontSize: 9, color: '#9ca3af' },
  hint: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', fontSize: 11, color: '#9ca3af', padding: '0 24px', lineHeight: 1.6 },
};
