-- Администратор PostgreSQL выполняет файл только для нового локального DEV.
-- Пароль поступает из окружения psql, а не из SQL-файла.
\set ON_ERROR_STOP on
\getenv app_password DB_PASSWORD
CREATE ROLE veqta_dev LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION;
CREATE DATABASE veqta_dev OWNER veqta_dev TEMPLATE template0 ENCODING 'UTF8';
REVOKE ALL ON DATABASE veqta_dev FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE veqta_dev TO veqta_dev;
\connect veqta_dev
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
