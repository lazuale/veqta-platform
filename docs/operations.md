# Эксплуатация и восстановление

DEV и PROD независимы по checkout, Linux-пользователю, БД, секретам, static root и unit. Общий Nginx/PostgreSQL обслуживает другие приложения; ни одно из них не удаляется и не останавливается при обслуживании VEQTA. Первичная установка — [deployment.md](deployment.md).

## Контракт этой версии

Эта линия исходников устанавливается с нуля: одна Workwear `0001_initial` создаёт окончательную схему. Она не обновляет базы прежней линии миграций. Не запускайте apply с --migrate на прежнем DEV/PROD и не подменяйте историю через --fake. Подготовьте отдельный новый сервер/кластер и выполните приёмку. Существующие базы, службы и данные сохраняются; переключение требует отдельного решения.

Повседневное изменение организаций и СИЗ выполняется в [Django Admin](directories.md), без миграций и перезапуска. Снимки существующих заявок и назначений не переписываются.

Дальнейшие обычные обновления кода относятся к окружениям, созданным именно этой начальной схемой. Перед применением фиксируют SHA, проверяют резервную копию и план миграций. Возврат всегда согласует код и базу одной версии: резервная копия прежней линии не совместима с этой схемой автоматически.

## Состояние и диагностика

```bash
sudo bash /opt/veqta-dev/scripts/server_verify.sh dev
sudo bash /opt/veqta-prod/scripts/server_verify.sh prod
sudo journalctl -u veqta-dev.service -n 80 --no-pager
sudo journalctl -u veqta-prod.service -n 80 --no-pager
sudo nginx -t
```

Verifier читает env выбранного контура, выполняет read-only проверку реальных `current_database()`/`current_user`, затем прямой Gunicorn и HTTPS на loopback с корректным SNI. Он требует HTTP 200 и Secure CSRF-cookie **ожидаемого** контура. Curl не использует `.curlrc` или proxy environment. Проверка БД ограничена 15 с (+ до 2 с kill), HTTP — 20 попытками с max-time 3 с и паузой 1 с на ресурс. Внешние DNS, права конкретного аналитика и полноценная работа UI проверяются отдельно.

Apply сериализует применение обоих контуров общей Nginx-блокировкой. При занятой блокировке дождитесь окончания другого оператора; не удаляйте lock-файл действующего процесса. Nginx reload асинхронен относительно приёмки: скрипт ждёт реальные HTTP-ответы, а не считает сам reload доказательством готовности.

## Отказ посередине установки

| Место отказа | Состояние и безопасное продолжение |
|---|---|
| Clone или права checkout | Каталог может быть неполным. Проверьте владельца, `git status` и полный SHA. Если `.git` неисправен, сохраните каталог для анализа и выполните перенос в новый каталог; не продолжайте apply вслепую. |
| Пользователь/роль/БД уже существует | Cold start намеренно останавливается. Установите, какой этап завершён; не удаляйте роль, базу или env ради повторного запуска. |
| Env создан, роль ещё не создана | Сохраните этот env и его пароль; продолжите SQL только после проверки отсутствия соответствующих роли и БД. |
| Роль создана, CREATE DATABASE не прошёл | Не повторяйте CREATE ROLE и не генерируйте новый пароль. После проверки владельца/свойств роли создайте только отсутствующую БД и выполните REVOKE/GRANT из соответствующего SQL. |
| БД есть, initialize прервался | Посмотрите таблицы и `django_migrations`. При таблицах initialize повторно не запускается; после проверки контура используйте штатный `migrate --plan`, затем `migrate --noinput`. Не используйте `--fake`. |
| pip/collectstatic | Операции идут на месте; venv/статика могут быть частично изменены. Верните согласованный SHA и повторите apply после устранения причины. При несовместимых зависимостях сначала остановите только службу выбранного приложения в согласованное окно. |
| Ошибка nginx -t или конфликт целевого hostname | До reload живой Nginx не менялся; EXIT trap возвращает только файл/ссылку сайта. Найдите прежний hostname во всей конфигурации, включая conf.d; не отключайте соседние сайты автоматически. |
| Ошибка restart, HTTP или БД после restart | Новый unit/venv/статика могли остаться. Trap восстанавливает сайт, а после отправленного reload пытается reload старой конфигурации. Ошибка этого reload сообщается явно. Проверьте состояние сервиса и полную конфигурацию прежде следующего apply. |
| Certbot не получил сертификат | Временный HTTP-сайт остаётся для challenge и возвращает 503 на обычные URL. Проверьте DNS, A/AAAA, порт 80 и повторите только выпуск сертификата. |

