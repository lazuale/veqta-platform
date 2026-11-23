-- Общая установка. DB_PASSWORD и DB_MIGRATION_PASSWORD поступают из окружения.
\set ON_ERROR_STOP on
\set runtime_password ''
\set migration_password ''
\getenv runtime_password DB_PASSWORD
\getenv migration_password DB_MIGRATION_PASSWORD

SELECT current_database() = 'postgres'
    AND current_setting('server_version_num')::integer / 10000 = 19
    AND NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database')
    AND NOT EXISTS (SELECT FROM pg_roles WHERE rolname IN (:'runtime_role', :'migrator_role'))
    AND length(:'runtime_password') > 0 AND length(:'migration_password') > 0
    AND :'runtime_password' <> :'migration_password' AS new_target
\gset
\if :new_target
\else
  DO $$ BEGIN
    RAISE EXCEPTION 'Нужны PostgreSQL 19, база обслуживания postgres, новый контур и разные пароли';
  END $$;
\endif

SET password_encryption = 'scram-sha-256';
BEGIN;
CREATE ROLE :"runtime_role" LOGIN PASSWORD :'runtime_password'
    NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE :"migrator_role" LOGIN PASSWORD :'migration_password'
    NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT;
COMMIT;
\unset runtime_password
\unset migration_password

\if :{?roles_only}
  -- Изолированное восстановление берёт БД и её ACL из архива.
  \quit
\endif

CREATE DATABASE :"database" OWNER :"migrator_role" TEMPLATE template0 ENCODING 'UTF8';
\ir access.sql
