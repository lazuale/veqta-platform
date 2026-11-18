-- Только новый изолированный контур. Существующие базы и роли не изменяются.
-- DB_PASSWORD — пароль приложения; DB_OWNER_PASSWORD — отдельный пароль миграций.
\set ON_ERROR_STOP on
\getenv app_password DB_PASSWORD
\getenv owner_password DB_OWNER_PASSWORD
CREATE ROLE veqta_dev_owner LOGIN PASSWORD :'owner_password' NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION;
CREATE ROLE veqta_dev LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION;
CREATE DATABASE veqta_dev OWNER veqta_dev_owner TEMPLATE template0 ENCODING 'UTF8';
REVOKE ALL ON DATABASE veqta_dev FROM PUBLIC;
GRANT CONNECT ON DATABASE veqta_dev TO veqta_dev;
\connect veqta_dev
ALTER SCHEMA public OWNER TO veqta_dev_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO veqta_dev;
-- Права по умолчанию действуют и на таблицы из миграций Django.
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_dev_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO veqta_dev;
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_dev_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO veqta_dev;
-- Отзываем EXECUTE у PUBLIC глобально: запрета на уровне схемы недостаточно.
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_dev_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE veqta_dev_owner IN SCHEMA public
    GRANT EXECUTE ON FUNCTIONS TO veqta_dev;
