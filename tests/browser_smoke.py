"""Exercise the built page at a project subpath in Chromium, with sample captures and a mocked probe lookup."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def hop(ip, country, asn, name, ix=None):
    data = {"ip": ip, "country": country, "asn": asn, "as_name": name}
    if ix:
        data["ix"] = {"name": ix, "city": "Seattle", "country": "US"}
    return data


def capture(capture_id, at, left):
    traces = [
        {"measurement": 101, "probe": 9001, "probe_asn": 852, "probe_description": "x", "target": "142.0.0.10",
         "target_label": "Vancouver anchor <i>x</i>", "dst_addr": "142.0.0.10", "dst_country": "CA", "reached": True,
         "hops": [hop("24.244.0.1", "CA", "AS852", "TELUS"), hop("206.81.80.10", "US", "AS6461", "Zayo", "SIX Seattle"),
                  hop("142.0.0.10", "CA", "AS813", "Host")],
         "countries": ["CA", "US", "CA"], "left_country": left, "foreign_countries": ["US"] if left else [],
         "exchanges": ["SIX Seattle"], "unknown_hops": 0},
        {"measurement": 100, "probe": 9002, "probe_asn": 577, "probe_description": "y", "target": "142.0.0.20",
         "target_label": "Montreal anchor", "dst_addr": "142.0.0.20", "dst_country": "CA", "reached": False,
         "hops": [hop("199.212.24.1", "CA", "AS577", "Bell")], "countries": ["CA"], "left_country": False,
         "foreign_countries": [], "exchanges": [], "unknown_hops": 0},
    ]
    return {"schema": 1, "id": capture_id, "at": at, "geolocation": "ipinfo-lite", "exchange_prefixes": 1,
            "settings": {"probe_country": "CA", "probes_per_target": 10, "address_family": 4, "protocol": "ICMP"},
            "targets": [{"target": "142.0.0.10", "label": "Vancouver anchor", "measurement": 101},
                        {"target": "142.0.0.20", "label": "Montreal anchor", "measurement": 100}],
            "traces": traces,
            "summary": {"traces": 2, "left_country": int(left), "reached": 1, "with_unknown_hops": 0,
                        "by_probe_network": {"852": {"traces": 1, "left_country": int(left)}, "577": {"traces": 1, "left_country": 0}},
                        "foreign_transit": [{"asn": "AS6461", "as_name": "Zayo", "traces": 1}] if left else []}}


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    screenshots = ROOT / "browser-checks"
    screenshots.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        site = Path(temporary) / "atlas-canada"
        shutil.copytree(ROOT / "dist", site)
        (site / "data/runs").mkdir(parents=True, exist_ok=True)
        old, new = capture("2026-11-23T0941Z", "2026-11-23T09:41:00Z", False), capture("2026-11-24T0941Z", "2026-11-24T09:41:00Z", True)
        for c in (old, new):
            (site / f"data/runs/{c['id']}.json").write_text(json.dumps(c))
        (site / "data/index.json").write_text(json.dumps({"schema": 1, "home_probe_id": 9001, "captures": [
            {"id": c["id"], "at": c["at"], "file": f"runs/{c['id']}.json", "targets": 2, "traces": 2,
             "left_country": c["summary"]["left_country"], "geolocation": "ipinfo-lite"} for c in (old, new)]}))
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=temporary))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                executable = os.environ.get("CHROMIUM_EXECUTABLE")
                browser = p.chromium.launch(executable_path=executable) if executable else p.chromium.launch()
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("https://atlas.ripe.net/**", lambda route: route.fulfill(
                    status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                    body=json.dumps({"id": 9001, "status": {"name": "Connected", "since": "2026-11-20T00:00:00Z"},
                                     "asn_v4": 852, "country_code": "CA", "description": "<b>brix</b>"})))
                page.goto(f"http://127.0.0.1:{server.server_port}/atlas-canada/")
                page.wait_for_selector("#headline:not([hidden])")
                assert page.locator("#headline-pct").inner_text() == "50%"
                assert "2026-11-24 09:41 UTC" in page.locator("#capture-info").inner_text()
                assert page.locator("#traces li.trace").count() == 1
                assert "CA → US → CA" in page.locator("#traces").inner_text()
                assert "<i>x</i>" in page.locator("#traces").inner_text() and page.locator("#traces i").count() == 0
                assert page.locator("#trend rect").count() == 2
                page.locator("#only-left").uncheck()
                assert page.locator("#traces li.trace").count() == 2
                page.locator("#network-filter").select_option("AS577")
                assert page.locator("#traces li.trace").count() == 1
                page.locator("#capture").select_option("2026-11-23T0941Z")
                page.wait_for_function("document.querySelector('#headline-pct').textContent === '0%'")
                assert page.locator("#probe-input").input_value() == "9001"
                page.locator("#probe-form button").click()
                page.wait_for_selector("#probe-result:not([hidden])")
                assert "Connected" in page.locator("#probe-result").inner_text()
                assert page.locator("#probe-result b").count() == 0
                page.screenshot(path=str(screenshots / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                page.screenshot(path=str(screenshots / "mobile.png"), full_page=True)
                assert not errors, errors
                browser.close()
            print("Browser checks passed: captures, filters, trend, probe lookup, safe text and mobile layout")
        finally:
            server.shutdown()


if __name__ == "__main__":
    main()
