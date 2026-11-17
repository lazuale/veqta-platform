# Установка с нуля

## Требования

Целевая ОС — Linux; на Windows используйте WSL. PROD работает как служба Linux. Проверенный интерпретатор указан в `.python-version`: Python 3.12.14. Django 6.1.3 поддерживает также Python 3.13 и 3.14, но проверки этой поставки выполнены на Python 3.12.14. Версии библиотек и SHA-256 зафиксированы в `requirements.lock`; SQLite не используется. Схема проверена на PostgreSQL 18.6.

На Ubuntu с доступными пакетами PostgreSQL 18:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv postgresql-18 postgresql-client-18
sudo systemctl enable --now postgresql
python3 --version
psql --version
```

Если дистрибутив не предоставляет PostgreSQL 18, подключите [официальный репозиторий PostgreSQL](https://www.postgresql.org/download/linux/ubuntu/) для своего выпуска ОС. Другую основную версию сервера эта инструкция не предполагает.

Подключения к PostgreSQL на `127.0.0.1:5432` должны проходить с паролем. В `pg_hba.conf` перед более общими правилами достаточно следующих строк; они не предоставляют одной роли доступ к другому контуру:

```text
host veqta_dev veqta_dev 127.0.0.1/32 scram-sha-256
host test_veqta_dev veqta_dev 127.0.0.1/32 scram-sha-256
host veqta_prod veqta_prod 127.0.0.1/32 scram-sha-256
```

После изменения файла администратор перечитывает конфигурацию PostgreSQL. Не заменяйте проверку пароля на trust.

## DEV

Распакуйте архив, перейдите в `veqta-platform` и выполните:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip check
python scripts/create_config.py --environment dev
set -a
. ./.env.dev
set +a
sudo --preserve-env=DB_PASSWORD -u postgres psql -X -v ON_ERROR_STOP=1 -f deploy/postgresql/dev.sql
python manage.py veqta_initialize --environment dev --database-name veqta_dev
python manage.py makemigrations --check --dry-run
python manage.py createsuperuser
python manage.py runserver 127.0.0.1:8000
```

Администратор создаётся интерактивно: придумайте свой логин и пароль. Готовой учётной записи в проекте нет. Откройте http://127.0.0.1:8000/ и войдите. У администратора доступен пустой Workwear.

`create_config.py` создаёт `.env.dev` с правами 0600 и случайными секретами; существующий файл не перезаписывает. Команда SQL предполагает новую роль и новую БД; повторный запуск останавливается на ошибке и ничего не удаляет. Если sudo запрещает сохранение переменной окружения, администратор может выполнить тот же SQL из psql с локально заданной `DB_PASSWORD`; пароль не следует вставлять в репозиторий или историю команд.

В новой оболочке снова активируйте venv и загрузите `.env.dev` через `set -a`. Не запускайте одновременно команды из разных контуров в одной оболочке.

## PROD

Нужны отдельный домен, доступный только администраторам сервер и действующий сертификат доверенного центра. Домен в примерах — заглушка. Установка выполняется на новом узле; существующий сервер инструкция не изменяет.

Подготовьте системного пользователя и положите исходники в `/opt/veqta-platform`:

```bash
sudo apt-get install -y nginx
sudo useradd --system --home /opt/veqta-platform --shell /usr/sbin/nologin veqta
sudo install -d -o veqta -g veqta -m 0750 /opt/veqta-platform
sudo cp -a ./veqta-platform/. /opt/veqta-platform/
sudo chown -R veqta:veqta /opt/veqta-platform
sudo -u veqta python3 -m venv /opt/veqta-platform/.venv
sudo -u veqta /opt/veqta-platform/.venv/bin/python -m pip install --require-hashes -r /opt/veqta-platform/requirements.lock
```

Эти команды копирования выполняются из каталога, где лежит распакованная папка `veqta-platform`. Создайте настройки под своим доменом:

