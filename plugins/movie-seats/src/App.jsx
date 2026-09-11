import React, { useEffect, useMemo, useState } from 'react';
import {
  client,
  useConfig,
  useElementData,
  useVariable,
  useLoadingState,
} from '@sigmacomputing/plugin';

// Declared once, at module scope. Each `name` is the key the value arrives
// under in `config` AND the key a workbook spec's plugin `config` must use, so
// renaming one silently unbinds every workbook already using it.
//
// `seat` is first on purpose: it is both what the seat shows and what gets
// written into the workbook control, so it is the binding everything else
// hangs off.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'seat', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'seatRow', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'seatNumber', source: 'source', allowMultiple: false,
    allowedTypes: ['integer', 'number'] },
  { type: 'column', name: 'section', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'status', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'price', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },

  // The two-way channel to a workbook control. Bind this to a multi-select
  // list control: clicking a seat writes every selected seat id into it, and
  // clearing the control clears the map. Nothing else in the SDK can move a
  // value from a plugin into the rest of a workbook.
  { type: 'variable', name: 'selectedSeats', allowedTypes: ['text-list'] },

  { type: 'group', name: 'Style' },
  { type: 'color', name: 'accent', source: 'Style' },
  { type: 'dropdown', name: 'maxSeats', source: 'Style',
    values: ['No limit', '2', '4', '6', '8'], defaultValue: 'No limit' },
  { type: 'toggle', name: 'showPrices', source: 'Style', defaultValue: true },
  { type: 'toggle', name: 'aisles', source: 'Style', defaultValue: true },
  { type: 'text', name: 'screenLabel', source: 'Style', placeholder: 'SCREEN' },
]);

// Section tints, applied in declaration order to whatever section names the
// data actually carries -- so this works for "Orchestra/Mezzanine" just as
// well as for the sections in seats.csv.
const TINTS = ['#38bdf8', '#a78bfa', '#34d399', '#fb7185', '#facc15', '#f97316'];

// Rendered only when nothing is bound, and badged as demo data, because a
// fallback that isn't labelled is indistinguishable from success.
const DEMO = (() => {
  const plan = [['A', 10, 'Recliner', 24.5], ['B', 12, 'Premium', 19.5],
                ['C', 12, 'Premium', 19.5], ['D', 14, 'Standard', 15],
                ['E', 14, 'Standard', 15], ['F', 12, 'Balcony', 12]];
  const out = [];
  plan.forEach(([letter, count, section, price], r) => {
    for (let n = 1; n <= count; n++) {
      out.push({
        seat: `${letter}${n}`, seatRow: letter, seatNumber: n, section, price,
        sold: (n * 7 + r * 5) % 9 < 2,
      });
    }
  });
  return out;
})();

const money = (v) => `$${(Number(v) || 0).toFixed(2)}`;

const isSold = (v) => {
  const s = String(v ?? '').trim().toLowerCase();
  return s === 'sold' || s === 'taken' || s === 'unavailable' || s === 'booked'
    || s === 'false' || s === 'no';
};

