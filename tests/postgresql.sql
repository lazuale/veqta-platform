-- Только изолированный кластер тестов. DEV и PROD не изменяются.
\set ON_ERROR_STOP on
\set database test_veqta_dev
\set runtime_role svc_platform_dev
\set migrator_role svc_platform_dev_migrator
CREATE DATABASE :"database" OWNER :"migrator_role" TEMPLATE template0 ENCODING 'UTF8';
\ir ../deploy/postgresql/access.sql
