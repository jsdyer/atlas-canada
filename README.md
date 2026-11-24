# Atlas Canada

Daily RIPE Atlas traceroutes from Canadian probes, highlighting **possible
cross-border paths** using hop geolocation and exchange-prefix records.
Published at `coolcode.ca/atlas-canada/`. Geolocation is evidence to investigate,
not proof of a router's physical location or a country's share of all traffic.

## Using the tool

<!-- coolcode:guide -->
Choose a capture to inspect possible cross-border routes by probe network or destination. The percentage includes only completed traces with known Canadian endpoints; unanswered hops, unlocated addresses and excluded traces are shown separately. IPinfo geolocation and PeeringDB exchange prefixes can place a router in the wrong country, so the result can overcount or undercount real border crossings. Expand a route and follow its RIPE Atlas measurement link to inspect the evidence.
<!-- /coolcode:guide -->

- **Capture date** chooses which daily run to show. The headline, tables and
  traceroutes all belong to that capture; the date is shown under the selector.
- **Over time** charts the detour share across every capture.
- **Traceroutes** lists each path as a country sequence (`CA → US → CA`) with the
  foreign networks and exchange points on it. Expand a trace to see every public hop.
- **Probe status** looks up any probe on atlas.ripe.net. If `home_probe_id` is set
  in `config.json`, it is filled in automatically.

## How a capture works

1. Picks Canadian RIPE Atlas anchors, one per city (up to `anchor_limit`), plus
   any `extra_targets`.
2. Creates one-off traceroutes from `probes_per_target` probes in `probe_country`
   to each target, in a single request, tagged `atlas-canada`.
3. Waits for them to finish (up to `max_wait_minutes`), downloads the results,
   and looks up each public hop: country and network from IPinfo Lite, exchange
   points from PeeringDB, probe networks from the Atlas probe API.
4. A completed trace is eligible when both endpoints are known Canadian. It is
   flagged when at least one public hop is located outside Canada. The stored
   `left_country` field means this flag, not a proven physical border crossing.
   Only the first responding address at each hop is represented; alternate
   replies and return paths are outside the analysis.
5. Saves `web/data/runs/<capture-id>.json`, adds it to `web/data/index.json`,
   commits, and starts the Pages workflow. If no results come back, nothing is
   saved and the published page stays as it was.

Each run spends Atlas credits on roughly *targets × probes_per_target*
traceroute results (about 90 with the defaults).

## Setup

1. **Settings → Secrets and variables → Actions → New repository secret:**

   | Secret | Required | Used for |
   | --- | --- | --- |
   | `RIPE_ATLAS_KEY` | Yes | Creating measurements. Give the key *Schedule a new measurement* and an expiry date |
   | `IPINFO_TOKEN` | Strongly recommended | Country and network of every hop. Without it only exchange points are located |
   | `PEERINGDB_API_KEY` | No | Higher PeeringDB rate limits |

2. **Settings → Actions → General → Workflow permissions → Read and write.**
3. **Settings → Pages → Source → GitHub Actions.**
4. Run **Actions → Atlas Canada · Collect → Run workflow** for the first capture
   (it takes about 10–20 minutes, mostly waiting for Atlas).

## Expand or customize

All settings are in `config.json`:

| Setting | Default | Meaning |
| --- | --- | --- |
| `probe_country` | `CA` | Country the probes are chosen from, and the "home" country for detours |
| `probes_per_target` | `10` | Probes per destination (1–100) |
| `address_family` | `4` | `4` or `6` |
| `protocol` | `ICMP` | `ICMP`, `UDP` or `TCP` |
| `anchor_country`, `anchor_limit` | `CA`, `8` | Anchors used as destinations, one per city. `0` turns anchors off |
| `extra_targets` | `canada.ca` | More destinations: `{"host": "...", "label": "...", "country": "CA"}`. `country` is optional; without it the destination's country comes from IPinfo |
| `home_probe_id` | `null` | Your probe's number, pre-filled in the probe lookup |
| `max_wait_minutes`, `poll_seconds` | `25`, `30` | How long to wait for Atlas |

Example: add CIRA and a Halifax destination, and use 20 probes:

```json
"probes_per_target": 20,
"extra_targets": [
  {"host": "canada.ca", "label": "Government of Canada (canada.ca)"},
  {"host": "cira.ca", "label": "CIRA", "country": "CA"},
  {"host": "www.dal.ca", "label": "Dalhousie University", "country": "CA"}
]
```

Commit the change; the next daily run uses it. To run now, use **Atlas Canada ·
Collect → Run workflow**. A successful run commits the capture and starts
**Browser tool · Check and publish**. Earlier capture timestamps and evidence are preserved, so
captures with different targets or probe counts sit side by side; each one
records the settings it used.

**Schedule:** `.github/workflows/collect.yml` runs daily at 09:41 UTC
(`41 9 * * *`). `.github/workflows/schedule-guard.yml` keeps the schedule enabled
in this public repository. Also register `collect.yml` with the independent
Linux scheduler via `workflow_dispatch`, with an expected daily cadence. `web/data/status.json` exposes `last_success` using
the original capture time; editing the page never makes old data appear fresh.
Re-run `python3 scripts/add_schedule_guard.py --repository jsdyer/atlas-canada .github/workflows/collect.yml`
if you rename the collector.

Not included yet: on-demand "trace to my host" from the page, which needs a
server-side key (a future Cloudflare Worker).

## Development

```sh
node --test tests/*.test.mjs
python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 -m http.server 8000 --directory dist
```

The collector tests use a fake network and never create real measurements.
For browser checks: `pip install -r tests/browser-requirements.txt`,
`python3 -m playwright install chromium`, then `python3 tests/browser_smoke.py`.

## Files

| Path | Purpose |
| --- | --- |
| `config.json` | Targets, probe selection and limits |
| `scripts/collect.py` | Creates measurements, analyses paths, writes captures |
| `web/data/index.json` | List of captures |
| `web/data/runs/` | One file per capture |
| `web/` | Page, styles, DOM code (`app.mjs`) and pure logic (`model.mjs`) |
| `.github/workflows/collect.yml` | Daily collection, commit and publish |
| `.github/workflows/pages.yml` | Test, build and deploy to Pages |
| `.github/workflows/schedule-guard.yml` | Keeps the schedule enabled |
| `tool.json` | Catalogue record for coolcode.ca |

## Collection maintenance

The catalogue, imported README guide, repository audit, sitemap, Cloudflare
visitor registry and Linux refresh watchdog are managed in
[jsdyer/jsdyer.github.io](https://github.com/jsdyer/jsdyer.github.io).
Follow its [tool lifecycle checklist](https://github.com/jsdyer/jsdyer.github.io/blob/main/docs/tool-lifecycle.md)
when changing this tool's identity, source, deployment, inputs or refresh schedule.
The independent scheduler must actually be installed on the Linux server; a
GitHub schedule guard alone cannot guarantee refreshes continue.

`python3 scripts/reanalyse.py` reassesses older captures from their existing
public measurement results and current probe metadata without spending credits.
It preserves timestamps and saved hop geolocation. Changes to this repair script
trigger the collector workflow in read-only reanalysis mode, then publish.
