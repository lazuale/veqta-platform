# Серверное развёртывание VEQTA

Чистая установка создаёт базу PostgreSQL, схему Workwear и права ролей. Для действующего контура с применённой `0001_initial` переходите к [обновлению](#7-обновление-существующего-контура-и-откат).

## Подготовка сервера

Базовый стек проекта — Ubuntu 26.04.1, Python 3.14 с venv, PostgreSQL 19, Gunicorn, Nginx и systemd. Версии зависимостей приложения закреплены в `requirements.lock`. Подключите [репозиторий пакетов PostgreSQL для Ubuntu](https://www.postgresql.org/download/linux/ubuntu/) и установите необходимые пакеты.

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends ca-certificates git curl openssl nginx \
  python3.14 python3.14-venv postgresql-19 postgresql-client-19 util-linux coreutils mawk certbot
sudo systemctl enable --now postgresql nginx certbot.timer
```

На подготовленном сервере сверяйте версии и доступность действующего кластера. Если требуется другой пакетный источник PostgreSQL, согласуйте его для нового сервера. Настройте два отдельных DNS-имени, доступ HTTP/80 для webroot и HTTPS/443 для пользователей. Согласуйте записи A/AAAA с реальными адресами Nginx. PostgreSQL и Gunicorn доступны только локально. Пример рассчитывает на локальный сокет `/var/run/postgresql`, порт 5432 и административную роль `postgres`.

```bash
python3.14 --version
psql --version
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -U postgres -d postgres \
  -c 'SHOW server_version;' -c 'SHOW listen_addresses;'
sudo nginx -t
sudo systemctl is-active postgresql nginx
```

В `pg_hba.conf` разрешите вход каждой роли в её базу с loopback по SCRAM. Учитывайте порядок правил и сохраняйте настройки других приложений PostgreSQL. Интеграционный прогон использует отдельный кластер.

## 1. Контуры и предпосылки

| Параметр | DEV | PROD |
|---|---|---|
| Домен | своё DNS-имя DEV в `VEQTA_HOSTNAME` | отдельное DNS-имя PROD |
| Checkout, Git и venv: root:root | `/opt/veqta-dev` | `/opt/veqta-prod` |
| Linux-пользователь / группа | `veqta-dev` | `veqta-prod` |
| Env, root:группа 0640 | `/etc/veqta/dev.env` | `/etc/veqta/prod.env` |
| База PostgreSQL | `veqta_dev` | `veqta_prod` |
| Runtime | `svc_platform_dev` | `svc_platform_prod` |
| Migrator, владелец БД и схемы | `svc_platform_dev_migrator` | `svc_platform_prod_migrator` |
| Env миграций, root:root 0600 | `/etc/veqta/dev-migrate.env` | `/etc/veqta/prod-migrate.env` |
| Gunicorn | `127.0.0.1:8767` | `127.0.0.1:8765` |
| Статика: root:www-data, чтение Nginx | `/var/www/veqta/dev-static` | `/var/www/veqta/prod-static` |
| Systemd | `veqta-dev.service` | `veqta-prod.service` |

Bootstrap отзывает доступ `PUBLIC` к базам и схеме `public` каждого контура. Runtime получает только прикладные права. Для служебных команд используется отдельный файл окружения Migrator.

Перед Cold Start проверьте, что каталоги, роли, базы и домены свободны. Для работающего контура используйте [обновление](#7-обновление-существующего-контура-и-откат).

## 2. Получение и фиксация исходников — оператор без sudo

В обычной оболочке оператора клонируйте исходники и выберите полный SHA с успешной проверкой в CI:

```bash
VEQTA_SOURCE=$(mktemp -d "$HOME/veqta-source.XXXXXXXX")
git clone https://github.com/lazuale/veqta-platform.git "$VEQTA_SOURCE"
git -C "$VEQTA_SOURCE" --no-pager log -5 --format='%H %s'
read -r -p 'Полный SHA проверенного коммита (40 hex): ' VEQTA_REVISION
[[ "$VEQTA_REVISION" =~ ^[0-9a-f]{40}$ ]] || { echo 'Неверный SHA'; exit 1; }
git -C "$VEQTA_SOURCE" cat-file -e "${VEQTA_REVISION}^{commit}"
git -C "$VEQTA_SOURCE" checkout --detach "$VEQTA_REVISION"
git -C "$VEQTA_SOURCE" status --porcelain
git -C "$VEQTA_SOURCE" rev-parse HEAD
```

Проверьте пустой вывод `status` и совпадение SHA. Каталог оператора используется только для получения Git-объектов. Привилегированные команды запускаются из отдельного каталога, принадлежащего root.

## 3. Создание нового контура

Выполните блок один раз для каждого нового контура. В примере выбран DEV. Для PROD повторите с `VEQTA_TARGET=prod` и отдельным DNS-именем.

```bash
VEQTA_TARGET=dev
# Для второго независимого запуска: VEQTA_TARGET=prod
read -r -p 'DNS-имя HTTPS выбранного контура: ' VEQTA_HOSTNAME
sudo bash -s -- "$VEQTA_SOURCE" "$VEQTA_REVISION" "$VEQTA_TARGET" "$VEQTA_HOSTNAME" <<'VEQTA_BOOTSTRAP'
set -Eeuo pipefail
umask 022
source_repo=$1
revision=$2
target=$3
[[ "$target" == dev || "$target" == prod ]]
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
actor="veqta-${target}"
app="/opt/veqta-${target}"
config="/etc/veqta/${target}.env"
database="veqta_${target}"
domain=$4
migration_config="/etc/veqta/${target}-migrate.env"
# Не source из операторского staging: его рабочее дерево принадлежит не root.
[[ ! -e "$app" && ! -L "$app" && ! -e "$config" && ! -L "$config" && ! -e "$migration_config" && ! -L "$migration_config" ]]
if id "$actor" >/dev/null 2>&1; then
    echo 'STOP: пользователь уже существует; проверьте частичную установку по operations.md' >&2
    exit 1
fi
existing=$(runuser -u postgres -- psql -X -At -h /var/run/postgresql -p 5432 -U postgres -d postgres -v target="$database" -v runtime_role="svc_platform_$target" <<'VEQTA_SQL'
SELECT EXISTS(SELECT FROM pg_database WHERE datname = :'target')
    OR EXISTS(SELECT FROM pg_roles WHERE rolname IN (:'runtime_role', :'runtime_role' || '_migrator'));
VEQTA_SQL
)
[[ "$existing" == f ]] || { echo 'STOP: роль или база уже существует'; exit 1; }
install -d -o root -g root -m 0755 "$app"
git -c safe.directory="$source_repo" clone --no-local --no-checkout "$source_repo" "$app"
git -C "$app" remote set-url origin https://github.com/lazuale/veqta-platform.git
git -C "$app" checkout --detach "$revision"
[[ "$(git -C "$app" rev-parse HEAD)" == "$revision" ]]
status=$(git -C "$app" status --porcelain)
[[ -z "$status" ]]
# Только здесь разрешён source: проверенный SHA находится в root-owned checkout.
source "$app/scripts/server_checks.sh"
DOMAIN=$domain
veqta_check_hostname
useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$actor"
python3.14 "$app/scripts/create_config.py" --environment "$target" --hostname "$domain"
install -d -o root -g root -m 0755 /etc/veqta
install -o root -g "$actor" -m 0640 "$app/.env.$target" "$config"
install -o root -g root -m 0600 "$app/.env.$target-migrate" "$migration_config"
rm -- "$app/.env.$target" "$app/.env.$target-migrate"
(
    set -a; source "$migration_config"; set +a
    export DB_MIGRATION_PASSWORD=$DB_PASSWORD
    set -a; source "$config"; set +a
    # Оба пароля передаются psql только в окружении этого дочернего процесса.
    runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432 -U postgres -d postgres -f "$app/deploy/postgresql/$target.sql"
)
python3.14 -m venv "$app/.venv"
"$app/.venv/bin/python" -m pip install --no-cache-dir --require-hashes -r "$app/requirements.lock"
"$app/.venv/bin/python" -m pip check
runuser -u "$actor" -- test -r "$app/manage.py"
runuser -u "$actor" -- test -x "$app/.venv/bin/python"
veqta_target "$target"
veqta_check_trusted_code
veqta_load_config
veqta_root_django config.migration veqta_initialize --environment "$target" --database-name "$database"
VEQTA_BOOTSTRAP
```

Блок создаёт Unix-пользователя, файлы окружения, БД и штатную начальную схему. Установка останавливается при существующей роли, базе или каталоге. Если часть шагов уже выполнена, сверяйтесь с [восстановлением после отказа](operations.md#отказ-посередине-установки).

## 4. HTTPS

Если выбранный домен уже имеет сертификат в `/etc/letsencrypt/live/<домен>/`, проверьте срок, имя в `fullchain.pem` и наличие `privkey.pem`. Затем перейдите к применению. Для нового домена проверьте, что он свободен в активной конфигурации `sudo nginx -T` и доступен снаружи по HTTP. Certbot использует webroot. Контактный e-mail укажите в оболочке оператора.

```bash
read -r -p 'Контактный e-mail для ACME: ' VEQTA_ACME_EMAIL
sudo bash -s -- "$VEQTA_TARGET" "$VEQTA_ACME_EMAIL" <<'VEQTA_HTTPS'
set -Eeuo pipefail
target=$1
email=$2
[[ "$target" == dev || "$target" == prod ]]
[[ -n "$email" ]]
source "/opt/veqta-$target/scripts/server_checks.sh"
veqta_target "$target"
veqta_load_config
[[ ! -e "/etc/letsencrypt/live/$DOMAIN" && ! -L "/etc/letsencrypt/live/$DOMAIN" ]]
[[ ! -e "$NGINX_SITE" && ! -L "$NGINX_SITE" && ! -e "$NGINX_LINK" && ! -L "$NGINX_LINK" ]]
install -d -o root -g root -m 0755 /var/www/veqta/acme
veqta_render_nginx -http > "$NGINX_SITE"
chmod 0644 "$NGINX_SITE"
ln -s "$NGINX_SITE" "$NGINX_LINK"
if ! veqta_check_nginx; then
    rm -- "$NGINX_LINK" "$NGINX_SITE"
    echo 'STOP: временный сайт не принят; работающий Nginx не перезагружался' >&2
    exit 1
fi
systemctl reload nginx
certbot certonly --webroot --webroot-path /var/www/veqta/acme --cert-name "$DOMAIN" -d "$DOMAIN" --email "$email" --agree-tos --non-interactive
openssl x509 -in "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" -checkend 0 -checkhost "$DOMAIN" -noout
VEQTA_HTTPS
```

Временный сайт отдаёт challenge ACME и возвращает 503 на обычные запросы. При ошибке выдачи проверьте DNS и порт 80 и повторите команду `certbot certonly`. [Работа Certbot с webroot](https://eff-certbot.readthedocs.io/en/stable/using.html).

## 5. Применение и проверка

```bash
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_apply.sh" "$VEQTA_TARGET"
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_verify.sh" "$VEQTA_TARGET"
```

Apply проверяет доверенность checkout, схему и сертификат, устанавливает зависимости, собирает статику и применяет конфигурации systemd и Nginx. Проверка Nginx учитывает все активные include и конфликт имени сервера. При ошибке восстанавливаются файлы сайта Nginx. Схема, зависимости, статика и unit меняются на месте. [Приёмка и действия при сбое](operations.md).

Продление для нового webroot-сертификата:

```bash
sudo install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo install -o root -g root -m 0755 "/opt/veqta-$VEQTA_TARGET/deploy/letsencrypt/renew-nginx.sh" /etc/letsencrypt/renewal-hooks/deploy/veqta-nginx.sh
sudo systemctl list-timers --all certbot.timer
sudo bash -s -- "$VEQTA_TARGET" <<'VEQTA_RENEW'
set -Eeuo pipefail
source "/opt/veqta-$1/scripts/server_checks.sh"
veqta_target "$1"
veqta_load_config
certbot renew --dry-run --cert-name "$DOMAIN" --run-deploy-hooks
VEQTA_RENEW
```

Для ранее настроенных сертификатов используйте действующее расписание продления. Канонический HTTPS-сайт сохраняет путь ACME, а hook проверяет конфигурацию перед reload.

## 6. Администратор, данные и приёмка

Создайте первого администратора штатной командой. `createsuperuser` проверяет историю миграций и работает с настройками Migrator.

```bash
sudo bash -c '
    set -Eeuo pipefail
    source "/opt/veqta-$1/scripts/server_checks.sh"
    veqta_target "$1"
    veqta_load_config
    veqta_root_django config.migration createsuperuser
' bash "$VEQTA_TARGET"
```

Войдите через HTTPS и настройте [организации и каталог](directories.md), затем [примите кадровый справочник](import-1c.md). Проверьте вход, права аналитика, сохранение заявки, статику и HTTPS на новом контуре. Для регрессии на синтетических данных используйте [отдельный тестовый кластер](testing.md).

## 7. Обновление существующего контура и откат

До смены checkout зафиксируйте SHA, сохраните локальные правки и подготовьте проверенную копию БД по [порядку применения выпуска](operations.md#применение-выпуска). Особое внимание уделите `config/settings.py` и `workwear/migrations/0001_initial.py`. Сравните изменения с выпуском до переноса коммита.

Получите проверенный SHA в каталог оператора, как в разделе 2. Служебному пользователю GitHub-токен не нужен.

```bash
sudo bash -s -- "$VEQTA_SOURCE" "$VEQTA_REVISION" "$VEQTA_TARGET" <<'VEQTA_UPDATE'
set -Eeuo pipefail
source_repo=$1
revision=$2
target=$3
[[ "$target" == dev || "$target" == prod ]]
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
app="/opt/veqta-$target"
# До исполнения административного скрипта проверяем его и весь прямой путь.
[[ ! -L "$app" && ! -L "$app/scripts" && ! -L "$app/scripts/server_checks.sh" ]]
for part in /opt "$app" "$app/scripts" "$app/scripts/server_checks.sh"; do
    untrusted=$(find "$part" -maxdepth 0 \( ! -user root -o -perm /022 \) -print -quit)
    [[ -z "$untrusted" ]] || {
        echo "STOP: недоверенный административный скрипт или путь: $part" >&2; exit 2;
    }
done
source "$app/scripts/server_checks.sh"
veqta_target "$target"
veqta_check_trusted_code
veqta_clean_checkout
[[ "$(git -c safe.directory="$source_repo" -C "$source_repo" rev-parse HEAD)" == "$revision" ]]
# Git-объекты и checkout изменяет только доверенный root.
bundle=$(mktemp /run/veqta-source.XXXXXXXX.bundle)
trap 'rm -f -- "$bundle"' EXIT
git -c safe.directory="$source_repo" -C "$source_repo" bundle create "$bundle" HEAD
chmod 0600 "$bundle"
git -c safe.directory="$APP" -C "$APP" fetch "$bundle" HEAD
git -c safe.directory="$APP" -C "$APP" checkout --detach "$revision"
[[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" == "$revision" ]]
veqta_check_trusted_code
veqta_clean_checkout
VEQTA_UPDATE
```

Перед apply проверьте выбранный SHA и успешное завершение блока. При сбое сохраните checkout и выясните причину по [руководству эксплуатации](operations.md).

После проверки копии и согласования окна примените выбранный SHA.

```bash
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_apply.sh" "$VEQTA_TARGET" --migrate
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_verify.sh" "$VEQTA_TARGET"
```

Apply выполняет `makemigrations --check --dry-run`, `migrate --plan` и `migrate --check` под Migrator. Флаг `--migrate` разрешает применить найденные миграции. При наличии `0001_initial` обновление применяет `0002_database_acl` без переноса данных. Возврат к предыдущему SHA допустим после проверки его совместимости со схемой. [Порядок восстановления](operations.md#возврат-рабочей-бд-из-проверенной-копии).

## Настройки и права процессов

`scripts/create_config.py --environment dev|prod --hostname DNS` создаёт два файла с отдельными паролями. Шаблоны приложения находятся в `deploy/dev.env.example` и `deploy/prod.env.example`. Runtime получает только файл приложения. Служебный запуск `veqta_root_django config.migration` читает защищённый env от root и проверяет фактический сеанс PostgreSQL. `veqta_initialize` предназначена для новой пустой базы. В рабочем контуре применяйте `migrate`.

| Переменная | Назначение |
|---|---|
| `VEQTA_ENV`, `DB_NAME`, `DB_USER` | Выбранный контур, его база и роль из таблицы выше |
| `VEQTA_HOSTNAME` | Строчное DNS-имя, единое для Nginx, сертификата и проверки |
| `DB_PASSWORD` | Разный пароль для каждой роли |
| `DB_HOST`, `DB_PORT`, `DB_SSLMODE` | Адрес, порт и режим TLS PostgreSQL. По умолчанию `prefer` |
| `DJANGO_SECRET_KEY` | Независимый случайный ключ каждого контура |
| `DJANGO_DEBUG`, `DJANGO_TRUST_PROXY`, `DJANGO_SECURE_COOKIES` | Для HTTPS-сервера `0`, `1` и `1` |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | Список хостов и HTTPS-адресов, включая домен выбранного контура |
| `DJANGO_STATIC_ROOT` | Каталог статики выбранного контура |

Gunicorn слушает только локальный интерфейс. Nginx передаёт `X-Forwarded-Proto`. PROD перенаправляет HTTP на HTTPS и включает HSTS. Для каждого контура используется своё имя cookie с атрибутом `Secure`. Удалённая PostgreSQL требует соединения с `verify-full` и доверенным центром сертификации libpq. Штатная установка рассчитана на локальную PostgreSQL.

`veqta_initialize`, `workwear_import_1c` и `workwear_load_catalog` сверяют указанный контур, настройки и фактические `current_database()` и `current_user`. Для миграций `server_apply.sh --migrate` дополнительно сравнивает адрес, порт и режим TLS файлов приложения и владельца. Права PostgreSQL описаны в [архитектуре](architecture.md#инварианты-postgresql). Управление организациями и СИЗ выполняется [через Admin](directories.md).