export default function App() {
  const config = useConfig();

  // Data arrives KEYED BY COLUMN ID -- an object of parallel arrays, not row
  // objects. Zip by index.
  const sigmaData = useElementData(config.source);

  const [variable, setVariable] = useVariable(config.selectedSeats);
  const [, setLoading] = useLoadingState(true);

  // Local mirror, so the map still works while an author is building the
  // workbook and has not bound a control yet.
  const [local, setLocal] = useState([]);

  const seats = useMemo(() => {
    const ids = sigmaData?.[config.seat];
    if (!ids || !ids.length) return null;
    const rowsCol = sigmaData?.[config.seatRow];
    const numsCol = sigmaData?.[config.seatNumber];
    const sectCol = sigmaData?.[config.section];
    const statCol = sigmaData?.[config.status];
    const priceCol = sigmaData?.[config.price];
    const out = [];
    for (let i = 0; i < ids.length; i++) {
      const id = String(ids[i] ?? '').trim();
      if (!id) continue;
      // Both row and number fall back to parsing the id, so a one-column
      // binding ("A12") still draws a sensible house.
      const parsed = /^([A-Za-z]+)\s*-?\s*(\d+)$/.exec(id);
      out.push({
        seat: id,
        seatRow: String(rowsCol?.[i] ?? parsed?.[1] ?? '?'),
        seatNumber: Number(numsCol?.[i] ?? parsed?.[2] ?? i + 1),
        section: sectCol?.[i] == null ? '' : String(sectCol[i]),
        price: priceCol?.[i] == null ? null : Number(priceCol[i]),
        sold: statCol ? isSold(statCol[i]) : false,
      });
    }
    return out.length ? out : null;
  }, [sigmaData, config.seat, config.seatRow, config.seatNumber,
      config.section, config.status, config.price]);

  const isDemo = !seats;
  const data = seats ?? DEMO;
  useEffect(() => { setLoading(false); }, [seats, setLoading]);

  // The CURRENT value of a variable lives at .defaultValue.value, despite the
  // name. A text-list control hands back an array; a single-value control
  // hands back a scalar.
  const bound = !!config.selectedSeats;
  const fromControl = variable?.defaultValue?.value;
  const controlSeats = useMemo(() => {
    if (!bound) return null;
    const raw = Array.isArray(fromControl) ? fromControl
      : fromControl == null || fromControl === '' ? [] : [fromControl];
    return raw.map((v) => String(v));
  }, [bound, fromControl]);

  // Render from the local list and let the control push into it, rather than
  // rendering the control's value directly. A write goes out and comes back
  // through the workbook, so rendering the round trip makes a fast second
  // click read a stale selection and drop the first seat. The effect keeps
  // the two in step whenever the control changes for any other reason --
  // someone editing it by hand, a reset action, a page load.
  const controlKey = controlSeats ? controlSeats.join('\u0000') : null;
  useEffect(() => {
    if (controlKey == null) return;
    setLocal(controlKey === '' ? [] : controlKey.split('\u0000'));
  }, [controlKey]);

  const selected = local;
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const limit = config.maxSeats && config.maxSeats !== 'No limit'
    ? Number(config.maxSeats) : Infinity;

  const commit = (next) => {
    setLocal(next);
    // setVariable is variadic -- that is how a list control takes several
    // values, and calling it with none clears the control.
    if (bound) setVariable(...next);
  };

  const toggle = (seat) => {
    if (seat.sold) return;
    if (selectedSet.has(seat.seat)) {
      commit(selected.filter((s) => s !== seat.seat));
    } else {
      const next = selected.length >= limit
        ? [...selected.slice(1), seat.seat]   // oldest out, newest in
        : [...selected, seat.seat];
      commit(next);
    }
  };

  // Rows in first-appearance order, seats sorted along each row.
  const rows = useMemo(() => {
    const byRow = new Map();
    data.forEach((s) => {
      if (!byRow.has(s.seatRow)) byRow.set(s.seatRow, []);
      byRow.get(s.seatRow).push(s);
    });
    return [...byRow.entries()].map(([letter, list]) => ({
      letter,
      seats: [...list].sort((a, b) => a.seatNumber - b.seatNumber),
    }));
  }, [data]);

  const sections = useMemo(() => {
    const map = new Map();
    data.forEach((s) => {
      if (s.section && !map.has(s.section)) {
        map.set(s.section, { name: s.section, price: s.price,
                             tint: TINTS[map.size % TINTS.length] });
      }
    });
    return [...map.values()];
  }, [data]);
  const tintOf = (name) => sections.find((x) => x.name === name)?.tint || '#38bdf8';

  const accent = config.accent || '#fbbf24';
  const widest = Math.max(...rows.map((r) => r.seats.length), 1);
  const aisles = config.aisles !== false;
  const gutters = aisles ? 2 : 0;
  const total = selected.reduce((sum, id) => {
    const s = data.find((x) => x.seat === id);
    return sum + (s?.price || 0);
  }, 0);

  // Every dimension is in vw: the plugin owns its whole iframe, so 1vw is 1%
  // of the element's width and the house scales with the element instead of
  // needing a resize observer.
  const seatStyle = (s) => {
    const on = selectedSet.has(s.seat);
    return {
      aspectRatio: '1 / 1',
      minWidth: 0,
      borderRadius: '28% 28% 18% 18%',
      border: `1px solid ${s.sold ? '#334155' : on ? accent : tintOf(s.section)}`,
      background: s.sold ? '#1b2432' : on ? accent : `${tintOf(s.section)}22`,
      color: s.sold ? '#475569' : on ? '#0b1220' : '#cbd5e1',
      cursor: s.sold ? 'not-allowed' : 'pointer',
      font: `600 clamp(5px, 0.85vw, 12px)/1 inherit`,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 0,
      boxShadow: on ? `0 0 0 2px ${accent}55` : 'none',
      transition: 'background .12s, box-shadow .12s',
    };
  };

  return (
    <div style={{
      height: '100%', overflow: 'auto', background: '#0b1220', color: '#e2e8f0',
      padding: 'clamp(8px, 1.4vw, 20px)', display: 'flex', flexDirection: 'column',
      gap: 'clamp(6px, 1vw, 14px)',
    }}>
      {/* Screen */}
      <div style={{ textAlign: 'center' }}>
        <div style={{
          height: 'clamp(6px, 0.9vw, 12px)', margin: '0 8%',
          borderRadius: '50% 50% 6px 6px / 100% 100% 6px 6px',
          background: `linear-gradient(180deg, ${accent}, ${accent}22)`,
          boxShadow: `0 0 clamp(10px, 2vw, 28px) ${accent}55`,
        }} />
        <div style={{
          marginTop: 4, fontSize: 'clamp(7px, 0.85vw, 11px)', letterSpacing: '.4em',
          color: '#94a3b8', textTransform: 'uppercase',
        }}>{config.screenLabel || 'Screen'}</div>
      </div>

      {/* House */}
      <div style={{ display: 'flex', flexDirection: 'column',
                    gap: 'clamp(2px, 0.5vw, 7px)' }}>
        {rows.map((row) => {
          const count = row.seats.length;
          const offset = Math.floor((widest - count) / 2);
          return (
            <div key={row.letter} style={{
              display: 'grid', alignItems: 'center',
              gridTemplateColumns: `clamp(10px, 1.6vw, 22px) repeat(${widest + gutters}, minmax(0, 1fr)) clamp(10px, 1.6vw, 22px)`,
              gap: 'clamp(2px, 0.45vw, 6px)',
            }}>
              <div style={{ fontSize: 'clamp(6px, 0.85vw, 11px)', color: '#64748b',
                            textAlign: 'center' }}>{row.letter}</div>
              {row.seats.map((s, i) => {
                // Two aisles: after the fourth seat and before the last four.
                const past = aisles ? (i >= 4 ? 1 : 0) + (i >= count - 4 ? 1 : 0) : 0;
                const col = 2 + offset + i + past;
                return (
                  <button
                    key={s.seat}
                    onClick={() => toggle(s)}
                    disabled={s.sold}
                    title={`${s.seat}${s.section ? ` · ${s.section}` : ''}` +
                           `${s.price != null ? ` · ${money(s.price)}` : ''}` +
                           `${s.sold ? ' · sold' : ''}`}
                    style={{ ...seatStyle(s), gridColumn: col }}
                  >{s.seat}</button>
                );
              })}
              <div style={{ fontSize: 'clamp(6px, 0.85vw, 11px)', color: '#64748b',
                            textAlign: 'center', gridColumn: widest + gutters + 2 }}>
                {row.letter}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'clamp(6px, 1.2vw, 16px)',
                    fontSize: 'clamp(7px, 0.85vw, 11px)', color: '#94a3b8',
                    alignItems: 'center' }}>
        {sections.map((sec) => (
          <span key={sec.name} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <i style={{ width: 9, height: 9, borderRadius: 3, background: `${sec.tint}44`,
                        border: `1px solid ${sec.tint}` }} />
            {sec.name}{config.showPrices !== false && sec.price != null
              ? ` · ${money(sec.price)}` : ''}
          </span>
        ))}
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <i style={{ width: 9, height: 9, borderRadius: 3, background: accent }} /> Selected
        </span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <i style={{ width: 9, height: 9, borderRadius: 3, background: '#1b2432',
                      border: '1px solid #334155' }} /> Sold
        </span>
        {isDemo && (
          <span style={{ marginLeft: 'auto', padding: '2px 7px', borderRadius: 4,
                         background: '#7f1d1d', color: '#fecaca', fontWeight: 700,
                         letterSpacing: '.08em' }}>
            DEMO DATA — bind a seat element
          </span>
        )}
      </div>

      {/* Selection summary */}
      <div style={{
        marginTop: 'auto', display: 'flex', alignItems: 'center', flexWrap: 'wrap',
        gap: 'clamp(6px, 1vw, 14px)', paddingTop: 'clamp(5px, 0.8vw, 10px)',
        borderTop: '1px solid #1e293b', fontSize: 'clamp(8px, 0.95vw, 13px)',
      }}>
        <strong style={{ color: accent }}>
          {selected.length} seat{selected.length === 1 ? '' : 's'}
        </strong>
        <span style={{ color: '#cbd5e1', flex: 1, minWidth: 0, overflow: 'hidden',
                       textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {selected.length ? selected.join(', ') : 'Click a seat to select it'}
        </span>
        {config.showPrices !== false && selected.length > 0 && (
          <span style={{ color: '#e2e8f0' }}>Total {money(total)}</span>
        )}
        {limit !== Infinity && (
          <span style={{ color: '#64748b' }}>max {limit}</span>
        )}
        {selected.length > 0 && (
          <button onClick={() => commit([])} style={{
            background: 'transparent', border: '1px solid #334155', color: '#94a3b8',
            borderRadius: 5, padding: '3px 9px', cursor: 'pointer',
            font: 'inherit', fontSize: 'clamp(7px, 0.85vw, 11px)',
          }}>Clear</button>
        )}
        {!bound && (
          <span style={{ color: '#f59e0b', fontSize: 'clamp(7px, 0.85vw, 11px)' }}>
            no control bound — selections stay in the plugin
          </span>
        )}
      </div>
    </div>
  );
}
