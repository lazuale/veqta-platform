-- Создаём контур DEV из базы обслуживания PostgreSQL.
\set ON_ERROR_STOP on
\set database veqta_dev
\set runtime_role svc_platform_dev
\set migrator_role svc_platform_dev_migrator
\ir bootstrap.sql
