import http.client
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from ingest.app import Handler, valid_payload, token_ok


class IngestTests(unittest.TestCase):
    def test_authorized_header(self):
        token = "a" * 64
        self.assertTrue(token_ok("Bearer " + token, token))
        self.assertFalse(token_ok("Bearer " + "b" * 64, token))
        self.assertFalse(token_ok("Basic " + token, token))
        self.assertFalse(token_ok(None, token))

    def test_raw_sensor_line(self):
        self.assertTrue(valid_payload(
            b"sensordash_reading,host=DESKTOP,sensor=/amdcpu/0/temperature/0,kind=Temperature value=61.75 1793450400000\n"
        ))
        self.assertTrue(valid_payload(
            b"sensordash_reading,host=PC,sensor=/gpu/0/control/0,kind=Control value=0 1793450400000\n"
        ))

    def test_sensor_catalog_and_cooling_binding_gauges(self):
        descriptors = [
            b"sensordash_sensor_info,host=My_PC,sensor=/cpu/0/temperature/0,kind=Temperature,name=CPU_Package,alias=CPU_Core,device=AMD_Ryzen value=1 1793450400000",
            b"sensordash_cooling_binding,host=My_PC,sensor=/cpu/0/temperature/0,system=loop-01,system_name=Walnut_Loop,component=heat-cpu,component_name=CPU_Block,role=heat-source,kind=Temperature value=1 1793450400000",
            b"sensordash_cooling_binding,host=My_PC,sensor=/cpu/0/temperature/0,system=loop-01,system_name=Walnut_Loop,component=heat-cpu,component_name=CPU_Block,role=heat-source,kind=Temperature value=0 1793450460000",
        ]
        self.assertTrue(valid_payload(b"\n".join(descriptors) + b"\n"))
        self.assertFalse(valid_payload(descriptors[0].replace(b"value=1", b"value=2")))
        self.assertFalse(valid_payload(descriptors[1].replace(b"role=heat-source", b"role=heat source")))
        self.assertFalse(valid_payload(descriptors[1].replace(b"component_name=CPU_Block", b"component_name=CPU,Block")))
        self.assertFalse(valid_payload(descriptors[1] + b"\nrogue_metric value=1 1793450400000"))
        self.assertFalse(valid_payload(descriptors[1].replace(b"value=1", b"value=nan")))

    def test_provenance_aware_power_and_component_metadata(self):
        valid = (
            b"sensordash_component_info,host=PC,system=loop1,component=fans,"
            b"system_name=Walnut_Loop,component_name=Radiator_Bank,"
            b"role=fan-group value=1 1793450400000\n"
            b"sensordash_accessory_power,host=PC,system=loop1,component=fans,"
            b"source=calibrated,revision=12345678abcdef00 value=12.25 1793450400000\n"
        )
        self.assertTrue(valid_payload(valid))
        self.assertFalse(valid_payload(valid.replace(b"source=calibrated", b"source=unverified")))
        self.assertFalse(valid_payload(valid.replace(b"value=12.25", b"value=-12.25")))
        self.assertFalse(valid_payload(valid.replace(b"revision=12345678abcdef00", b"revision=wrong")))
        self.assertFalse(valid_payload(valid.replace(b"role=fan-group", b"role=fan group")))
        self.assertFalse(valid_payload(valid.replace(b"sensordash_accessory_power", b"fan_control_command")))

    def test_storage_event_and_spool_schema_is_strict(self):
        lines = [
            b"sensordash_sensor_info,host=main-desktop,sensor=/hdd/0/temperature/0,kind=Temperature,name=Composite,alias=Drive_Temp,device=FireCuda,device_type=Storage value=1 1793450400000",
            b"sensordash_spool,host=main-desktop,file=database value=123456 1793450400000",
            b"sensordash_spool,host=main-desktop,file=wal value=4096 1793450400000",
            b"sensordash_event,host=main-desktop,sensor=/hdd/0/temperature/0,severity=warning,event=raised value=1 1793450400000",
            b"sensordash_event,host=main-desktop,sensor=/hdd/0/temperature/0,severity=info,event=cleared value=1 1793450410000",
        ]
        self.assertTrue(valid_payload(b"\n".join(lines) + b"\n"))
        self.assertFalse(valid_payload(lines[0].replace(b"device_type=Storage", b"device_type=Storage,secret=1")))
        self.assertFalse(valid_payload(lines[2].replace(b"file=wal", b"file=/ProgramData/telemetry.db")))
        self.assertFalse(valid_payload(lines[3].replace(b"event=raised", b"event=fan-control")))
        self.assertFalse(valid_payload(lines[3].replace(b"value=1", b"value=0")))
        self.assertFalse(valid_payload(lines[3] + b",message=Computer_off"))

    def test_reject_injection_and_oversize(self):
        self.assertFalse(valid_payload(b"other,host=PC value=1 1793450400000"))
        self.assertFalse(valid_payload(
            b"sensordash_reading,host=PC,sensor=something,kind=Temperature value=NaN 1793450400000"
        ))
        self.assertFalse(valid_payload(
            b"sensordash_reading,host=PC,sensor=something,kind=Temperature value=2 1793450400000\nattack!"
        ))
        self.assertFalse(valid_payload(b"a" * (256 * 1024 + 1)))
        self.assertFalse(valid_payload(b""))




class IngestHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.expected_token = "a" * 64
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def request(self, method, path, token="", body=b""):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        headers = {"Authorization": "Bearer " + token} if token else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status
        response.read()
        connection.close()
        return result

    def test_rejects_unauthorized_and_wrong_paths(self):
        body = b"sensordash_reading,host=PC,sensor=cpu,kind=Temperature value=41 1793450400000"
        with patch("ingest.app.forward") as mock:
            self.assertEqual(self.request("POST", "/ingest", body=body), 401)
            self.assertEqual(self.request("POST", "/write", token="a" * 64, body=body), 404)
            self.assertEqual(self.request("GET", "/ingest", token="a" * 64), 405)
            mock.assert_not_called()

    def test_catalog_payload_is_accepted_without_write_authority(self):
        metadata = (
            b"sensordash_sensor_info,host=PC,sensor=/gpu/0/temperature/0,"
            b"kind=Temperature,name=GPU_Core,alias=GPU_Core,device=Radeon_RX value=1 1793450400000\n"
        )
        with patch("ingest.app.forward", return_value=204) as forward:
            self.assertEqual(self.request("POST", "/ingest", token="a" * 64, body=metadata), 204)
            forward.assert_called_once_with(metadata)

    def test_authenticated_valid_and_invalid_body(self):
        valid = b"sensordash_reading,host=PC,sensor=cpu,kind=Temperature value=41 1793450400000"
        with patch("ingest.app.forward", return_value=204) as mock:
            self.assertEqual(self.request("POST", "/ingest", token="a" * 64, body=valid), 204)
            mock.assert_called_once_with(valid)
        with patch("ingest.app.forward") as mock:
            self.assertEqual(self.request("POST", "/ingest", token="a" * 64, body=b"not metrics"), 422)
            mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
