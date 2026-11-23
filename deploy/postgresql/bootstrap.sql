-- scripts/bootstrap_database.py передаёт верификаторы SCRAM через окружение psql.
\set ON_ERROR_STOP on
\set runtime_verifier ''
\set migrator_verifier ''
\getenv runtime_verifier VEQTA_RUNTIME_VERIFIER
\getenv migrator_verifier VEQTA_MIGRATOR_VERIFIER

SELECT current_database() = 'postgres'
    AND current_setting('server_version_num')::integer / 10000 = 19
    AND NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database')
    AND NOT EXISTS (SELECT FROM pg_roles WHERE rolname IN (:'runtime_role', :'migrator_role'))
    AND :'runtime_verifier' LIKE 'SCRAM-SHA-256$%'
    AND :'migrator_verifier' LIKE 'SCRAM-SHA-256$%' AS new_target
\gset
\if :new_target
\else
  DO $$ BEGIN
    RAISE EXCEPTION 'Нужны PostgreSQL 19, база обслуживания postgres, новый контур и верификаторы SCRAM';
  END $$;
\endif

BEGIN;
CREATE ROLE :"runtime_role" LOGIN PASSWORD :'runtime_verifier'
    NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT;
CREATE ROLE :"migrator_role" LOGIN PASSWORD :'migrator_verifier'
    NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS NOINHERIT;
COMMIT;
\unset runtime_verifier
\unset migrator_verifier

\if :{?roles_only}
  -- Изолированное восстановление берёт БД и её ACL из архива.
  \quit
\endif

CREATE DATABASE :"database" OWNER :"migrator_role" TEMPLATE template0 ENCODING 'UTF8';
\ir access.sql
