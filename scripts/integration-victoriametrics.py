#!/usr/bin/env python3
"""Integration verification against an isolated, ephemeral VictoriaMetrics container.

Run after starting VictoriaMetrics locally at localhost:18428. Never touches
the user's Pi, credentials, WireGuard network or production DNS.
"""
import json
from pathlib import Path
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
    # Bypass short-lived query caches for immediate write/read assertions.
    url = BASE + "/api/v1/query?" + urllib.parse.urlencode({"query": expression, "nocache": "1"})
    with urllib.request.urlopen(url, timeout=3) as response:
        data = json.load(response)
    if data["status"] != "success":
        raise AssertionError(data)
    return data["data"]["result"]


def query_range(expression: str, start: float, end: float) -> list:
    url = BASE + "/api/v1/query_range?" + urllib.parse.urlencode({
        "query": expression, "start": start, "end": end,
        "step": 10, "nocache": "1"
    })
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.load(response)
    assert data["status"] == "success", data
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
        f"sensordash_reading,host=CI,sensor=/hdd/0/temperature/0,kind=Temperature value=38 {stamp}",
        f"sensordash_sensor_info,host=CI,sensor=/hdd/0/temperature/0,kind=Temperature,name=NVMe_Temp,alias=Primary_Drive,device=FireCuda,device_type=Storage value=1 {stamp}",
        f"sensordash_event,host=CI,sensor=cpu_temp,severity=warning,event=raised value=1 {stamp}",
        f"sensordash_event,host=CI,sensor=cpu_temp,severity=info,event=cleared value=1 {stamp + 1500}",
        f"sensordash_spool,host=CI,file=database value=1048576 {stamp}",
        f"sensordash_spool,host=CI,file=wal value=4096 {stamp}",
        f"sensordash_spool,host=CI,file=shm value=32768 {stamp}",
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
        assert {x['metric']['component_name'] for x in named} == {'CPU_Block', 'Auxiliary'}, named
        assert {x['metric']['name'] for x in named} == {'CPU_Package'}, named
        assert {x['metric']['alias'] for x in named} == {'CPU_Temp'}, named
        assert {x['metric']['device'] for x in named} == {'Test_PC'}, named
        assert all(float(x['value'][1]) in (1.0, 50.0) for x in named), named

    # Presentation-only label cleanup leaves the archived sensor metadata intact.
    decorated = query(
        'label_replace(label_replace(' + INFO +
        ', "name", "$1 $2", "name", "^(.*)_(.*)$"), '
        '"alias", "$1 $2", "alias", "^(.*)_(.*)$")'
    )
    assert len(decorated) == 1, decorated
    assert decorated[0]['metric']['name'] == 'CPU Package', decorated
    assert decorated[0]['metric']['alias'] == 'CPU Temp', decorated
    assert {x['metric']['name'] for x in query(INFO)} == {'CPU_Package'}

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
    # Storage/threshold-event integration uses real VictoriaMetrics operators,
    # not just dashboard JSON import. No historical detail or fan commands.
    storage_match = (
        'sensordash_reading_value{host="CI",kind="Temperature"}'
        ' * on(host,sensor) group_left(name,device,device_type)'
        ' (sensordash_sensor_info_value{host="CI",device_type="Storage"} == 1)'
    )
    for _ in range(20):
        storage = query(storage_match)
        event = query('sensordash_event_value{host="CI"}')
        spool = query('sensordash_spool_value{host="CI"}')
        if len(storage) == 1 and len(event) == 2 and len(spool) == 3:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "Storage metadata/events/spool series missing: "
            + repr([storage, event, spool])
        )
    assert storage[0]["metric"]["device_type"] == "Storage", storage
    assert storage[0]["metric"]["sensor"] == "/hdd/0/temperature/0", storage
    assert float(storage[0]["value"][1]) == 38.0, storage
    assert {x["metric"]["severity"] for x in event} == {"info", "warning"}, event
    assert {x["metric"]["event"] for x in event} == {"raised", "cleared"}, event
    assert {x["metric"]["file"] for x in spool} == {"database", "wal", "shm"}, spool
    samples = query('sum(count_over_time(sensordash_event_value{host="CI"}[10m]))')
    assert len(samples) == 1 and float(samples[0]["value"][1]) == 2, samples
    # The System Health dashboard reports observed freshness, not an
    # inferred Windows or collector state. Test the actual 24h lookback
    # operator against VictoriaMetrics' PromQL-compatible API.
    freshness = query(
        'time() - max(timestamp('
        'sensordash_reading_value{host="CI"}[24h]))'
    )
    assert len(freshness) == 1, freshness
    age_seconds = float(freshness[0]["value"][1])
    assert 0 <= age_seconds <= 120, freshness
    observed_rate = query(
        'sum(count_over_time(sensordash_reading_value{host="CI"}[5m])) / 5'
    )
    assert len(observed_rate) == 1, observed_rate
    assert float(observed_rate[0]["value"][1]) == 0.4, observed_rate
    # Real PromQL against isolated VictoriaMetrics: user aliases should
    # override display names, while untouched sensors keep their raw names.
    # Stored series and original metadata must remain completely unchanged.
    new_sensor_lines = [
        f"sensordash_reading,host=CI,sensor=/amdcpu/0/power/0,kind=Power value=82 {stamp + 1800}",
        f"sensordash_sensor_info,host=CI,sensor=/amdcpu/0/power/0,kind=Power,name=Package,alias=CPU_Watts,device=AMD_CPU,device_type=Cpu value=1 {stamp + 1800}",
        f"sensordash_reading,host=CI,sensor=/amdcpu/0/power/1,kind=Power value=41 {stamp + 1800}",
        f"sensordash_sensor_info,host=CI,sensor=/amdcpu/0/power/1,kind=Power,name=Core_Power,alias=unassigned,device=AMD_CPU,device_type=Cpu value=1 {stamp + 1800}",
    ]
    payload = ("\n".join(new_sensor_lines) + "\n").encode("ascii")
    with urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v2/write?precision=ms", data=payload,
        headers={"Content-Type": "text/plain"}, method="POST",
    ), timeout=10) as response:
        assert 200 <= response.status < 300

    root = Path(__file__).resolve().parents[1]
    panels = json.loads((root / "grafana/dashboards/overview.json").read_text())["panels"]
    # VictoriaMetrics ingestion is asynchronous. Wait for BOTH underlying
    # datasets before validating the joined, alias-first dashboard expression,
    # so a delayed raw/metadata import isn't mistaken for a PromQL failure.
    expected_power_ids = {"/amdcpu/0/power/0", "/amdcpu/0/power/1"}
    raw_power = []
    power_metadata = []
    for _ in range(60):
        raw_power = query('sensordash_reading_value{host="CI",kind="Power"}')
        power_metadata = query(
            'sensordash_sensor_info_value{host="CI",kind="Power"} == 1')
        if ({x["metric"].get("sensor") for x in raw_power} == expected_power_ids and
                {x["metric"].get("sensor") for x in power_metadata} == expected_power_ids):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "Power samples/metadata did not ingest: raw=" + repr(raw_power) +
            ", metadata=" + repr(power_metadata))

    power_expr = next(p for p in panels if p["id"] == 201)["targets"][0]["expr"]
    power_expr = power_expr.replace('host=~"$host"', 'host="CI"')
    for _ in range(40):
        series = query(power_expr)
        if {x["metric"].get("sensor") for x in series} == expected_power_ids:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "Live alias/fallback power series missing: " + repr(series) +
            "; raw=" + repr(query('sensordash_reading_value{host="CI",kind="Power"}')) +
            "; metadata=" + repr(query(
                'sensordash_sensor_info_value{host="CI",kind="Power"} == 1')))
    names = {x["metric"]["sensor"]: x["metric"]["name"] for x in series}
    assert names == {
        "/amdcpu/0/power/0": "CPU Watts",
        "/amdcpu/0/power/1": "Core Power",
    }, names
    assert {x["metric"]["sensor"]: x["metric"]["alias"] for x in query(
        'sensordash_sensor_info_value{host="CI",kind="Power"} == 1'
    )} == {
        "/amdcpu/0/power/0": "CPU_Watts",
        "/amdcpu/0/power/1": "unassigned",
    }, "Display aliases must not modify archived sensor catalog values"
    assert {x["metric"]["sensor"] for x in query(
        'sensordash_reading_value{host="CI",kind="Power"}'
    )} == {"/amdcpu/0/power/0", "/amdcpu/0/power/1"}

    # NVMe "Warning Temperature" and "Critical Temperature" are fixed
    # drive limits, not live physical observations. Keep the raw series for
    # sensor reference, but ensure every selected *operational* chart drops
    # them even if a user assigns an alias.
    threshold_lines = [
        f"sensordash_reading,host=CI,sensor=/hdd/0/temperature/1,kind=Temperature value=89 {stamp + 2000}",
        f"sensordash_sensor_info,host=CI,sensor=/hdd/0/temperature/1,kind=Temperature,name=Warning_Temperature,alias=Drive_Warning,device=FireCuda,device_type=Storage value=1 {stamp + 2000}",
        f"sensordash_reading,host=CI,sensor=/hdd/0/temperature/2,kind=Temperature value=94 {stamp + 2000}",
        f"sensordash_sensor_info,host=CI,sensor=/hdd/0/temperature/2,kind=Temperature,name=Critical_Temperature,alias=unassigned,device=FireCuda,device_type=Storage value=1 {stamp + 2000}",
        f"sensordash_cooling_binding,host=CI,sensor=/hdd/0/temperature/1,system=loop,system_name=Lab_Loop,component=test,component_name=Aux_Sensor,role=other,kind=Temperature value=1 {stamp + 2000}",
    ]
    with urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v2/write?precision=ms",
        data=("\n".join(threshold_lines) + "\n").encode("ascii"),
        headers={"Content-Type": "text/plain"}, method="POST",
    ), timeout=10) as response:
        assert 200 <= response.status < 300
    # Query each sensor by its exact identity: a broad regex selector may use
    # a stale VictoriaMetrics metric-name index immediately after new writes.
    # This verifies that the original physical reading AND both reference
    # thresholds are independently retrievable from raw storage.
    expected_storage = {
        "/hdd/0/temperature/0": 38.0,
        "/hdd/0/temperature/1": 89.0,
        "/hdd/0/temperature/2": 94.0,
    }
    for _ in range(30):
        raw_storage = [
            item
            for sensor in expected_storage
            for item in query(
                'sensordash_reading_value{host="CI",kind="Temperature",sensor="'
                + sensor + '"}'
            )
        ]
        observed_storage = {
            item["metric"].get("sensor"): float(item["value"][1])
            for item in raw_storage
        }
        if observed_storage == expected_storage:
            break
        time.sleep(0.5)
    else:
        raise AssertionError(
            "Each raw NVMe sensor must remain independently queryable: "
            + repr(observed_storage)
        )
    for _ in range(20):
        bound_threshold = query(
            'sensordash_cooling_binding_value{host="CI",'
            'sensor="/hdd/0/temperature/1",system="loop"} == 1'
        )
        if len(bound_threshold) == 1:
            break
        time.sleep(0.5)
    else:
        raise AssertionError("Test threshold is not assigned to cooling system")
    root = Path(__file__).resolve().parents[1]
    for dashboard, panel_ids in (
        ("portal-metrics.json", (9,)),
        ("cooling-systems.json", (2,)),
        ("overview.json", (200, 301)),
        ("storage.json", (5,)),
        ("sensordash.json", (1,)),
    ):
        panels = json.loads((root / "grafana/dashboards" / dashboard).read_text())["panels"]
        for panel_id in panel_ids:
            expr = next(p["targets"][0]["expr"] for p in panels if p["id"] == panel_id)
            expr = expr.replace('host=~"$host"', 'host="CI"').replace('host=~".+"', 'host="CI"')
            expr = expr.replace('sensor=~"$sensor"', 'sensor=~".*"')
            expr = expr.replace('system=~"$system"', 'system="loop"')
            result = query(expr)
            observed = {x["metric"].get("sensor") for x in result}
            # The Cooling chart only includes explicitly assigned sensors;
            # the physical NVMe Composite sensor is not assigned here.
            expected_physical = ("cpu_temp" if dashboard == "cooling-systems.json"
                                 else "/hdd/0/temperature/0")
            assert expected_physical in observed, (dashboard, panel_id, observed)
            assert "/hdd/0/temperature/1" not in observed, (dashboard, panel_id, observed)
            assert "/hdd/0/temperature/2" not in observed, (dashboard, panel_id, observed)
    metadata = query('sensordash_sensor_info_value{host="CI",device_type="Storage"} == 1')
    assert {x["metric"]["sensor"] for x in metadata} == {
        "/hdd/0/temperature/0", "/hdd/0/temperature/1", "/hdd/0/temperature/2"
    }, "Static thresholds must stay visible as reference sensor metadata"

    # Real range-query regression: keep the same winning sensor across all
    # sparkline points, and render metadata aliases only in displayed labels.
    base_ms = int(time.time() * 1000) - 180_000
    fixture = [
        ("cpu_peak", "Cpu", "CCD1__Tdie_", "unassigned", [34, 63.7, 39]),
        ("cpu_other", "Cpu", "CPU_Package", "Package", [35, 58, 38]),
        ("gpu_peak", "GpuAmd", "GPU_Memory", "VRAM_Hotspot", [38, 68, 44]),
        ("gpu_other", "GpuAmd", "GPU_Edge", "unassigned", [35, 48, 42]),
    ]
    lines = []
    for sensor, device_type, name, alias, measurements in fixture:
        for offset, value in zip((0, 45000, 90000), measurements):
            lines.append(f"sensordash_reading,host=CI,sensor={sensor},kind=Temperature "
                         f"value={value} {base_ms + offset}")
        lines.append(f"sensordash_sensor_info,host=CI,sensor={sensor},kind=Temperature,"
                     f"device_type={device_type},device=Lab_PC,name={name},alias={alias} "
                     f"value=1 {base_ms + 90000}")
    with urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v2/write?precision=ms",
        data=("\n".join(lines) + "\n").encode("ascii"),
        headers={"Content-Type": "text/plain"}, method="POST",
    ), timeout=10) as response:
        assert 200 <= response.status < 300

    for _ in range(30):
        ready = all(
            query(f'sensordash_reading_value{{host="CI",sensor="{sensor}"}}')
            and query(f'sensordash_sensor_info_value{{host="CI",sensor="{sensor}"}}')
            for sensor, _, _, _, _ in fixture
        )
        if ready:
            break
        time.sleep(0.5)
    assert ready, "Peak fixture and names must ingest"

    peak_panels = json.loads(
        (root / "grafana/dashboards/overview.json").read_text()
    )["panels"]
    for panel_id, sensor, display_name, peak in (
        (5, "cpu_peak", "CCD1 Tdie", 63.7),
        (6, "gpu_peak", "VRAM Hotspot", 68.0),
    ):
        expr = next(p["targets"][0]["expr"] for p in peak_panels if p["id"] == panel_id)
        expr = (expr.replace('host=~"$host"', 'host="CI"')
                .replace("$__range", "6m").replace("$__interval", "20s"))
        end = time.time()
        results = query_range(expr, end - 360, end)
        assert len(results) == 1, (panel_id, results)
        series = results[0]
        assert series["metric"]["sensor"] == sensor, series
        assert series["metric"]["name"] == display_name, series
        numbers = [float(v) for _, v in series["values"]]
        assert len(numbers) > 2, series
        assert abs(max(numbers) - peak) < 0.001, (panel_id, numbers)
        assert min(numbers) < peak, "Sparkline must show actual sensor history"
        original = query(f'sensordash_sensor_info_value{{host="CI",sensor="{sensor}"}}')
        assert original[0]["metric"]["name"] in ("CCD1__Tdie_", "GPU_Memory")
        assert query_range(expr.replace('host="CI"', 'host="NO-SUCH-HOST"'),
                           end - 360, end) == []

    # Hardware temperature Stat uses one result for both the value AND
    # the alias-aware display name. Previously independent hidden variables
    # could outlive a panel refresh and attribute the wrong sensor.
    hardware_dashboard = json.loads(
        (root / "grafana/dashboards/sensordash.json").read_text()
    )
    hardware_panels = {p["id"]: p for p in hardware_dashboard["panels"]}
    raw = 'sensordash_reading_value{host=~"$host",sensor=~"$sensor",kind="Temperature"}'
    info = ('sensordash_sensor_info_value{host=~"$host",'
            'name!~"(?i).*(warning|critical).*"} == 1')
    original = {
        111: f"max({raw} * on(host,sensor) group_left(name,device,device_type) ({info}))",
        112: f"max(max_over_time({raw}[$__range]) * "
             f"on(host,sensor) group_left(name,device,device_type) ({info}))"
    }

    def configured(expression: str, sensor: str = ".*") -> str:
        return (expression.replace('host=~"$host"', 'host="CI"')
                .replace('sensor=~"$sensor"', 'sensor=~"' + sensor + '"')
                .replace("$__range", "6m"))

    for panel_id in (111, 112):
        query_expr = hardware_panels[panel_id]["targets"][0]["expr"]
        assert "topk(1," in query_expr
        for sensor_filter in (".*", "gpu_peak", "cpu_peak"):
            result = query(configured(query_expr, sensor_filter))
            reference = query(configured(original[panel_id], sensor_filter))
            assert len(result) == len(reference) == 1, (
                panel_id, sensor_filter, result, reference)
            assert abs(float(result[0]["value"][1]) -
                       float(reference[0]["value"][1])) < 0.001, (
                panel_id, result, reference)
            assert result[0]["metric"].get("sensor"), result
            assert result[0]["metric"].get("name"), result
            if sensor_filter == "gpu_peak":
                assert result[0]["metric"]["sensor"] == "gpu_peak", result
                assert result[0]["metric"]["name"] == "VRAM Hotspot", result
            if sensor_filter == "cpu_peak":
                assert result[0]["metric"]["sensor"] == "cpu_peak", result
                assert result[0]["metric"]["name"] == "CCD1 Tdie", result
        assert query(configured(query_expr).replace('host="CI"', 'host="NO-SUCH-HOST"')) == []

    # Ingest a *new, higher reading* for another CPU sensor to simulate a
    # winner change between two consecutive Grafana refreshes. There can be
    # no stale name: only one query supplies both fields on each refresh.
    new_ms = int(time.time() * 1000)
    with urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v2/write?precision=ms",
        data=(f"sensordash_reading,host=CI,sensor=cpu_other,kind=Temperature "
              f"value=75 {new_ms}\n").encode("ascii"),
        headers={"Content-Type": "text/plain"}, method="POST",
    ), timeout=10) as response:
        assert 200 <= response.status < 300
    for _ in range(25):
        latest = query(configured(hardware_panels[111]["targets"][0]["expr"]))
        peak = query(configured(hardware_panels[112]["targets"][0]["expr"]))
        if (len(latest) == len(peak) == 1
                and latest[0]["metric"].get("sensor") == "cpu_other"
                and peak[0]["metric"].get("sensor") == "cpu_other"
                and abs(float(latest[0]["value"][1]) - 75) < 0.001
                and abs(float(peak[0]["value"][1]) - 75) < 0.001):
            break
        time.sleep(0.5)
    else:
        raise AssertionError(("New winner did not update name/value atomically",
                              latest, peak))
    for result in (latest, peak):
        assert result[0]["metric"]["name"] == "Package", result
    assert query(configured(original[111]))[0]["value"][1] == latest[0]["value"][1]
    assert query(configured(original[112]))[0]["value"][1] == peak[0]["value"][1]

    # Workload/idle report: exercise the *actual Grafana PromQL* against
    # physical-like CPU Total and GPU Core activity, watts and coolant data.
    # The isolated CI instance is deliberately separate from a live Pi.
    workload = {
        "cpu_util": ("Load", "CPU_Total", "Cpu", "Ryzen", 2),
        "gpu_util": ("Load", "GPU_Core", "GpuAmd", "Radeon", 3),
        "cpu_die": ("Temperature", "Core__Tdie_", "Cpu", "Ryzen", 39),
        "gpu_temp": ("Temperature", "GPU_Core", "GpuAmd", "Radeon", 32),
        "cpu_watts": ("Power", "Package", "Cpu", "Ryzen", 24),
        "gpu_watts": ("Power", "GPU_Package", "GpuAmd", "Radeon", 18),
        "coolant": ("Temperature", "Water_Temperature", "Cooler", "high_flow_NEXT", 26),
        "vram": ("Data", "GPU_Memory_Used", "GpuAmd", "Radeon", 2147483648),
    }
    end_ms = int(time.time() * 1000) - 2000
    samples = []
    # Forty-one actual measurements / counter over 16m40s (25s spacing),
    # so the last 15m window has >30 physical observations per activity source.
    for i in range(41):
        t = end_ms - (40-i)*25000
        for sensor, (kind, _name, _family, _device, val) in workload.items():
            samples.append(
                f"sensordash_reading,host=CI-WORK,sensor={sensor},kind={kind} "
                f"value={val} {t}"
            )
            # Metadata is periodically republished in production. Keep the
            # ephemeral fixture faithful so historical joins have provenance.
            if i % 8 == 0:
                samples.append(
                    f"sensordash_sensor_info,host=CI-WORK,sensor={sensor},"
                    f"kind={kind},name={_name},device_type={_family},device={_device},"
                    f"alias=unassigned value=1 {t}"
                )
    with urllib.request.urlopen(urllib.request.Request(
        BASE + "/api/v2/write?precision=ms",
        data=("\\n".join(samples) + "\\n").encode("ascii"),
        headers={"Content-Type": "text/plain"}, method="POST"
    ), timeout=10) as response:
        assert 200 <= response.status < 300

    work_report = json.loads((Path(__file__).resolve().parents[1] /
       "grafana/dashboards/reporting-summary.json").read_text(encoding="utf-8"))
    work_row = next(p for p in work_report["panels"] if p["id"] == 220)
    work_stats = {p["id"]: p for p in work_row["panels"]}

    def work_expr(panel):
        return (panel["targets"][0]["expr"]
                .replace('host=~"$host"', 'host="CI-WORK"')
                .replace("$__range", "20m"))

    ready = []
    for _ in range(25):
        ready = query(work_expr(work_stats[222]))
        if ready:
            break
        time.sleep(0.5)
    assert ready, "Physical CPU/GPU data never qualified as idle in VM"

    expected = {222: 39, 223: 32, 224: 24, 225: 18, 226: 26,
                227: 100, 228: 0, 229: 0}
    for id, value in expected.items():
        rows = query(work_expr(work_stats[id]))
        assert len(rows) == 1, ("Workload result absent", id, rows)
        actual = float(rows[0]["value"][1])
        assert abs(actual - value) < 0.1, (
            "Unexpected workload/idle result", id, actual, value)
    idle_minutes = query(work_expr(work_stats[221]))
    assert len(idle_minutes) == 1 and float(idle_minutes[0]["value"][1]) > 0, (
        "Expected positive count of sampled qualifying idle minutes", idle_minutes)
    assert query(work_expr(work_stats[222]).replace("CI-WORK", "NO-SUCH-HOST")) == [], (
        "No CPU/GPU samples must not create a zero-valued idle baseline")
    print("PASS: physical CPU/GPU load and VRAM catalog joins, qualified "
          "idle medians, workload shares and missing-host gap safety")

    print("PASS: atomic sensor names and temperatures, aliases, filters, "
          "winner changes, numerical equivalence and empty data")
    print("PASS: peak ranges, single physical sensor, user alias priority, "
          "underscores and history sparklines")

    print("PASS: VictoriaMetrics raw + derived watts, metadata, threshold events,"
          " spool footprint, storage joins and removal tombstones")


if __name__ == "__main__":
    main()
