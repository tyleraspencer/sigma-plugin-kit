// Archetype: conversion funnel.
//
// Stages in the order the data gives them, each a centred bar whose width is
// its share of the first stage, with step-to-step conversion between them.
// Copied over src/App.jsx by `new-plugin.sh <name> --from funnel`.
//
// NOTE: a funnel's stage order is data order, not value order -- so unlike the
// other archetypes this one does NOT sort by value. If the source query does
// not return the stages in sequence, sort it there.
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
  { type: 'toggle', name: 'showDropoff', source: 'Style', defaultValue: true },
  { type: 'toggle', name: 'taper', source: 'Style', defaultValue: true },

  // 'text-list' is a LIST control (what a plugin writes a selection into);
  // plain 'text' is a text INPUT box. ControlType names the control's kind,
  // not its selection mode, so a single-select list is still 'text-list'.
  // Declaring ['text'] here makes Sigma render "Invalid selection" in the
  // panel -- on a binding that otherwise works, because the spec binds by
  // controlId and bypasses the panel's picker.
  { type: 'variable', name: 'selected', allowedTypes: ['text-list'] },
]);

const DEMO = [
  ['Viewed product', 48200], ['Added to cart', 21400], ['Started checkout', 12800],
  ['Entered payment', 9100], ['Purchased', 7650],
].map(([label, value]) => ({ label, value }));

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

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
  const stages = rows ?? (isDemo ? DEMO : []);

  // Re-render on a real size change only. An unguarded observer whose callback
  // re-renders into the observed element loops forever with an empty console.
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

  const accent = config.accent || '#2563eb';
  const top = stages.length ? stages[0].value : 0;
  // Below this the per-stage caption has nowhere to go; drop it rather than
  // letting it overlap the bar.
  const compact = size.h > 0 && size.h / Math.max(stages.length, 1) < 44;

  const pick = (label) => {
    if (!config.selected) return;
    if (String(label) === String(selected)) setVariable();
    else setVariable(String(label));
  };

  if (!stages.length) {
    return (
      <div style={S.hint}>
        {client.sigmaEnv === 'author'
          ? 'Bind an element, a stage column and a value column in the editor panel.'
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

      <div style={S.stages}>
        {stages.map((s, i) => {
          const isSel = selected != null && String(s.label) === String(selected);
          const ofTop = top ? (s.value / top) * 100 : 0;
          // taper off gives every stage full width, so narrow frames stay legible
          const width = config.taper === false ? 100 : Math.max(ofTop, 8);
          const prev = i > 0 ? stages[i - 1].value : null;
          const step = prev ? (s.value / prev) * 100 : null;
          return (
            <div
              key={`${s.label}-${i}`}
              onClick={() => pick(s.label)}
              style={{ ...S.stage, opacity: selected != null && !isSel ? 0.35 : 1 }}
            >
              <div style={S.barRow}>
                <div
                  style={{
                    ...S.bar,
                    width: `${width}%`,
                    background: accent,
                    outline: isSel ? `2px solid ${accent}` : 'none',
                    outlineOffset: 2,
                  }}
                >
                  <span style={S.barLabel} title={s.label}>{s.label}</span>
                  <span style={S.barValue}>{fmt(s.value)}</span>
                </div>
              </div>
              {!compact && (
                <div style={S.caption}>
                  {ofTop.toFixed(1)}% of first
                  {config.showDropoff !== false && step != null &&
                    ` · ${step.toFixed(1)}% from previous`}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div style={S.legend}>
        {stages.length} stages · overall{' '}
        {top ? ((stages[stages.length - 1].value / top) * 100).toFixed(1) : '0.0'}%
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
  // minHeight 0 so the stages compress instead of overflowing the iframe.
  stages: { flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column',
            justifyContent: 'space-evenly', gap: 4, overflow: 'hidden' },
  stage: { cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 2, minHeight: 0 },
  barRow: { display: 'flex', justifyContent: 'center' },
  bar: { height: 22, borderRadius: 3, display: 'flex', alignItems: 'center',
         justifyContent: 'space-between', padding: '0 8px', color: '#fff',
         fontSize: 11, fontWeight: 600, minWidth: 0, transition: 'width .3s ease' },
  barLabel: { whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginRight: 8 },
  barValue: { fontVariantNumeric: 'tabular-nums', flex: '0 0 auto' },
  caption: { fontSize: 9, color: '#9ca3af', textAlign: 'center', fontVariantNumeric: 'tabular-nums' },
  legend: { flex: '0 0 auto', fontSize: 9, color: '#9ca3af' },
  hint: { height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center',
          textAlign: 'center', fontSize: 11, color: '#9ca3af', padding: '0 24px', lineHeight: 1.6 },
};
