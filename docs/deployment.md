# Развёртывание VEQTA на сервере pg-analytics

## Стандарт среды

Один Git-репозиторий, два независимых checkout. ОС Ubuntu 26.04.1; на этом сервере проверены Python 3.14.4, Django 6.1.3 и PostgreSQL 18.7. PostgreSQL и Nginx общие с другими системами: **их не переустанавливать, не останавливать и не очищать**.

| | DEV | PROD |
|---|---|---|
| HTTPS | `test.platform.veqta.ru` | `platform.veqta.ru` |
| Исходники | `/opt/veqta-dev` | `/opt/veqta-prod` |
| Пользователь | `veqta-dev` | `veqta-prod` |
| Окружение | `/etc/veqta/dev.env` | `/etc/veqta/prod.env` |
| База и роль | `veqta_dev` | `veqta_prod` |
| Gunicorn (loopback) | `127.0.0.1:8767` | `127.0.0.1:8765` |
| Статика | `/var/www/veqta/dev-static` | `/var/www/veqta/prod-static` |
| Systemd | `veqta-dev.service` | `veqta-prod.service` |

Сертификаты Let's Encrypt находятся вне Git. Файлы в `deploy/systemd/` и `deploy/nginx/` — канонический источник конфигурации. Команды запуска и проверки — `scripts/server_apply.sh`, `scripts/server_verify.sh`. Не хранить пароли, выгрузки 1С, дампы или `.env.*` в Git.

## Подготовка исходников

Исходники из GitHub `lazuale/veqta-platform` получает **оператор с правом доступа к репозиторию**. Системные пользователи не имеют GitHub-токенов и не работают с приватными ключами оператора. Скопируйте или клонируйте проверенный коммит в `/opt/veqta-dev` либо `/opt/veqta-prod`, назначьте владельца соответствующему системному пользователю и убедитесь в `git status --porcelain` (пустой вывод).

На существующем сервере проверенный исходный checkout находится в `/home/analitik/veqta-platform`. Для нового PROD (только если `/opt/veqta-prod` ещё отсутствует):

```bash
sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin veqta-prod
sudo install -d -o veqta-prod -g veqta-prod -m 0750 /opt/veqta-prod
sudo git -c safe.directory=/home/analitik/veqta-platform clone --local --no-hardlinks --branch main /home/analitik/veqta-platform /opt/veqta-prod
sudo chown -R veqta-prod:veqta-prod /opt/veqta-prod
sudo -u veqta-prod git -C /opt/veqta-prod remote set-url origin https://github.com/lazuale/veqta-platform.git
```

Сверьте полный SHA коммита с `main` в GitHub. Никакой `git pull` из службы и автоматического обновления из незакреплённой ветки нет. DEV уже размещён в `/opt/veqta-dev`; повторно создавать его не нужно.

## Настройки

Для каждого контура конфигурацию создают **один раз**, отдельно. Системная конфигурация хранится в `/etc/veqta/<контур>.env`, владелец `root`, группа соответствующего приложения, права `0640`. Для PROD:

```bash
sudo -u veqta-prod python3.14 /opt/veqta-prod/scripts/create_config.py --environment prod --hostname platform.veqta.ru
sudo install -d -o root -g root -m 0755 /etc/veqta
sudo install -o root -g veqta-prod -m 0640 /opt/veqta-prod/.env.prod /etc/veqta/prod.env
sudo rm /opt/veqta-prod/.env.prod
```

Существующий `/etc/veqta/dev.env` **не перезаписывать**: в нём уже находятся секреты действующего DEV. Для HTTPS DEV должны быть `DJANGO_DEBUG=0`, `DJANGO_TRUST_PROXY=1`, `DJANGO_SECURE_COOKIES=1`, `DJANGO_ALLOWED_HOSTS=test.platform.veqta.ru,localhost,127.0.0.1`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://test.platform.veqta.ru`, `DJANGO_STATIC_ROOT=/var/www/veqta/dev-static`. Добавьте отсутствующий `DJANGO_SECURE_COOKIES=1` в действующий файл ровно один раз с правами root. Серверный DEV и PROD используют `Secure` для session/CSRF cookies.

