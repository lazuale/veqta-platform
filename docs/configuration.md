# Настройка контуров

Приложение читает только переменные процесса. Оно не ищет конфигурацию в соседних каталогах и не переключается на другую БД при ошибке.

| Переменная | DEV | PROD |
|---|---|---|
| `VEQTA_ENV` | `dev` | `prod` |
| `DB_NAME` | `veqta_dev` | `veqta_prod` |
| `DB_USER` службы | `veqta_dev` | `veqta_prod` |
| `DB_USER` миграций | `veqta_dev_owner` | `veqta_prod_owner` |
| `VEQTA_HOSTNAME` | своё DNS-имя DEV | другое DNS-имя PROD |
| `DB_PASSWORD` | случайный пароль роли DEV | другой случайный пароль |
| `DB_HOST`, `DB_PORT` | обычно `127.0.0.1`, `5432` | локальная PostgreSQL либо явный адрес |
| `DB_SSLMODE` | `prefer` для локального сервера | `prefer` для loopback; для удалённого — `verify-full` |
| `DJANGO_SECRET_KEY` | случайная строка от 50 символов | отдельная случайная строка |
| `DJANGO_DEBUG` | серверный HTTPS: `0`; локальный HTTP: `1` | только `0` |
| `DJANGO_ALLOWED_HOSTS` | имя из `VEQTA_HOSTNAME`; `localhost` локально | имя из `VEQTA_HOSTNAME` |
| `DJANGO_TRUST_PROXY` | `1` на HTTPS-сервере; `0` локально | `1`, только за доверенным прокси |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | сервер: `https://` + `VEQTA_HOSTNAME`; локально обычно пусто | `https://` + `VEQTA_HOSTNAME` |
| `DJANGO_SECURE_COOKIES` | серверный HTTPS: `1`; локальный HTTP: `0` | `1` |
| `DJANGO_STATIC_ROOT` | `/var/www/veqta/dev-static` на сервере | `/var/www/veqta/prod-static` |

Для серверного HTTPS DEV укажите `DJANGO_DEBUG=0`, `DJANGO_TRUST_PROXY=1`, `DJANGO_SECURE_COOKIES=1`. PROD всегда требует Secure cookies. Локальные браузерные тесты используют `config.testing`: обычный режим HTTP или `VEQTA_TEST_HTTPS=1` только для изолированных Gunicorn/Nginx. Оба режима требуют DEV и используют test_veqta_dev; исходные настройки службы не меняются. Подробности: [deployment.md](deployment.md).

`create_config.py --environment dev|prod --hostname DNS` требует DNS-имя для каждого контура. Допускаются только строчные DNS-метки длиной до 63 символов; схема, порт, IP, wildcard, пустые метки и завершающая точка отвергаются. Он создаёт `.env.ENV` и `.env.ENV-migrate` с разными паролями. Первый устанавливается как `/etc/veqta/ENV.env` root:veqta-ENV 0640, второй — `/etc/veqta/ENV-migrate.env` root:root 0600.

`VEQTA_HOSTNAME` — источник домена для Nginx, сертификатов и проверки HTTP. Генератор согласованно заполняет стандартные настройки Django ALLOWED_HOSTS/CSRF. Серверные скрипты проверяют их совпадение с доменом. Домены в Nginx-шаблонах и hook Certbot отсутствуют. Фиксированные `/opt/veqta-ENV`, static paths и loopback-порты — технический контракт двух контуров, а не справочники Admin.

Переменные Django обязательны, кроме указанных значений по умолчанию. `VEQTA_HOSTNAME` обязателен для серверных скриптов; локальная разработка с явным ALLOWED_HOSTS может обходиться без него. Флаги принимают только 0 или 1. Общий ключ или общий пароль между контурами не используется. Имена cookies тоже различаются. `ALLOWED_HOSTS=*` запрещён.

Для удалённой PostgreSQL сертификат проверяется по имени сервера при `DB_SSLMODE=verify-full`. Корневой сертификат libpq можно задать стандартной переменной `PGSSLROOTCERT`; файл хранится вне репозитория. В поставке проверено локальное подключение, а не удалённый сервер БД.

`veqta_initialize`, `workwear_import_1c` и `workwear_load_catalog` требуют `--environment` и `--database-name`. Команды сверяют их с настройками, ролью подключения, `current_database()` и `current_user`. Ошибка происходит до чтения источника и записи рабочих данных. `veqta_initialize` требует роль владельца; импорт и каталог — прикладную роль. Роль другого контура и postgres не принимаются. `server_apply.sh --migrate` отдельно сверяет DB_HOST, DB_PORT и DB_SSLMODE обеих конфигураций, затем фактические имя БД и роль владельца. Checkout/venv — `root:root`, без записи группе и остальным; env приложения — `root:veqta-ENV 0640`, env миграций — `root:root 0600`. Административные команды не наследуют PYTHONPATH, PYTHONHOME и LD_PRELOAD.

Штатные команды Django `migrate`, `createsuperuser`, `collectstatic` работают с уже выбранным окружением. Перед их запуском проверьте несекретные значения:

```bash
printf '%s / %s / %s\n' "$VEQTA_ENV" "$DB_NAME" "$DB_USER"
```

Только `veqta_dev_owner` может создавать временную тестовую БД. Прикладные роли и `veqta_prod_owner` не имеют CREATEDB. Прикладная роль не владеет схемой и не состоит в роли владельца; её DML ограничивают FK/CHECK/триггеры. `deploy/postgresql/*.sql` убирает доступ PUBLIC к рабочим базам. Одна роль не получает доступ к другой базе.

PROD всегда перенаправляет на HTTPS, использует защищённые cookies, HSTS и защиту от встраивания в чужой iframe. Nginx обязан перезаписывать заголовок протокола, а прямой доступ к Gunicorn — оставаться закрытым.

Отправка электронной почты в этом приложении отсутствует. `MAILERS={}` намеренно запрещает использование почтового канала. В `SILENCED_SYSTEM_CHECKS` исключено только предупреждение `mail.W001` об отсутствии почтового канала; проверки HTTPS, cookies и секретного ключа работают штатно. Если появится функция отправки писем, настройте нативный MAILERS и уберите это исключение.
