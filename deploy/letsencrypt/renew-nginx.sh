#!/bin/sh
# Deploy hook Certbot: имена выбранных контуров берутся из их настроек.
set -eu
for config in /etc/veqta/dev.env /etc/veqta/prod.env; do
    [ -r "$config" ] || continue
    hostname=$(sed -n 's/^VEQTA_HOSTNAME=\([a-z0-9.-]*\)$/\1/p' "$config")
    [ -n "$hostname" ] || continue
    for domain in ${RENEWED_DOMAINS:-}; do
        if [ "$domain" = "$hostname" ]; then
            nginx -t
            systemctl reload nginx
            exit 0
        fi
    done
done
