#!/usr/bin/env python3
"""Run one round of RIPE Atlas traceroutes from Canadian probes and save a dated capture.

Run by .github/workflows/collect.yml. Uses only Python's standard library.
Secrets (read from the environment):
  RIPE_ATLAS_KEY     required: an Atlas key with "Schedule a new measurement"
  IPINFO_TOKEN       recommended: country and network of every hop (IPinfo Lite)
  PEERINGDB_API_KEY  optional: higher PeeringDB rate limits for exchange-point labels
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import ipaddress
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ATLAS = "https://atlas.ripe.net/api/v2"
SCHEMA = 1
HOST = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}")
TERMINAL_STATUS = {4, 5, 6, 7, 8, 9}  # stopped, forced to stop, no suitable probes, failed, denied, cancelled


class Net:
    def __init__(self, user_agent: str, timeout: float = 30):
        self.user_agent, self.timeout = user_agent, timeout

    def json(self, url, headers=None, method="GET", body=None):
        headers = {"User-Agent": self.user_agent, "Accept": "application/json", **(headers or {})}
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content = response.read()
        try:
            return response.status, json.loads(content) if content else None
        except ValueError:
            return response.status, None


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(moment: dt.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def validate_config(config: dict) -> None:
    if not re.fullmatch(r"[A-Z]{2}", str(config.get("probe_country", ""))):
        raise ValueError("probe_country must be a two-letter country code")
    if config.get("address_family") not in (4, 6):
        raise ValueError("address_family must be 4 or 6")
    if config.get("protocol") not in ("ICMP", "UDP", "TCP"):
        raise ValueError("protocol must be ICMP, UDP or TCP")
    for key, low, high in (("probes_per_target", 1, 100), ("anchor_limit", 0, 50)):
        if not isinstance(config.get(key), int) or not low <= config[key] <= high:
            raise ValueError(f"{key} must be a whole number from {low} to {high}")
    for extra in config.get("extra_targets", []):
        host = str(extra.get("host", "")).lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not HOST.fullmatch(host):
                raise ValueError(f"extra target is not a hostname or IP address: {host!r}")
    if config.get("home_probe_id") is not None and not isinstance(config["home_probe_id"], int):
        raise ValueError("home_probe_id must be a probe number or null")


def paged(net, url, headers=None, limit=20):
    for _ in range(limit):
        status, data = net.json(url, headers)
        if status != 200 or not isinstance(data, dict):
            raise OSError(f"Atlas request failed (HTTP {status}): {url}")
        yield from data.get("results", [])
        url = data.get("next")
        if not url:
            return


def choose_targets(net, config) -> list[dict]:
    """Canadian anchors, one per city (up to anchor_limit), plus any extra targets."""
    targets, cities = [], set()
    if config["anchor_limit"]:
        query = urllib.parse.urlencode({"country": config["anchor_country"], "page_size": 200})
        anchors = sorted(paged(net, f"{ATLAS}/anchors/?{query}"), key=lambda a: (str(a.get("city")), a.get("id", 0)))
        family = "ip_v4" if config["address_family"] == 4 else "ip_v6"
        for anchor in anchors:
            city = anchor.get("city") or "Unknown"
            if city in cities or not anchor.get(family) or anchor.get("is_disabled"):
                continue
            cities.add(city)
            targets.append({"target": anchor[family], "label": f"{city} anchor ({anchor.get('fqdn')})",
                            "kind": "anchor", "city": city, "country": anchor.get("country"),
                            "asn": anchor.get("as_v4" if family == "ip_v4" else "as_v6")})
            if len(targets) >= config["anchor_limit"]:
                break
    for extra in config.get("extra_targets", []):
        targets.append({"target": extra["host"].lower(), "label": extra.get("label") or extra["host"],
                        "kind": "extra", "city": None, "country": extra.get("country"), "asn": None})
    if not targets:
        raise ValueError("No targets: raise anchor_limit or add extra_targets")
    return targets


def create_measurements(net, key, config, targets) -> list[int]:
    definitions = [{
        "target": t["target"], "type": "traceroute", "af": config["address_family"],
        "protocol": config["protocol"], "description": f"atlas-canada: {t['label']}"[:250],
        "tags": [config["measurement_tag"]], "resolve_on_probe": False,
    } for t in targets]
    body = {"definitions": definitions, "is_oneoff": True,
            "probes": [{"type": "country", "value": config["probe_country"], "requested": config["probes_per_target"]}]}
    status, data = net.json(f"{ATLAS}/measurements/", {"Authorization": f"Key {key}"}, "POST", body)
    ids = (data or {}).get("measurements") if isinstance(data, dict) else None
    if status not in (200, 201) or not isinstance(ids, list) or len(ids) != len(targets):
        detail = json.dumps(data)[:400] if data else ""
        raise OSError(f"Could not create measurements (HTTP {status}) {detail}")
    return ids


def wait_for(net, ids, config, sleep=time.sleep, clock=time.monotonic) -> dict:
    deadline = clock() + config["max_wait_minutes"] * 60
    statuses = {}
    pending = set(ids)
    while pending:
        for mid in sorted(pending):
            status, data = net.json(f"{ATLAS}/measurements/{mid}/")
            if status == 200 and isinstance(data, dict):
                state = data.get("status", {})
                statuses[mid] = state.get("name")
                if state.get("id") in TERMINAL_STATUS:
                    pending.discard(mid)
        if not pending or clock() >= deadline:
            break
        sleep(config["poll_seconds"])
    return statuses


def hop_addresses(result: dict) -> list[str]:
    """The first responding address at each hop, in order, without repeats."""
    hops = []
    for hop in result.get("result", []) or []:
        replies = [r.get("from") for r in hop.get("result", []) or [] if isinstance(r, dict) and r.get("from")]
        if replies and replies[0] not in hops:
            hops.append(replies[0])
    return hops


def is_global(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def lookup_ips(net, ips, token) -> dict:
    if not token:
        return {}

    def one(ip):
        status, data = net.json(f"https://api.ipinfo.io/lite/{ip}", {"Authorization": f"Bearer {token}"})
        if status == 200 and isinstance(data, dict) and not data.get("bogon"):
            return ip, {"asn": data.get("asn"), "as_name": data.get("as_name"), "country": data.get("country_code")}
        return ip, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        return {ip: info for ip, info in pool.map(one, sorted(ips)) if info}


def load_exchanges(net, key) -> list:
    """PeeringDB exchange-point prefixes as (network, name, city, country)."""
    headers = {"Authorization": f"Api-Key {key}"} if key else {}
    base = "https://www.peeringdb.com/api"
    try:
        responses = [net.json(f"{base}/{path}", headers) for path in
                     ("ix?fields=id,name,city,country", "ixlan?fields=id,ix_id", "ixpfx?fields=ixlan_id,prefix")]
    except OSError:
        return []
    if any(status != 200 or not isinstance(data, dict) for status, data in responses):
        return []
    (_, ix), (_, ixlan), (_, ixpfx) = responses
    exchanges = {i["id"]: i for i in ix.get("data", [])}
    lan_to_ix = {lan["id"]: lan["ix_id"] for lan in ixlan.get("data", [])}
    prefixes = []
    for prefix in ixpfx.get("data", []):
        exchange = exchanges.get(lan_to_ix.get(prefix.get("ixlan_id")))
        try:
            network = ipaddress.ip_network(prefix["prefix"], strict=False)
        except (KeyError, ValueError):
            continue
        if exchange:
            prefixes.append((network, exchange.get("name"), exchange.get("city"), exchange.get("country")))
    return prefixes


def exchange_for(ip: str, prefixes) -> dict | None:
    address = ipaddress.ip_address(ip)
    for network, name, city, country in prefixes:
        if address.version == network.version and address in network:
            return {"name": name, "city": city, "country": country}
    return None


def load_probes(net, ids) -> dict:
    probes = {}
    ids = sorted(set(ids))
    for start in range(0, len(ids), 100):
        chunk = ",".join(map(str, ids[start:start + 100]))
        query = urllib.parse.urlencode({"id__in": chunk, "fields": "id,asn_v4,asn_v6,country_code,description,geometry",
                                        "page_size": 100})
        for probe in paged(net, f"{ATLAS}/probes/?{query}"):
            coords = (probe.get("geometry") or {}).get("coordinates") or [None, None]
            probes[probe["id"]] = {"asn": probe.get("asn_v4") or probe.get("asn_v6"),
                                   "country": probe.get("country_code"),
                                   "description": (probe.get("description") or "")[:120],
                                   "lon": coords[0], "lat": coords[1]}
    return probes


def analyse(results_by_target, targets, ip_info, exchanges, probes, home_country) -> list[dict]:
    traces = []
    for target, measurement_id, results in results_by_target:
        for result in results:
            hops = [ip for ip in hop_addresses(result) if is_global(ip)]
            path = []
            for ip in hops:
                info = ip_info.get(ip, {})
                hop = {"ip": ip, "asn": info.get("asn"), "as_name": info.get("as_name"), "country": info.get("country")}
                exchange = exchange_for(ip, exchanges)
                if exchange:
                    hop["ix"] = exchange
                    hop["country"] = hop["country"] or exchange["country"]
                path.append(hop)
            dst = result.get("dst_addr")
            dst_country = target["country"] or ip_info.get(dst, {}).get("country")
            countries = []
            for hop in path:
                if hop["country"] and (not countries or countries[-1] != hop["country"]):
                    countries.append(hop["country"])
            last = result.get("result", [])[-1:] or [{}]
            reached = any(isinstance(r, dict) and r.get("from") == dst for r in last[0].get("result", []) or [])
            probe = probes.get(result.get("prb_id"), {})
            src_country = probe.get("country") or home_country
            foreign = sorted({h["country"] for h in path if h["country"] and h["country"] != home_country})
            traces.append({
                "measurement": measurement_id, "probe": result.get("prb_id"), "probe_asn": probe.get("asn"),
                "probe_description": probe.get("description"), "target": target["target"], "target_label": target["label"],
                "dst_addr": dst, "dst_country": dst_country, "reached": reached, "hops": path, "countries": countries,
                "left_country": src_country == home_country and dst_country == home_country and bool(foreign),
                "foreign_countries": foreign,
                "exchanges": sorted({h["ix"]["name"] for h in path if h.get("ix")}),
                "unknown_hops": sum(1 for h in path if not h["country"]),
            })
    return traces


def summarize(traces, home_country: str) -> dict:
    by_network = {}
    for trace in traces:
        entry = by_network.setdefault(str(trace["probe_asn"] or "unknown"), {"traces": 0, "left_country": 0})
        entry["traces"] += 1
        entry["left_country"] += trace["left_country"]
    transit = {}
    for trace in traces:
        if trace["left_country"]:
            for asn in {h["asn"] for h in trace["hops"] if h["asn"] and h["country"] and h["country"] != home_country}:
                transit[asn] = transit.get(asn, 0) + 1
    names = {h["asn"]: h["as_name"] for t in traces for h in t["hops"] if h["asn"]}
    return {"traces": len(traces), "left_country": sum(t["left_country"] for t in traces),
            "reached": sum(t["reached"] for t in traces),
            "with_unknown_hops": sum(bool(t["unknown_hops"]) for t in traces),
            "by_probe_network": by_network,
            "foreign_transit": [{"asn": asn, "as_name": names.get(asn), "traces": count}
                                for asn, count in sorted(transit.items(), key=lambda kv: (-kv[1], kv[0]))[:20]]}


def validate_capture(capture: dict) -> None:
    if capture.get("schema") != SCHEMA or not isinstance(capture.get("traces"), list) or not capture["traces"]:
        raise ValueError("capture has no traces; nothing was saved")
    for trace in capture["traces"]:
        if not isinstance(trace.get("hops"), list) or "left_country" not in trace:
            raise ValueError("capture contains a malformed trace")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as handle:
        json.dump(data, handle, indent=1, ensure_ascii=False, sort_keys=True)
        handle.write("\n")
    os.replace(handle.name, path)


def run(root: Path, net, env: dict, now: dt.datetime | None = None, sleep=time.sleep, clock=time.monotonic) -> dict:
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    validate_config(config)
    key = env.get("RIPE_ATLAS_KEY")
    if not key:
        raise ValueError("RIPE_ATLAS_KEY is not set; add it under Settings → Secrets and variables → Actions")
    now = now or utc_now()
    targets = choose_targets(net, config)
    ids = create_measurements(net, key, config, targets)
    print(f"Created measurements {ids}")
    statuses = wait_for(net, ids, config, sleep, clock)
    results_by_target = []
    for target, mid in zip(targets, ids):
        status, data = net.json(f"{ATLAS}/measurements/{mid}/results/?format=json", {"Authorization": f"Key {key}"})
        results = data if status == 200 and isinstance(data, list) else []
        results_by_target.append((target, mid, results))
        print(f"{mid}: {statuses.get(mid)} · {len(results)} results · {target['label']}")
    all_results = [r for _, _, results in results_by_target for r in results]
    hops = {ip for r in all_results for ip in hop_addresses(r) if is_global(ip)}
    hops |= {r["dst_addr"] for r in all_results if r.get("dst_addr") and is_global(r["dst_addr"])}
    ip_info = lookup_ips(net, hops, env.get("IPINFO_TOKEN"))
    exchanges = load_exchanges(net, env.get("PEERINGDB_API_KEY"))
    probes = load_probes(net, [r["prb_id"] for r in all_results if r.get("prb_id")]) if all_results else {}
    traces = analyse(results_by_target, targets, ip_info, exchanges, probes, config["probe_country"])
    capture_id = now.strftime("%Y-%m-%dT%H%MZ")
    capture = {
        "schema": SCHEMA, "id": capture_id, "at": iso(now),
        "settings": {k: config[k] for k in ("probe_country", "probes_per_target", "address_family", "protocol")},
        "geolocation": "ipinfo-lite" if env.get("IPINFO_TOKEN") else None,
        "exchange_prefixes": len(exchanges),
        "targets": [{**t, "measurement": mid, "status": statuses.get(mid)} for t, mid in zip(targets, ids)],
        "traces": traces, "summary": summarize(traces, config["probe_country"]),
    }
    validate_capture(capture)
    write_json(root / "web/data/runs" / f"{capture_id}.json", capture)
    index_path = root / "web/data/index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"schema": SCHEMA, "captures": []}
    index["captures"] = [c for c in index["captures"] if c["id"] != capture_id] + [{
        "id": capture_id, "at": capture["at"], "file": f"runs/{capture_id}.json", "targets": len(targets),
        "traces": capture["summary"]["traces"], "left_country": capture["summary"]["left_country"],
        "geolocation": capture["geolocation"]}]
    index["captures"].sort(key=lambda c: c["id"])
    index["home_probe_id"] = config.get("home_probe_id")
    write_json(index_path, index)
    return capture["summary"]


def main():
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    summary = run(ROOT, Net(config["user_agent"]), dict(os.environ))
    print(f"{summary['traces']} traces, {summary['left_country']} left the country")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Collection failed: {exc}")
