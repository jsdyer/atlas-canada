import { captureList, networkRows, targetRows, pathText, foreignNetworks, filterTraces, trend, parseProbeId, probeSummary, percent } from './model.mjs';

const $ = selector => document.querySelector(selector);
const SVG = 'http://www.w3.org/2000/svg';
const TRACE_LIMIT = 200;
let index = null;
let capture = null;
let captureLoad = 0;
let probeLoad = 0;

function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function when(value) {
  if (value == null) return '—';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
}

const atlasLink = id => el('a', `#${id}`, { href: `https://atlas.ripe.net/measurements/${id}/`, rel: 'noreferrer' });

function renderTrend() {
  const points = trend(index);
  $('#trend-section').hidden = points.length < 2;
  if (points.length < 2) return;
  const width = 640, height = 160, pad = 24;
  const svg = document.createElementNS(SVG, 'svg');
  const max = Math.max(10, ...points.map(p => p.pct));
  const step = Math.min(36, (width - pad * 2) / points.length);
  const used = Math.max(160, pad * 2 + step * points.length);
  svg.setAttribute('viewBox', `0 0 ${used} ${height}`);
  svg.style.maxWidth = `${used}px`;
  points.forEach((point, i) => {
    const h = ((height - pad * 2) * point.pct) / max;
    const rect = document.createElementNS(SVG, 'rect');
    rect.setAttribute('x', String(pad + i * step + step * 0.15));
    rect.setAttribute('y', String(height - pad - h));
    rect.setAttribute('width', String(Math.max(1, step * 0.7)));
    rect.setAttribute('height', String(Math.max(h, 1)));
    rect.setAttribute('class', point.id === capture?.id ? 'bar current' : 'bar');
    const title = document.createElementNS(SVG, 'title');
    title.textContent = `${when(point.at)}: ${point.pct}% (${point.leftCountry} of ${point.traces})`;
    rect.append(title);
    svg.append(rect);
  });
  const label = document.createElementNS(SVG, 'text');
  label.setAttribute('x', String(pad));
  label.setAttribute('y', '14');
  label.textContent = `max ${max}%`;
  svg.append(label);
  $('#trend').replaceChildren(svg);
}

function fillSelect(select, options) {
  const keep = select.value;
  select.replaceChildren(select.options[0]);
  for (const [value, label] of options) select.append(el('option', label, { value }));
  select.value = options.some(([value]) => value === keep) ? keep : '';
}

function renderTraces() {
  const list = filterTraces(capture, {
    onlyLeft: $('#only-left').checked, network: $('#network-filter').value, target: $('#target-filter').value });
  $('#trace-count').textContent = `${list.length} ${list.length === 1 ? 'traceroute' : 'traceroutes'}${list.length > TRACE_LIMIT ? `, first ${TRACE_LIMIT} shown` : ''}.`;
  const box = $('#traces');
  box.replaceChildren();
  for (const trace of list.slice(0, TRACE_LIMIT)) {
    const item = el('li', null, { class: trace.left_country ? 'trace left' : 'trace' });
    const head = el('p');
    head.append(el('strong', `Probe ${trace.probe}`), ` (AS${trace.probe_asn ?? '?'}) → ${trace.target_label} · `, atlasLink(trace.measurement));
    const path = el('p', pathText(trace), { class: 'path' });
    item.append(head, path);
    const foreign = foreignNetworks(trace);
    if (foreign.length) item.append(el('p', `Foreign networks: ${foreign.join(', ')}`, { class: 'muted' }));
    if (trace.exchanges.length) item.append(el('p', `Exchange points: ${trace.exchanges.join(', ')}`, { class: 'muted' }));
    if (!trace.reached) item.append(el('p', 'Destination did not reply to the last hop.', { class: 'muted' }));
    if (trace.unanswered_hops) item.append(el('p', `${trace.unanswered_hops} unanswered hops.`, {class:'muted'}));
    if (!trace.eligible) item.append(el('p', 'Excluded from the percentage: incomplete route or endpoints not both known Canadian.', {class:'muted'}));
    const details = el('details');
    details.append(el('summary', `${trace.hops.length} public hops`));
    const hops = el('ol', null, { class: 'hops' });
    for (const hop of trace.hops) {
      hops.append(el('li', `${hop.ip}  ${hop.country ?? '??'}  ${hop.asn ?? ''} ${hop.as_name ?? ''}${hop.ix ? `  [${hop.ix.name}]` : ''}`));
    }
    details.append(hops);
    item.append(details);
    box.append(item);
  }
}

