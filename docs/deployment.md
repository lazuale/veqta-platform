# Развёртывание DEV и PROD из GitHub

Инструкция рассчитана на новый сервер без объектов VEQTA либо на подготовленный общий сервер. Сначала выполните подходящую ветку [installation.md](installation.md). При ошибке любого блока остановитесь и используйте [operations.md](operations.md); не повторяйте создание существующей БД и не удаляйте данные ради повтора.

## 1. Контуры и предпосылки

| Параметр | DEV | PROD |
|---|---|---|
| Домен | `test.platform.veqta.ru` | `platform.veqta.ru` |
| Checkout | `/opt/veqta-dev` | `/opt/veqta-prod` |
| Linux-пользователь / группа | `veqta-dev` | `veqta-prod` |
| Env, root:группа 0640 | `/etc/veqta/dev.env` | `/etc/veqta/prod.env` |
| База / роль PostgreSQL | `veqta_dev` | `veqta_prod` |
| Gunicorn | `127.0.0.1:8767` | `127.0.0.1:8765` |
| Статика | `/var/www/veqta/dev-static` | `/var/www/veqta/prod-static` |
| Systemd | `veqta-dev.service` | `veqta-prod.service` |

Команды предполагают PostgreSQL 18 на локальном сокете `/var/run/postgresql`, порту 5432 и административную роль `postgres`. До создания объектов проверьте, что это выбранный кластер. Общие PostgreSQL/Nginx не останавливаются. Каталоги, роли, БД и hostnames не должны быть заняты другой системой. Работающие контуры обновляются по разделу 7, а не повторным cold start.

## 2. Получение и фиксация исходников — оператор без sudo

Публичный репозиторий не требует GitHub-токена. Используется новый staging в домашнем каталоге оператора; источник не зависит от имени пользователя и старого checkout:

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

Пустой status и совпадение полного SHA — обязательны. При будущем переводе репозитория в private оператор использует свой разрешённый SSH/credential-helper доступ при clone. Не вставляйте token в URL или команды и не переносите GitHub-секреты сервисным пользователям. Локальный перенос ниже не требует от них сетевой авторизации.

## 3. Создание одного нового контура

Сначала задайте `dev` и выполните блок. Затем задайте `prod` и выполните **тот же блок полностью**. Никакого заранее существующего DEV не требуется. Переменные `VEQTA_SOURCE` и `VEQTA_REVISION` остаются в той же операторской оболочке после раздела 2.

```bash
VEQTA_TARGET=dev
# Для второго независимого запуска: VEQTA_TARGET=prod
sudo bash -s -- "$VEQTA_SOURCE" "$VEQTA_REVISION" "$VEQTA_TARGET" <<'VEQTA_BOOTSTRAP'
set -Eeuo pipefail
umask 027
source_repo=$1
revision=$2
target=$3
[[ "$target" == dev || "$target" == prod ]]
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
actor="veqta-${target}"
app="/opt/veqta-${target}"
config="/etc/veqta/${target}.env"
database="veqta_${target}"
domain=platform.veqta.ru
[[ "$target" != dev ]] || domain=test.platform.veqta.ru
[[ ! -e "$app" && ! -L "$app" && ! -e "$config" && ! -L "$config" ]]
if id "$actor" >/dev/null 2>&1; then
    echo 'STOP: пользователь уже существует; проверьте частичную установку по operations.md' >&2
    exit 1
fi
existing=$(runuser -u postgres -- psql -X -At -h /var/run/postgresql -p 5432 -U postgres -d postgres -v target="$database" <<'VEQTA_SQL'
SELECT EXISTS(SELECT FROM pg_database WHERE datname = :'target')
    OR EXISTS(SELECT FROM pg_roles WHERE rolname = :'target');
VEQTA_SQL
)
[[ "$existing" == f ]] || { echo 'STOP: роль или база уже существует'; exit 1; }
useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$actor"
install -d -o "$actor" -g "$actor" -m 0750 "$app"
git -c safe.directory="$source_repo" clone --no-local --no-checkout "$source_repo" "$app"
chown -R "$actor:$actor" "$app"
runuser -u "$actor" -- git -C "$app" remote set-url origin https://github.com/lazuale/veqta-platform.git
runuser -u "$actor" -- git -C "$app" checkout --detach "$revision"
[[ "$(runuser -u "$actor" -- git -C "$app" rev-parse HEAD)" == "$revision" ]]
status=$(runuser -u "$actor" -- git -C "$app" status --porcelain)
[[ -z "$status" ]]
runuser -u "$actor" -- python3.14 "$app/scripts/create_config.py" --environment "$target" --hostname "$domain"
install -d -o root -g root -m 0755 /etc/veqta
install -o root -g "$actor" -m 0640 "$app/.env.$target" "$config"
rm -- "$app/.env.$target"
set -a; source "$config"; set +a
# Пароль передаётся через окружение, SQL читает root-перенаправление stdin.
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432 -U postgres -d postgres < "$app/deploy/postgresql/$target.sql"
runuser -u "$actor" -- python3.14 -m venv "$app/.venv"
runuser -u "$actor" -- "$app/.venv/bin/python" -m pip install --no-cache-dir --require-hashes -r "$app/requirements.lock"
runuser -u "$actor" -- "$app/.venv/bin/python" -m pip check
runuser -u "$actor" -- bash -c '
    set -Eeuo pipefail
    set -a; source "$1"; set +a
    cd "$2"
    exec .venv/bin/python manage.py veqta_initialize --environment "$3" --database-name "veqta_$3"
' bash "$config" "$app" "$target"
VEQTA_BOOTSTRAP
```

