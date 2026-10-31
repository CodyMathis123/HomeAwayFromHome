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
    assert len(query(RAW)) == 1, "Tombstone must never delete original raw sensor"
    # Thermal delta is valid only with measured coolant and room-air probes.
    therm = [
        f"sensordash_reading,host=CI,sensor=coolant,kind=Temperature value=31 {stamp + 2000}",
        f"sensordash_reading,host=CI,sensor=ambient,kind=Temperature value=22 {stamp + 2000}",
        f"sensordash_cooling_binding,host=CI,sensor=coolant,system=loop,system_name=Lab_Loop,component=cool,component_name=Coolant,role=coolant-probe,kind=Temperature value=1 {stamp + 2000}",
        f"sensordash_cooling_binding,host=CI,sensor=ambient,system=loop,system_name=Lab_Loop,component=room,component_name=Room_Air,role=room-ambient,kind=Temperature value=1 {stamp + 2000}",
    ]
    thermal_write = urllib.request.Request(
        BASE + "/api/v2/write?precision=ms", method="POST",
        data=("\n".join(therm) + "\n").encode("ascii"),
        headers={"Content-Type": "text/plain"},
    )
    with urllib.request.urlopen(thermal_write, timeout=10) as response:
        assert 200 <= response.status < 300
    def role(role_name: str) -> str:
        return (
            'max by(host,system) ((sensordash_cooling_binding_value{host="CI",system="loop",kind="Temperature",role="'
            + role_name + '"} == 1)'
            ' * on(host,sensor) group_left()'
            ' sensordash_reading_value{host="CI",kind="Temperature"})'
        )
    delta_query = role("coolant-probe") + " - on(host,system) " + role("room-ambient")
    for _ in range(20):
        delta = query(delta_query)
        if delta and abs(float(delta[0]["value"][1]) - 9.0) < 0.00001:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "Cooling delta did not equal 31°C - 22°C; "
            + repr({
                "delta": delta,
                "coolant": query(role("coolant-probe")),
                "ambient": query(role("room-ambient")),
                "coolant_raw": query('sensordash_reading_value{host="CI",sensor="coolant"}'),
                "ambient_raw": query('sensordash_reading_value{host="CI",sensor="ambient"}'),
                "coolant_bind": query('sensordash_cooling_binding_value{host="CI",role="coolant-probe"}'),
                "ambient_bind": query('sensordash_cooling_binding_value{host="CI",role="room-ambient"}'),
                "coolant_join_without_aggregate": query(
                    'sensordash_reading_value{host="CI",sensor="coolant",kind="Temperature"}'
                    ' * on(host,sensor) group_right()'
                    ' (sensordash_cooling_binding_value{host="CI",role="coolant-probe"} == 1)'
                ),
                "ambient_join_without_aggregate": query(
                    'sensordash_reading_value{host="CI",sensor="ambient",kind="Temperature"}'
                    ' * on(host,sensor) group_right()'
                    ' (sensordash_cooling_binding_value{host="CI",role="room-ambient"} == 1)'
                ),
                "coolant_expr": role("coolant-probe"),
                "ambient_expr": role("room-ambient"),
            })
        )
    print("PASS: real VictoriaMetrics joins, tombstones and coolant-to-room delta")


if __name__ == "__main__":
    main()