function renderCapture() {
  const s = capture.summary;
  $('#capture-info').textContent = `Capture taken ${when(capture.at)}: ${capture.targets.length} destinations, up to ${capture.settings.probes_per_target} ${capture.settings.probe_country} probes each, IPv${capture.settings.address_family} ${capture.settings.protocol}.`;
  $('#headline').hidden = false;
  $('#headline-pct').textContent = s.eligible ? `${percent(s.left_country, s.eligible)}%` : '—';
  $('#headline-detail').textContent = `${s.left_country} of ${s.eligible ?? 0} eligible completed routes (${s.traces} total). ${s.with_unknown_hops} traces had unlocated public addresses; ${s.with_unanswered_hops ?? 'unknown number of'} traces had unanswered hops. Incomplete routes and unknown or non-Canadian endpoints are excluded. Geolocation can misplace routers; these are possible detours, not proof of a physical border crossing.${capture.geolocation ? '' : ' IPinfo geolocation was unavailable.'}`;

  const networks = networkRows(capture);
  const nbody = $('#networks tbody');
  nbody.replaceChildren();
  for (const row of networks) {
    const tr = el('tr');
    nbody.append(tr);
    tr.append(el('td', `${row.asn ?? 'Unknown'}${row.name ? ` ${row.name}` : ''}`), el('td', `${row.eligible} eligible / ${row.traces}`), el('td', row.pct == null ? '—' : `${row.leftCountry} (${row.pct}%)`));
  }
  const transit = $('#transit');
  transit.replaceChildren();
  for (const t of s.foreign_transit) transit.append(el('li', `${t.asn}${t.as_name ? ` ${t.as_name}` : ''}: ${t.traces} ${t.traces === 1 ? 'trace' : 'traces'}`));
  if (!s.foreign_transit.length) transit.append(el('li', 'None in this capture.'));

  const targets = targetRows(capture);
  const tbody = $('#targets tbody');
  tbody.replaceChildren();
  for (const row of targets) {
    const tr = el('tr');
    const link = el('td');
    link.append(atlasLink(row.measurement));
    tr.append(el('td', row.label), el('td', `${row.eligible} eligible / ${row.traces}`), el('td', row.pct == null ? '—' : `${row.leftCountry} (${row.pct}%)`), link);
    tbody.append(tr);
  }
  fillSelect($('#network-filter'), networks.filter(r => r.asn).map(r => [r.asn, `${r.asn}${r.name ? ` ${r.name}` : ''}`]));
  fillSelect($('#target-filter'), targets.map(r => [r.target, r.label]));
  for (const id of ['#network-section', '#target-section', '#trace-section']) $(id).hidden = false;
  renderTraces();
  renderTrend();
}

async function loadCapture(entry) {
  const current = ++captureLoad;
  capture = null;
  for (const id of ['#headline','#network-section','#target-section','#trace-section','#trend-section']) $(id).hidden = true;
  $('#capture-info').textContent = `Loading ${when(entry.at)}…`;
  const response = await fetch(`./data/${entry.file}`);
  if (!response.ok) throw new Error(String(response.status));
  const next = await response.json();
  if (current !== captureLoad) return;
  capture = next;
  renderCapture();
}

async function lookupProbe(event) {
  event.preventDefault();
  const current = ++probeLoad;
  const status = $('#probe-status');
  const result = $('#probe-result');
  result.hidden = true;
  let id;
  try {
    id = parseProbeId($('#probe-input').value);
  } catch (error) {
    status.textContent = error.message;
    return;
  }
  status.textContent = `Looking up probe ${id}…`;
  try {
    const response = await fetch(`https://atlas.ripe.net/api/v2/probes/${id}/`, { headers: { Accept: 'application/json' }, credentials:'omit', referrerPolicy:'no-referrer', signal:AbortSignal.timeout(12000) });
    if (current !== probeLoad) return;
    if (response.status === 404) { status.textContent = `There is no probe ${id}.`; return; }
    if (!response.ok) throw new Error(String(response.status));
    const probe = probeSummary(await response.json());
    if (current !== probeLoad) return;
    result.replaceChildren();
    const fact = (term, value) => { if (value) result.append(el('dt', term), el('dd', String(value))); };
    fact('Status', `${probe.connected ? '● ' : '○ '}${probe.status}${probe.since ? ` since ${when(probe.since)}` : ''}`);
    fact('Network', probe.asn ? `AS${probe.asn}` : null);
    fact('Country', probe.country);
    fact('Description', probe.description);
    fact('First connected', probe.firstConnected ? when(probe.firstConnected) : null);
    fact('Anchor', probe.anchor ? 'Yes' : null);
    result.hidden = false;
    status.textContent = '';
    const link = el('a', `Probe ${probe.id} on RIPE Atlas ↗`, { href: `https://atlas.ripe.net/probes/${probe.id}/`, rel: 'noreferrer' });
    status.append(link);
  } catch {
    if (current !== probeLoad) return;
    status.textContent = 'The lookup failed. Try again in a moment.';
  }
}

$('#probe-form').addEventListener('submit', lookupProbe);
for (const id of ['#only-left', '#network-filter', '#target-filter']) $(id).addEventListener('change', () => capture && renderTraces());

try {
  const response = await fetch('./data/index.json', { cache: 'no-cache' });
  if (!response.ok) throw new Error(String(response.status));
  index = await response.json();
  const captures = captureList(index);
  if (index.home_probe_id) $('#probe-input').value = String(index.home_probe_id);
  const select = $('#capture');
  for (const entry of captures) select.append(el('option', when(entry.at), { value: entry.id }));
  if (!captures.length) {
    $('#capture-info').textContent = '';
    $('#empty').hidden = false;
    select.disabled = true;
  } else {
    select.addEventListener('change', () => loadCapture(captures.find(c => c.id === select.value)).catch(() => {
      $('#capture-info').textContent = 'That capture could not be loaded.';
    }));
    await loadCapture(captures[0]);
  }
} catch {
  $('#capture-info').textContent = 'The captures could not be loaded.';
}
