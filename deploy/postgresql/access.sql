-- Права для новой базы. Владельцем схемы и объектов миграций становится Migrator.
REVOKE ALL ON DATABASE :"database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"database" TO :"runtime_role";
\connect :database
BEGIN;
ALTER SCHEMA public OWNER TO :"migrator_role";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"runtime_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO :"runtime_role";
-- Отзыв EXECUTE действует на все схемы: исключения задаются для отдельных функций.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_role" REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
COMMIT;
