#!/usr/bin/env bash
# Негативные проверки Unix UID на одноразовом изолированном пути GitHub runner.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Требуется root' >&2; exit 2; }
umask 022
APP=$(mktemp -d /opt/veqta-permissions.XXXXXXXX)
trap 'rm -rf -- "$APP"' EXIT
chmod 0755 "$APP"
mkdir -p "$APP/.git" "$APP/.venv/lib" "$APP/config" "$APP/scripts" "$APP/workwear/management/commands"
for file in manage.py config/settings.py scripts/server_checks.sh \
            workwear/management/commands/veqta_initialize.py .venv/lib/dependency.py; do
  printf '# synthetic trusted code\n' > "$APP/$file"
  chmod 0644 "$APP/$file"
done
printf 'synthetic owner password\n' > "$APP/owner.env"
chmod 0600 "$APP/owner.env"
# Синтетическая кодовая база повторяет контракт root-owned checkout независимо от ACL runner.
chown -R root:root "$APP"
chmod -R go-w "$APP"
source "$(dirname "$0")/../scripts/server_checks.sh"
veqta_check_trusted_code
for file in manage.py config/settings.py scripts/server_checks.sh \
            workwear/management/commands/veqta_initialize.py .venv/lib/dependency.py; do
  if runuser -u nobody -- sh -c 'printf malicious >> "$1"' sh "$APP/$file" 2>/dev/null; then
    echo "FAIL: прикладной UID изменил $file" >&2; exit 1
  fi
done
if runuser -u nobody -- test -r "$APP/owner.env"; then
  echo 'FAIL: прикладной UID прочёл секрет владельца' >&2; exit 1
fi
if runuser -u nobody -- test -w "$APP"; then
  echo 'FAIL: прикладной UID может менять checkout' >&2; exit 1
fi
chmod g+w "$APP/config/settings.py"
if veqta_check_trusted_code >/dev/null 2>&1; then
  echo 'FAIL: права group-write не обнаружены' >&2; exit 1
fi
chmod g-w "$APP/config/settings.py"
chown nobody "$APP/.venv/lib/dependency.py"
if veqta_check_trusted_code >/dev/null 2>&1; then
  echo 'FAIL: прикладной владелец зависимости не обнаружен' >&2; exit 1
fi
chown root "$APP/.venv/lib/dependency.py"
veqta_check_trusted_code

# Root-owned checkout с group-writable родителем нельзя считать доверенным.
unsafe_parent=$(mktemp -d /opt/veqta-parent-test.XXXXXXXX)
mkdir -p "$unsafe_parent/app/.git" "$unsafe_parent/app/.venv"
chmod 0755 "$unsafe_parent/app"
chmod g+w "$unsafe_parent"
if (APP="$unsafe_parent/app"; veqta_check_trusted_code >/dev/null 2>&1); then
  echo 'FAIL: доступный для подмены родительский каталог принят' >&2
  rm -rf -- "$unsafe_parent"
  exit 1
fi
rm -rf -- "$unsafe_parent"

# Root-owned symlink не может направлять доверенный импорт в world-writable /tmp.
ln -s /tmp "$APP/workwear/unsafe_python_modules"
if veqta_check_trusted_code >/dev/null 2>&1; then
  echo 'FAIL: недоверенная цель симлинка не обнаружена' >&2; exit 1
fi
rm "$APP/workwear/unsafe_python_modules"

# Административный Python не наследует внедрённый PYTHONPATH.
mkdir -p "$APP/.venv/bin"
cat > "$APP/.venv/bin/python" <<'VEQTA_FAKE_PYTHON'
#!/bin/sh
test -z "${PYTHONPATH-}" || exit 71
test -z "${PYTHONHOME-}" || exit 72
test -z "${LD_PRELOAD-}" || exit 73
test "$DB_USER" = veqta_dev_owner || exit 74
test "$DB_PASSWORD" = synthetic-owner || exit 75
test "$VEQTA_ENV" = dev || exit 76
VEQTA_FAKE_PYTHON
chmod 0755 "$APP/.venv/bin/python"
# CI runner может наследовать ACL/permission в newly created .venv/bin.
chown -R root:root "$APP"
chmod -R go-w "$APP"
cat > "$APP/owner.env" <<'VEQTA_FAKE_ENV'
VEQTA_ENV=dev
DB_NAME=veqta_dev
DB_USER=veqta_dev_owner
DB_PASSWORD=synthetic-owner
DB_HOST=127.0.0.1
DB_PORT=5432
DB_SSLMODE=prefer
VEQTA_FAKE_ENV
chmod 0600 "$APP/owner.env"
ENV_NAME=dev
MIGRATION_CONFIG="$APP/owner.env"
VEQTA_ENV=dev
DB_NAME=veqta_dev
DB_USER=veqta_dev
DB_PASSWORD=synthetic-runtime
DB_HOST=127.0.0.1
DB_PORT=5432
DB_SSLMODE=prefer
DJANGO_SECRET_KEY=synthetic-secret
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost
DJANGO_TRUST_PROXY=1
DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost
DJANGO_SECURE_COOKIES=1
DJANGO_STATIC_ROOT=/tmp/synthetic-static
PYTHONPATH=/tmp/untrusted-python veqta_migrate

# /proc PID/environ владельца не должен читаться непривилегированным UID.
(DB_PASSWORD=synthetic-owner /bin/sleep 8) &
owner_pid=$!
if runuser -u nobody -- cat "/proc/$owner_pid/environ" >/dev/null 2>&1; then
  kill "$owner_pid" || true
  echo 'FAIL: чужой Unix UID прочёл root process environ' >&2; exit 1
fi
kill "$owner_pid" 2>/dev/null || true
wait "$owner_pid" 2>/dev/null || true
echo 'Linux permissions and isolated privileged Python (CI runner): PASS'
