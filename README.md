# Atlas Canada

Daily RIPE Atlas traceroutes from Canadian probes to Canadian destinations,
showing which **Canada-to-Canada paths pass through another country** (boomerang
routing), which ISPs' probes detour most, and which foreign networks and
exchange points carry the detours. Published at `coolcode.ca/atlas-canada/`.

## Using the tool

<!-- coolcode:guide -->
Pick a capture date to see that day's share of Canadian-probe traceroutes to Canadian destinations that crossed another country, broken down by the probe's network and by destination. Each hop is located with IPinfo Lite and PeeringDB exchange-point prefixes; router addresses are sometimes geolocated to the operator's home country, so the share is a lower bound, and hops with no location are never counted as foreign. Every row links to the original measurement on RIPE Atlas.
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
4. A trace "left Canada" when the probe is in Canada, the destination is in
   Canada, and at least one located hop is in another country.
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
**Browser tool · Check and publish**. Earlier captures are kept unchanged, so
captures with different targets or probe counts sit side by side; each one
records the settings it used.

**Schedule:** `.github/workflows/collect.yml` runs daily at 09:41 UTC
(`41 9 * * *`). `.github/workflows/schedule-guard.yml` keeps the schedule enabled
in this public repository. Also register `collect.yml` with the independent
Linux scheduler via `workflow_dispatch`, with an expected freshness of one day.
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
