#!/usr/bin/env bash
# Проверка развернутого DEV/PROD без изменений файлов, служб и базы.
set -Eeuo pipefail
[[ $# == 1 && ( "$1" == dev || "$1" == prod ) ]] || { echo 'Использование: bash scripts/server_verify.sh dev|prod' >&2; exit 2; }
ENV_NAME=$1
DOMAIN=platform.veqta.ru
PORT=8765
if [[ "$ENV_NAME" == dev ]]; then DOMAIN=test.platform.veqta.ru; PORT=8767; fi
SERVICE="veqta-${ENV_NAME}.service"
APP="/opt/veqta-${ENV_NAME}"
CONFIG="/etc/veqta/${ENV_NAME}.env"

systemctl is-active --quiet "$SERVICE"
test -d "$APP/.git"
test -f "$CONFIG"
nginx -t

echo '=== Фактическая версия ==='
git --no-pager -c safe.directory="$APP" -C "$APP" log -1 --oneline

echo '=== systemd ==='
systemctl show "$SERVICE" -p ActiveState -p SubState -p User -p WorkingDirectory

echo '=== Gunicorn ==='
CODE=$(curl -sS --max-time 5 -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https" -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login/")
[[ "$CODE" == 200 ]] || { echo "FAIL: HTTP Gunicorn $CODE" >&2; exit 1; }

echo '=== HTTPS и статика ==='
HEADER=$(curl -fsS --max-time 10 -D - -o /dev/null --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/login/")
CSS_CODE=$(curl -sS --max-time 10 -o /dev/null -w '%{http_code}' --resolve "${DOMAIN}:443:127.0.0.1" "https://${DOMAIN}/static/admin/css/base.css")
[[ "$CSS_CODE" == 200 ]] || { echo "FAIL: HTTP CSS $CSS_CODE" >&2; exit 1; }
# В обоих HTTPS-контурах Cookie CSRF должна иметь флаг Secure.
printf '%s\n' "$HEADER" | tr -d '\r' | grep -Eiq '^set-cookie: veqta_(dev|prod)_csrf=.*; Secure([;[:space:]]|$)' || {
  echo 'FAIL: нет Secure у CSRF cookie' >&2; exit 1;
}
echo "VEQTA ${ENV_NAME^^} SERVER VERIFY: PASS"
