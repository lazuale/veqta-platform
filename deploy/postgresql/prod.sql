-- Администратор PostgreSQL выполняет файл только для новой PROD-БД.
\set ON_ERROR_STOP on
\getenv app_password DB_PASSWORD
CREATE ROLE veqta_prod LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE veqta_prod OWNER veqta_prod TEMPLATE template0 ENCODING 'UTF8';
REVOKE ALL ON DATABASE veqta_prod FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE veqta_prod TO veqta_prod;
\connect veqta_prod
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