Генератор создаёт независимые ключи и пароли, не печатает их, не перезаписывает существующий файл. В обоих серверных контурах DEBUG=0, proxy=1 и Secure cookies=1. При частичном отказе не генерируйте новый пароль поверх уже созданной роли: порядок продолжения — в operations.md. SQL рассчитан на один запуск, initialize — только на пустую БД. Наличие shell `nologin` не мешает явному `runuser ... bash -c` для административных команд.

## 4. Первичный HTTPS без остановки общего Nginx

Если сертификаты обоих доменов уже прошли проверки из installation.md, **пропустите выпуск** и перейдите к разделу 5. Для нового сертификата ниже используется webroot; `certbot --nginx` не переписывает канонические конфигурации приложения.

DNS выбранного домена должен вести на сервер, HTTP/80 быть доступен извне. Перед началом просмотрите активные `server_name` через `sudo nginx -T` и убедитесь, что выбранное имя не обслуживает другую систему. Вывод глобальной конфигурации не коммитьте и не публикуйте.

В той же операторской оболочке задайте `VEQTA_TARGET=dev` либо `prod` и действующий контактный e-mail для ACME. Выполните для каждого нового сертификата:

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
[[ ! -e "/etc/letsencrypt/live/$DOMAIN" && ! -L "/etc/letsencrypt/live/$DOMAIN" ]]
[[ ! -e "$NGINX_SITE" && ! -L "$NGINX_SITE" && ! -e "$NGINX_LINK" && ! -L "$NGINX_LINK" ]]
install -d -o root -g root -m 0755 /var/www/veqta/acme
install -o root -g root -m 0644 "$APP/deploy/nginx/veqta-$target-http.conf" "$NGINX_SITE"
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

До apply временный HTTP-сайт намеренно возвращает 503 на обычные URL и отдаёт только ACME challenge. При сетевой ошибке Certbot он остаётся для повторной выдачи; повторяйте только команду `certbot certonly` с теми же параметрами, не весь bootstrap. При ошибке reload проверьте `nginx -t` и журнал общего Nginx, не останавливая его.

## 5. Применение и конечная проверка

Когда выбранный checkout, env, БД и сертификат готовы:

```bash
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_apply.sh" "$VEQTA_TARGET"
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_verify.sh" "$VEQTA_TARGET"
```

Выполните отдельно для dev и prod. Apply не скачивает код и не создаёт БД. Он проверяет чистоту Git с обязательным успешным status, env/сертификат, ставит зависимости, проверяет схему, собирает статику, проверяет **все активные include Nginx** через `nginx -t` и отвергает конфликт целевого server_name даже при коде 0. Соседние сайты не отключаются. Общая lock-файл блокировка не позволяет двум контурам применять Nginx одновременно.

Gunicorn и оба HTTPS-ресурса ожидаются до 20 попыток каждый: connect timeout 2 с, весь запрос 3 с, пауза 1 с. Поэтому ожидание одного ресурса ограничено примерно 80 с, а не ровно 20 с. Curl отключает `.curlrc` и proxy environment; TLS-сертификат проверяется с правильным SNI, без `-k`. Login обязан вернуть 200 и Secure CSRF-cookie именно выбранного контура; 3xx не принимается. CSS обязан вернуть 200. Финальная read-only проверка PostgreSQL подтверждает базу и роль под пользователем приложения, ограничена 15 с плюс до 2 с на принудительное завершение.

Verifier повторяет эти проверки без изменения настроек, служб и рабочих данных. Вход в браузере, роли и прикладные сценарии проверяются отдельно. При неуспехе apply EXIT trap возвращает файлы сайта Nginx; до отправленного reload он не перезагружает работающий Nginx. **Unit, venv, статика и схема автоматически не откатываются**. Состояние и дальнейшие действия описаны в operations.md.

Продление для нового webroot-сертификата:

```bash
sudo install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy
sudo install -o root -g root -m 0755 "/opt/veqta-$VEQTA_TARGET/deploy/letsencrypt/renew-nginx.sh" /etc/letsencrypt/renewal-hooks/deploy/veqta-nginx.sh
sudo systemctl list-timers --all certbot.timer
sudo certbot renew --dry-run --cert-name "$([ "$VEQTA_TARGET" = dev ] && echo test.platform.veqta.ru || echo platform.veqta.ru)" --run-deploy-hooks
```

На новой Ubuntu timer включён в installation.md. На подготовленном сервере подтвердите существующее расписание (apt timer или другой уже принятый механизм), не создавая дублирующее. Hook реагирует только на два домена VEQTA и делает `nginx -t` перед reload. Итоговые HTTP-конфигурации сохраняют ACME location, поэтому продление webroot не требует остановки Nginx. Для сертификатов, ранее полученных другим способом, сохраните их согласованный способ продления.

## 6. Администратор, данные и приёмка

Для выбранного контура создайте администратора штатной интерактивной командой; пароль не передаётся в командной строке:

```bash
sudo -u "veqta-$VEQTA_TARGET" bash -c '
    set -Eeuo pipefail
    set -a; source "/etc/veqta/$1.env"; set +a
    cd "/opt/veqta-$1"
    exec .venv/bin/python manage.py createsuperuser
' bash "$VEQTA_TARGET"
```

После миграций нет сотрудников, импортов, заявок и рабочего каталога. Утверждённый каталог СИЗ на 11 позиций и три CSV 1С загружаются отдельно по [import_1c.md](import_1c.md). Включать рабочие данные/учётные записи в Git не требуется и нельзя.

Повторите [testing.md](testing.md) из серверного DEV с синтетическими данными; браузерный временный сервер использует `config.testing`, отдельную `test_veqta_dev` и DEBUG=True только внутри этого тестового процесса. Проверки PROD не запускаются через settings.testing. Выполните вход/выход, смену пароля, права аналитика, Workwear, CSRF, Secure cookies, статику и HTTPS. Отдельно проверьте запуск обеих служб после перезагрузки на согласованном испытательном узле; это не означает разрешение перезагрузить общий действующий сервер ради установки.

## 7. Обновление существующего контура и откат

Получите выбранный проверенный SHA в операторский staging, как в разделе 2. Для каждого целевого checkout отдельно убедитесь в чистом Git и перенесите объект коммита из staging. Системному пользователю не нужен GitHub-токен:

```bash
sudo bash -s -- "$VEQTA_SOURCE" "$VEQTA_REVISION" "$VEQTA_TARGET" <<'VEQTA_UPDATE'
set -Eeuo pipefail
source_repo=$1
revision=$2
target=$3
[[ "$target" == dev || "$target" == prod ]]
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
source "/opt/veqta-$target/scripts/server_checks.sh"
veqta_target "$target"
veqta_clean_checkout
# root может читать закрытый staging оператора; изменения получает только выбранный checkout.
bundle=$(mktemp /run/veqta-source.XXXXXXXX.bundle)
trap 'rm -f -- "$bundle"' EXIT
git -c safe.directory="$source_repo" -C "$source_repo" bundle create "$bundle" HEAD
chown "$ACTOR:$ACTOR" "$bundle"
chmod 0600 "$bundle"
runuser -u "$ACTOR" -- git -C "$APP" fetch "$bundle" HEAD
runuser -u "$ACTOR" -- git -C "$APP" checkout --detach "$revision"
[[ "$(runuser -u "$ACTOR" -- git -C "$APP" rev-parse HEAD)" == "$revision" ]]
VEQTA_UPDATE
```

Staging должен оставаться detached на выбранном SHA: перед update ещё раз сверьте его `rev-parse HEAD` с `VEQTA_REVISION`. Bundle содержит историю выбранного HEAD; секреты оператора в checkout не копируются. При ошибке fetch/checkout не запускайте apply. Никогда не используйте force checkout/reset поверх несогласованных изменений.

До изменения рабочей схемы PROD создайте и восстановите пробную копию по operations.md. Затем для выбранного контура:

```bash
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_apply.sh" "$VEQTA_TARGET" --migrate
sudo bash "/opt/veqta-$VEQTA_TARGET/scripts/server_verify.sh" "$VEQTA_TARGET"
```

Если миграций нет, `--migrate` не нужен. Без него apply откажет при неприменённых миграциях. Обновление venv/статики происходит на месте, перезапуск может кратковременно прервать запросы; согласуйте окно. Автоматического отката схемы нет. Возврат к предыдущему проверенному SHA и повторный apply допустим лишь при совместимости кода с текущей схемой. `--fake`, `flush` и удаление рабочей БД не являются штатным откатом.
