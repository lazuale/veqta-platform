#!/usr/bin/env bash
# Применяет уже проверенный checkout; не загружает код и не создаёт/удаляет БД.
set -Eeuo pipefail
umask 027

usage() { echo 'Использование: sudo bash scripts/server_apply.sh dev|prod [--migrate]' >&2; exit 2; }
[[ $# -ge 1 && $# -le 2 ]] || usage
ENV_NAME=$1
[[ "$ENV_NAME" == dev || "$ENV_NAME" == prod ]] || usage
[[ $# -eq 1 || "${2:-}" == --migrate ]] || usage
[[ $EUID -eq 0 ]] || { echo 'Требуется root (sudo)' >&2; exit 2; }

APP="/opt/veqta-${ENV_NAME}"
ACTOR="veqta-${ENV_NAME}"
CONFIG="/etc/veqta/${ENV_NAME}.env"
SERVICE="veqta-${ENV_NAME}.service"
NGINX_SITE="/etc/nginx/sites-available/veqta-${ENV_NAME}"
NGINX_LINK="/etc/nginx/sites-enabled/veqta-${ENV_NAME}"
STATIC_ROOT="/var/www/veqta/${ENV_NAME}-static"
case "$ENV_NAME" in
  dev) DOMAIN=test.platform.veqta.ru; PORT=8767 ;;
  prod) DOMAIN=platform.veqta.ru; PORT=8765 ;;
esac

exec 9>"/run/lock/veqta-${ENV_NAME}-deploy.lock"
flock -n 9 || { echo 'Установка этого контура уже выполняется' >&2; exit 2; }

require() { "$@" || { echo "STOP: проверка не пройдена: $1" >&2; exit 2; }; }
require test -d "$APP/.git"
require test -f "$CONFIG"
require id "$ACTOR"
[[ "$(stat -c %U "$APP")" == "$ACTOR" ]] || { echo 'STOP: неверный владелец исходников' >&2; exit 2; }
[[ "$(stat -c %U:%G:%a "$CONFIG")" == "root:${ACTOR}:640" ]] || { echo 'STOP: настройки должны иметь права root:veqta-ENV 0640' >&2; exit 2; }
[[ ! -L "$NGINX_SITE" ]] || { echo 'STOP: неожиданный симлинк вместо сайта Nginx' >&2; exit 2; }
[[ ! -e "$NGINX_LINK" && ! -L "$NGINX_LINK" || "$(readlink "$NGINX_LINK")" == "$NGINX_SITE" ]] || { echo 'STOP: другой сайт с тем же именем' >&2; exit 2; }
for enabled in /etc/nginx/sites-enabled/*; do
  [[ "$enabled" == "$NGINX_LINK" || ! -e "$enabled" ]] && continue
  if awk -v domain="$DOMAIN" '
    $1 == "server_name" {
      for (i=2; i<=NF; i++) {
        gsub(/;/, "", $i)
        if ($i == domain) found=1
      }
    }
    END {exit !found}
  ' "$enabled"; then
    echo "STOP: домен $DOMAIN уже назначен другому сайту $enabled" >&2
    exit 2
  fi
done
[[ -z "$(runuser -u "$ACTOR" -- git -C "$APP" status --porcelain)" ]] || { echo 'STOP: в checkout есть незакоммиченные изменения' >&2; exit 2; }

# Доверяем только root-owned файлу окружения; секреты не печатаем.
set -a
# shellcheck source=/dev/null
source "$CONFIG"
set +a
[[ "$VEQTA_ENV" == "$ENV_NAME" && "$DB_NAME" == "veqta_${ENV_NAME}" && "$DB_USER" == "veqta_${ENV_NAME}" ]] || { echo 'STOP: неверная база/роль/окружение' >&2; exit 2; }
[[ "${DJANGO_DEBUG:-}" == 0 && "${DJANGO_TRUST_PROXY:-}" == 1 ]] || { echo 'STOP: для серверного HTTPS требуются DEBUG=0 и TRUST_PROXY=1' >&2; exit 2; }
[[ ",${DJANGO_ALLOWED_HOSTS:-}," == *",${DOMAIN},"* ]] || { echo 'STOP: домен отсутствует в ALLOWED_HOSTS' >&2; exit 2; }
[[ ",${DJANGO_CSRF_TRUSTED_ORIGINS:-}," == *",https://${DOMAIN},"* ]] || { echo 'STOP: неверный CSRF_TRUSTED_ORIGINS' >&2; exit 2; }
[[ "${DJANGO_STATIC_ROOT:-}" == "$STATIC_ROOT" ]] || { echo 'STOP: неверный STATIC_ROOT' >&2; exit 2; }
[[ "$ENV_NAME" == prod || "${DJANGO_SECURE_COOKIES:-}" == 1 ]] || { echo 'STOP: включите DJANGO_SECURE_COOKIES=1 для HTTPS DEV' >&2; exit 2; }
require test -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
require test -f "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
require openssl x509 -in "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" -checkend 0 -noout

RUN_PY=(runuser -u "$ACTOR" -- bash -c)
run_django() {
  local cmd="$1"
  # Используется только зафиксированная команда без пользовательского ввода.
  "${RUN_PY[@]}" "set -Eeuo pipefail; set -a; source '$CONFIG'; set +a; cd '$APP'; .venv/bin/python manage.py $cmd"
}

printf 'Контур: %s | домен: %s | коммит: ' "$ENV_NAME" "$DOMAIN"
runuser -u "$ACTOR" -- git -C "$APP" rev-parse --short HEAD

if [[ ! -x "$APP/.venv/bin/python" ]]; then
  runuser -u "$ACTOR" -- python3.14 -m venv "$APP/.venv"
fi
runuser -u "$ACTOR" -- "$APP/.venv/bin/python" -m pip install \
  --no-cache-dir --disable-pip-version-check --require-hashes -r "$APP/requirements.lock"
runuser -u "$ACTOR" -- "$APP/.venv/bin/python" -m pip check
run_django 'check'
run_django 'makemigrations --check --dry-run'
if [[ "${2:-}" == --migrate ]]; then
  echo 'Применяем миграции целевого контура (флаг --migrate задан явно)'
  run_django 'migrate --noinput'
fi
run_django 'migrate --check'

install -d -o root -g root -m 0755 /var/www/veqta
install -d -o "$ACTOR" -g www-data -m 2750 "$STATIC_ROOT"
run_django 'collectstatic --noinput'
chgrp -R www-data "$STATIC_ROOT"
chmod -R g+rX "$STATIC_ROOT"

# Конфигурации Nginx и systemd полностью определены зафиксированным коммитом.
# Ошибка nginx -t не приводит к его перезагрузке; предыдущий сайт восстанавливается.
TMP_DIR=$(mktemp -d /run/veqta-nginx.XXXXXXXX)
SITE_EXISTED=0
LINK_CREATED=0
if [[ -f "$NGINX_SITE" ]]; then
  cp -p "$NGINX_SITE" "$TMP_DIR/previous-site.conf"
  SITE_EXISTED=1
fi
rollback_nginx() {
  if [[ "${CONFIG_ACCEPTED:-0}" != 1 ]]; then
    if [[ "$SITE_EXISTED" == 1 ]]; then
      cp -p "$TMP_DIR/previous-site.conf" "$NGINX_SITE"
    else
      rm -f "$NGINX_SITE"
    fi
    if [[ "$LINK_CREATED" == 1 ]]; then rm -f "$NGINX_LINK"; fi
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx || true; fi
  fi
  rm -rf -- "$TMP_DIR"
}
trap rollback_nginx EXIT

install -o root -g root -m 0644 "$APP/deploy/nginx/veqta-${ENV_NAME}.conf" "$NGINX_SITE"
if [[ ! -e "$NGINX_LINK" ]]; then
  ln -s "$NGINX_SITE" "$NGINX_LINK"
  LINK_CREATED=1
fi
nginx -t || { echo 'STOP: nginx -t не прошёл, конфигурация сайта будет восстановлена' >&2; exit 1; }

install -o root -g root -m 0644 "$APP/deploy/systemd/$SERVICE" "/etc/systemd/system/$SERVICE"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

READY=0
for _ in $(seq 1 20); do
  CODE=$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' -H "Host: $DOMAIN" "http://127.0.0.1:$PORT/login/" 2>/dev/null || true)
  if [[ "$CODE" == 200 ]]; then READY=1; break; fi
  sleep 1
done
[[ "$READY" == 1 ]] || { journalctl -u "$SERVICE" -n 50 --no-pager; echo 'FAIL: Gunicorn не готов' >&2; exit 1; }
systemctl is-active --quiet "$SERVICE"
systemctl reload nginx

HTTPS_CODE=$(curl -sS --max-time 10 --resolve "${DOMAIN}:443:127.0.0.1" \
  -o /dev/null -w '%{http_code}' "https://${DOMAIN}/login/")
[[ "$HTTPS_CODE" == 200 ]] || { echo "FAIL: HTTPS/login вернул $HTTPS_CODE" >&2; exit 1; }
CSS_CODE=$(curl -sS --max-time 10 --resolve "${DOMAIN}:443:127.0.0.1" \
  -o /dev/null -w '%{http_code}' "https://${DOMAIN}/static/admin/css/base.css")
[[ "$CSS_CODE" == 200 ]] || { echo "FAIL: HTTPS/static вернул $CSS_CODE" >&2; exit 1; }

CONFIG_ACCEPTED=1
echo "VEQTA ${ENV_NAME^^} SERVER APPLY: PASS"
