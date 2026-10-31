#!/usr/bin/env python3
"""Integration verification against an isolated, ephemeral VictoriaMetrics container.

Run after starting VictoriaMetrics locally at localhost:18428. Never touches
the user's Pi, credentials, WireGuard network or production DNS.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:18428"
RAW = 'sensordash_reading_value{host="CI",sensor="cpu_temp",kind="Temperature"}'
INFO = 'sensordash_sensor_info_value{host="CI",sensor="cpu_temp"} == 1'
BIND = 'sensordash_cooling_binding_value{host="CI",system="loop",role="heat-source"} == 1'
JOIN = (
    'sensordash_reading_value{host="CI",kind="Temperature"}'
    ' * on(host,sensor) group_right()'
    ' (sensordash_cooling_binding_value{host="CI",system="loop",kind="Temperature"} == 1)'
)
# Readable Grafana labels are joined at query time, not copied into raw IDs.
# The same sensor may be assigned to several cooling components.
NAMED_JOIN = (
    '(' + JOIN + ') * on(host,sensor) group_left(name,alias,device) '
    '(sensordash_sensor_info_value{host="CI"} == 1)'
)
NAMED_BINDINGS = (
    '(sensordash_cooling_binding_value{host="CI",system="loop"} == 1) '
    '* on(host,sensor) group_left(name,alias,device) '
    '(sensordash_sensor_info_value{host="CI"} == 1)'
)
POWER = 'sensordash_accessory_power_value{host="CI",system="loop",component="fans"}'
COMP = 'sensordash_component_info_value{host="CI",system="loop",component="fans"} == 1'
POWER_JOIN = (
    POWER + ' * on(host,system,component) group_left(component_name,system_name,role)'
    ' (sensordash_component_info_value{host="CI"} == 1)'
)


def query(expression: str) -> list:
    url = BASE + "/api/v1/query?" + urllib.parse.urlencode({"query": expression})
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.load(response)
    if data["status"] != "success":
        raise AssertionError(data)
    return data["data"]["result"]


def main() -> None:
    for _ in range(45):
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as response:
                if response.status == 200:
                    break
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise AssertionError("Ephemeral VictoriaMetrics failed to start")

    stamp = int(time.time() * 1000) - 3000
    lines = [
        f"sensordash_reading,host=CI,sensor=cpu_temp,kind=Temperature value=50 {stamp}",
        f"sensordash_sensor_info,host=CI,sensor=cpu_temp,kind=Temperature,name=CPU_Package,alias=CPU_Temp,device=Test_PC value=1 {stamp}",
        f"sensordash_cooling_binding,host=CI,sensor=cpu_temp,system=loop,system_name=Lab_Loop,component=cpu,component_name=CPU_Block,role=heat-source,kind=Temperature value=1 {stamp}",
        f"sensordash_cooling_binding,host=CI,sensor=cpu_temp,system=loop,system_name=Lab_Loop,component=aux,component_name=Auxiliary,role=other,kind=Temperature value=1 {stamp}",
        f"sensordash_component_info,host=CI,system=loop,component=fans,system_name=Lab_Loop,component_name=Radiator_Bank,role=fan-group value=1 {stamp}",
        f"sensordash_accessory_power,host=CI,system=loop,component=fans,source=measured,revision=1111111111111111 value=12.25 {stamp}",
        f"sensordash_accessory_power,host=CI,system=loop,component=fans,source=calibrated,revision=2222222222222222 value=8.5 {stamp + 1500}",
    ]
    req = urllib.request.Request(
        BASE + "/api/v2/write?precision=ms",
        data=("\n".join(lines) + "\n").encode("ascii"),
        headers={"Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        assert 200 <= response.status < 300, response.status

    for _ in range(30):
        results = [query(q) for q in (RAW, INFO, BIND, JOIN)]
        if all(results):
            break
        time.sleep(1)
    else:
        raise AssertionError(
            "VictoriaMetrics failed to expose raw, catalog, binding and joined series: "
            + str([len(x) for x in results])
        )

    assert float(results[0][0]["value"][1]) == 50.0, results[0]
    joined = results[3]
    assert len(joined) == 2, joined
    assert all(float(p["value"][1]) == 50.0 for p in joined), joined
    assert {p["metric"]["system"] for p in joined} == {"loop"}, joined
    assert {p["metric"]["component_name"] for p in joined} == {"CPU_Block", "Auxiliary"}, joined

    # Both assignments for this single sensor retain friendly catalog names.
    # The read-only association table and series legends use these labels.
    for expression in (NAMED_BINDINGS, NAMED_JOIN):
        named = query(expression)
        assert len(named) == 2, named
        assert {x["metric"]["component_name"] for x in named} == {"CPU_Block", "Auxiliary"}, named
        assert {x["metric"]["name"] for x in named} == {"CPU_Package"}, named
        assert {x["metric"]["alias"] for x in named} == {"CPU_Temp"}, named
        assert {x["metric"]["device"] for x in named} == {"Test_PC"}, named
        assert all(float(x["value"][1]) in (1.0, 50.0) for x in named), named

    # A removed assignment receives the same label set with value=0. The
    # no-bool comparison filters it out, while preserving the single raw series.
    removed = (
        f"sensordash_cooling_binding,host=CI,sensor=cpu_temp,system=loop,"
        f"system_name=Lab_Loop,component=aux,component_name=Auxiliary,"
        f"role=other,kind=Temperature value=0 {stamp + 1500}\n"
    ).encode("ascii")
    deletion = urllib.request.Request(
        BASE + "/api/v2/write?precision=ms", data=removed, method="POST",
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(deletion, timeout=10) as response:
        assert 200 <= response.status < 300
    for _ in range(20):
        remaining = query(JOIN)
        if len(remaining) == 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("Old cooling binding remained active: " + repr(remaining))
    assert remaining[0]["metric"]["component_name"] == "CPU_Block", remaining
    assert len(query(NAMED_JOIN)) == 1, "Deleted association must disappear from friendly labels"
    assert len(query(NAMED_BINDINGS)) == 1, "Deleted assignment must disappear from inventory"
    assert len(query(RAW)) == 1, "Tombstone must never delete original raw sensor"
    for _ in range(20):
        values = [query(POWER), query(COMP), query(POWER_JOIN)]
        if len(values[0]) == 2 and len(values[1]) == 1 and len(values[2]) == 2:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("Derived power or component metadata missing: " + repr(values))
    with_labels = values[2]
    assert {x["metric"]["component_name"] for x in with_labels} == {"Radiator_Bank"}, with_labels
    assert {x["metric"]["source"] for x in with_labels} == {"measured", "calibrated"}, with_labels
    assert {x["metric"]["revision"] for x in with_labels} == {"1111111111111111", "2222222222222222"}, with_labels
    assert {float(x["value"][1]) for x in with_labels} == {12.25, 8.5}, with_labels
    print("PASS: VictoriaMetrics raw + derived watts, provenance, component join and removal tombstones")


if __name__ == "__main__":
    main()
