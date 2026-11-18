#!/usr/bin/env bash
# Негативные проверки Unix UID на одноразовом изолированном пути GitHub runner.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Требуется root' >&2; exit 2; }
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
echo 'Linux permissions (isolated runner): PASS'
