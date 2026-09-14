import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  client,
  useConfig,
  useElementData,
  useVariable,
  useLoadingState,
} from '@sigmacomputing/plugin';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
// BUNDLED, not fetched. This started life as a runtime `fetch('./zcta.json')`
// from the file deployed beside the bundle -- same origin, CORS wide open, 200
// from the CDN -- and the shapes still never appeared inside Sigma, which
// serves plugins in an iframe that can restrict `connect-src`. A blocked
// fetch fails silently and every ZIP falls back to a bubble, which is
// indistinguishable from the feature not existing. 148 KB inside a bundle
// that is already 2 MB buys the whole failure class away.
import BUNDLED_ZCTA from './zcta.json';
import BUNDLED_ALIAS from './zcta-alias.json';
import BUNDLED_STATES from './states.json';
import { mapSources, mapLayers, HIT_LAYERS, OVERVIEW_LAYERS, RAMP, SURFACE } from './layers.js';

// Declared once, at module scope. Each `name` is the key the value arrives
// under in `config` AND the key a workbook spec's plugin `config` must use, so
// renaming one silently unbinds every workbook already using it.
//
// Panel order is load-bearing for the workbook generator: it takes the first
// column binding as the label and the second as the value, so zip and value
// come first even though the map reads latitude/longitude just as hard.
client.config.configureEditorPanel([
  { type: 'element', name: 'source' },
  { type: 'column', name: 'zip', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },
  { type: 'column', name: 'value', source: 'source', allowMultiple: false,
    allowedTypes: ['number', 'integer'] },
  // No latitude/longitude. The ZIP *is* the geography: every position and
  // every shape comes from the bundled ZCTA boundaries, so a coordinate pair
  // would be a second source of truth for something already determined -- and
  // one that has to be carried through every query and grouping to get here.
  { type: 'column', name: 'place', source: 'source', allowMultiple: false,
    allowedTypes: ['text'] },

  { type: 'group', name: 'Map' },
  // secure:true renders a password input and withholds the value from the
  // pre-hydrated query string. It is still a publishable pk.* token, but the
  // plugin bundle is served from a public host, so a token baked into the
  // source would be world-readable -- hence a panel field, not a constant.
  { type: 'text', name: 'mapboxToken', source: 'Map', secure: true,
    placeholder: 'pk.eyJ1Ijoi...' },
  { type: 'dropdown', name: 'basemap', source: 'Map',
    values: ['Dark', 'Light', 'Streets', 'Satellite', 'Navigation night'],
    defaultValue: 'Dark' },
  { type: 'color', name: 'accent', source: 'Map' },
  // What fills the national view, where a ZCTA is under a pixel wide. States
  // are the only choice here that shows an actual boundary at that zoom.
  { type: 'dropdown', name: 'overview', source: 'Map',
    values: ['States', 'Heatmap', 'None'], defaultValue: 'States' },
  { type: 'toggle', name: 'showLegend', source: 'Map', defaultValue: true },
  // ZCTA boundaries, joined to the data on the ZIP column. Defaults to the
  // file deployed beside this bundle; point it at your own GeoJSON to cover
  // ZIPs this one does not.
  { type: 'text', name: 'boundariesUrl', source: 'Map',
    placeholder: 'leave blank for the bundled ZCTAs' },

  // The two-way channel to a workbook control, and the whole point of this
  // plugin: a lasso writes the ZIPs it caught into a multi-select list
  // control, and every other element can then filter on it.
  // 'text-list' is a LIST control (what a plugin writes a selection into);
  // plain 'text' is a text INPUT box. ControlType names the control's kind,
  // not its selection mode, so a single-select list is still 'text-list'.
  // Declaring ['text'] here makes Sigma render "Invalid selection" in the
  // panel -- on a binding that otherwise works, because the spec binds by
  // controlId and bypasses the panel's picker.
  { type: 'variable', name: 'selectedZips', allowedTypes: ['text-list'] },
]);

const BASEMAPS = {
  Dark: 'mapbox://styles/mapbox/dark-v11',
  Light: 'mapbox://styles/mapbox/light-v11',
  Streets: 'mapbox://styles/mapbox/streets-v12',
  Satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
  'Navigation night': 'mapbox://styles/mapbox/navigation-night-v1',
};

// Sequential blue, low to high, stepped for a dark surface (#1a1a19) and
// validated as an ordinal ramp: monotone lightness, visible step gaps, and the
// darkest step still clears 2:1 against the basemap. The selection highlight is
// the orange categorical slot -- 26.8 CVD delta-E from the ramp's midpoint, so
// a lassoed ZIP is never confusable with a merely high-value one.
const INK = '#ffffff';
const INK_DIM = '#c3c2b7';
const DEFAULT_ACCENT = '#d95926';

/** Join key only -- never displayed, and never written to the control, which
 *  gets the ZIP exactly as the data spells it. Tolerates ZIP+4, a stray
 *  decimal from a numeric column, and a leading zero lost upstream. */
const zipKey = (v) => {
  const m = String(v ?? '').trim().match(/\d+/);
  return m ? m[0].slice(0, 5).padStart(5, '0') : '';
};

