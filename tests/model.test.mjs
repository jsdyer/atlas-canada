import test from 'node:test';
import assert from 'node:assert/strict';
import { captureList, networkRows, targetRows, pathText, foreignNetworks, filterTraces, trend, parseProbeId, probeSummary, percent } from '../web/model.mjs';

const hop = (ip, country, asn, as_name) => ({ ip, country, asn, as_name });
const capture = {
  id: '2026-11-24T0700Z',
  summary: { by_probe_network: { 852: { traces: 2, eligible: 2, left_country: 1 }, 577: { traces: 1, eligible: 1, left_country: 0 }, unknown: { traces: 1, eligible: 1, left_country: 0 } } },
  traces: [
    { probe: 1, probe_asn: 852, target: '142.0.0.10', target_label: 'Vancouver anchor', measurement: 101, eligible: true, left_country: true,
      countries: ['CA', 'US', 'CA'], hops: [hop('a', 'CA', 'AS852', 'TELUS'), hop('b', 'US', 'AS6461', 'Zayo'), hop('c', 'US', 'AS6461', 'Zayo'), hop('d', 'CA', 'AS813', 'Host')] },
    { probe: 2, probe_asn: 852, target: '142.0.0.20', target_label: 'Montreal anchor', measurement: 100, eligible: true, left_country: false, countries: ['CA'], hops: [hop('a', 'CA', 'AS852', 'TELUS')] },
    { probe: 3, probe_asn: 577, target: '142.0.0.20', target_label: 'Montreal anchor', measurement: 100, eligible: true, left_country: false, countries: [], hops: [] },
    { probe: 4, probe_asn: null, target: '142.0.0.20', target_label: 'Montreal anchor', measurement: 100, eligible: true, left_country: false, countries: [], hops: [] },
  ],
};

test('newest capture first', () => {
  assert.deepEqual(captureList({ captures: [{ id: 'a' }, { id: 'c' }, { id: 'b' }] }).map(c => c.id), ['c', 'b', 'a']);
  assert.throws(() => captureList({}), TypeError);
});

test('network rows use AS names seen in hops and sort by share', () => {
  const rows = networkRows(capture);
  assert.deepEqual(rows[0], { asn: 'AS852', name: 'TELUS', traces: 2, eligible: 2, leftCountry: 1, pct: 50 });
  assert.equal(rows.find(r => r.asn === null).traces, 1);
});

test('target rows count each destination', () => {
  const rows = targetRows(capture);
  assert.deepEqual(rows.map(r => [r.label, r.traces, r.pct]), [['Vancouver anchor', 1, 100], ['Montreal anchor', 3, 0]]);
});

test('path text and foreign networks', () => {
  assert.equal(pathText(capture.traces[0]), 'CA → US → CA');
  assert.equal(pathText(capture.traces[2]), 'no located hops');
  assert.deepEqual(foreignNetworks(capture.traces[0]), ['AS6461 Zayo']);
});

test('filters combine', () => {
  assert.equal(filterTraces(capture, { onlyLeft: true }).length, 1);
  assert.equal(filterTraces(capture, { network: 'AS852' }).length, 2);
  assert.equal(filterTraces(capture, { target: '142.0.0.20', network: 'AS577' }).length, 1);
});

test('trend is oldest first and handles any number of captures', () => {
  const captures = Array.from({ length: 400 }, (_, i) => ({ id: `2026-${String(i).padStart(4, '0')}`, traces: 10, eligible: 10, left_country: i % 10 }));
  const points = trend({ captures: captures.reverse() });
  assert.equal(points.length, 400);
  assert.equal(points[0].id, '2026-0000');
  assert.equal(points[3].pct, 30);
});

test('percent avoids division by zero', () => { assert.equal(percent(1, 0), 0); assert.equal(percent(1, 3), 33.3); });

test('probe ids and summaries', () => {
  assert.equal(parseProbeId(' #1234 '), 1234);
  assert.throws(() => parseProbeId('abc'));
  assert.throws(() => parseProbeId('0'));
  const probe = probeSummary({ id: 7, status: { name: 'Connected', since: '2026-11-01T00:00:00Z' }, asn_v4: 852, country_code: 'CA', is_anchor: false });
  assert.equal(probe.connected, true);
  assert.equal(probe.asn, 852);
  assert.throws(() => probeSummary({}));
});

test('unknown endpoint eligibility does not become a zero percent result', () => {
  assert.equal(networkRows({summary:{by_probe_network:{unknown:{traces:3,left_country:0,eligible:0}}}})[0].pct, null);
  assert.deepEqual(trend({captures:[{id:'old',traces:3,left_country:0}]}), []);
});
