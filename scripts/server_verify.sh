#!/usr/bin/env bash
# Проверяем DEV/PROD, не изменяя настройки, службы и данные.
set -Eeuo pipefail
[[ $# == 1 && ( "$1" == dev || "$1" == prod ) ]] || { echo 'Использование: bash scripts/server_verify.sh dev|prod' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'Требуется root (sudo): проверяются закрытые настройки и сертификаты Nginx' >&2; exit 2; }
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=server_checks.sh
source "$SCRIPT_DIR/server_checks.sh"
veqta_target "$1"

test -d "$APP/.git"
test -f "$CONFIG"
veqta_check_trusted_code
veqta_clean_checkout
veqta_load_config
veqta_check_nginx

echo '=== Ревизия checkout ==='
git --no-pager -c safe.directory="$APP" -C "$APP" log -1 --format='%H %s'

echo '=== systemd ==='
systemctl is-active --quiet "$SERVICE"
veqta_check_unit

echo '=== PostgreSQL ==='
veqta_check_database
veqta_root_django config.migration migrate --check
veqta_root_python config.migration -m scripts.database_access

echo '=== Django ==='
veqta_check_django

echo '=== Gunicorn ==='
veqta_check_http
echo '=== HTTPS и статика ==='
veqta_check_https
echo "VEQTA ${ENV_NAME^^} SERVER VERIFY: PASS"