// Ten real store ZIPs, so the unbound render looks like the real thing at a
// glance -- and carries a `demo data` badge so it cannot be mistaken for one.
const DEMO = [
  { zip: '94188', place: 'San Francisco, CA', value: 21500000 },
  { zip: '90220', place: 'Compton, CA', value: 18900000 },
  { zip: '98108', place: 'Seattle, WA', value: 12400000 },
  { zip: '85026', place: 'Phoenix, AZ', value: 16800000 },
  { zip: '77201', place: 'Houston, TX', value: 24100000 },
  { zip: '60505', place: 'Aurora, IL', value: 15200000 },
  { zip: '30901', place: 'Augusta, GA', value: 9800000 },
  { zip: '33022', place: 'Hollywood, FL', value: 13600000 },
  { zip: '10199', place: 'New York, NY', value: 27300000 },
  { zip: '02169', place: 'Quincy, MA', value: 11100000 },
];

const fmt = (v) => {
  const n = Number(v) || 0, a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
};

// Both axes in the SAME units -- radians. mercatorY is a natural log, so a
// longitude left in degrees is ~57x too wide for it and squashes the map into
// a horizontal band.
const mercatorX = (lon) => (lon * Math.PI) / 180;
const mercatorY = (lat) => {
  const clamped = Math.max(-85.05, Math.min(85.05, lat));
  return Math.log(Math.tan(Math.PI / 4 + (clamped * Math.PI) / 360));
};

/** Web-mercator fit into a box. Used only when there is no basemap to project
 *  against -- no token yet, a rejected token, or the local bind harness. The
 *  shapes, the dots and the lasso stay correct relative to each other. */
function fitProjection(bounds, w, h) {
  const pad = 34;
  if (!bounds || w < 8 || h < 8) return () => [w / 2, h / 2];
  const [minLon, minLat, maxLon, maxLat] = bounds;
  const minX = mercatorX(minLon), maxX = mercatorX(maxLon);
  const minY = mercatorY(minLat), maxY = mercatorY(maxLat);
  const spanX = Math.max(maxX - minX, 1e-6);
  const spanY = Math.max(maxY - minY, 1e-6);
  // One scale for both axes, or the map is stretched and the lasso lies.
  const k = Math.min((w - pad * 2) / spanX, (h - pad * 2) / spanY);
  const offX = (w - spanX * k) / 2;
  const offY = (h - spanY * k) / 2;
  return (lon, lat) => [
    offX + (mercatorX(lon) - minX) * k,
    offY + (maxY - mercatorY(lat)) * k,
  ];
}

/** Ray casting. `poly` is [[x, y], ...] in the same space as x/y. */
function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

/** Signed-area centroid of a ring, and its absolute area. */
function ringCentroid(ring) {
  let a = 0, cx = 0, cy = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [x0, y0] = ring[j], [x1, y1] = ring[i];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  if (!a) return { c: ring[0], area: 0 };
  a *= 0.5;
  return { c: [cx / (6 * a), cy / (6 * a)], area: Math.abs(a) };
}

/** GeoJSON FeatureCollection -> Map(zip -> {rings, centroid, bounds, geometry}).
 *  Only outer rings are kept for hit-testing, which is the right trade for
 *  ZCTAs: a hole big enough to matter would be its own ZIP anyway. */
function indexBoundaries(fc) {
  const out = new Map();
  for (const f of fc?.features || []) {
    const zip = zipKey(f?.properties?.zip ?? f?.properties?.ZCTA5 ?? f?.id);
    const g = f?.geometry;
    if (!zip || !g) continue;
    const polys = g.type === 'Polygon' ? [g.coordinates]
      : g.type === 'MultiPolygon' ? g.coordinates : null;
    if (!polys) continue;

    const rings = [];
    let best = null, bestArea = -1;
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    for (const poly of polys) {
      const outer = poly[0];
      if (!outer || outer.length < 4) continue;
      rings.push(outer);
      const { c, area } = ringCentroid(outer);
      if (area > bestArea) { bestArea = area; best = c; }
      for (const [lon, lat] of outer) {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
      }
    }
    if (!rings.length || !best) continue;
    out.set(zip, { rings, centroid: best, bounds: [minLon, minLat, maxLon, maxLat], geometry: g });
  }
  return out;
}

/** Outer rings of a GeoJSON polygon/multipolygon. */
const outerRings = (g) => (
  g.type === 'Polygon' ? [g.coordinates[0]]
    : g.type === 'MultiPolygon' ? g.coordinates.map((p) => p[0])
    : []
);

/** States, pre-indexed once at module scope with a bbox per state so the
 *  point-in-polygon below skips almost everything cheaply. */
const STATES = BUNDLED_STATES.features.map((f) => {
  const rings = outerRings(f.geometry);
  let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
  for (const r of rings) for (const [lon, lat] of r) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  return { ab: f.properties.ab, name: f.properties.state, geometry: f.geometry,
           rings, bbox: [minLon, minLat, maxLon, maxLat] };
});

/** Which state contains this point.
 *
 *  Derived from the coordinates the plugin already has rather than from a
 *  `state` column, deliberately: adding a sixth column binding would mean
 *  re-binding -- and therefore re-publishing -- every workbook already using
 *  this plugin, to draw a layer that the lat/long fully determine anyway.
 */
function stateAt(lon, lat) {
  for (const st of STATES) {
    const b = st.bbox;
    if (lon < b[0] || lon > b[2] || lat < b[1] || lat > b[3]) continue;
    for (const ring of st.rings) {
      if (pointInPolygon(lon, lat, ring)) return st;
    }
  }
  return null;
}

