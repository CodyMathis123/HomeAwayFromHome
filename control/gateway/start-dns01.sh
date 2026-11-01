#!/bin/sh
# Fixed entrypoint. Import scoped Cloudflare token from Docker secret, never
# from a Compose environment string or an external command.
set -eu
token=/run/secrets/cloudflare_dns01_token
if [ ! -r "$token" ]; then
  echo "Cloudflare DNS-01 token secret not mounted" >&2
  exit 1
fi
IFS= read -r CF_API_TOKEN < "$token" || {
  echo "Cloudflare DNS-01 secret must end with a newline" >&2
  exit 1
}
if [ -z "$CF_API_TOKEN" ]; then
  echo "Cloudflare DNS-01 secret is empty" >&2
  exit 1
fi
export CF_API_TOKEN
exec /usr/bin/caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
