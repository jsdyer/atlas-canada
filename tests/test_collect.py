import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import datetime as dt

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import collect  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ATLAS = collect.ATLAS

# Public addresses used as fake hops. Geolocation comes from the fake IPinfo table below.
GEO = {
    "24.244.0.1": ("AS852", "TELUS Communications Inc.", "CA"),
    "206.81.80.10": ("AS6461", "Zayo Bandwidth", "US"),      # inside the fake Seattle exchange prefix
    "64.125.0.1": ("AS6461", "Zayo Bandwidth", "US"),
    "199.212.24.1": ("AS22652", "Fibrenoire", "CA"),
    "142.0.0.10": ("AS813", "Example Anchor Host", "CA"),
    "142.0.0.20": ("AS813", "Example Anchor Host", "CA"),
}


def trace(probe, dst, hops):
    return {"prb_id": probe, "dst_addr": dst, "result": [
        {"hop": i + 1, "result": [{"from": ip, "rtt": 1.0}] if ip != "*" else [{"x": "*"}]} for i, ip in enumerate(hops)]}


class FakeNet:
    def __init__(self, results, anchors=None, create_status=201):
        self.results, self.calls, self.posts = results, [], []
        self.anchors = anchors if anchors is not None else [
            {"id": 1, "fqdn": "ca-yvr-as813.anchors.atlas.ripe.net", "ip_v4": "142.0.0.10", "city": "Vancouver", "country": "CA", "as_v4": 813},
            {"id": 2, "fqdn": "ca-yvr-2.anchors.atlas.ripe.net", "ip_v4": "142.0.0.11", "city": "Vancouver", "country": "CA", "as_v4": 813},
            {"id": 3, "fqdn": "ca-yul-as813.anchors.atlas.ripe.net", "ip_v4": "142.0.0.20", "city": "Montreal", "country": "CA", "as_v4": 813},
        ]
        self.create_status = create_status
        self.polls = 0

    def json(self, url, headers=None, method="GET", body=None):
        self.calls.append((method, url, headers or {}))
        if method == "POST" and url == f"{ATLAS}/measurements/":
            self.posts.append(body)
            return self.create_status, {"measurements": [100 + i for i in range(len(body["definitions"]))]}
        if url.startswith(f"{ATLAS}/anchors/"):
            return 200, {"results": self.anchors, "next": None}
        if url.startswith(f"{ATLAS}/probes/"):
            return 200, {"results": [{"id": 9001, "asn_v4": 852, "country_code": "CA", "description": "brix",
                                      "geometry": {"coordinates": [-123.1, 49.3]}},
                                     {"id": 9002, "asn_v4": 577, "country_code": "CA", "description": "x"}], "next": None}
        if "/results/" in url:
            mid = int(url.split("/measurements/")[1].split("/")[0])
            return 200, self.results.get(mid, [])
        if url.startswith(f"{ATLAS}/measurements/"):
            self.polls += 1
            return 200, {"status": {"id": 4 if self.polls > 2 else 2, "name": "Stopped" if self.polls > 2 else "Ongoing"}}
        if url.startswith("https://api.ipinfo.io/lite/"):
            ip = url.rsplit("/", 1)[1]
            if ip in GEO:
                asn, name, country = GEO[ip]
                return 200, {"asn": asn, "as_name": name, "country_code": country}
            return 200, {"ip": ip, "bogon": True}
        if url.startswith("https://www.peeringdb.com/api/ix?"):
            return 200, {"data": [{"id": 1, "name": "SIX Seattle", "city": "Seattle", "country": "US"}]}
        if url.startswith("https://www.peeringdb.com/api/ixlan?"):
            return 200, {"data": [{"id": 10, "ix_id": 1}]}
        if url.startswith("https://www.peeringdb.com/api/ixpfx?"):
            return 200, {"data": [{"ixlan_id": 10, "prefix": "206.81.80.0/22"}]}
        raise OSError(f"no route for {url}")


BOOMERANG = trace(9001, "142.0.0.10", ["192.168.1.1", "24.244.0.1", "206.81.80.10", "64.125.0.1", "*", "142.0.0.10"])
DOMESTIC = trace(9002, "142.0.0.20", ["10.0.0.1", "199.212.24.1", "142.0.0.20"])


class CollectTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = json.loads((ROOT / "config.json").read_text())
        config["extra_targets"] = []
        self.config = config
        self.save_config()

    def save_config(self):
        (self.root / "config.json").write_text(json.dumps(self.config))

    def run_once(self, net, env=None, when="2026-11-24T07:00:00+00:00"):
        env = {"RIPE_ATLAS_KEY": "k", "IPINFO_TOKEN": "t"} if env is None else env
        return collect.run(self.root, net, env, dt.datetime.fromisoformat(when), sleep=lambda s: None)

    def test_flags_canada_to_canada_paths_that_leave_canada(self):
        net = FakeNet({100: [DOMESTIC], 101: [BOOMERANG]})
        summary = self.run_once(net)
        self.assertEqual(summary["traces"], 2)
        self.assertEqual(summary["left_country"], 1)
        self.assertEqual(summary["foreign_transit"], [{"asn": "AS6461", "as_name": "Zayo Bandwidth", "traces": 1}])
        capture = json.loads((self.root / "web/data/runs/2026-11-24T0700Z.json").read_text())
        boomerang = next(t for t in capture["traces"] if t["probe"] == 9001)
        self.assertTrue(boomerang["left_country"])
        self.assertEqual(boomerang["countries"], ["CA", "US", "CA"])
        self.assertEqual(boomerang["exchanges"], ["SIX Seattle"])
        self.assertTrue(boomerang["reached"])
        self.assertEqual([h["ip"] for h in boomerang["hops"]][0], "24.244.0.1")  # private hop dropped
        self.assertEqual(capture["summary"]["by_probe_network"]["852"], {"traces": 1, "eligible": 1, "left_country": 1})

    def test_one_target_per_city_and_one_shared_request(self):
        net = FakeNet({100: [DOMESTIC], 101: [BOOMERANG]})
        self.run_once(net)
        self.assertEqual(len(net.posts), 1)
        definitions = net.posts[0]["definitions"]
        self.assertEqual([d["target"] for d in definitions], ["142.0.0.20", "142.0.0.10"])  # Montreal, Vancouver
        self.assertTrue(net.posts[0]["is_oneoff"])
        self.assertEqual(net.posts[0]["probes"][0], {"type": "country", "value": "CA", "requested": 10})
        post = next(c for c in net.calls if c[0] == "POST")
        self.assertEqual(post[2]["Authorization"], "Key k")

    def test_accepts_more_targets_than_the_defaults(self):
        anchors = [{"id": i, "fqdn": f"a{i}", "ip_v4": f"142.0.1.{i}", "city": f"City {i}", "country": "CA"} for i in range(30)]
        self.config.update(anchor_limit=20, extra_targets=[{"host": "cira.ca"}, {"host": "Example.CA", "label": "Ex"}])
        self.save_config()
        net = FakeNet({100: [DOMESTIC]}, anchors=anchors)
        self.run_once(net)
        targets = [d["target"] for d in net.posts[0]["definitions"]]
        self.assertEqual(len(targets), 22)
        self.assertEqual(targets[-2:], ["cira.ca", "example.ca"])

    def test_captures_accumulate_in_the_index(self):
        net = FakeNet({100: [DOMESTIC], 101: [BOOMERANG]})
        self.run_once(net, when="2026-11-24T07:00:00+00:00")
        self.config["anchor_limit"] = 1
        self.save_config()
        self.run_once(FakeNet({100: [DOMESTIC]}), when="2026-11-25T07:00:00+00:00")
        index = json.loads((self.root / "web/data/index.json").read_text())
        self.assertEqual([c["id"] for c in index["captures"]], ["2026-11-24T0700Z", "2026-11-25T0700Z"])
        self.assertEqual([c["targets"] for c in index["captures"]], [2, 1])

    def test_no_results_saves_nothing(self):
        with self.assertRaisesRegex(ValueError, "no traces"):
            self.run_once(FakeNet({}))
        self.assertFalse((self.root / "web/data/index.json").exists())

    def test_requires_atlas_key(self):
        with self.assertRaisesRegex(ValueError, "RIPE_ATLAS_KEY"):
            self.run_once(FakeNet({}), env={})

    def test_creation_failure_is_reported(self):
        with self.assertRaisesRegex(OSError, "Could not create"):
            self.run_once(FakeNet({}, create_status=403))

    def test_without_ipinfo_paths_have_no_countries(self):
        net = FakeNet({100: [DOMESTIC], 101: [BOOMERANG]})
        summary = self.run_once(net, env={"RIPE_ATLAS_KEY": "k"})
        self.assertFalse(any("ipinfo" in c[1] for c in net.calls))
        self.assertEqual(summary["left_country"], 1)  # still found via the exchange-point country
        self.assertGreater(summary["with_unknown_hops"], 0)

    def test_rejects_bad_config(self):
        for change in ({"probe_country": "Canada"}, {"address_family": 5}, {"probes_per_target": 0},
                       {"extra_targets": [{"host": "not a host"}]}, {"home_probe_id": "abc"}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    collect.validate_config({**self.config, **change})

    def test_hop_addresses_skips_timeouts_and_repeats(self):
        self.assertEqual(collect.hop_addresses(trace(1, "x", ["a", "*", "a", "b"])), ["a", "b"])


if __name__ == "__main__":
    unittest.main()

class AssessmentTests(unittest.TestCase):
    def test_missing_probe_metadata_and_incomplete_routes_not_eligible(self):
        target={'target':'142.0.0.10','label':'Test','country':'CA'}
        info={ip:{'asn':a,'as_name':n,'country':c} for ip,(a,n,c) in GEO.items()}
        missing=collect.analyse([(target,1,[BOOMERANG])], [target],info,[],{},'CA')[0]
        self.assertFalse(missing['eligible']);self.assertFalse(missing['left_country'])
        incomplete=trace(9001,'142.0.0.10',['24.244.0.1','64.125.0.1','*'])
        observed=collect.analyse([(target,1,[incomplete])],[target],info,[],{9001:{'country':'CA'}},'CA')[0]
        self.assertFalse(observed['eligible']);self.assertEqual(observed['unanswered_hops'],1)

    def test_ipv6_uses_ipv6_probe_asn(self):
        class Net:
            def json(self,*a):return 200,{'results':[{'id':1,'asn_v4':4,'asn_v6':6,'country_code':'CA'}]}
        self.assertEqual(collect.load_probes(Net(),[1],6)[1]['asn'],6)
