#!/usr/bin/env sh
# Deploy hook Certbot: только сертификаты VEQTA, без остановки общего Nginx.
set -eu
for domain in ${RENEWED_DOMAINS:-}; do
    case "$domain" in
        platform.veqta.ru|test.platform.veqta.ru)
            nginx -t
            systemctl reload nginx
            exit 0
            ;;
    esac
done
