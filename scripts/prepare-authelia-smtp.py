#!/usr/bin/env python3
"""Prepare Authelia's filesystem -> Resend notifier switch, without restarting services.

The only YAML edit is removal of the *existing, simple* filesystem notifier
subtree. All other configuration is preserved byte-for-byte. Fail closed on
unexpected layouts, and back up the complete original first.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"data/control/authelia/configuration.yml"
ENV=ROOT/"secrets/control/lldap-smtp.env"
SECRET=ROOT/"secrets/control/authelia_smtp_password"
BACKUP=ROOT/"secrets/control/authelia-notifier-original.yml"

def prepare(text: str) -> str:
    lines=text.splitlines(keepends=True)
    starts=[i for i,line in enumerate(lines)
            if re.fullmatch(r"notifier:\s*(?:#.*)?\n?",line)]
    if len(starts)!=1:
        raise ValueError("Expected exactly one root-level notifier mapping")
    first=starts[0]
    end=next((i for i in range(first+1,len(lines))
              if lines[i].strip() and not lines[i].lstrip().startswith("#")
              and not lines[i][0].isspace()),len(lines))
    section=lines[first+1:end]
    filesystem=[j for j,line in enumerate(section)
                if re.match(r"^  filesystem:\s*(?:#.*)?$",line.rstrip("\r\n"))]
    smtp=[j for j,line in enumerate(section)
          if re.match(r"^  smtp:\s*(?:#.*)?$",line.rstrip("\r\n"))]
    if len(filesystem)!=1 or smtp:
        raise ValueError("Expected a single filesystem notifier and no SMTP notifier")
    start=filesystem[0]
    stop=next((j for j in range(start+1,len(section))
               if re.match(r"^  [a-z][\w-]*:",section[j])),len(section))
    subtree="".join(section[start:stop])
    if not re.search(r"(?m)^    filename:\s*",subtree):
        raise ValueError("Filesystem notifier must contain a filename key")
    for line in subtree.splitlines()[1:]:
        if line.strip() and not line.lstrip().startswith("#") and not line.startswith("    "):
            raise ValueError("Unexpected filesystem subtree; manual review needed")
    updated=lines[:first+1]+section[:start]+section[stop:]+lines[end:]
    return "".join(updated)

def get_key() -> str:
    s=ENV.stat()
    if s.st_uid!=0 or s.st_mode & 0o077 or not stat.S_ISREG(s.st_mode):
        raise ValueError("LLDAP SMTP env must be root-owned, private and regular")
    passwords=[line.partition("=")[2] for line in ENV.read_text().splitlines()
               if line.startswith("LLDAP_SMTP_OPTIONS__PASSWORD=")]
    if len(passwords)!=1 or not passwords[0].startswith("re_"):
        raise ValueError("Expected one Resend API key in existing LLDAP SMTP file")
    return passwords[0]

def atomic_write(path: Path, data: bytes, mode: int, uid: int, gid: int):
    fd,name=tempfile.mkstemp(prefix=".authelia-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name,mode)
        os.chown(name,uid,gid)
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--plan",action="store_true")
    modes.add_argument("--apply",action="store_true")
    modes.add_argument("--rollback",action="store_true")
    args=parser.parse_args()
    if os.geteuid()!=0:
        parser.error("Run as root with sudo")
    try:
        if args.rollback:
            if not BACKUP.is_file():
                raise ValueError("No matching original notifier backup found")
            original=BACKUP.read_bytes()
            if "filesystem:" not in original.decode():
                raise ValueError("Stored original notifier is unexpected")
            current=CONFIG.stat()
            atomic_write(CONFIG,original,stat.S_IMODE(current.st_mode),
                         current.st_uid,current.st_gid)
            print("Restored original Authelia YAML. Recreate ONLY authelia with normal overlays.")
            return 0
        original=CONFIG.read_bytes()
        replacement=prepare(original.decode("utf-8")).encode()
        key=get_key()
        if args.plan:
            print("PASS: existing filesystem notifier is safe to replace")
            print("PASS: existing Resend SMTP key is available (never printed)")
            print("No files or running services changed.")
            return 0
        if BACKUP.exists():
            raise ValueError("Backup already exists: do not overwrite prior state; review or rollback first")
        BACKUP.parent.mkdir(parents=True,exist_ok=True)
        atomic_write(BACKUP,original,0o600,0,0)
        atomic_write(SECRET,(key+"\n").encode(),0o640,0,65534)
        current=CONFIG.stat()
        atomic_write(CONFIG,replacement,stat.S_IMODE(current.st_mode),
                     current.st_uid,current.st_gid)
        print("Prepared Authelia config and protected SMTP secret; no containers restarted.")
        print("Original YAML safely backed up in secrets/control/authelia-notifier-original.yml")
        print("Next: validate proposed Compose config and restart ONLY authelia with the SMTP overlay.")
        return 0
    except (OSError,UnicodeError,ValueError) as exc:
        # No secret values in the error messages.
        print("FAIL: "+str(exc),file=sys.stderr)
        return 1

if __name__=="__main__":
    raise SystemExit(main())