Проверка административного состояния локального PostgreSQL не меняет данные:

```bash
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -U postgres -d postgres -c "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname IN ('veqta_dev','veqta_prod');"
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -U postgres -d postgres -c "SELECT datname, pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname IN ('veqta_dev','veqta_prod');"
```

При частичном DEV/PROD cold start выполняйте только оставшиеся строки `deploy/postgresql/<контур>.sql` после сравнения с этим состоянием. Для схемы команды выполняются под пользователем нужного приложения с загруженным `/etc/veqta/<контур>.env`; способ показан в deployment.md. Пример проверки схемы:

```bash
sudo -u veqta-prod bash -c '
    set -Eeuo pipefail
    set -a; source /etc/veqta/prod.env; set +a
    cd /opt/veqta-prod
    .venv/bin/python manage.py migrate --plan
'
```

Это пример PROD; для DEV одновременно замените все `prod` на `dev`. Apply не является транзакцией релиза: схема, зависимости и unit не откатываются автоматически. Полный откат требует проверенной совместимости либо восстановления БД и соответствующего SHA.

## Резервная копия и пробное восстановление

До миграций PROD нужна проверенная точка восстановления. Копии и env хранятся вне Git с ограниченным доступом. Следующий сеанс root обслуживает **один выбранный контур**; не меняйте target посередине:

```bash
sudo -i
set -Eeuo pipefail
umask 077
VEQTA_TARGET=prod
# Для DEV до загрузки конфигурации выберите dev.
[[ "$VEQTA_TARGET" == dev || "$VEQTA_TARGET" == prod ]]
source "/opt/veqta-$VEQTA_TARGET/scripts/server_checks.sh"
veqta_target "$VEQTA_TARGET"
veqta_load_config
veqta_clean_checkout
veqta_check_database
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="/var/backups/veqta/$VEQTA_TARGET"
install -d -o root -g root -m 0700 "$backup_dir"
backup="$backup_dir/${DB_NAME}_$stamp.dump"
[[ ! -e "$backup" ]]
PGPASSWORD="$DB_PASSWORD" /usr/lib/postgresql/18/bin/pg_dump --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" --dbname="$DB_NAME" --format=custom --file="$backup"
runuser -u "$ACTOR" -- git -C "$APP" rev-parse HEAD > "$backup.sha"
/usr/lib/postgresql/18/bin/pg_restore --list "$backup" >/dev/null
```

Не включайте shell xtrace: пароль передаётся в окружении процесса, а не в argv или SQL-текст. `pg_dump` получает согласованный снимок БД, но для точки возврата релиза согласуйте окно и зафиксируйте также неизменность кода/env. Env с ключом Django и пароль роли сохраните отдельно от публичного кода с теми же ограничениями доступа.

Продолжите **в том же root-сеансе**. Пробное восстановление создаёт новую БД, рабочая БД не меняется:

```bash
restore_db="veqta_${VEQTA_TARGET}_restore_check_$stamp"
runuser -u postgres -- /usr/lib/postgresql/18/bin/createdb -h /var/run/postgresql -p 5432 -U postgres --owner="$DB_USER" --template=template0 "$restore_db"
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432 -U postgres -d postgres -v restore_db="$restore_db" <<'VEQTA_REVOKE'
REVOKE ALL ON DATABASE :"restore_db" FROM PUBLIC;
VEQTA_REVOKE
PGPASSWORD="$DB_PASSWORD" /usr/lib/postgresql/18/bin/pg_restore --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" --dbname="$restore_db" --no-owner --no-privileges --exit-on-error "$backup"
PGPASSWORD="$DB_PASSWORD" psql -X -v ON_ERROR_STOP=1 --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" --dbname="$restore_db" <<'VEQTA_RESTORE_CHECK'
SELECT count(*) AS migrations FROM django_migrations;
SELECT count(*) AS employees FROM workwear_employee;
SELECT count(*) AS requests FROM workwear_request;
SELECT count(*) AS events FROM workwear_workwearevent;
SELECT count(*) AS workwear_triggers FROM pg_trigger WHERE tgname LIKE 'ww_%';
VEQTA_RESTORE_CHECK
```

