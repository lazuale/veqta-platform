#!/usr/bin/env bash
# Создаём одноразовые кластеры с локальными паролями и проверяем выпуск.
set -Eeuo pipefail
umask 077
[[ $# == 0 || ( $# == 1 && "$1" == --browser ) ]] || exit 2
[[ $(id -u) != 0 ]] || { echo 'Запустите проверки от обычного пользователя' >&2; exit 2; }
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
for tool in initdb pg_ctl psql pg_dump pg_restore; do
  [[ "$("$tool" --version)" == *") 19."* ]] || {
    echo "Нужна утилита PostgreSQL 19: $tool" >&2; exit 2;
  }
done

lab=$(mktemp -d)
cleanup() {
  local cluster
  for cluster in primary restore upgrade; do
    if [[ -s "$lab/$cluster/postmaster.pid" ]]; then
      pg_ctl -D "$lab/$cluster" -m fast -w stop >/dev/null || return
    fi
  done
  rm -rf -- "$lab"
}
trap cleanup EXIT

for name in ${!PG@}; do unset "$name"; done
export PGHOST=127.0.0.1 PGUSER=postgres PGDATABASE=postgres PGCONNECT_TIMEOUT=5 PGSSLMODE=disable
export DB_HOST=127.0.0.1 DB_SSLMODE=disable
export DJANGO_ALLOWED_HOSTS=localhost,testserver,127.0.0.1 DJANGO_CSRF_TRUSTED_ORIGINS=https://localhost
export DJANGO_DEBUG=0 DJANGO_TRUST_PROXY=1 DJANGO_SECURE_COOKIES=1
python_command=(python)
if [[ -n "${COVERAGE_FILE:-}" ]]; then
  python_command+=( -m coverage run --append --branch --source=config,portal,workwear --omit='*/migrations/*' )
fi

new_password() { python -c 'import secrets; print(secrets.token_urlsafe(48))'; }

start_cluster() {
  local cluster=$1
  PGPORT=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
  PGPASSWORD=$(new_password)
  export PGPORT PGPASSWORD
  printf '%s\n' "$PGPASSWORD" > "$lab/password"
  initdb -D "$lab/$cluster" -U postgres -A scram-sha-256 --pwfile="$lab/password" --encoding=UTF8 --no-locale > "$lab/init-$cluster.log"
  rm -- "$lab/password"
  if ! pg_ctl -D "$lab/$cluster" -l "$lab/$cluster.log" \
      -o "-h $PGHOST -p $PGPORT -c unix_socket_directories=''" -w start; then
    cat "$lab/$cluster.log" >&2
    return 1
  fi
  psql -XAtw -c 'SHOW server_version'
}

expected_failure() {
  local expected=$1 output
  shift
  if output=$("$@" 2>&1) || [[ "$output" != *"$expected"* ]]; then
    printf 'Ожидался отказ: %s\n%s\n' "$expected" "$output" >&2
    return 1
  fi
}

bootstrap() {
  PGOPTIONS="-c log_statement=all" DB_PASSWORD="$runtime_password" DB_MIGRATION_PASSWORD="$migrator_password" \
    python scripts/bootstrap_database.py "$VEQTA_ENV" "$@"
}

migrator_python() (
  unset PGPASSWORD VEQTA_TEST_DEV_RUNTIME VEQTA_TEST_PROD_RUNTIME
  unset VEQTA_TEST_DEV_MIGRATOR VEQTA_TEST_PROD_MIGRATOR
  export DB_USER="svc_platform_${VEQTA_ENV}_migrator" DB_PASSWORD="$migrator_password"
  export DJANGO_SETTINGS_MODULE=config.migration
  exec "${python_command[@]}" "$@"
)

migrator() { migrator_python manage.py "$@"; }

runtime() (
  unset PGPASSWORD VEQTA_TEST_DEV_RUNTIME VEQTA_TEST_PROD_RUNTIME
  unset VEQTA_TEST_DEV_MIGRATOR VEQTA_TEST_PROD_MIGRATOR
  export DB_USER="svc_platform_$VEQTA_ENV" DB_PASSWORD="$runtime_password"
  export DJANGO_SETTINGS_MODULE=config.settings
  exec "${python_command[@]}" "$@"
)

start_cluster primary
export DB_PORT=$PGPORT
for contour in dev prod; do
  export VEQTA_ENV=$contour DB_NAME="veqta_$contour"
  DJANGO_SECRET_KEY=$(new_password)
  export DJANGO_SECRET_KEY
  runtime_password=$(new_password)
  migrator_password=$(new_password)
  export "VEQTA_TEST_${contour^^}_RUNTIME=$runtime_password"
  export "VEQTA_TEST_${contour^^}_MIGRATOR=$migrator_password"

  bootstrap
  expected_failure 'новый контур' bootstrap
  expected_failure 'Контур, имя БД и роль' runtime manage.py migrate --plan --settings=config.migration
  DB_NAME=postgres expected_failure 'Контур, имя БД и роль' migrator migrate --noinput
  migrator migrate --noinput
  migrator_python -m scripts.database_access --protect-migrations
  expected_failure 'permission denied for table django_migrations' runtime manage.py migrate --plan
  migrator makemigrations --check --dry-run
  migrator migrate --plan
  migrator migrate --check

  DJANGO_SUPERUSER_PASSWORD=$(new_password)
  export DJANGO_SUPERUSER_PASSWORD
  migrator createsuperuser --noinput --username coldstart_admin --email admin@example.invalid
  runtime -m tests.coldstart
  unset DJANGO_SUPERUSER_PASSWORD
  migrator migrate --noinput
  migrator_python -m scripts.database_access --protect-migrations
  migrator migrate --check
  migrator_python -m scripts.database_access
done
python - "$lab/primary.log" <<'PYTHON'
import os
import sys
from pathlib import Path

log = Path(sys.argv[1]).read_text()
for contour in ("DEV", "PROD"):
    for role in ("RUNTIME", "MIGRATOR"):
        assert os.environ[f"VEQTA_TEST_{contour}_{role}"] not in log
print("Bootstrap: открытые пароли ролей отсутствуют при log_statement=all")
PYTHON
python -m tests.release_database

# Используем тот же архиватор, что и при серверном резервном копировании.
source scripts/server_backup.sh
(
  cd "$lab"
  DB_USER=svc_platform_prod_migrator DB_PASSWORD="$migrator_password" \
    veqta_archive_database "$lab" > backup.sha256
  sha256sum --check --strict backup.sha256
)
archives=("$lab"/veqta_prod_*.dump)
[[ ${#archives[@]} == 1 && -s "${archives[0]}" ]]
DB_USER=svc_platform_prod DB_PASSWORD="$runtime_password" \
  expected_failure 'копию создаёт Migrator' veqta_archive_database "$lab"
(
  start_cluster restore
  restore_port=$PGPORT
  runtime_password=$(new_password)
  migrator_password=$(new_password)
  bootstrap --roles-only
  pg_restore --no-password --create --exit-on-error --dbname=postgres "${archives[0]}"
  # Оригинал и копия используют разные реквизиты при одинаковых именах ролей.
  VEQTA_TEST_RESTORED_MIGRATOR="$migrator_password" \
    VEQTA_TEST_RESTORED_RUNTIME="$runtime_password" DB_USER=svc_platform_prod_migrator \
    DB_PASSWORD="$migrator_password" python -m tests.release_database "$restore_port"
  DB_PORT="$restore_port" migrator_python -m scripts.database_access
)

# Собираем исходные базы кодом двух фактических выпусков и сохраняем их данные при переходе.
(
  start_cluster upgrade
  export DB_PORT=$PGPORT
  seed=$(cat <<'PYTHON'
from tests.factories import make_employees, make_import, make_items, make_organization, make_user
from workwear.services import create_request
from workwear.sync import save_draft

organization = make_organization("Организация предыдущего выпуска")
employee = make_employees(make_import(), 1, organization)[0]
item = make_items(1)[0]
actor = make_user()
request = create_request(organization_id=organization.pk, actor=actor)
save_draft(request_id=request.pk, actor=actor, expected_version=request.version,
    members=[{"employee_id": employee.pk, "item_ids": [item.pk]}], finish=True)
PYTHON
)
  for contour in dev prod; do
    export VEQTA_ENV=$contour DB_NAME="veqta_$contour"
    runtime_password=$(new_password)
    migrator_password=$(new_password)
    export "VEQTA_TEST_${contour^^}_RUNTIME=$runtime_password"
    export "VEQTA_TEST_${contour^^}_MIGRATOR=$migrator_password"
    release="$lab/release-$contour"
    mkdir "$release"
    if [[ "$contour" == dev ]]; then
      git archive 94172bac3166ea5d1882159f932b5d848164db58 | tar -x -C "$release"
      (
        cd "$release"
        DB_PASSWORD="$runtime_password" DB_OWNER_PASSWORD="$migrator_password" psql -Xw -f deploy/postgresql/dev.sql
        export DB_USER=veqta_dev_owner DB_PASSWORD="$migrator_password" DJANGO_SETTINGS_MODULE=config.settings
        python manage.py veqta_initialize --environment dev --database-name veqta_dev
        python manage.py shell -c "$seed"
      )
      # На сервере роли уже переименованы. Права и владельцы сохраняются по OID.
      psql -Xw -c '
        ALTER ROLE veqta_dev RENAME TO svc_platform_dev;
        ALTER ROLE veqta_dev_owner RENAME TO svc_platform_dev_migrator;
        ALTER ROLE svc_platform_dev NOINHERIT;
        ALTER ROLE svc_platform_dev_migrator NOINHERIT NOCREATEDB;
      '
    else
      git archive e689dc3d69e3aca69324d9e031655bb3c2af044e | tar -x -C "$release"
      (
        cd "$release"
        bootstrap
        DB_USER=svc_platform_prod_migrator DB_PASSWORD="$migrator_password" \
          DJANGO_SETTINGS_MODULE=config.migration python manage.py veqta_initialize --environment prod --database-name veqta_prod
        DB_USER=svc_platform_prod_migrator DB_PASSWORD="$migrator_password" \
          DJANGO_SETTINGS_MODULE=config.migration python manage.py shell -c "$seed"
      )
    fi
    python -m tests.release_database --upgrade "$contour"
  done
)

export VEQTA_ENV=dev DB_NAME=veqta_dev
export DB_USER=svc_platform_dev_migrator DB_PASSWORD="$VEQTA_TEST_DEV_MIGRATOR"
export DJANGO_SETTINGS_MODULE=tests.settings
psql -Xw -f tests/postgresql.sql
"${python_command[@]}" manage.py test tests --keepdb --noinput --verbosity 2

if [[ ${1:-} == --browser ]]; then
  export DB_RUNTIME_PASSWORD="$VEQTA_TEST_DEV_RUNTIME"
  options=(--browser --matrix)
  [[ -z "${VEQTA_TEST_AXE:-}" ]] || options+=(--axe-script "$VEQTA_TEST_AXE")
  [[ -z "${VEQTA_TEST_NGINX:-}" ]] || options+=(--nginx-executable "$VEQTA_TEST_NGINX")
  failed=0
  for engine in chromium firefox webkit; do
    psql -Xw -c 'DROP DATABASE test_veqta_dev'
    psql -Xw -f tests/postgresql.sql
    extra=()
    if [[ "$engine" == chromium ]]; then
      extra+=(--http-load)
      [[ -z "${VEQTA_TEST_CHROMIUM:-}" ]] || extra+=(--browser-executable "$VEQTA_TEST_CHROMIUM")
    fi
    python -m tests.acceptance "${options[@]}" --engine "$engine" "${extra[@]}" \
      --artifacts "test-results/rc/$engine" || failed=1
  done
  exit "$failed"
fi
