# Локальный запуск

Команды рассчитаны на Linux и Bash. Выполняйте их обычным пользователем из корня проекта в одной оболочке с активным `venv`. Проверяйте результат каждого блока. [Установка зависимостей](../README.md#быстрый-старт).

## Создайте локальную базу

В Ubuntu пакеты `postgresql-18` и `postgresql-client-18` устанавливают утилиты в `/usr/lib/postgresql/18/bin`. Для установки с другим путём задайте `VEQTA_PG_BIN` с каталогом исполняемых файлов. Все три команды должны показать PostgreSQL 18.

```bash
export PATH="${VEQTA_PG_BIN:-/usr/lib/postgresql/18/bin}:$PATH"
initdb --version
pg_ctl --version
psql --version
```

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

## Создайте схему и администратора

```bash
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python manage.py veqta_initialize --environment dev --database-name veqta_dev
python manage.py check
python manage.py migrate --check
python manage.py createsuperuser
```

## Примите учебный справочник

На новой локальной базе можно проверить Workwear на одном вымышленном сотруднике. Подготовьте три CSV и проверьте архив. Для запуска с пустыми справочниками сразу переходите к [запуску приложения](#запустите-приложение).

```bash
VEQTA_DEMO=$(mktemp -d "$VEQTA_PG/demo.XXXXXXXX")
cat > "$VEQTA_DEMO/Employees.csv" <<'CSV'
extId;tabNumber;name;positionExtId;unitExtId;orgExtId;orgName
00000000-0000-4000-8000-000000000001;DEMO-00001;Учебный Сотрудник Примерович;00000000-0000-4000-8000-000000000002;00000000-0000-4000-8000-000000000003;veqta-demo;Учебная организация VEQTA
CSV
cat > "$VEQTA_DEMO/Positions.csv" <<'CSV'
extId;name
00000000-0000-4000-8000-000000000002;Учебная должность
CSV
cat > "$VEQTA_DEMO/Units.csv" <<'CSV'
extId;name;orgExtId;orgName
00000000-0000-4000-8000-000000000003;Учебный участок;veqta-demo;Учебная организация VEQTA
CSV
tar -czf "$VEQTA_DEMO/source.tar.gz" -C "$VEQTA_DEMO" Employees.csv Positions.csv Units.csv
VEQTA_DEMO_DATE=$(TZ=Europe/Moscow date +%F)
python manage.py workwear_import_1c "$VEQTA_DEMO/source.tar.gz" --environment dev \
  --database-name veqta_dev --source-export-date "$VEQTA_DEMO_DATE"
```

Проверьте в отчёте, что `mode` равен `DRY_RUN`, `unique_current_employees` равен 1, а предыдущего импорта нет. Примите этот же архив.

```bash
python manage.py workwear_import_1c "$VEQTA_DEMO/source.tar.gz" --environment dev \
  --database-name veqta_dev --source-export-date "$VEQTA_DEMO_DATE" --apply \
  --expected-employee-count 1 --expected-previous-import-id 0
```

## Запустите приложение

```bash
python manage.py runserver 127.0.0.1:8000
```

Войдите созданной учётной записью, затем откройте Django Admin и Workwear. [Вход](http://127.0.0.1:8000/login/) · [Django Admin](http://127.0.0.1:8000/admin/) · [Workwear](http://127.0.0.1:8000/workwear/).

Если приняли учебный справочник, создайте и завершите первую заявку.

1. В Admin найдите «Учебная организация VEQTA», включите «Разрешено создавать заявки» и сохраните запись.
2. Добавьте активную позицию спецодежды. Для примера задайте код `DEMO-01`, название «Учебная позиция», категорию «Учебная», срок 365 дней и порядок 0. [Справочники](directories.md).
3. В Workwear создайте заявку на учебную организацию. Добавьте сотрудника по табельному номеру `DEMO-00001`, назначьте учебную позицию и сохраните заявку. Нажмите «Завершить заявку» и подтвердите действие. Проверьте состояние заявки в реестре. [Работа с заявками](workwear.md).

## Запустите тесты

Остановите Django сочетанием Ctrl+C. В той же оболочке выполните:

```bash
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python manage.py test tests --noinput
```

Django создаст и удалит отдельную `test_veqta_dev`. [Полный прогон, нагрузка и браузеры](testing.md). Когда кластер больше не нужен, остановите его командой `pg_ctl -D "$VEQTA_PG/data" -m fast -w stop`. Файлы кластера остаются по пути `$VEQTA_PG`.