```bash
cd /opt/veqta-platform
sudo -u veqta .venv/bin/python scripts/create_config.py --environment prod --hostname portal.example.org
sudo install -d -o root -g veqta -m 0750 /etc/veqta
sudo install -o root -g veqta -m 0640 .env.prod /etc/veqta/prod.env
sudo rm .env.prod
sudo install -d -o veqta -g www-data -m 0755 /var/www/veqta/static
```

Следующий блок выполняйте в административной оболочке на новом узле. Нужен доступ к файлу настроек, PostgreSQL и запуску от имени `veqta`:

```bash
sudo -i
cd /opt/veqta-platform
set -a
. /etc/veqta/prod.env
set +a
sudo --preserve-env=DB_PASSWORD -u postgres psql -X -v ON_ERROR_STOP=1 -f deploy/postgresql/prod.sql
sudo --preserve-env=VEQTA_ENV,DJANGO_SECRET_KEY,DJANGO_DEBUG,DJANGO_ALLOWED_HOSTS,DJANGO_TRUST_PROXY,DJANGO_CSRF_TRUSTED_ORIGINS,DJANGO_STATIC_ROOT,DB_NAME,DB_USER,DB_PASSWORD,DB_HOST,DB_PORT,DB_SSLMODE -u veqta .venv/bin/python manage.py veqta_initialize --environment prod --database-name veqta_prod
sudo --preserve-env=VEQTA_ENV,DJANGO_SECRET_KEY,DJANGO_DEBUG,DJANGO_ALLOWED_HOSTS,DJANGO_TRUST_PROXY,DJANGO_CSRF_TRUSTED_ORIGINS,DJANGO_STATIC_ROOT,DB_NAME,DB_USER,DB_PASSWORD,DB_HOST,DB_PORT,DB_SSLMODE -u veqta .venv/bin/python manage.py collectstatic --noinput
sudo --preserve-env=VEQTA_ENV,DJANGO_SECRET_KEY,DJANGO_DEBUG,DJANGO_ALLOWED_HOSTS,DJANGO_TRUST_PROXY,DJANGO_CSRF_TRUSTED_ORIGINS,DJANGO_STATIC_ROOT,DB_NAME,DB_USER,DB_PASSWORD,DB_HOST,DB_PORT,DB_SSLMODE -u veqta .venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py check --deploy --fail-level WARNING
.venv/bin/gunicorn --check-config config.wsgi:application
exit
```

В `.env` нет сложного синтаксиса оболочки: генератор использует только безопасные для shell значения. Если добавляете свои значения с пробелами, согласуйте формат с `EnvironmentFile` systemd и shell.

Установите сертификат обычным для организации способом. В `deploy/nginx.conf` замените все вхождения `portal.example.org` и пути сертификата. Затем:

```bash
sudo install -m 0644 deploy/veqta.service /etc/systemd/system/veqta.service
sudo install -m 0644 deploy/nginx.conf /etc/nginx/sites-available/veqta
sudo ln -s /etc/nginx/sites-available/veqta /etc/nginx/sites-enabled/veqta
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now veqta
sudo systemctl reload nginx
sudo systemctl status veqta --no-pager
curl -I https://portal.example.org/login/
```

Gunicorn слушает только `127.0.0.1:8765`. Доступ к этому порту извне закрыт. Nginx выставляет собственный `X-Forwarded-Proto`; значение от клиента не передаётся приложению. DEV размещают на другом имени хоста, чтобы HSTS PROD не затронул локальную разработку.

## Первые данные

Миграции создают таблицы, ограничения, триггеры и права, но не кадровые данные. После запуска в обеих базах — ноль сотрудников, принятых импортов, заявок, назначений и событий. Утверждённый каталог из 11 позиций загрузите по [инструкции импорта](import_1c.md), затем примите три справочника 1С. Пока каталог или справочник отсутствует, интерфейс остаётся доступен, но полноценная заявка на выдачу не формируется.
