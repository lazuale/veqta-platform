-- Выводим отклонения от утверждённых прав в одном согласованном снимке каталогов.
WITH target AS (
    SELECT (SELECT oid FROM pg_roles WHERE rolname = %(runtime)s) AS runtime,
           current_user::regrole::oid AS migrator,
           (SELECT oid FROM pg_namespace WHERE nspname = 'public') AS public_schema
), relations AS (
    SELECT c.oid, c.relname::text AS name, c.relowner AS owner, c.relacl AS acl,
           CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END::"char" AS kind,
           CASE
               WHEN c.relname IN ('django_migrations', 'django_migrations_id_seq') THEN ARRAY[]::text[]
               WHEN c.relkind = 'S' THEN ARRAY['USAGE']
               WHEN c.relname::text = ANY(%(append_tables)s::text[]) THEN ARRAY['INSERT', 'SELECT']
               ELSE ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']
           END AS allowed
    FROM pg_class AS c, target
    WHERE c.relnamespace = public_schema AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
), defaults AS (
    SELECT expected.*, d.defaclacl AS acl
    FROM target
    CROSS JOIN LATERAL (VALUES
        (public_schema, 'r'::"char", 'r'::"char", ARRAY['DELETE', 'INSERT', 'SELECT', 'UPDATE']),
        (public_schema, 'S'::"char", 's'::"char", ARRAY['USAGE']),
        (0::oid, 'f'::"char", 'f'::"char", ARRAY[]::text[])
    ) AS expected(namespace, catalog_kind, kind, allowed)
    LEFT JOIN pg_default_acl AS d ON d.defaclrole = migrator
        AND d.defaclnamespace = expected.namespace AND d.defaclobjtype = expected.catalog_kind
), objects AS (
    SELECT 'база ' || datname AS name, oid, datdba AS owner, datacl AS acl,
           'd'::"char" AS kind, ARRAY['CONNECT'] AS allowed
    FROM pg_database WHERE datname = current_database()
    UNION ALL
    SELECT 'схема public', oid, nspowner, nspacl, 'n'::"char", ARRAY['USAGE']
    FROM pg_namespace WHERE nspname = 'public'
    UNION ALL
    SELECT 'public.' || name, oid, owner, acl, kind, allowed FROM relations
    UNION ALL
    SELECT 'public.' || p.oid::regprocedure::text, p.oid, proowner, proacl, 'f'::"char", ARRAY[]::text[]
    FROM pg_proc AS p, target WHERE pronamespace = public_schema
), acl AS (
    SELECT name, owner, allowed, coalesce(acl, acldefault(kind, owner)) AS entries FROM objects
    UNION ALL
    SELECT 'права по умолчанию (' || catalog_kind::text || ')', migrator, allowed,
           coalesce(acl, CASE WHEN namespace = 0 THEN acldefault(kind, migrator) END)
    FROM defaults, target
), required_objects AS (
    SELECT unnest(ARRAY['django_migrations'] || %(append_tables)s::text[]) AS name, 'r'::"char" AS kind
    UNION ALL SELECT 'django_migrations_id_seq', 'S'::"char"
), columns AS (
    SELECT r.*, attname, attnum, attacl FROM relations AS r
    JOIN pg_attribute ON attrelid = r.oid AND attnum > 0 AND NOT attisdropped
    WHERE r.kind = 'r'
)
SELECT 'версия PostgreSQL отличается от 19 или сеанс вне режима origin' AS problem
WHERE current_setting('server_version_num')::int / 10000 <> 19
    OR current_setting('session_replication_role') <> 'origin'
UNION ALL
SELECT 'роль отсутствует или имеет неверные атрибуты: ' || expected.name
FROM (VALUES (%(runtime)s), (current_user::text)) AS expected(name)
LEFT JOIN pg_roles AS r ON r.rolname = expected.name
WHERE r.oid IS NULL OR NOT rolcanlogin OR rolinherit OR rolsuper OR rolcreatedb
    OR rolcreaterole OR rolreplication OR rolbypassrls
UNION ALL
SELECT 'недопустимое членство: ' || member::regrole::text || ' → ' || roleid::regrole::text
FROM pg_auth_members, target
WHERE member IN (runtime, migrator) OR roleid IN (runtime, migrator)
UNION ALL
SELECT 'недопустимые права на session_replication_role'
FROM target
WHERE has_parameter_privilege(runtime, 'session_replication_role', 'SET, ALTER SYSTEM')
    OR has_parameter_privilege(migrator, 'session_replication_role', 'SET, ALTER SYSTEM')
