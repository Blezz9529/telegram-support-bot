#!/bin/sh
set -e

if [ "${TELEGRAM_PROXY_ENABLED:-0}" != "1" ]; then
  echo "Xray proxy disabled (TELEGRAM_PROXY_ENABLED!=1)"
  exit 0
fi

: "${XRAY_VLESS_ID:?XRAY_VLESS_ID is required}"
: "${XRAY_SERVER:?XRAY_SERVER is required}"
: "${XRAY_PORT:?XRAY_PORT is required}"
: "${XRAY_PBK:?XRAY_PBK is required}"
: "${XRAY_SNI:?XRAY_SNI is required}"
: "${XRAY_SID:?XRAY_SID is required}"
: "${XRAY_SPX:?XRAY_SPX is required}"

XRAY_FP="${XRAY_FP:-chrome}"
XRAY_LOGLEVEL="${XRAY_LOGLEVEL:-warning}"

cat >/etc/xray/config.json <<EOF
{
  "log": {
    "loglevel": "${XRAY_LOGLEVEL}"
  },
  "inbounds": [
    {
      "listen": "0.0.0.0",
      "port": 10809,
      "protocol": "http",
      "settings": {
        "allowTransparent": false
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "${XRAY_SERVER}",
            "port": ${XRAY_PORT},
            "users": [
              {
                "id": "${XRAY_VLESS_ID}",
                "encryption": "none"
              }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "fingerprint": "${XRAY_FP}",
          "serverName": "${XRAY_SNI}",
          "publicKey": "${XRAY_PBK}",
          "shortId": "${XRAY_SID}",
          "spiderX": "${XRAY_SPX}"
        }
      }
    }
  ]
}
EOF

exec xray run -c /etc/xray/config.json
