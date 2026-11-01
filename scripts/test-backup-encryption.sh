#!/usr/bin/env bash
# Safe, local-only age round-trip test. Does not touch application state or services.
set -euo pipefail
umask 077

for bin in age age-keygen sha256sum mktemp; do
  command -v "$bin" >/dev/null || { echo "ERROR: required $bin is not installed" >&2; exit 1; }
done

tmp="$(mktemp -d)"
cleanup() { rm -rf -- "$tmp"; }
trap cleanup EXIT HUP INT TERM
chmod 0700 "$tmp"

age-keygen -o "$tmp/key.txt" >/dev/null 2>&1
chmod 0600 "$tmp/key.txt"
recipient="$(age-keygen -y "$tmp/key.txt")"
[[ "$recipient" == age1* ]] || { echo "ERROR: invalid temporary age recipient" >&2; exit 1; }

printf 'homeaway-crypto-smoke-v1\n' > "$tmp/plain"
age -r "$recipient" -o "$tmp/cipher.age" "$tmp/plain"
age -d -i "$tmp/key.txt" -o "$tmp/decrypted" "$tmp/cipher.age"

expected="$(sha256sum "$tmp/plain" | cut -d ' ' -f 1)"
actual="$(sha256sum "$tmp/decrypted" | cut -d ' ' -f 1)"
[[ "$expected" == "$actual" ]] ||
  { echo "FAIL: decrypted test payload differs" >&2; exit 1; }

echo "PASS: local age encrypt/decrypt round-trip verified."
echo "Synthetic payload only. Temporary test key removed on exit."
echo "No production credentials, Docker containers, host services, or backup archives were touched."