const keyOf = (list) => list.slice().sort().join(' ');

export default function App() {
  const config = useConfig();

  // Data arrives KEYED BY COLUMN ID -- an object of parallel arrays, not row
  // objects.
  const sigmaData = useElementData(config.source);
  const [variable, setVariable] = useVariable(config.selectedZips);
  const [, setLoading] = useLoadingState(true);

  const rows = useMemo(() => {
    const zipCol = sigmaData?.[config.zip];
    if (!zipCol) return null;
    const vals = sigmaData?.[config.value];
    const places = sigmaData?.[config.place];

    // Fold duplicates: a detail-level element gives one row per order, and a
    // ZIP has to be one shape or the lasso selects the same place many times.
    const byZip = new Map();
    for (let i = 0; i < zipCol.length; i++) {
      const zip = String(zipCol[i] ?? '').trim();
      if (!zip) continue;
      const v = Number(vals?.[i]);
      const prev = byZip.get(zip);
      if (prev) {
        prev.value += Number.isFinite(v) ? v : 0;
      } else {
        byZip.set(zip, {
          zip,
          place: places?.[i] != null ? String(places[i]) : '',
          value: Number.isFinite(v) ? v : 0,
        });
      }
    }
    return byZip.size ? Array.from(byZip.values()) : null;
  }, [sigmaData, config.zip, config.value, config.place]);

  useEffect(() => { setLoading(false); }, [rows, setLoading]);

  const isDemo = !rows && !config.source;
  const points = useMemo(() => rows ?? (isDemo ? DEMO : []), [rows, isDemo]);

  // --- ZCTA boundaries ----------------------------------------------------
  const boundariesUrl = String(config.boundariesUrl || '').trim();
  const [shapes, setShapes] = useState(() => indexBoundaries(BUNDLED_ZCTA));
  const [shapeError, setShapeError] = useState(null);
  useEffect(() => {
    if (!boundariesUrl) {
      setShapes(indexBoundaries(BUNDLED_ZCTA));
      setShapeError(null);
      return undefined;
    }
    let cancelled = false;
    setShapeError(null);
    fetch(boundariesUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((fc) => { if (!cancelled) setShapes(indexBoundaries(fc)); })
      .catch((e) => {
        if (cancelled) return;
        // Fall back to the bundled set rather than to nothing: a typo in the
        // override should not cost you the shapes that ship with the plugin.
        setShapes(indexBoundaries(BUNDLED_ZCTA));
        setShapeError(e?.message || String(e));
      });
    return () => { cancelled = true; };
  }, [boundariesUrl]);

  // --- selection: a local copy, synced FROM the control -------------------
  // Rendering straight off the variable reads a stale list on a fast second
  // lasso -- the write goes out to the workbook and only comes back as an
  // update a beat later, which would drop the first selection.
  const [selected, setSelected] = useState(() => new Set());
  const pending = useRef(null);
  const controlValue = variable?.defaultValue?.value;
  const controlKey = useMemo(() => {
    const arr = Array.isArray(controlValue)
      ? controlValue
      : controlValue == null || controlValue === '' ? [] : [controlValue];
    return keyOf(arr.map(String));
  }, [controlValue]);

  useEffect(() => {
    if (pending.current !== null) {
      // Ignore echoes until the workbook catches up with our own last write.
      if (pending.current === controlKey) pending.current = null;
      return;
    }
    setSelected(new Set(controlKey ? controlKey.split(' ') : []));
  }, [controlKey]);

  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const commit = useCallback((zipList) => {
    const next = Array.from(new Set(zipList));
    pending.current = keyOf(next);
    setSelected(new Set(next));
    // setVariable is variadic; calling it with none clears the control.
    if (config.selectedZips) setVariable(...next);
  }, [config.selectedZips, setVariable]);

  // --- size: the author resizes this element whenever they like ----------
  const wrapRef = useRef(null);
  const mapRef = useRef(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const sizeRef = useRef(size);
  useLayoutEffect(() => {
    const measure = () => {
      const el = wrapRef.current;
      if (!el) return;
      const w = el.clientWidth, h = el.clientHeight;
      // The comparison is load-bearing: the callback re-renders into the
      // observed element, so an unguarded observer feeds itself forever.
      if (w === sizeRef.current.w && h === sizeRef.current.h) return;
      sizeRef.current = { w, h };
      setSize({ w, h });
      if (mapRef.current) mapRef.current.resize();
    };
    measure();
    if (!window.ResizeObserver) return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(wrapRef.current || document.body);
    return () => ro.disconnect();
  }, []);

  // --- the basemap --------------------------------------------------------
  // The container is rendered unconditionally and `mounted` is what tells this
  // effect it exists. An earlier version returned a "no data yet" node
  // instead, so on any load where the token arrived before the rows -- which
  // is every reload of a saved workbook -- this effect ran once with no
  // container, bailed, and never ran again: a token that was definitely set,
  // and no map, forever.
  const mapDiv = useRef(null);
  const [mounted, setMounted] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [mapError, setMapError] = useState(null);
  const [tick, bump] = useState(0);

  const token = String(config.mapboxToken || '').trim();
  const styleUrl = BASEMAPS[config.basemap] || BASEMAPS.Dark;

  const attachContainer = useCallback((node) => {
    mapDiv.current = node;
    setMounted(Boolean(node));
  }, []);

  useEffect(() => {
    if (!token || !mounted || !mapDiv.current) return undefined;
    setMapError(null);
    let map;
    try {
      mapboxgl.accessToken = token;
      map = new mapboxgl.Map({
        container: mapDiv.current,
        style: styleUrl,
        center: [-98.5, 39.5],
        zoom: 3,
        attributionControl: true,
      });
    } catch (err) {
      setMapError(err?.message || String(err));
      return undefined;
    }
    mapRef.current = map;
    const redraw = () => bump((n) => n + 1);
    map.on('load', () => { setMapReady(true); redraw(); });
    map.on('move', redraw);
    map.on('error', (e) => setMapError(e?.error?.message || 'Mapbox could not load this style.'));
    return () => {
      setMapReady(false);
      mapRef.current = null;
      map.remove();
    };
  }, [token, styleUrl, mounted]);

  // --- one record per ZIP, with its shape if there is one -----------------
  const { maxValue, minValue } = useMemo(() => {
    let mx = -Infinity, mn = Infinity;
    for (const p of points) {
      if (p.value > mx) mx = p.value;
      if (p.value < mn) mn = p.value;
    }
    return { maxValue: mx === -Infinity ? 0 : mx, minValue: mn === Infinity ? 0 : mn };
  }, [points]);

  const zips = useMemo(() => {
    const span = Math.max(maxValue - minValue, 1e-9);
    return points.map((p) => {
      const key = zipKey(p.zip);
      // Some ZIPs are PO-box or point ZIPs with no tabulation area of their
      // own. `alias` maps each to the ZCTA that physically contains it, so it
      // still lands in the right place and carries a shape -- flagged as
      // `via`, and named in the tooltip, because the outline drawn is that
      // neighbouring ZCTA's rather than the ZIP's own.
      const via = !shapes?.has(key) ? (BUNDLED_ALIAS[key] || null) : null;
      const shape = shapes?.get(via || key) || null;
      const t = maxValue > 0 ? (p.value - minValue) / span : 0;
      return {
        ...p,
        shape,
        via,
        t,
        lon: shape ? shape.centroid[0] : null,
        lat: shape ? shape.centroid[1] : null,
        color: RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))],
      };
    }).filter((z) => z.shape);
  }, [points, shapes, maxValue, minValue]);

  // Reported, never silently dropped: a ZIP the boundary file cannot place is
  // a ZIP missing from the map.
  const unplaced = useMemo(
    () => points.length - zips.length, [points.length, zips.length]);

  // Roll the ZIP measure up to whichever state each ZIP falls in. Own colour
  // domain: state totals are an order of magnitude larger than ZIP ones, and
  // colouring them on the ZIP scale would paint every state the top step.
  const stateFC = useMemo(() => {
    const totals = new Map();
    for (const z of zips) {
      const st = stateAt(z.lon, z.lat);
      if (!st) continue;
      totals.set(st.ab, (totals.get(st.ab) || 0) + z.value);
    }
    if (!totals.size) return { type: 'FeatureCollection', features: [] };
    let mn = Infinity, mx = -Infinity;
    for (const v of totals.values()) { if (v < mn) mn = v; if (v > mx) mx = v; }
    const span = Math.max(mx - mn, 1e-9);
    return {
      type: 'FeatureCollection',
      domain: [mn, mx],
      features: STATES.filter((st) => totals.has(st.ab)).map((st) => {
        const v = totals.get(st.ab);
        const t = (v - mn) / span;
        return {
          type: 'Feature', id: st.ab, geometry: st.geometry,
          properties: {
            ab: st.ab, state: st.name, value: v,
            color: RAMP[Math.min(RAMP.length - 1, Math.floor(t * RAMP.length))],
          },
        };
      }),
    };
  }, [zips]);

  const dataBounds = useMemo(() => {
    if (!zips.length) return null;
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    for (const z of zips) {
      const b = z.shape.bounds;
      if (b[0] < minLon) minLon = b[0];
      if (b[1] < minLat) minLat = b[1];
      if (b[2] > maxLon) maxLon = b[2];
      if (b[3] > maxLat) maxLat = b[3];
    }
    return [minLon, minLat, maxLon, maxLat];
  }, [zips]);

  // Frame the data once it is known, rather than leaving the author on a
  // default continental view that may not contain a single ZIP.
  const fitKey = dataBounds ? dataBounds.join(',') : '';
  useEffect(() => {
    if (!mapReady || !mapRef.current || !dataBounds) return;
    mapRef.current.fitBounds(
      [[dataBounds[0], dataBounds[1]], [dataBounds[2], dataBounds[3]]],
      { padding: 48, duration: 0, maxZoom: 9 },
    );
  }, [mapReady, fitKey]);   // eslint-disable-line react-hooks/exhaustive-deps

  // --- the choropleth, as real Mapbox layers ------------------------------
  const accent = config.accent || DEFAULT_ACCENT;
  const shapeFC = useMemo(() => ({
    type: 'FeatureCollection',
    features: zips.filter((z) => z.shape).map((z) => ({
      type: 'Feature',
      geometry: z.shape.geometry,
      properties: {
        zip: z.zip,
        // Paint values are precomputed rather than written as style
        // expressions: re-setting a hundred-odd features on a selection change
        // costs nothing, and it keeps one colour scale in one place for both
        // render paths -- Mapbox layers here, SVG when there is no token.
        color: selected.has(z.zip) ? accent : z.color,
        opacity: selected.has(z.zip) ? 0.92 : selected.size ? 0.35 : 0.8,
        line: selected.has(z.zip) ? '#ffffff' : z.color,
        // The outline is what makes a two-pixel ZCTA visible at all, so it
        // carries a real width rather than a hairline.
        lineWidth: selected.has(z.zip) ? 2.2 : 1.4,
      },
    })),
  }), [zips, selected, accent]);

  // The same ZIPs as points: what the heat surface is built from, and what
  // draws the ones with no polygon.
  const pointFC = useMemo(() => ({
    type: 'FeatureCollection',
    features: zips.map((z) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [z.lon, z.lat] },
      properties: {
        zip: z.zip,
        // Heat weight has a floor: a linear 0..1 makes the smallest store
        // contribute literally nothing, and a ZIP that is present should read
        // as present.
        weight: 0.15 + z.t * 0.85,
        color: selected.has(z.zip) ? accent : z.color,
        sel: selected.has(z.zip) ? 1 : 0,
        noshape: z.shape ? 0 : 1,
      },
    })),
  }), [zips, selected, accent]);

  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    const src = map.getSource('zips');
    if (src) {
      src.setData(shapeFC);
      map.getSource('zip-points')?.setData(pointFC);
      map.getSource('states')?.setData(stateFC);
      return;
    }
    // tolerance well below the 0.375 default: Mapbox simplifies per tile, and
    // at continental zoom the default is coarse enough to crush a ZCTA-sized
    // polygon out of existence entirely. 121 features, so the cost is nil.
    for (const [id, def] of mapSources(shapeFC, pointFC, stateFC)) map.addSource(id, def);
    for (const layer of mapLayers()) map.addLayer(layer);
  }, [mapReady, shapeFC, pointFC, stateFC]);

  // One overview geography at a time. Every layer is always in the style; only
  // its visibility changes, so switching costs no re-tiling.
  const overview = config.overview || 'States';
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || !map) return;
    for (const [mode, ids] of Object.entries(OVERVIEW_LAYERS)) {
      for (const id of ids) {
        if (!map.getLayer(id)) continue;
        map.setLayoutProperty(id, 'visibility', mode === overview ? 'visible' : 'none');
      }
    }
  }, [mapReady, overview]);

  // Zoom drives which domain the legend is describing. Read off the same move
  // ticker the projection uses rather than its own listener.
  const zoom = mapReady && mapRef.current ? mapRef.current.getZoom() : null;
  // With no basemap there is no zoom, and the fallback projection is always
  // framed on the whole dataset -- which IS the overview scale, so the states
  // are what is filled there.
  const showingStates = overview === 'States' && (zoom === null || zoom < 7);

  // --- screen-space geometry ---------------------------------------------
  // In map mode Mapbox draws the shapes and this projection only places labels
  // and answers the lasso. With no basemap, it draws them too.
  const placed = useMemo(() => {
    const map = mapRef.current;
    const project = mapReady && map
      ? (lon, lat) => { const q = map.project([lon, lat]); return [q.x, q.y]; }
      : fitProjection(dataBounds, size.w, size.h);
    // A ZIP that has a boundary is drawn AS that boundary, at every zoom.
    // An earlier version cross-faded a value-scaled bubble in as the polygon
    // got small, on the theory that a ZCTA is a speck at continental zoom --
    // true, but it meant the default view of a national dataset was still a
    // bubble map, which is the thing the shapes were meant to replace. The
    // outline below is what carries a small ZCTA now. `bubble` survives only
    // for the ZIPs that have no polygon at all.
    return zips.map((z) => {
      const [x, y] = project(z.lon, z.lat);
      const r = 4.5 + Math.sqrt(z.t) * 12;
      // Every ZIP has a shape now, so nothing needs a bubble; `r` survives
      // only to offset labels and size the hover ring.
      return { ...z, x, y, r, bubble: 0 };
    });
  }, [zips, dataBounds, mapReady, size.w, size.h, tick]);

  const placedRef = useRef(placed);
  placedRef.current = placed;

  // Only needed when there is no basemap to draw the shapes for us.
  const flatShapes = useMemo(() => {
    if (mapReady || !size.w || !dataBounds) return [];
    const project = fitProjection(dataBounds, size.w, size.h);
    return zips
      .filter((z) => z.shape)
      .map((z) => ({
        zip: z.zip,
        color: z.color,
        rings: z.shape.rings.map((ring) => ring.map(([lon, lat]) => project(lon, lat))),
      }));
  }, [zips, dataBounds, mapReady, size.w, size.h]);
  const flatShapesRef = useRef(flatShapes);
  flatShapesRef.current = flatShapes;

  // With no basemap there is no zoom to hand over at, so the states simply sit
  // underneath -- which is the one context the fallback projection otherwise
  // has none of.
  const flatStates = useMemo(() => {
    if (mapReady || !size.w || !dataBounds || overview !== 'States') return [];
    const project = fitProjection(dataBounds, size.w, size.h);
    return stateFC.features.map((f) => ({
      ab: f.properties.ab,
      color: f.properties.color,
      rings: outerRings(f.geometry).map((ring) => ring.map(([lon, lat]) => project(lon, lat))),
    }));
  }, [stateFC, dataBounds, mapReady, size.w, size.h, overview]);

  // Selective direct labels -- never a number on every ZIP. Every selected one
  // is named (that is the answer to "what did I just lasso?"), then the
  // largest remaining ones fill the space left, skipping any that would
  // collide with a label already placed.
  const labelled = useMemo(() => {
    if (size.w < 260 || size.h < 200) return [];
    const budget = Math.max(3, Math.min(14, Math.floor((size.w * size.h) / 42000)));
    const order = placed
      .slice()
      .sort((a, b) => (selected.has(b.zip) - selected.has(a.zip)) || (b.value - a.value));
    const boxes = [], out = [];
    for (const p of order) {
      if (out.length >= budget && !selected.has(p.zip)) break;
      if (p.x < -40 || p.y < -20 || p.x > size.w + 40 || p.y > size.h + 20) continue;
      const box = { x1: p.x + p.r + 2, y1: p.y - 6, x2: p.x + p.r + 40, y2: p.y + 6 };
      if (boxes.some((b) => !(box.x2 < b.x1 || box.x1 > b.x2 || box.y2 < b.y1 || box.y1 > b.y2))) {
        continue;
      }
      boxes.push(box);
      out.push(p);
    }
    return out;
  }, [placed, selected, size.w, size.h]);

  // --- the lasso ----------------------------------------------------------
  const [lassoMode, setLassoMode] = useState(false);
  const [path, setPath] = useState(null);
  const pathRef = useRef(null);
  const addRef = useRef(false);
  const downRef = useRef(null);
  const lassoModeRef = useRef(lassoMode);
  lassoModeRef.current = lassoMode;

  const localXY = (ev) => {
    const rect = wrapRef.current.getBoundingClientRect();
    return [ev.clientX - rect.left, ev.clientY - rect.top];
  };

  // Capture phase on the wrapper, so a lasso can start anywhere -- including
  // over the Mapbox canvas, which would otherwise swallow the drag as a pan.
  // Anything that is not a lasso falls straight through to the map.
  const onPointerDownCapture = (ev) => {
    if (ev.button !== 0) return;
    const [x, y] = localXY(ev);
    downRef.current = { x, y };
    if (!lassoModeRef.current && !ev.shiftKey) return;

    ev.preventDefault();
    ev.stopPropagation();
    addRef.current = ev.shiftKey;
    pathRef.current = [[x, y]];
    setPath(pathRef.current.slice());

    const onMove = (e) => {
      const rect = wrapRef.current.getBoundingClientRect();
      const px = e.clientX - rect.left, py = e.clientY - rect.top;
      const pts = pathRef.current;
      if (!pts) return;
      const last = pts[pts.length - 1];
      if (Math.hypot(px - last[0], py - last[1]) < 3) return;
      pts.push([px, py]);
      setPath(pts.slice());
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      const poly = pathRef.current;
      pathRef.current = null;
      setPath(null);
      if (!poly || poly.length < 3) return;
      // A ZIP is caught when its centroid falls inside the loop. Centroid
      // rather than "any overlap": it is the rule people already expect from a
      // lasso, and it does not hand you a whole county for clipping a corner.
      const hit = placedRef.current
        .filter((p) => pointInPolygon(p.x, p.y, poly))
        .map((p) => p.zip);
      commit(addRef.current ? [...selectedRef.current, ...hit] : hit);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  };

  // --- hover and click, hit-tested by hand --------------------------------
  // The SVG stays pointer-events:none so the basemap keeps every interaction
  // it ships with; hit-testing here gives back pan, zoom and rotate untouched.
  const [hover, setHover] = useState(null);
  const hitTest = useCallback((x, y) => {
    const list = placedRef.current;
    const map = mapRef.current;
    if (mapReady && map && map.getLayer('zip-fill')) {
      const layers = HIT_LAYERS.filter((l) => map.getLayer(l));
      const f = map.queryRenderedFeatures([x, y], { layers })[0];
      if (f) {
        const z = list.find((p) => p.zip === f.properties.zip);
        if (z) return z;
      }
    }
    let best = null, bestD = Infinity;
    for (const p of list) {
      if (p.bubble < 0.05) continue;
      const d = Math.hypot(p.x - x, p.y - y);
      if (d <= p.r + 4 && d < bestD) { best = p; bestD = d; }
    }
    if (best) return best;
    for (const s of flatShapesRef.current) {
      if (s.rings.some((ring) => pointInPolygon(x, y, ring))) {
        return list.find((p) => p.zip === s.zip) || null;
      }
    }
    return null;
  }, [mapReady]);

  const onPointerMove = (ev) => {
    if (pathRef.current) return;
    const [x, y] = localXY(ev);
    const best = hitTest(x, y);
    setHover((prev) => (prev?.zip === best?.zip ? prev : best));
  };

  const onClick = () => {
    const down = downRef.current;
    // A pan ends in a click too -- only treat a stationary press as one.
    if (!down || !hover) return;
    if (hitTest(down.x, down.y)?.zip !== hover.zip) return;
    const next = new Set(selectedRef.current);
    if (next.has(hover.zip)) next.delete(hover.zip);
    else next.add(hover.zip);
    commit(Array.from(next));
  };

  const selectedCount = selected.size;
  const selectedTotal = useMemo(
    () => placed.reduce((s, p) => (selected.has(p.zip) ? s + p.value : s), 0),
    [placed, selected],
  );

  // Exactly which state the basemap is in, in one line. "No map" has several
  // causes that look identical on screen, and guessing between them from a
  // screenshot is what costs a round trip.
  const notice = !token
    ? 'No basemap: add a Mapbox token under Map in the editor panel. Shapes and lasso work without one.'
    : mapError ? `Mapbox: ${mapError}`
    : shapeError ? `Boundaries URL failed (${shapeError}) -- using the bundled ZCTAs.`
    : shapes && placed.length && pointOnly === placed.length
      ? 'No ZIP in this data matched a boundary -- check the ZIP column is the 5-digit code.'
      : null;

  return (
    <div
      ref={wrapRef}
      style={{ ...S.wrap, cursor: lassoMode ? 'crosshair' : hover ? 'pointer' : 'default' }}
      onPointerDownCapture={onPointerDownCapture}
      onPointerMove={onPointerMove}
      onPointerLeave={() => setHover(null)}
      onClick={onClick}
    >
      <div ref={attachContainer} style={S.map} />

      <svg style={S.overlay} width={size.w || '100%'} height={size.h || '100%'}>
        {flatStates.map((st) => (
          <path
            key={`st-${st.ab}`}
            d={st.rings.map((ring) => `M${ring.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join('L')}Z`).join('')}
            fill={st.color}
            fillOpacity={0.72}
            stroke={INK_DIM}
            strokeWidth={0.7}
            strokeOpacity={0.45}
          />
        ))}
        {flatShapes.map((s) => {
          const isSel = selected.has(s.zip);
          // When the states carry the fill, the ZIP outlines must NOT also be
          // filled: the two live on different value domains an order of
          // magnitude apart, and one ramp cannot honestly mean both at once.
          // Selection still fills, because that is a state, not a measure.
          const onStates = showingStates && flatStates.length > 0;
          return (
            <path
              key={`s-${s.zip}`}
              d={s.rings.map((ring) => `M${ring.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join('L')}Z`).join('')}
              fill={isSel ? accent : onStates ? 'none' : s.color}
              fillOpacity={isSel ? 0.92 : selectedCount ? 0.35 : 0.8}
              stroke={isSel ? '#ffffff' : onStates ? INK : s.color}
              strokeWidth={isSel ? 2.2 : 1.4}
              strokeOpacity={onStates && !isSel ? 0.55 : 0.95}
            />
          );
        })}
        {placed.filter((p) => p.bubble > 0.02).map((p) => {
          const isSel = selected.has(p.zip);
          return (
            <circle
              key={p.zip}
              cx={p.x}
              cy={p.y}
              r={isSel ? p.r + 1.5 : p.r}
              fill={isSel ? accent : p.color}
              fillOpacity={p.bubble * (isSel ? 0.92 : selectedCount ? 0.3 : 0.72)}
              // A surface-coloured ring keeps overlapping dots separable.
              stroke={isSel ? '#ffffff' : SURFACE}
              strokeWidth={isSel ? 1.6 : 1}
              strokeOpacity={p.bubble * (isSel ? 0.9 : 0.55)}
            />
          );
        })}
        {labelled.map((p) => (
          <text
            key={`t-${p.zip}`}
            x={p.x + p.r + 3}
            y={p.y + 3.5}
            fill={selected.has(p.zip) ? '#ffffff' : INK_DIM}
            fontSize={10}
            fontWeight={selected.has(p.zip) ? 700 : 400}
            // Halo, so a label stays readable over any basemap underneath it.
            stroke={SURFACE}
            strokeWidth={2.5}
            strokeOpacity={0.75}
            paintOrder="stroke"
            style={{ fontVariantNumeric: 'tabular-nums' }}
          >
            {p.zip}
          </text>
        ))}
        {hover && (
          <circle cx={hover.x} cy={hover.y} r={hover.r + 4} fill="none"
                  stroke={INK} strokeOpacity={0.8} strokeWidth={1.2} />
        )}
        {path && path.length > 1 && (
          <path d={`M${path.map((q) => q.join(',')).join('L')}Z`}
                fill={accent} fillOpacity={0.12}
                stroke={accent} strokeWidth={1.8}
                strokeDasharray="5 4" strokeLinejoin="round" />
        )}
      </svg>

      <div style={S.toolbar}>
        <span style={S.title}>ZIP lasso</span>
        {isDemo && <span style={S.badge}>demo data</span>}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setLassoMode((m) => !m); }}
          style={{ ...S.btn, ...(lassoMode ? { background: accent, color: '#fff', borderColor: accent } : null) }}
        >
          {lassoMode ? 'Lasso on' : 'Lasso'}
        </button>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); commit([]); }}
          style={{ ...S.btn, opacity: selectedCount ? 1 : 0.45 }}
        >
          Clear
        </button>
        <span style={S.count}>
          {selectedCount
            ? `${selectedCount} selected of ${placed.length} ZIPs (${fmt(selectedTotal)})`
            : unplaced
              ? `${placed.length} ZIPs, ${unplaced} with no boundary`
              : `${placed.length} ZIPs`}
        </span>
      </div>

      {config.showLegend !== false && size.w > 340 && placed.length > 0 && (
        <div style={S.legend}>
          {/* State totals and ZIP values are an order of magnitude apart, so
              the legend has to name which one the ramp is currently showing --
              the same five colours mean different numbers either side of the
              handover. */}
          <span style={S.legendWhat}>{showingStates ? 'by state' : 'by ZIP'}</span>
          <span style={S.legendLabel}>
            {fmt(showingStates ? stateFC.domain?.[0] ?? 0 : minValue)}
          </span>
          <span style={S.ramp}>
            {RAMP.map((c) => <i key={c} style={{ ...S.rampStep, background: c }} />)}
          </span>
          <span style={S.legendLabel}>
            {fmt(showingStates ? stateFC.domain?.[1] ?? 0 : maxValue)}
          </span>
          <span style={{ ...S.swatch, background: accent }} />
          <span style={S.legendLabel}>selected</span>
        </div>
      )}

      {hover && (
        <div style={{
          ...S.tip,
          left: Math.min(Math.max(hover.x + 14, 8), Math.max(size.w - 190, 8)),
          top: Math.min(Math.max(hover.y - 10, 8), Math.max(size.h - 76, 8)),
        }}>
          <div style={S.tipZip}>{hover.zip}</div>
          {hover.place ? <div style={S.tipPlace}>{hover.place}</div> : null}
          <div style={S.tipVal}>{fmt(hover.value)}</div>
          {hover.via && (
            <div style={S.tipVia}>no ZCTA of its own; shown as {hover.via}</div>
          )}
        </div>
      )}

      {!placed.length && (
        <div style={S.hint}>
          {client.sigmaEnv === 'author'
            ? 'Bind an element and a ZIP column in the editor panel. Positions come from the ZIP itself.'
            : 'No locations to display.'}
        </div>
      )}

      {notice && <div style={S.notice}>{notice}</div>}
      {!lassoMode && !path && (
        <div style={S.help}>Shift-drag to lasso, or click a ZIP to toggle it</div>
      )}
    </div>
  );
}

