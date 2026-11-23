-- Создаём контур PROD из базы обслуживания PostgreSQL.
\set ON_ERROR_STOP on
\set database veqta_prod
\set runtime_role svc_platform_prod
\set migrator_role svc_platform_prod_migrator
\ir bootstrap.sql
