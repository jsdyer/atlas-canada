// Pure functions for the Atlas Canada page. No DOM access here.

export function captureList(index) {
  if (!index || !Array.isArray(index.captures)) throw new TypeError('Unexpected index data');
  return [...index.captures].sort((a, b) => b.id.localeCompare(a.id));
}

export function percent(part, whole) {
  return whole ? Math.round((part / whole) * 1000) / 10 : 0;
}

const asKey = asn => (asn == null ? 'unknown' : String(asn).replace(/^AS/i, ''));

export function asNames(capture) {
  const names = {};
  for (const trace of capture.traces ?? []) {
    for (const hop of trace.hops ?? []) {
      if (hop.asn && hop.as_name) names[asKey(hop.asn)] = hop.as_name;
    }
  }
  return names;
}

export function networkRows(capture) {
  const names = asNames(capture);
  return Object.entries(capture.summary?.by_probe_network ?? {})
    .map(([asn, counts]) => ({
      asn: asn === 'unknown' ? null : `AS${asKey(asn)}`,
      name: names[asKey(asn)] ?? null,
      traces: counts.traces,
      leftCountry: counts.left_country,
      pct: percent(counts.left_country, counts.traces),
    }))
    .sort((a, b) => b.pct - a.pct || b.traces - a.traces || String(a.asn).localeCompare(String(b.asn)));
}

export function targetRows(capture) {
  const rows = new Map();
  for (const trace of capture.traces ?? []) {
    const row = rows.get(trace.target) ?? { target: trace.target, label: trace.target_label, measurement: trace.measurement, traces: 0, leftCountry: 0 };
    row.traces += 1;
    row.leftCountry += trace.left_country ? 1 : 0;
    rows.set(trace.target, row);
  }
  return [...rows.values()].map(r => ({ ...r, pct: percent(r.leftCountry, r.traces) }))
    .sort((a, b) => b.pct - a.pct || a.label.localeCompare(b.label));
}

export function pathText(trace) {
  return (trace.countries ?? []).length ? trace.countries.join(' → ') : 'no located hops';
}

export function foreignNetworks(trace, home = 'CA') {
  const seen = new Map();
  for (const hop of trace.hops ?? []) {
    if (hop.country && hop.country !== home && hop.asn && !seen.has(hop.asn)) seen.set(hop.asn, hop.as_name ?? '');
  }
  return [...seen].map(([asn, name]) => (name ? `${asn} ${name}` : asn));
}

export function filterTraces(capture, { onlyLeft = false, network = '', target = '' } = {}) {
  return (capture.traces ?? []).filter(t =>
    (!onlyLeft || t.left_country)
    && (!network || `AS${asKey(t.probe_asn)}` === network)
    && (!target || t.target === target));
}

export function trend(index) {
  return [...index.captures].sort((a, b) => a.id.localeCompare(b.id))
    .map(c => ({ id: c.id, at: c.at, traces: c.traces, leftCountry: c.left_country, pct: percent(c.left_country, c.traces) }));
}

export function parseProbeId(input) {
  const text = String(input ?? '').trim().replace(/^#/, '');
  if (!/^\d{1,8}$/.test(text) || Number(text) < 1) throw new Error('Enter a probe number, such as 1234');
  return Number(text);
}

export function probeSummary(data) {
  if (!data || typeof data.id !== 'number') throw new Error('Unexpected probe data');
  return {
    id: data.id,
    status: data.status?.name ?? 'Unknown',
    since: data.status?.since ?? null,
    asn: data.asn_v4 ?? data.asn_v6 ?? null,
    country: data.country_code ?? null,
    description: data.description ?? '',
    anchor: Boolean(data.is_anchor),
    firstConnected: data.first_connected ?? null,
    connected: data.status?.name === 'Connected',
  };
}
