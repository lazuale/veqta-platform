# Подготовка сервера с нуля

Эта инструкция относится к Cold Start на новом отдельном окружении. Workwear устанавливается одной начальной миграцией в пустую БД. Если на сервере уже работает VEQTA прежней версии, не используйте его базы и занятые каталоги для этих команд. Подготовьте отдельный сервер или изолированный кластер; действующие DEV и PROD не очищаются и не переустанавливаются. Переключение выполняется отдельно после приёмки.

Для работы серверного приложения используются Ubuntu 26.04.1 LTS, Python 3.14, PostgreSQL 18, Nginx и systemd. Проверенная базовая версия: Python 3.14.4 / PostgreSQL 18.7 / Django 6.1.3 / Gunicorn 27.0.0. Python-пакеты закреплены в `requirements.lock`.

Развёртывание состоит из этой подготовки и [полной инструкции DEV/PROD](deployment.md). Старые архивы, существующий DEV, чужой компьютер и история переписки не требуются.

## A. Чистая Ubuntu 26.04

Следующие команды относятся **только к новому серверу**. Исполнитель — обычный оператор с sudo. Нужны исходящий HTTPS к GitHub/PyPI и два разных публичных DNS-имени DEV и PROD, направленных на сервер; они задаются при генерации конфигурации. Для webroot ACME входящие TCP 80/443 должны доходить до Nginx; если опубликована AAAA-запись, IPv6 должен быть настроен. Примеры Nginx слушают IPv4: удалите неподдерживаемую AAAA либо отдельно настройте IPv6-listen.

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends ca-certificates git curl openssl nginx python3.14 python3.14-venv postgresql-18 postgresql-client-18 util-linux coreutils mawk certbot
sudo systemctl enable --now postgresql nginx
sudo systemctl enable --now certbot.timer
```

Не заменяйте `/usr/bin/python3` и не используйте системный pip для пакетов приложения. Venv создаётся отдельно для каждого контура от root в root-owned checkout с доступом чтения и исполнения для рабочего UID. Старые каталоги с владельцем `veqta-dev`/`veqta-prod` нельзя просто применить новым `server_apply.sh`: он откажет; требуется отдельная контролируемая процедура переноса без изменения действующей БД. Не отключайте firewall целиком; разрешите 80/443 в действующей политике сети. PostgreSQL 5432 и Gunicorn 8765/8767 наружу не публикуются.

Проверьте фактические версии и локальный кластер:

```bash
python3.14 --version
python3.14 -m venv --help >/dev/null
psql --version
nginx -v
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -U postgres -d postgres -c 'SHOW server_version;'
sudo -u postgres psql -X -h /var/run/postgresql -p 5432 -U postgres -d postgres -c 'SHOW listen_addresses;'
command -v git curl openssl flock runuser timeout awk
sudo nginx -t
sudo systemctl is-active postgresql nginx
```

Нужны сервер и клиент PostgreSQL 18, Python 3.14 с venv и доступная PostgreSQL на loopback:5432. Если пакетный источник даёт PostgreSQL ниже 18.7, для **нового** сервера доступен официальный PGDG-репозиторий:

```bash
sudo apt-get install --no-install-recommends postgresql-common
sudo /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh
sudo apt-get update
sudo apt-get install postgresql-18 postgresql-client-18
```

Не запускайте эту замену источника пакетов на общем сервере ради VEQTA. При иной актуальной patch-версии Python/PostgreSQL зафиксируйте её и выполните приёмку; результаты базовой версии не подтверждают другую сборку автоматически. `.python-version` описывает базовую версию для инструментов разработки, но не устанавливает интерпретатор.

Убедитесь, что локальный административный вход `sudo -u postgres psql` разрешён политикой сервера. Приложения будут подключаться к `127.0.0.1:5432` под собственными ролями с паролями. На новой штатной Ubuntu кластер создаётся пакетами. Если кластер не запущен, изучите `pg_lsclusters` и журнал `postgresql@18-main`; не создавайте второй кластер на занятом порту и не удаляйте существующие данные.

Далее выполняйте [deployment.md](deployment.md): Git → каждый новый контур → сертификаты → apply/verify → администратор → приёмка.

## B. PostgreSQL, Nginx, Python и сертификаты уже подготовлены

Пакетную установку из раздела A **пропустите**. Проверьте версии, путь к Python с venv, локальный административный доступ PostgreSQL и доступность loopback:5432 командами выше. PostgreSQL и Nginx могут обслуживать другие системы: не переустанавливать, не останавливать, не очищать их и не переписывать глобальный `pg_hba.conf`/`nginx.conf`.

Если локальный HBA отличается от штатного, администратор должен разрешить только нужные соединения `veqta_dev → veqta_dev`, `veqta_prod → veqta_prod` с loopback и роли `veqta_dev_owner`, `veqta_prod_owner` к соответствующим базам. Для владельца DEV дополнительно нужны подключения к `postgres` и одноразовой `test_veqta_dev`. Не добавляйте общие `trust`/`host all all` ради приложения. Подключение и изоляцию подтвердите приёмкой.

Проверьте готовые сертификаты каждого домена:

```bash
read -r -p 'DNS-имя проверяемого контура: ' VEQTA_HOSTNAME
sudo openssl x509 -in "/etc/letsencrypt/live/$VEQTA_HOSTNAME/fullchain.pem" -checkend 0 -checkhost "$VEQTA_HOSTNAME" -noout
sudo test -r "/etc/letsencrypt/live/$VEQTA_HOSTNAME/privkey.pem"
# Повторите для другого имени DEV/PROD.
```

Далее [deployment.md](deployment.md) создаёт **и DEV, и PROD независимо**. При готовых сертификатах первичный HTTP/ACME-этап пропускается. Продление существующих сертификатов должно использовать уже выбранный администратором механизм; его не заменяют вслепую.

## Локальная разработка

Используйте отдельную PostgreSQL и локальный env с `VEQTA_ENV=dev`, новым ключом, localhost, `DJANGO_DEBUG=1`, `DJANGO_TRUST_PROXY=0`, `DJANGO_SECURE_COOKIES=0`. Серверный `/etc/veqta/dev.env` не изменяется для локального HTTP. `runserver` не предназначен для постоянной эксплуатации. Проверки и временная тестовая БД описаны в [testing.md](testing.md).

## Первое заполнение

После миграций создайте администратора штатной `createsuperuser` в нужном контуре, откройте `/admin/` и заполните организации и каталог по [directories.md](directories.md). Новая база не содержит готовых организаций, позиций СИЗ или пользователей. Загрузите кадровые CSV по [import_1c.md](import_1c.md); проверьте связь по `orgExtId` и разрешение новых заявок.

## Первичные источники

- [Ubuntu 26.04: серверные изменения, PostgreSQL 18](https://documentation.ubuntu.com/release-notes/26.04/changes-since-previous-interim/)
- [PostgreSQL: Ubuntu и официальный PGDG](https://www.postgresql.org/download/linux/ubuntu/)
- [Certbot: webroot, renewal и hooks](https://eff-certbot.readthedocs.io/en/stable/using.html)
