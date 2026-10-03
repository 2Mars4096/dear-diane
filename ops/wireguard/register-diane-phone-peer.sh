#!/usr/bin/env bash
#
# Register the DAN phone Android WireGuard peer on the NY relay.
#
# Usage on the relay:
#   sudo DAN_PHONE_PUBLIC_KEY="<public-key>" ./register-diane-phone-peer.sh
#
# Optional env overrides:
#   WG_INTERFACE=wgny
#   WG_CONF_PATH=/etc/wireguard/wgny.conf
#   DAN_PHONE_ALLOWED_IP=10.77.77.6/32

set -euo pipefail

WG_INTERFACE="${WG_INTERFACE:-wgny}"
WG_CONF_PATH="${WG_CONF_PATH:-/etc/wireguard/${WG_INTERFACE}.conf}"
DAN_PHONE_ALLOWED_IP="${DAN_PHONE_ALLOWED_IP:-10.77.77.6/32}"
DAN_PHONE_PUBLIC_KEY="${DAN_PHONE_PUBLIC_KEY:-}"

die() {
  echo "$*" >&2
  exit 1
}

[[ "${EUID}" -eq 0 ]] || die "Run as root, for example with sudo."
[[ -n "${DAN_PHONE_PUBLIC_KEY}" ]] || die "Set DAN_PHONE_PUBLIC_KEY."
[[ -f "${WG_CONF_PATH}" ]] || die "Missing WireGuard config: ${WG_CONF_PATH}"

tmp_conf="$(mktemp)"
trap 'rm -f "${tmp_conf}"' EXIT

awk '
  /^# DAN phone peer: begin$/ { skip = 1; next }
  /^# DAN phone peer: end$/ { skip = 0; next }
  skip != 1 { print }
' "${WG_CONF_PATH}" > "${tmp_conf}"

cat >> "${tmp_conf}" <<EOF

# DAN phone peer: begin
[Peer]
PublicKey = ${DAN_PHONE_PUBLIC_KEY}
AllowedIPs = ${DAN_PHONE_ALLOWED_IP}
# DAN phone peer: end
EOF

install -m 600 "${tmp_conf}" "${WG_CONF_PATH}"

if wg show "${WG_INTERFACE}" >/dev/null 2>&1; then
  wg set "${WG_INTERFACE}" peer "${DAN_PHONE_PUBLIC_KEY}" allowed-ips "${DAN_PHONE_ALLOWED_IP}"
else
  systemctl restart "wg-quick@${WG_INTERFACE}"
fi

systemctl enable "wg-quick@${WG_INTERFACE}" >/dev/null
echo "Registered DAN phone peer ${DAN_PHONE_ALLOWED_IP} on ${WG_INTERFACE}."
