# Локальный запуск

Команды рассчитаны на Linux и Bash. Нужны Git, Python 3.14 с `venv` и утилиты PostgreSQL 18 `initdb`, `pg_ctl` и `psql` в `PATH`. Запускайте их обычным пользователем. Продолжайте в одной оболочке из корня клона и проверяйте результат каждого блока. [Клонирование и установка зависимостей](../README.md#быстрый-старт).

## Создайте локальную базу

Для ознакомления поднимите отдельный кластер с паролями SCRAM и доступом через loopback. Порт 55432 должен быть свободен. Для другого порта достаточно поменять `DB_PORT` здесь до запуска.

```bash
VEQTA_PG=$(mktemp -d /tmp/veqta-pg.XXXXXXXX)
export DB_HOST=127.0.0.1 DB_PORT=55432 DB_SSLMODE=disable
VEQTA_PG_PASSWORD=$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(32))')
(umask 077; printf '%s\n' "$VEQTA_PG_PASSWORD" > "$VEQTA_PG/bootstrap.password")
initdb -D "$VEQTA_PG/data" -U postgres -A scram-sha-256 \
  --pwfile="$VEQTA_PG/bootstrap.password" --encoding=UTF8 --locale=C.UTF-8
rm -- "$VEQTA_PG/bootstrap.password"
pg_ctl -D "$VEQTA_PG/data" -l "$VEQTA_PG/postgres.log" \
  -o "-h $DB_HOST -p $DB_PORT -c unix_socket_directories=" -w start
```

```bash
export VEQTA_ENV=dev DB_NAME=veqta_dev DB_USER=veqta_dev
export DJANGO_DEBUG=1 DJANGO_TRUST_PROXY=0 DJANGO_SECURE_COOKIES=0
export DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,testserver
export DJANGO_SECRET_KEY="$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(64))')"
export DB_PASSWORD="$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(32))')"
export DB_OWNER_PASSWORD="$(python -c 'from secrets import token_urlsafe; print(token_urlsafe(32))')"
PGPASSWORD="$VEQTA_PG_PASSWORD" psql -X -h "$DB_HOST" -p "$DB_PORT" -U postgres -d postgres \
  -f deploy/postgresql/dev.sql && unset VEQTA_PG_PASSWORD
```

SQL создаёт `veqta_dev` и две роли. Схему создаёт владелец, а обычные команды приложения используют отдельную роль с доступом к данным.

## Запустите приложение

```bash
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python manage.py veqta_initialize --environment dev --database-name veqta_dev
python manage.py check
python manage.py migrate --check
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Откройте [страницу входа](http://127.0.0.1:8000/login/) и войдите созданной учётной записью. Проверьте Portal, [Admin](http://127.0.0.1:8000/admin/) и [Workwear](http://127.0.0.1:8000/workwear/). Для первой заявки создайте организацию и позицию СИЗ, затем примите тестовую выгрузку сотрудников. [Справочники](directories.md) · [Формат 1С](import-1c.md).

## Запустите тесты

Остановите Django сочетанием Ctrl+C. В той же оболочке выполните:

```bash
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python manage.py test tests --noinput
```

Django создаст и удалит отдельную `test_veqta_dev`. [Полный прогон, нагрузка и браузеры](testing.md). Когда кластер больше не нужен, остановите его командой `pg_ctl -D "$VEQTA_PG/data" -m fast -w stop`. Файлы кластера остаются по пути `$VEQTA_PG`.