const S = {
  wrap: { position: 'relative', width: '100%', height: '100%', overflow: 'hidden',
          background: SURFACE, userSelect: 'none' },
  map: { position: 'absolute', inset: 0 },
  overlay: { position: 'absolute', inset: 0, pointerEvents: 'none' },
  toolbar: { position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center',
             gap: 8, padding: '6px 10px', borderRadius: 6, background: 'rgba(26,26,25,.82)',
             border: '1px solid rgba(255,255,255,.12)', backdropFilter: 'blur(6px)',
             flexWrap: 'wrap', maxWidth: 'calc(100% - 20px)', zIndex: 2 },
  title: { fontSize: 10, fontWeight: 700, letterSpacing: '.09em', textTransform: 'uppercase',
           color: INK_DIM },
  badge: { fontSize: 9, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
           padding: '2px 6px', borderRadius: 3, background: '#fab219', color: '#3a2a00' },
  btn: { font: 'inherit', fontSize: 11, lineHeight: 1, padding: '5px 9px', borderRadius: 4,
         border: '1px solid rgba(255,255,255,.22)', background: 'transparent', color: INK,
         cursor: 'pointer' },
  count: { fontSize: 11, color: INK, fontVariantNumeric: 'tabular-nums' },
  legend: { position: 'absolute', bottom: 28, left: 10, display: 'flex', alignItems: 'center',
            gap: 6, padding: '5px 9px', borderRadius: 6, background: 'rgba(26,26,25,.82)',
            border: '1px solid rgba(255,255,255,.12)', zIndex: 2 },
  legendLabel: { fontSize: 9, color: INK_DIM, fontVariantNumeric: 'tabular-nums' },
  legendWhat: { fontSize: 9, color: INK, fontWeight: 700, letterSpacing: '.04em',
                marginRight: 2 },
  ramp: { display: 'flex', borderRadius: 2, overflow: 'hidden' },
  rampStep: { display: 'block', width: 14, height: 8 },
  swatch: { width: 8, height: 8, borderRadius: '50%', marginLeft: 6 },
  tip: { position: 'absolute', pointerEvents: 'none', padding: '6px 9px', borderRadius: 5,
         background: 'rgba(26,26,25,.94)', border: '1px solid rgba(255,255,255,.16)',
         color: INK, minWidth: 96, zIndex: 3 },
  tipZip: { fontSize: 12, fontWeight: 700, fontVariantNumeric: 'tabular-nums' },
  tipPlace: { fontSize: 10, color: INK_DIM, marginTop: 1 },
  tipVia: { fontSize: 9, color: INK_DIM, marginTop: 3, opacity: .85, maxWidth: 150 },
  tipVal: { fontSize: 11, marginTop: 3, fontVariantNumeric: 'tabular-nums' },
  notice: { position: 'absolute', bottom: 10, right: 10, maxWidth: '52%', padding: '6px 9px',
            borderRadius: 5, background: 'rgba(26,26,25,.86)',
            border: '1px solid rgba(255,255,255,.16)', color: INK_DIM, fontSize: 10,
            lineHeight: 1.45, zIndex: 2 },
  help: { position: 'absolute', bottom: 10, left: 10, fontSize: 9, color: INK_DIM,
          opacity: .8, zIndex: 2 },
  hint: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'center', textAlign: 'center', fontSize: 11, color: INK_DIM,
          padding: '0 24px', lineHeight: 1.6, background: SURFACE, zIndex: 1 },
};
