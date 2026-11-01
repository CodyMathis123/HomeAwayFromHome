"""Synthetic pcctl SSH trust verification and fingerprint enrollment tests."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "control/pcctl"))


def load(name, path):
    spec=importlib.util.spec_from_file_location(name, ROOT/path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify=load("pcverify_test", "control/pcverify/agent.py")
pin=load("pcpin_test", "scripts/pin-pc-hostkey.py")


def candidate(ip="192.168.50.44",user="pcctl",port=22):
    return {"id":"living-room","name":"Living Room","address":ip,
            "mac":"02:aa:bb:cc:dd:ee","broadcast":"192.168.50.255",
            "ssh_user":user,"ssh_port":port}


class SshTrustTests(unittest.TestCase):
    def test_real_auth_status_only_with_pinned_host(self):
        calls=[]
        def fake(command,**kwargs):
            calls.append(command)
            if command[0]=="ssh-keygen":
                return subprocess.CompletedProcess(command,0,"# Host is pinned\n","")
            if command[0]=="ssh":
                return subprocess.CompletedProcess(command,0,"ready\n","")
            raise AssertionError("Unexpected process")
        with patch.object(verify.subprocess,"run",side_effect=fake):
            result=verify.probe(candidate())
        self.assertEqual(result,{"verified":True,"code":"ssh_key_and_status_verified"})
        cmd=calls[1]
        self.assertEqual(cmd[0],"ssh")
        self.assertEqual(cmd[-1],"status")
        self.assertEqual(cmd[-2],"pcctl@192.168.50.44")
        self.assertIn("StrictHostKeyChecking=yes",cmd)
        self.assertIn("HostKeyAlgorithms=ssh-ed25519",cmd)
        self.assertIn("PreferredAuthentications=publickey",cmd)
        self.assertIn("PasswordAuthentication=no",cmd)
        self.assertIn("HostbasedAuthentication=no",cmd)
        self.assertIn("GlobalKnownHostsFile=/dev/null",cmd)
        self.assertIn("PasswordAuthentication=no",cmd)
        self.assertIn("UpdateHostKeys=no",cmd)
        self.assertNotIn("shutdown",cmd)
        self.assertNotIn("sleep",cmd)

    def test_unpinned_host_never_initiates_ssh(self):
        with patch.object(verify.subprocess,"run",return_value=
            subprocess.CompletedProcess(["ssh-keygen"],1,"","")) as run:
            result=verify.probe(candidate())
        self.assertFalse(result["verified"])
        self.assertEqual(result["code"],"host_key_not_pinned")
        self.assertEqual(run.call_count,1)

    def test_no_success_for_bad_status_or_auth(self):
        for resultcode,text in ((1,""),(0,"hello"),(0,"ready\nextra")):
            def fake(cmd,**kwargs):
                if cmd[0]=="ssh-keygen":
                    return subprocess.CompletedProcess(cmd,0,"pinned","")
                return subprocess.CompletedProcess(cmd,resultcode,text,"")
            with self.subTest(resultcode=resultcode,text=text),\
                 patch.object(verify.subprocess,"run",side_effect=fake):
                result=verify.probe(candidate())
                self.assertEqual(result["code"],"ssh_auth_or_status_failed")

    def test_invalid_targets_never_reach_process(self):
        for entry in (candidate("8.8.8.8"),candidate("127.0.0.1"),
                      candidate("169.254.1.1"),candidate(user="root"),
                      candidate(port=0),candidate(ip="172.31.1.1",user="admin")):
            with self.subTest(entry=entry),patch.object(verify.subprocess,"run") as run:
                result=verify.probe(entry)
                self.assertEqual(result["code"],"invalid_candidate")
                run.assert_not_called()

    def test_pinned_key_with_ssh_timeout_denied(self):
        def fake(cmd,**kwargs):
            if cmd[0]=="ssh-keygen":
                return subprocess.CompletedProcess(cmd,0,"pinned","")
            raise subprocess.TimeoutExpired(cmd,11)
        with patch.object(verify.subprocess,"run",side_effect=fake):
            result=verify.probe(candidate())
        self.assertFalse(result["verified"])
        self.assertEqual(result["code"],"ssh_unavailable")


class PinHostKeyTests(unittest.TestCase):
    def test_rfc1918_ip_only_and_port_aware(self):
        self.assertEqual(pin.target_name("192.168.50.44",22),"192.168.50.44")
        self.assertEqual(pin.target_name("192.168.50.44",2222),"[192.168.50.44]:2222")
        for value in ("8.8.8.8","127.0.0.1","169.254.1.2"):
            with self.assertRaises(ValueError):
                pin.target_name(value,22)

    def test_fingerprint_mismatch_prevents_write(self):
        expected="SHA256:"+"A"*43
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/"known_hosts"
            with patch.object(pin,"discover",return_value=(
                    "192.168.50.44 ssh-ed25519 AAAATESTKEY", "SHA256:"+"B"*43)):
                with self.assertRaisesRegex(ValueError,"MISMATCH"):
                    pin.pin("192.168.50.44",22,expected,apply=True,known_hosts=dest)
            self.assertFalse(dest.exists())

    def test_plan_is_read_only_even_with_matching_fingerprint(self):
        expected="SHA256:"+"A"*43
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/"known_hosts"
            with patch.object(pin,"discover",return_value=(
                    "192.168.50.44 ssh-ed25519 AAAATESTKEY",expected)):
                result=pin.pin("192.168.50.44",22,expected,apply=False,known_hosts=dest)
            self.assertFalse(result["pinned"])
            self.assertFalse(dest.exists())

    def test_apply_only_appends_matching_public_key(self):
        expected="SHA256:"+"A"*43
        line="192.168.50.44 ssh-ed25519 AAAATESTKEY"
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/"known_hosts"
            dest.write_text("")
            account=SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid())
            with patch.object(pin,"discover",return_value=(line,expected)),\
                 patch.object(pin.os,"geteuid",return_value=0),\
                 patch.object(pin.pwd,"getpwnam",return_value=account),\
                 patch.object(pin.subprocess,"run",return_value=
                    subprocess.CompletedProcess(["ssh-keygen"],1,"","")):
                result=pin.pin("192.168.50.44",22,expected,apply=True,known_hosts=dest)
            self.assertTrue(result["pinned"])
            self.assertEqual(dest.read_text(),line+"\n")
            self.assertEqual(dest.stat().st_mode&0o777,0o600)

    def test_existing_pinned_host_cannot_be_overwritten(self):
        expected="SHA256:"+"A"*43
        with tempfile.TemporaryDirectory() as root:
            dest=Path(root)/"known_hosts"
            dest.write_text("old pinned key\n")
            with patch.object(pin,"discover",return_value=(
                 "192.168.50.44 ssh-ed25519 AAAATESTKEY",expected)),\
                 patch.object(pin.os,"geteuid",return_value=0),\
                 patch.object(pin.subprocess,"run",return_value=
                   subprocess.CompletedProcess(["ssh-keygen"],0,"pinned","")):
                with self.assertRaisesRegex(ValueError,"already pinned"):
                    pin.pin("192.168.50.44",22,expected,apply=True,known_hosts=dest)
            self.assertEqual(dest.read_text(),"old pinned key\n")

    def test_symlinked_known_hosts_refused(self):
        expected="SHA256:"+"A"*43
        with tempfile.TemporaryDirectory() as root:
            outside=Path(root)/"outside"
            outside.write_text("original")
            dest=Path(root)/"known_hosts"
            dest.symlink_to(outside)
            with patch.object(pin,"discover",return_value=(
                 "192.168.50.44 ssh-ed25519 AAAATESTKEY",expected)),\
                 patch.object(pin.os,"geteuid",return_value=0):
                with self.assertRaisesRegex(ValueError,"symlinked"):
                    pin.pin("192.168.50.44",22,expected,apply=True,known_hosts=dest)
            self.assertEqual(outside.read_text(),"original")


if __name__=="__main__":
    unittest.main()
