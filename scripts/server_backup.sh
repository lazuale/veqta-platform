#!/usr/bin/env bash
# Архив прикладной базы с владельцами, ACL и правами по умолчанию.
veqta_archive_database() (
  set -Eeuo pipefail
  umask 077
  local destination=$1 name tool actual temporary archive
  [[ ( $VEQTA_ENV == dev || $VEQTA_ENV == prod ) &&
      $DB_NAME == "veqta_$VEQTA_ENV" && $DB_USER == "svc_platform_${VEQTA_ENV}_migrator" ]] || {
    echo 'STOP: копию создаёт Migrator выбранного контура' >&2; exit 2;
  }
  if [[ -n "${VEQTA_PG_BIN:-}" ]]; then
    export PATH="$VEQTA_PG_BIN:$PATH"
  fi
  for tool in psql pg_dump pg_restore; do
    [[ "$("$tool" --version)" == *") 19."* ]] || {
      echo "STOP: требуется клиент PostgreSQL 19 ($tool)" >&2; exit 2;
    }
  done
  # Учитываем только параметры выбранного контура, без переменных libpq оператора.
  for name in ${!PG@}; do unset "$name"; done
  export PGHOST="$DB_HOST" PGPORT="$DB_PORT" PGDATABASE="$DB_NAME" PGUSER="$DB_USER"
  export PGPASSWORD="$DB_PASSWORD" PGSSLMODE="${DB_SSLMODE:-prefer}" PGCONNECT_TIMEOUT=5
  actual=$(psql -XAtw -c "
      SELECT current_database(), session_user, current_user,
             current_setting('server_version_num')::int / 10000
      FROM pg_roles WHERE rolname = session_user
        AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
  ") || exit 2
  [[ "$actual" == "$DB_NAME|$DB_USER|$DB_USER|19" ]] || {
    echo 'STOP: PostgreSQL подключила команду к другой базе или роли' >&2; exit 2;
  }

  temporary=$(mktemp "$destination/.$DB_NAME.XXXXXXXX") || exit 1
  trap 'rm -f -- "$temporary"' EXIT
  archive="$destination/${DB_NAME}_$(date -u +%Y%m%dT%H%M%SZ)_${temporary##*.}.dump"
  pg_dump --no-password --format=custom --file="$temporary" || exit 1
  pg_restore --list "$temporary" >/dev/null || exit 1
  ln -- "$temporary" "$archive" || exit 1
  sha256sum -- "$archive"
)

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  set -Eeuo pipefail
  [[ $EUID == 0 && $# == 2 && ( "$1" == dev || "$1" == prod ) ]] || {
    echo 'Использование: sudo bash scripts/server_backup.sh dev|prod /каталог/резервных/копий' >&2
    exit 2
  }

  backup_dir=$(realpath -e -- "$2")
  [[ -d "$backup_dir" && "$(stat -c %U:%G:%a "$backup_dir")" == root:root:700 ]] || {
    echo 'STOP: каталог резервных копий должен иметь права root:root 0700' >&2; exit 2;
  }
  script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
  source "$script_dir/server_checks.sh"
  veqta_target "$1"
  veqta_load_config
  veqta_load_migration_config
  veqta_archive_database "$backup_dir"
fi
