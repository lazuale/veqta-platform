# Проверка VEQTA

Для тестов используйте отдельный кластер PostgreSQL 18 и вымышленные данные. Настройте его по [инструкции локального запуска](getting-started.md). Команды выполняйте из корня проекта с активным `venv`. Проверки, создающие `test_veqta_dev`, запускайте последовательно.

## Django и PostgreSQL

Сначала проверьте рабочие настройки локального DEV. Для создания и миграции временной базы передайте реквизиты владельца только процессу тестов:

```bash
python -m pip check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --check
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python manage.py test tests --noinput --verbosity 2
```

Django создаёт и удаляет одноразовую БД. Тесты покрывают Admin, импорт, права, снимки, транзакции и ограничения PostgreSQL. Сценарий масштаба сохраняет и завершает заявку на 1000 сотрудников × 30 позиций. Тесты серверных скриптов можно запустить отдельно командой `python -m unittest tests.test_deployment -v`. Они проверяют подготовку и работу при отказах в синтетическом окружении.

## Покрытие Python и ветвлений

CI измеряет покрытие Python-кода через `coverage.py` 7.16.2, установленный по фиксированному SHA-256 вместе с тестовыми зависимостями. На независимом Cold Start DEV/PROD, тестах Django и сервисной приёмке собирается общее покрытие операторов и ветвлений для `config/`, `portal/` и `workwear/`, за исключением схемы миграции. Отчёт показывает неисполненные строки и переходы по каждому файлу, а не только итоговый процент.

Для локального воспроизведения после установки `requirements-browser.lock` и настройки одноразовой БД выполните:

```bash
mkdir -p test-results/coverage
export COVERAGE_FILE=test-results/coverage/.coverage
python -m coverage erase
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python -m coverage run --branch --source=config,portal,workwear \
  --omit='*/migrations/*' manage.py test tests --noinput --verbosity 2
DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python -m coverage run --append --branch --source=config,portal,workwear \
  --omit='*/migrations/*' -m tests.acceptance
python -m coverage report -m
python -m coverage html -d test-results/coverage/html
```

GitHub Actions сохраняет подробный JSON и HTML в артефакте `python-branch-coverage`. Этот показатель отражает только исполнение кода внутри процессов измерения. Отдельные процессы Gunicorn, браузеров и серверных утилит в него не входят; триггеры PostgreSQL также не измеряются `coverage.py`. Непокрытая строка или ветвление сами по себе не означают, что код недостижим, и не являются основанием для удаления.

## Cold Start

В новом тестовом кластере выполните только [создание локальной базы и ролей](getting-started.md#создайте-локальную-базу), до `veqta_initialize`. Запустите:

```bash
DB_RUNTIME_PASSWORD="$DB_PASSWORD" DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python -m tests.coldstart
```

Проверка требует пустую схему. Она применяет начальную миграцию, проверяет ограничения PostgreSQL, создаёт тестового администратора и первую заявку. Данные остаются в тестовом кластере. CI также проверяет отдельную PROD-базу и запрет подключения к чужому контуру. [Ограничения и роли](architecture.md#инварианты-postgresql).

## Конкуренция и браузеры

Сервисная проверка двух авторов, сохраняющих одну версию заявки:

```bash
DB_RUNTIME_PASSWORD="$DB_PASSWORD" DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python -m tests.acceptance
```

Для браузера установите зависимости и движки:

```bash
python -m pip install --require-hashes -r requirements-browser.lock
python -m playwright install chromium firefox webkit
```

Базовая проверка `--browser` использует временный Django-сервер. Добавьте `--matrix` для расширенной приёмки или `--server nginx` для стека Gunicorn, Nginx и TLS с тестовым сертификатом. Для последнего варианта нужны Nginx и OpenSSL. Временный сервер подключается как `veqta_dev` и читает только свою тестовую БД.

```bash
DB_RUNTIME_PASSWORD="$DB_PASSWORD" DB_USER=veqta_dev_owner DB_PASSWORD="$DB_OWNER_PASSWORD" \
  python -m tests.acceptance --browser --matrix --server nginx --engine chromium \
  --http-load --artifacts test-results/https/chromium
```

Повторите для `firefox` и `webkit` с отдельной папкой артефактов и без `--http-load`. При необходимости укажите `--browser-executable` или `--nginx-executable`. Для проверок axe добавьте `--axe-script` с путём к `axe.min.js` версии 4.11.0:

```bash
npm install --prefix test-results/axe --no-save --ignore-scripts --no-audit --no-fund axe-core@4.11.0
```

Базовый прогон проверяет вход, профиль, смену пароля, Admin, счётчик и фильтры реестра и полный цикл заявки. Расширенный сопоставляет список, матрицу и карточку с API и PostgreSQL, включая сохранение фокуса, выбор на нескольких страницах, исторические снимки, журнал, сбои сети и конфликт версий. Проверяются клавиатура и шесть размеров экрана, включая 390 px.

Размеры сценариев — 1, 10, 100, 500 и 1000 сотрудников с 30 позициями. Результаты содержат время операций, объём DOM и число SQL и HTTP-запросов. Восемь параллельных сервисных пользователей сохраняют по 100 × 30 назначений. `--http-load` повторяет эту нагрузку через HTTPS и проверяет конфликт двух запросов. JSON, журналы и скриншоты сохраняются в `--artifacts`.

## Исходники и CI

```bash
python scripts/check_source.py
python -m compileall -q config portal workwear tests scripts
bash -n scripts/server_checks.sh scripts/server_apply.sh scripts/server_verify.sh tests/linux_permissions.sh
sh -n deploy/letsencrypt/renew-nginx.sh
for script in portal/static/portal/*.js workwear/static/workwear/*.js workwear/static/workwear/js/*.js; do
  node --input-type=module --check < "$script" || exit 1
done
git diff --check
```

`sudo bash tests/linux_permissions.sh` запускайте в одноразовом Linux-окружении. Сценарий проверяет границы прав Unix-пользователя на код, venv и закрытый файл миграций.

Workflow [Verify VEQTA](https://github.com/lazuale/veqta-platform/actions/workflows/verify.yml) запускает регрессию, Cold Start, проверки прав и исходников при push и pull request. На push в `main` и при ручном запуске также выполняются все три браузерных прогона через HTTPS. Для приёмки сверяйте SHA запуска, статусы шагов и артефакт `rc-browser-evidence`.

Серверную установку с systemd, публичным DNS и ACME подтверждают отдельно на целевой Ubuntu. Автоматические проверки браузера и axe покрывают заданные сценарии. Проверка со скринридером, нативным Safari на устройстве и длительная нагрузка с рабочим объёмом данных требуют отдельного окружения.
