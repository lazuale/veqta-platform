#!/usr/bin/env bash
# Применяет проверенный код, не создавая и не удаляя базы.
set -Eeuo pipefail
umask 022

usage() { echo 'Использование: sudo bash scripts/server_apply.sh dev|prod [--migrate]' >&2; exit 2; }
[[ $# -ge 1 && $# -le 2 ]] || usage
ENV_NAME=$1
[[ "$ENV_NAME" == dev || "$ENV_NAME" == prod ]] || usage
[[ $# -eq 1 || "${2:-}" == --migrate ]] || usage
[[ $EUID -eq 0 ]] || { echo 'Требуется root (sudo)' >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=server_checks.sh
source "$SCRIPT_DIR/server_checks.sh"
veqta_target "$ENV_NAME"

exec 9>"/run/lock/veqta-${ENV_NAME}-deploy.lock"
flock -n 9 || { echo 'Установка этого контура уже выполняется' >&2; exit 2; }
# Общий Nginx нельзя обновлять одновременно из DEV и PROD.
exec 8>/run/lock/veqta-nginx-deploy.lock
flock -n 8 || { echo 'Применение другого контура уже использует Nginx' >&2; exit 2; }

require() { "$@" || { echo "STOP: проверка не пройдена: $1" >&2; exit 2; }; }
require test -d "$APP/.git"
require test -f "$CONFIG"
require id "$ACTOR"
veqta_check_trusted_code || { echo 'STOP: права исполняемого кода не приняты' >&2; exit 2; }
[[ ! -L "$NGINX_SITE" ]] || { echo 'STOP: неожиданный симлинк вместо сайта Nginx' >&2; exit 2; }
[[ ! -e "$NGINX_LINK" && ! -L "$NGINX_LINK" || "$(readlink "$NGINX_LINK")" == "$NGINX_SITE" ]] || { echo 'STOP: другой сайт с тем же именем' >&2; exit 2; }
veqta_clean_checkout
veqta_load_config
veqta_check_nginx
require test -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
require test -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
require openssl x509 -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" -checkend 0 -noout
require openssl x509 -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" -checkhost "$DOMAIN" -noout

REVISION=$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)
[[ "$REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo 'STOP: неверный SHA checkout' >&2; exit 2; }
printf 'Контур: %s | домен: %s | коммит: %s\n' "$ENV_NAME" "$DOMAIN" "$REVISION"

if [[ ! -x "$APP/.venv/bin/python" ]]; then
  python3.14 -m venv "$APP/.venv"
fi
"$APP/.venv/bin/python" -m pip install \
  --no-cache-dir --disable-pip-version-check --require-hashes -r "$APP/requirements.lock"
"$APP/.venv/bin/python" -m pip check
runuser -u "$ACTOR" -- test -r "$APP/manage.py"
runuser -u "$ACTOR" -- test -x "$APP/.venv/bin/python"
veqta_check_trusted_code
veqta_clean_checkout
[[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" == "$REVISION" ]] || {
  echo 'STOP: SHA изменился во время подготовки' >&2; exit 2;
}
veqta_check_database
veqta_check_django
veqta_root_django config.migration makemigrations --check --dry-run
veqta_root_django config.migration migrate --plan
if [[ "${2:-}" == --migrate ]]; then
  echo 'Применяем миграции целевого контура (флаг --migrate задан явно)'
  veqta_root_django config.migration migrate --noinput
  veqta_root_python config.migration -m scripts.database_access --protect-migrations
fi
veqta_root_django config.migration migrate --check
veqta_root_python config.migration -m scripts.database_access

install -d -o root -g root -m 0755 /var/www/veqta
install -d -o root -g www-data -m 2750 "$STATIC_ROOT"
[[ -z "$(find "$STATIC_ROOT" -type l -print -quit)" ]] || {
  echo 'STOP: симлинк в каталоге статики' >&2; exit 2;
}
veqta_root_django config.settings collectstatic --noinput
chown -R root:www-data "$STATIC_ROOT"
chmod -R go-w "$STATIC_ROOT"
chmod -R g+rX "$STATIC_ROOT"
veqta_check_trusted_code
[[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" == "$REVISION" ]] || {
  echo 'STOP: SHA изменился перед запуском службы' >&2; exit 2;
}

# Конфигурации служб берём из проверенного коммита.
# Если nginx -t не прошёл, восстанавливаем прежний сайт без перезагрузки.
TMP_DIR=$(mktemp -d /run/veqta-nginx.XXXXXXXX)
SITE_EXISTED=0
LINK_CREATED=0
NGINX_RELOAD_REQUESTED=0
if [[ -f "$NGINX_SITE" ]]; then
  cp -p "$NGINX_SITE" "$TMP_DIR/previous-site.conf"
  SITE_EXISTED=1
fi
rollback_nginx() {
  local status=$?
  trap - EXIT
  if [[ "${CONFIG_ACCEPTED:-0}" != 1 ]]; then
    if [[ "$SITE_EXISTED" == 1 ]]; then
      cp -p "$TMP_DIR/previous-site.conf" "$NGINX_SITE"
    else
      rm -f "$NGINX_SITE"
    fi
    if [[ "$LINK_CREATED" == 1 ]]; then rm -f "$NGINX_LINK"; fi
    # До reload Nginx использует старые настройки; возвращаем только файлы.
    if [[ "$NGINX_RELOAD_REQUESTED" == 1 ]]; then
      if veqta_check_nginx && systemctl reload nginx; then
        echo 'Файлы сайта восстановлены, запрос reload Nginx отправлен; проверьте доступность' >&2
      else
        echo 'FAIL: файлы сайта возвращены, но reload Nginx не подтверждён' >&2
      fi
    fi
    echo 'STOP: применение не завершено; unit, venv, статика и миграции автоматически не откатывались. См. docs/operations.md' >&2
  fi
  rm -rf -- "$TMP_DIR"
  exit "$status"
}
trap rollback_nginx EXIT

veqta_render_nginx > "$TMP_DIR/new-site.conf"
install -o root -g root -m 0644 "$TMP_DIR/new-site.conf" "$NGINX_SITE"
if [[ ! -e "$NGINX_LINK" ]]; then
  ln -s "$NGINX_SITE" "$NGINX_LINK"
  LINK_CREATED=1
fi
veqta_check_nginx || { echo 'STOP: конфигурация Nginx не принята; сайт будет восстановлен' >&2; exit 1; }

install -o root -g root -m 0644 "$APP/deploy/systemd/$SERVICE" "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
# Сначала проверяем конфигурацию загруженного unit, не требуя запуска.
veqta_check_unit_config
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"
# Состояние active/running проверяем только после запуска.
veqta_check_unit

veqta_check_http || { journalctl -u "$SERVICE" -n 50 --no-pager; exit 1; }
systemctl is-active --quiet "$SERVICE"
NGINX_RELOAD_REQUESTED=1
systemctl reload nginx
veqta_check_https
veqta_check_database

CONFIG_ACCEPTED=1
echo "VEQTA ${ENV_NAME^^} SERVER APPLY: PASS"