UNION ALL
SELECT 'недопустимая настройка session_replication_role для контура'
FROM pg_db_role_setting, target
WHERE setrole IN (0, runtime, migrator)
    AND setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))
    AND EXISTS (
        SELECT FROM unnest(setconfig) AS setting
        WHERE split_part(setting, '=', 1) = 'session_replication_role'
            AND setting <> 'session_replication_role=origin'
    )
UNION ALL
SELECT 'межконтурный доступ: ' || rolname || ' → ' || datname
FROM pg_database, pg_roles, target
WHERE ((datname = %(other_database)s AND pg_roles.oid IN (runtime, migrator))
    OR (datname = current_database() AND rolname IN (%(other_runtime)s, %(other_runtime)s || '_migrator')))
    AND has_database_privilege(pg_roles.oid, pg_database.oid, 'CONNECT, CREATE, TEMPORARY')
UNION ALL
SELECT 'лишняя схема: ' || nspname
FROM pg_namespace WHERE left(nspname, 3) <> 'pg_' AND nspname NOT IN ('public', 'information_schema')
UNION ALL
SELECT 'нет обязательного объекта public.' || name
FROM required_objects, target
WHERE NOT EXISTS (
    SELECT FROM pg_class WHERE relnamespace = public_schema AND relname = name AND relkind = kind
)
UNION ALL
SELECT 'неверный владелец: ' || name FROM objects, target WHERE owner <> migrator
UNION ALL
SELECT 'неверный владелец: public.' || relname
FROM pg_class, target WHERE relnamespace = public_schema AND relowner <> migrator
    AND relkind NOT IN ('r', 'p', 'v', 'm', 'f', 'S')
UNION ALL
SELECT 'неверный владелец типа: public.' || typname
FROM pg_type, target WHERE typnamespace = public_schema AND typowner <> migrator
UNION ALL
SELECT 'неверные ACL: ' || name
FROM acl, target
WHERE ARRAY(
    SELECT privilege_type FROM aclexplode(entries) WHERE grantee = runtime ORDER BY privilege_type
) <> allowed OR EXISTS (
    SELECT FROM aclexplode(entries)
    WHERE grantor <> owner OR (grantee <> owner AND (grantee <> runtime OR is_grantable))
)
UNION ALL
SELECT 'лишние права по умолчанию: ' || defaclrole::regrole::text || ' (' || defaclobjtype::text || ')'
FROM pg_default_acl, target
WHERE defaclrole <> migrator OR NOT EXISTS (
    SELECT FROM defaults WHERE defaclnamespace = namespace AND defaclobjtype = catalog_kind
)
UNION ALL
SELECT 'неполные права владельца: ' || name
FROM objects
WHERE EXISTS (
    SELECT privilege_type FROM aclexplode(acldefault(kind, owner)) WHERE grantee = owner
    EXCEPT
    SELECT privilege_type FROM aclexplode(coalesce(acl, acldefault(kind, owner))) WHERE grantee = owner
)
UNION ALL
SELECT 'неверные эффективные права: ' || name
FROM objects, target
WHERE EXISTS (
    SELECT FROM aclexplode(acldefault(kind, owner)) AS privilege
    WHERE grantee = owner AND (CASE kind
        WHEN 'd' THEN has_database_privilege(runtime, oid, privilege_type)
        WHEN 'n' THEN has_schema_privilege(runtime, oid, privilege_type)
        WHEN 'r' THEN has_table_privilege(runtime, oid, privilege_type)
        WHEN 's' THEN has_sequence_privilege(runtime, oid, privilege_type)
        WHEN 'f' THEN has_function_privilege(runtime, oid, privilege_type)
    END) IS DISTINCT FROM (privilege_type = ANY(allowed))
)
UNION ALL
SELECT 'неверные права столбца: public.' || name || '.' || attname
FROM columns, target
WHERE EXISTS (
    SELECT FROM aclexplode(attacl)
    WHERE grantee <> owner AND (name = 'django_migrations' OR name = ANY(%(append_tables)s::text[])
        OR grantee <> runtime OR is_grantable OR NOT (privilege_type = ANY(allowed)))
) OR EXISTS (
    SELECT FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'REFERENCES']) AS privilege(name)
    WHERE has_column_privilege(runtime, columns.oid, attnum, privilege.name)
        IS DISTINCT FROM (privilege.name = ANY(allowed))
)
UNION ALL
SELECT 'отключён триггер ограничения: public.' || relname || '.' || tgname
FROM pg_trigger JOIN pg_class ON pg_class.oid = tgrelid, target
WHERE relnamespace = public_schema AND tgisinternal AND tgenabled NOT IN ('O', 'A')
ORDER BY problem;
