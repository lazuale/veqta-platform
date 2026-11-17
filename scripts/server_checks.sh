#!/usr/bin/env bash
# Общие read-only проверки серверных сценариев. Файл только определяет функции.

veqta_target() {
  ENV_NAME=$1
  [[ "$ENV_NAME" == dev || "$ENV_NAME" == prod ]] || return 2
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
}

veqta_load_config() {
  [[ "$(stat -c %U:%G:%a "$CONFIG")" == "root:${ACTOR}:640" ]] || {
    echo 'STOP: настройки должны иметь права root:veqta-ENV 0640' >&2; return 1;
  }
  set -a
  # shellcheck source=/dev/null
  source "$CONFIG"
  set +a
  [[ "$VEQTA_ENV" == "$ENV_NAME" && "$DB_NAME" == "veqta_${ENV_NAME}" && "$DB_USER" == "veqta_${ENV_NAME}" ]] || {
    echo 'STOP: неверная база/роль/окружение' >&2; return 1;
  }
  [[ "${DJANGO_DEBUG:-}" == 0 && "${DJANGO_TRUST_PROXY:-}" == 1 ]] || {
    echo 'STOP: для серверного HTTPS требуются DEBUG=0 и TRUST_PROXY=1' >&2; return 1;
  }
  [[ ",${DJANGO_ALLOWED_HOSTS:-}," == *",${DOMAIN},"* ]] || {
    echo 'STOP: домен отсутствует в ALLOWED_HOSTS' >&2; return 1;
  }
  [[ ",${DJANGO_CSRF_TRUSTED_ORIGINS:-}," == *",https://${DOMAIN},"* ]] || {
    echo 'STOP: неверный CSRF_TRUSTED_ORIGINS' >&2; return 1;
  }
  [[ "${DJANGO_STATIC_ROOT:-}" == "$STATIC_ROOT" ]] || {
    echo 'STOP: неверный STATIC_ROOT' >&2; return 1;
  }
  [[ "${DJANGO_SECURE_COOKIES:-1}" == 1 && ( "$ENV_NAME" == prod || "${DJANGO_SECURE_COOKIES:-}" == 1 ) ]] || {
    echo 'STOP: для серверного HTTPS требуются Secure cookies' >&2; return 1;
  }
}

veqta_clean_checkout() {
  local status
  if ! status=$(runuser -u "$ACTOR" -- git -C "$APP" status --porcelain); then
    echo 'STOP: Git не смог проверить рабочее дерево' >&2
    return 1
  fi
  [[ -z "$status" ]] || { echo 'STOP: в checkout есть незакоммиченные изменения' >&2; return 1; }
}

veqta_django() {
  # Аргументы management-команды передаются отдельно, без повторной интерпретации.
  runuser -u "$ACTOR" -- bash -c '
    set -Eeuo pipefail
    set -a; source "$1"; set +a
    cd "$2"; shift 2
    exec .venv/bin/python manage.py "$@"
  ' bash "$CONFIG" "$APP" "$@"
}

veqta_check_database() {
  # SELECT выполняется от роли приложения, проверяет и фактический адресат.
  # timeout ограничивает также зависшее уже установленное соединение.
  local result=0
  timeout --signal=TERM --kill-after=2s 15s runuser -u "$ACTOR" -- bash -c '
    set -Eeuo pipefail
    set -a; source "$1"; set +a
    cd "$2"
    export PGCONNECT_TIMEOUT=5
    exec .venv/bin/python manage.py shell -c '\''from django.conf import settings; from config.database import require_target; require_target(settings.VEQTA_ENV, settings.EXPECTED_DATABASE); print("PostgreSQL: целевая база и роль доступны")'\''
  ' bash "$CONFIG" "$APP" || result=$?
  if [[ "$result" != 0 ]]; then
    echo "FAIL: PostgreSQL ${ENV_NAME}: целевая база/роль недоступна или проверка прервана (код ${result})" >&2
    return "$result"
  fi
}

veqta_check_nginx() {
  local output
  # nginx -t читает ВСЕ активные include, в том числе conf.d и многострочные директивы.
  if ! output=$(nginx -t 2>&1); then
    printf '%s\n' "$output" >&2
    return 1
  fi
  printf '%s\n' "$output" >&2
  if [[ "$output" == *"conflicting server name \"${DOMAIN}\""* ]]; then
    echo "STOP: конфликт server_name ${DOMAIN} в активной конфигурации Nginx" >&2
    return 1
  fi
}

veqta_secure_cookie() {
  local expected=$1
  awk -v expected="$expected" '
    tolower($0) ~ /^set-cookie:/ {
      sub(/^[^:]*:[[:space:]]*/, "")
      count=split($0, fields, ";")
      if (index(fields[1], expected "=") != 1 || length(fields[1]) <= length(expected)+1) next
      for (i=2; i<=count; i++) {
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", fields[i])
        if (tolower(fields[i]) == "secure") found=1
      }
    }
    END {exit !found}
  '
}

veqta_wait_http() {
  local label=$1 url=$2 cookie=$3
  shift 3
  local response code=000 result=0 attempt
  for attempt in {1..20}; do
    result=0
    # --disable первым аргументом исключает ~/.curlrc; resolve не отключает proxy.
    response=$(curl --disable --noproxy '*' -sS --connect-timeout 2 --max-time 3 \
      -D - -o /dev/null -w $'\n%{http_code}' "$@" "$url" 2>/dev/null) || result=$?
    code=${response##*$'\n'}
    if [[ "$result" == 0 && "$code" == 200 ]]; then
      if [[ -z "$cookie" ]] || printf '%s\n' "${response%$'\n'*}" | tr -d '\r' | veqta_secure_cookie "$cookie"; then
        echo "${label}: 200 OK"
        return 0
      fi
    fi
    [[ "$attempt" == 20 ]] || sleep 1
  done
  echo "FAIL: ${label}: curl=${result}, HTTP=${code}; ожидается 200${cookie:+ и Secure cookie ${cookie}}" >&2
  return 1
}

veqta_check_http() {
  veqta_wait_http 'Gunicorn/login' "http://127.0.0.1:${PORT}/login/" "veqta_${ENV_NAME}_csrf" \
    -H "Host: $DOMAIN" -H 'X-Forwarded-Proto: https'
}

veqta_check_https() {
  veqta_wait_http 'HTTPS/login' "https://${DOMAIN}/login/" "veqta_${ENV_NAME}_csrf" \
    --resolve "${DOMAIN}:443:127.0.0.1" || return 1
  veqta_wait_http 'HTTPS/static' "https://${DOMAIN}/static/admin/css/base.css" '' \
    --resolve "${DOMAIN}:443:127.0.0.1"
}
