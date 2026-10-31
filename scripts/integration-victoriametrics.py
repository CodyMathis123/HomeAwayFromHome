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
    ' * on(host,sensor) group_right(system,system_name,component,component_name,role)'
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
    assert len(joined) == 1, joined
    assert float(joined[0]["value"][1]) == 50.0, joined
    assert joined[0]["metric"]["system"] == "loop", joined
    assert joined[0]["metric"]["component_name"] == "CPU_Block", joined
    print("PASS: VictoriaMetrics ingest, Prometheus conversion and cooling binding join")


if __name__ == "__main__":
    main()