## Создание новой базы PROD

Только когда `veqta_prod` (база **и** роль) ещё не существуют. Выполнять от root после загрузки **только** PROD-настроек; `psql` читает SQL из stdin, чтобы не зависеть от прав пользователя postgres на `/opt`:

```bash
sudo -i
cd /opt/veqta-prod
set -a; source /etc/veqta/prod.env; set +a
sudo --preserve-env=DB_PASSWORD -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres < deploy/postgresql/prod.sql
sudo -u veqta-prod python3.14 -m venv /opt/veqta-prod/.venv
sudo -u veqta-prod /opt/veqta-prod/.venv/bin/python -m pip install --no-cache-dir --require-hashes -r /opt/veqta-prod/requirements.lock
sudo -u veqta-prod bash -c 'cd /opt/veqta-prod; set -a; source /etc/veqta/prod.env; set +a; .venv/bin/python manage.py veqta_initialize --environment prod --database-name veqta_prod'
exit
```

**Не повторять cold start для базы с таблицами.** Для обновлений используется штатный `migrate` при явно выбранном контуре. Если SQL или инициализация остановились, сначала изучите фактическое состояние; не удаляйте существующую базу для повторной попытки.

## Применение и проверка

Когда checkout, БД, пароль, сертификат и `.env` готовы:

```bash
sudo bash /opt/veqta-dev/scripts/server_apply.sh dev
sudo bash /opt/veqta-dev/scripts/server_verify.sh dev
# PROD — отдельно, только после готовности PROD-базы:
sudo bash /opt/veqta-prod/scripts/server_apply.sh prod
sudo bash /opt/veqta-prod/scripts/server_verify.sh prod
```

`server_apply.sh` **не создаёт БД, не удаляет данные, не скачивает код, не изменяет соседний контур**. Применение миграций разрешено только с явным аргументом `--migrate`; для PROD предварительно должна быть проверенная точка восстановления, поскольку SQL-миграции могут быть необратимыми. Скрипт обновляет зависимости из lockfile, собирает статику, ставит unit и сайт Nginx и проверяет прямой HTTP и HTTPS. Перезапуск службы может кратковременно вернуть 502; готовность проверяется повторными попытками.

Пользователя создают один раз штатной командой `createsuperuser` внутри конкретного контура. Данные не включены в Git; пустой каталог спецодежды и отсутствие кадровых выгрузок после cold start — ожидаемое состояние.

## Обновление, возвращение предыдущей версии

Оператор получает обновление на `/home/analitik/veqta-platform` (или другой доверенный staging) и фиксирует проверенный SHA. **Никогда не обновляйте PROD напрямую при наличии несогласованных изменений в DEV.** Каждый checkout переключается на выбранный коммит отдельно; `server_apply.sh` проверяет чистое дерево, но **не делает git fetch/checkout**. После переключения выполняется `server_apply.sh` и `server_verify.sh` для нужного контура.

Перед изменением рабочей схемы PROD сделайте проверяемую копию БД вне Git. Для отката кода переключите checkout на ранее развернутый SHA и повторно примените конфигурации. **Откат схемы PostgreSQL автоматически не выполняется**; миграции требуют отдельной процедуры восстановления. Не используйте `migrate --fake`, `flush` или `DROP DATABASE` как способ отката релиза.

## Граница проверок

Ранее на исходной версии `a788fea` пройдены 64 теста Django, конкурентный сценарий и браузерный Chromium на серверном стеке. Этот факт **не является** подтверждением новой версии инфраструктурных файлов до применения и повторной проверки. После изменения DEV следует повторить тесты из `docs/testing.md`, затем выполнить серверную приёмку PROD (авторизация, Workwear, права, CSRF, cookies, статика и HTTPS).
