import tempfile
import unittest
from pathlib import Path

from scripts.validate_config import parse_env, validate


class ConfigTests(unittest.TestCase):
    BASE = {
        "PI_LAN_IP": "192.168.50.10",
        "LAN_CIDR": "192.168.50.0/24",
        "VPN_ENDPOINT": "vpn.personal-domain.net",
        "VPN_PORT": "51820",
        "VPN_CIDR": "10.13.13.0/24",
        "UPSTREAM_DNS": "1.1.1.1;9.9.9.9",
    }

    def test_valid_private_network(self):
        validate(dict(self.BASE))

    def test_catches_example_endpoint(self):
        cfg = {**self.BASE, "VPN_ENDPOINT": "vpn.example.com"}
        with self.assertRaisesRegex(ValueError, "VPN_ENDPOINT"):
            validate(cfg)
        validate(cfg, allow_example=True)

    def test_catches_overlapping_vpn(self):
        cfg = {**self.BASE, "VPN_CIDR": "192.168.50.0/24"}
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate(cfg)

    def test_catches_overlapping_docker_subnet(self):
        cfg = {**self.BASE, "VPN_CIDR": "10.42.42.0/24"}
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate(cfg)

    def test_rejects_public_pi(self):
        cfg = {**self.BASE, "PI_LAN_IP": "8.8.8.8", "LAN_CIDR": "8.8.8.0/24"}
        with self.assertRaises(ValueError):
            validate(cfg)

    def test_duplicate_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / ".env"
            p.write_text("PI_LAN_IP=192.168.50.10\nPI_LAN_IP=1.2.3.4\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                parse_env(p)


if __name__ == "__main__":
    unittest.main()
