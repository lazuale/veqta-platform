-- Только новый изолированный контур. Существующие базы и роли не изменяются.
-- DB_PASSWORD — пароль приложения; DB_OWNER_PASSWORD — отдельный пароль миграций.
\set ON_ERROR_STOP on
\getenv app_password DB_PASSWORD
\getenv owner_password DB_OWNER_PASSWORD
CREATE ROLE veqta_prod_owner LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE ROLE veqta_prod LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE veqta_prod OWNER veqta_prod_owner TEMPLATE template0 ENCODING 'UTF8';
REVOKE ALL ON DATABASE veqta_prod FROM PUBLIC;
GRANT CONNECT ON DATABASE veqta_prod TO veqta_prod;
\connect veqta_prod
ALTER SCHEMA public OWNER TO veqta_prod_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO veqta_prod;
-- Права по умолчанию действуют и на таблицы из миграций Django.
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_prod_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO veqta_prod;
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_prod_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO veqta_prod;
-- Отзываем EXECUTE у PUBLIC глобально: запрета на уровне схемы недостаточно.
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_prod_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_prod_owner IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO veqta_prod;