Сверьте счётчики с ожидаемым снимком; один существующий `.dump` недостаточен. Схема Cold Start содержит 13 `ww_`-триггеров; при изменении схемы сравнивайте с соответствующим коммитом, а не фиксированной цифрой. Guard приложения не разрешает запускать обычный VEQTA на произвольно названной БД: для полной прикладной проверки восстановите копию в отдельный изолированный кластер с каноническим именем базы. Не ослабляйте `require_target` ради проверки.

Если проверка неуспешна, сохраните исходную копию/журнал и устраните причину; не переключайте рабочее приложение на непроверенное восстановление. Удаление тестовой копии — только после проверки её точного имени, никогда рабочей БД.

## Возврат рабочей БД из проверенной копии

Это ручное восстановление в согласованное окно после успешной проверки выше, а не часть обычного apply. Остановится только выбранная служба VEQTA. PostgreSQL и Nginx продолжают работать. Предыдущая рабочая база **сохраняется** под другим именем, не удаляется.

В том же root-сеансе проверьте `VEQTA_TARGET`, `DB_NAME`, `DB_USER`, `restore_db`, путь `backup` и SHA из `backup.sha`. Для более ранней копии сначала восстановите её в новую проверочную БД по предыдущему разделу и задайте соответствующие переменные. Не используйте пример как автоматический откат произвольной БД.

```bash
[[ "$DB_NAME" == "veqta_$VEQTA_TARGET" && "$DB_USER" == "$DB_NAME" ]]
[[ "$restore_db" == "veqta_${VEQTA_TARGET}_restore_check_"* ]]
restore_revision=$(cat "$backup.sha")
[[ "$restore_revision" =~ ^[0-9a-f]{40}$ ]]
runuser -u "$ACTOR" -- git -C "$APP" cat-file -e "${restore_revision}^{commit}"
veqta_clean_checkout
retained_db="veqta_${VEQTA_TARGET}_before_restore_$(date -u +%Y%m%dT%H%M%SZ)"
systemctl stop "$SERVICE"
runuser -u postgres -- psql -X -v ON_ERROR_STOP=1 -h /var/run/postgresql -p 5432 -U postgres -d postgres -v working="$DB_NAME" -v restored="$restore_db" -v retained="$retained_db" <<'VEQTA_SWITCH'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname IN (:'working', :'restored') AND pid <> pg_backend_pid();
ALTER DATABASE :"working" RENAME TO :"retained";
ALTER DATABASE :"restored" RENAME TO :"working";
VEQTA_SWITCH
runuser -u "$ACTOR" -- git -C "$APP" checkout --detach "$restore_revision"
bash "$APP/scripts/server_apply.sh" "$VEQTA_TARGET"
bash "$APP/scripts/server_verify.sh" "$VEQTA_TARGET"
exit
```

Во время переключения другие операторы не должны менять выбранные БД или запускать службу. Если второе переименование не прошло, оставьте службу остановленной, проверьте имена в `pg_database` и верните сохранённой базе каноническое имя; не удаляйте ни одну из копий. Если оба переименования прошли, а старый код не стартует, данные всё ещё сохранены в обеих базах: проверьте совместимость SHA/схемы и используйте тот же ручной порядок для возврата. Не применяйте новые миграции автоматически к восстановленной базе.

## Пользователи, сертификаты и обновления

Пользователи, блокировки, группы и разрешения управляются штатным Django Admin. Изменение прав не требует своих таблиц авторизации. Кадровые CSV, env, backup и результаты импортов в Git не попадают. Общие ключи/пароли между контурами не используются.

Обновление из проверенного SHA, порядок применения миграций и webroot renewal описаны в deployment.md. После обновления отдельно проверьте read-доступ аналитика ко всем организациям, запрет включения сотрудника другой организации, конфликт версий и неизменность завершённой заявки. После смены кода одного verifier недостаточно для всей прикладной приёмки.
