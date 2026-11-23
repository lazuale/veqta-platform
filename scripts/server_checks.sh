#!/usr/bin/env bash
# Проверки развёртывания. Этот файл только определяет функции.

veqta_target() {
  ENV_NAME=$1
  [[ "$ENV_NAME" == dev || "$ENV_NAME" == prod ]] || return 2
  APP="/opt/veqta-${ENV_NAME}"
  ACTOR="veqta-${ENV_NAME}"
  CONFIG="/etc/veqta/${ENV_NAME}.env"
  MIGRATION_CONFIG="/etc/veqta/${ENV_NAME}-migrate.env"
  SERVICE="veqta-${ENV_NAME}.service"
  NGINX_SITE="/etc/nginx/sites-available/veqta-${ENV_NAME}"
  NGINX_LINK="/etc/nginx/sites-enabled/veqta-${ENV_NAME}"
  STATIC_ROOT="/var/www/veqta/${ENV_NAME}-static"
  case "$ENV_NAME" in
    dev) PORT=8767 ;;
    prod) PORT=8765 ;;
  esac
}

veqta_load_config() {
  [[ "$(stat -c %U:%G:%a "$CONFIG")" == "root:${ACTOR}:640" ]] || {
    echo 'STOP: настройки должны иметь права root:veqta-ENV 0640' >&2; return 1;
  }
  set -a
  unset VEQTA_ENV VEQTA_HOSTNAME DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT DB_SSLMODE
  unset DJANGO_SECRET_KEY DJANGO_DEBUG DJANGO_ALLOWED_HOSTS DJANGO_TRUST_PROXY
  unset DJANGO_CSRF_TRUSTED_ORIGINS DJANGO_STATIC_ROOT DJANGO_SECURE_COOKIES
  # shellcheck source=/dev/null
  source "$CONFIG"
  set +a
  DOMAIN=${VEQTA_HOSTNAME:-}
  veqta_check_hostname || return 1
  [[ "${VEQTA_ENV:-}" == "$ENV_NAME" && "${DB_NAME:-}" == "veqta_${ENV_NAME}" &&
      ( "${DB_USER:-}" == "veqta_${ENV_NAME}" || "${DB_USER:-}" == "svc_platform_${ENV_NAME}" ) ]] || {
    echo 'STOP: неверная база/роль/окружение' >&2; return 1;
  }
  [[ -n "${DB_HOST:-}" && -n "${DB_PORT:-}" && -n "${DB_PASSWORD:-}" ]] || {
    echo 'STOP: не заданы реквизиты PostgreSQL' >&2; return 1;
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

veqta_check_hostname() {
  local label
  local -a labels
  [[ ${#DOMAIN} -le 253 && "$DOMAIN" == *.* && "$DOMAIN" =~ [a-z] ]] || {
    echo 'STOP: нужен VEQTA_HOSTNAME с явным DNS-именем HTTPS' >&2; return 1;
  }
  IFS=. read -r -a labels <<< "$DOMAIN"
  [[ "$DOMAIN" != *. ]] || return 1
  for label in "${labels[@]}"; do
    [[ "$label" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || {
      echo 'STOP: недопустимый VEQTA_HOSTNAME' >&2; return 1;
    }
  done
}

veqta_render_nginx() {
  veqta_check_hostname || return 1
  local suffix=${1:-}
  [[ -z "$suffix" || "$suffix" == -http ]] || return 2
  sed -e "s/__HOSTNAME__/${DOMAIN}/g" -e "s/__PORT__/${PORT}/g" \
    -e "s|__STATIC_ROOT__|${STATIC_ROOT}|g" "$APP/deploy/nginx/veqta${suffix}.conf"
}


# Приложение не должно владеть исполняемым кодом, Git и виртуальным окружением.
veqta_check_trusted_code() {
  [[ ! -L "$APP" && -d "$APP/.git" && ! -L "$APP/.git" && ! -L "$APP/.venv" ]] || {
    echo 'STOP: недоверенный checkout или симлинк' >&2; return 1;
  }
  [[ "$(stat -c %U:%G "$APP")" == root:root && "$(readlink -f -- "$APP")" == "$APP" ]] || {
    echo 'STOP: checkout должен принадлежать root и не иметь симлинков в родительском пути' >&2; return 1;
  }
  local bad part="$APP"
  while [[ "$part" != / ]]; do
    bad=$(find "$part" -maxdepth 0 \( ! -user root -o -perm /022 \) -print -quit) || return 1
    [[ -z "$bad" ]] || {
      echo "STOP: недоверенный каталог пути checkout: $bad" >&2; return 1;
    }
    part=${part%/*}
    [[ -n "$part" ]] || part=/
  done
  # Права символьной ссылки не показательны. Проверяем владельца и путь цели.
  bad=$(find "$APP" \( ! -user root -o \( ! -type l -a -perm /022 \) \) -print -quit) || {
    echo 'STOP: проверка прав исходников завершилась ошибкой' >&2; return 1;
  }
  [[ -z "$bad" ]] || {
    echo "STOP: недоверенные владелец или права: $bad" >&2; return 1;
  }
  # Даже ссылка root может вести в каталог, доступный для записи посторонним.
  local link target part
  while IFS= read -r -d '' link; do
    target=$(readlink -e -- "$link") || {
      echo "STOP: недоступная цель симлинка: $link" >&2; return 1;
    }
    part=$target
    while [[ "$part" != / ]]; do
      bad=$(find "$part" -maxdepth 0 \( ! -user root -o -perm /022 \) -print -quit) || return 1
      [[ -z "$bad" ]] || {
        echo "STOP: недоверенная цель симлинка: $link -> $bad" >&2; return 1;
      }
      part=${part%/*}
      [[ -n "$part" ]] || part=/
    done
  done < <(find "$APP" -type l -print0)
}

# Административную команду запускаем из доверенного кода без переменных, меняющих загрузку Python.
veqta_admin_django() (
  [[ $EUID -eq 0 ]] || { echo 'STOP: административная команда требует root' >&2; exit 1; }
  cd "$APP"
  exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/root USER=root LOGNAME=root \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    VEQTA_ENV="$VEQTA_ENV" VEQTA_HOSTNAME="${VEQTA_HOSTNAME:-}" \
    DJANGO_SECRET_KEY="$DJANGO_SECRET_KEY" DJANGO_DEBUG="$DJANGO_DEBUG" \
    DJANGO_ALLOWED_HOSTS="$DJANGO_ALLOWED_HOSTS" DJANGO_TRUST_PROXY="$DJANGO_TRUST_PROXY" \
    DJANGO_CSRF_TRUSTED_ORIGINS="${DJANGO_CSRF_TRUSTED_ORIGINS:-}" \
    DJANGO_SECURE_COOKIES="$DJANGO_SECURE_COOKIES" DJANGO_STATIC_ROOT="$DJANGO_STATIC_ROOT" \
    DB_NAME="$DB_NAME" DB_USER="$DB_USER" DB_PASSWORD="$DB_PASSWORD" \
    DB_HOST="$DB_HOST" DB_PORT="$DB_PORT" DB_SSLMODE="${DB_SSLMODE:-prefer}" \
    "$APP/.venv/bin/python" -B manage.py "$@"
)

veqta_load_migration_config() {
  [[ "$(stat -c %U:%G:%a "$MIGRATION_CONFIG")" == root:root:600 ]] || {
    echo 'STOP: настройки миграций должны иметь права root:root 0600' >&2; return 1;
  }
  local app_host=$DB_HOST app_port=$DB_PORT app_ssl=${DB_SSLMODE:-prefer}
  unset VEQTA_ENV DB_NAME DB_USER DB_PASSWORD DB_HOST DB_PORT DB_SSLMODE
  set -a
  # shellcheck source=/dev/null
  source "$MIGRATION_CONFIG"
  set +a
  [[ "${VEQTA_ENV:-}" == "$ENV_NAME" && "${DB_NAME:-}" == "veqta_${ENV_NAME}" &&
      ( "${DB_USER:-}" == "veqta_${ENV_NAME}_owner" || "${DB_USER:-}" == "svc_platform_${ENV_NAME}_migrator" ) &&
      "${DB_HOST:-}" == "$app_host" &&
      "${DB_PORT:-}" == "$app_port" && "${DB_SSLMODE:-prefer}" == "$app_ssl" &&
      -n "${DB_PASSWORD:-}" ]] || {
    echo 'STOP: настройки миграций не совпадают с целевой PostgreSQL' >&2; return 1;
  }
}

veqta_owner_django() (
  [[ $EUID -eq 0 ]] || { echo 'STOP: миграции требуют root' >&2; exit 1; }
  umask 077
  veqta_check_trusted_code || exit 1
  veqta_load_migration_config || exit 1
  veqta_admin_django shell -c 'from django.conf import settings; from config.database import require_target; require_target(settings.VEQTA_ENV, settings.EXPECTED_DATABASE, owner=True)' || exit 1
  veqta_admin_django "$@"
)

veqta_migrate() {
  veqta_owner_django migrate --noinput
}

veqta_clean_checkout() {
  local status
  if ! status=$(git -c safe.directory="$APP" -C "$APP" status --porcelain); then
    echo 'STOP: Git не смог проверить рабочее дерево' >&2
    return 1
  fi
  [[ -z "$status" ]] || { echo 'STOP: в checkout есть незакоммиченные изменения' >&2; return 1; }
}

veqta_django() {
  local -a deadline=()
  local connect_timeout=''
  if [[ "${1:-}" == --bounded ]]; then
    deadline=(timeout --signal=TERM --kill-after=2s 15s)
    connect_timeout=5
    shift
  fi
  # Передаём аргументы без повторного разбора оболочкой.
  "${deadline[@]}" runuser -u "$ACTOR" -- bash -c '
    set -Eeuo pipefail
    set -a; source "$1"; set +a
    cd "$2"
    if [[ -n "$3" ]]; then export PGCONNECT_TIMEOUT="$3"; fi
    shift 3
    exec .venv/bin/python manage.py "$@"
  ' bash "$CONFIG" "$APP" "$connect_timeout" "$@"
}

veqta_check_database() {
  # Проверяем фактическую базу и роль от имени приложения.
  # Тайм-аут ограничивает и зависшее соединение.
  local result=0
  veqta_django --bounded shell -c 'from django.conf import settings; from config.database import require_target; require_target(settings.VEQTA_ENV, settings.EXPECTED_DATABASE); print("PostgreSQL: целевая база и роль доступны")' || result=$?
  if [[ "$result" != 0 ]]; then
    echo "FAIL: PostgreSQL ${ENV_NAME}: целевая база/роль недоступна или проверка прервана (код ${result})" >&2
    return "$result"
  fi
}

veqta_check_nginx() {
  local output
  # nginx -t проверяет все подключённые файлы конфигурации.
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
    # --disable исключает ~/.curlrc; --resolve не отключает прокси.
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
