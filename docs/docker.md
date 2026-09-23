# Docker: реальные данные и проверка приложения

## Первый запуск из Parquet

Установить и запустить Docker Desktop с Linux-контейнерами на Windows/macOS либо Docker Engine и Compose v2+ на Linux. Команды выполнять из корня `hack-686ce1df-id`, где находятся `Dockerfile` и `compose.yaml`; на Windows подходит PowerShell. Интернет нужен для первой сборки. После неё полный расчёт, интерфейс и локальные объяснения работают без сети; внешний AI требует соединения с провайдером.

В `data/raw/` должны находиться `nodes.parquet`, `edges.parquet` и `transactions.parquet`. Файлы передаются отдельно от репозитория. Для другого пути создать локальный `.env` по образцу `.env.example` или дополнить существующий, задав `AML_DATA_DIR`:

```dotenv
AML_DATA_DIR=./data/raw
```

Путь может быть абсолютным; в Windows удобно использовать `C:/datasets/money-graph`. Для пути с пробелами заключить значение в двойные кавычки. Каталог должен существовать и быть доступен Docker Desktop. На Linux UID/GID `10001:10001` внутри контейнера нужны права чтения входных файлов и прохода по каталогам.

```sh
docker compose -f compose.yaml -f compose.real.yaml up --build --wait --wait-timeout 90
```

Открыть **[http://localhost:8000](http://localhost:8000)**. Если в `.env` задан `APP_PORT`, использовать его. Готовность: [API health](http://localhost:8000/api/health). Swagger: [документация API](http://localhost:8000/docs); её оформление использует CDN.

`compose.real.yaml` запускает `python -m backend --data-dir /app/data/raw --output-dir /app/artifacts`. Вход подключён только для чтения, а результаты сохраняются в отдельном Docker volume. При каждом старте выполняется полный расчёт; уже существующий результат сверяется и остаётся неизменным. Отсутствующий каталог, неверные Parquet или несогласованные суммы останавливают запуск с диагностикой, без подстановки фикстур. В health должен быть `data_ready=true` и `run_id` с префиксом `real-`.

`--wait` ждёт готовности и оставляет контейнер работать в фоне. Лимит 90 секунд относится к запуску, а не скачиванию и сборке. Healthcheck требует загруженный набор и доступный HTML; одного HTTP 200 от health недостаточно.

```sh
# Проверка HTML/JS/CSS, реальных карточек, графа и всех выгрузок без вызовов AI
docker compose -f compose.yaml -f compose.real.yaml exec -T app python scripts/real_smoke.py --base-url http://127.0.0.1:8000
# Состояние и логи
docker compose -f compose.yaml -f compose.real.yaml ps
docker compose -f compose.yaml -f compose.real.yaml logs --tail=100 app
# Остановка с сохранением volume и исходных файлов
docker compose -f compose.yaml -f compose.real.yaml down
```

Скачать три CSV можно через меню «Выгрузки». Для независимой проверки схемы и значений внутри контейнера подставить `run_id` из health:

```sh
docker compose -f compose.yaml -f compose.real.yaml exec -T app python scripts/verify_task_exports.py "/app/artifacts/real-<hash>"
```

Все команды явно указывают Compose-файлы. Это выбирает нужный режим даже при старом `COMPOSE_FILE` в окружении и одинаково работает на macOS, Linux и Windows. Для сохранения рассчитанных наборов не добавлять `--volumes` к `down`.

## Загрузка готового набора без пересчёта

Нужен каталог с `manifest.json` со статусом `complete` и всеми перечисленными артефактами. Предварительный отчёт `audit_only` не подходит. Если набор ещё не создан, использовать основной запуск выше или выполнить локально:

```sh
.venv/bin/python -m backend.app.analytics.cli run --input-dir data/raw --output-dir artifacts --rules config/rules.json --mapping config/input_mapping.json
```

В Windows заменить `.venv/bin/python` на `.venv\Scripts\python.exe`. Команда сообщает `run_dir`. Задать этот путь в локальном `.env`:

```dotenv
AML_ARTIFACTS_DIR=./artifacts/real-<hash>
```

Затем:

```sh
docker compose -f compose.yaml -f compose.artifacts.yaml up --build --wait --wait-timeout 90
docker compose -f compose.yaml -f compose.artifacts.yaml exec -T app python scripts/real_smoke.py --base-url http://127.0.0.1:8000
```

Готовый каталог монтируется только для чтения в `/app/artifacts/run`; расчёт при старте не выполняется. Некорректный набор остаётся неготовым, с причиной в логах. API и CSV обслуживаются из одного снимка в памяти. Для другого результата изменить `AML_ARTIFACTS_DIR` и повторить `up`. Для повторной загрузки того же каталога:

```sh
docker compose -f compose.yaml -f compose.artifacts.yaml restart app
```

Проверка `real_smoke.py` предназначена для предоставленного набора: в нём есть seed, граница наблюдения и изоляты. Для произвольного другого набора состав проверочных примеров может отличаться.

## AI и локальные настройки

Реальные ключи записываются только в исключённый из Git `.env`. Существующий `.env` дополнить, не перезаписывая; `.env.example` остаётся шаблоном с пустыми секретами. Compose передаёт `OPENAI_API_KEY`, `AI_MODEL`, `AI_PROVIDER`, `AI_TIMEOUT_SECONDS` и `AI_MAX_TOOL_CALLS` серверу. При пустом ключе или модели доступны локальные объяснения.

После изменения `.env` повторить `up`, указав те же Compose-файлы. Команда `restart` не применяет новые переменные контейнера. Ключ не помещать в Compose, Dockerfile, build arguments, frontend или команды терминала. Настройка модели, ограничения и необязательная проверка подключения описаны в [инструкции AI](ai.md).

Другие необязательные настройки:

```dotenv
APP_PORT=8001
APP_MEMORY_LIMIT=2g
APP_CPUS=2.0
```

В этом примере приложение доступно на [http://localhost:8001](http://localhost:8001). Порт опубликован только на `127.0.0.1`. По умолчанию выделены 1 ГБ памяти, 2 CPU и максимум 128 процессов. Store держит результат в памяти; масштаб до миллиона узлов не измерялся.

## Явный режим искусственных фикстур

Для разработки без исходных данных:

```sh
docker compose -f compose.yaml up --build --wait --wait-timeout 90
```

Одиночный `compose.yaml` явно выбирает `fixture-contract-v1`: четыре искусственных узла `0007`, `7`, `0012`, `isolated`. Интерфейс показывает источник данных. Этот режим не выполняет реальный расчёт. AI-настройки из `.env` действуют и здесь; пустые значения дают fallback.

`docker_smoke.py` предназначен только для такого набора и проверяет локальный AI. Его запускать на fixture-сервере, созданном с пустыми `AI_MODEL` и `OPENAI_API_KEY`:

```sh
docker compose -f compose.yaml exec -T app python scripts/docker_smoke.py
```

Для настоящего набора использовать `real_smoke.py` из первого раздела. Если после запуска видна синтетика, проверить выбранные `-f`, порт и `run_id` в `/api/health`; затем запустить команду с `compose.real.yaml`.

## Диагностика

| Симптом | Действие |
|---|---|
| Cannot connect to the Docker daemon | Запустить Docker Desktop/Engine; на Windows выбрать Linux containers |
| Port is already allocated | Задать свободный `APP_PORT` в `.env` и повторить `up` с нужными `-f` |
| Ошибка монтирования data/raw | Проверить путь `AML_DATA_DIR`, наличие трёх Parquet и доступ Docker Desktop к каталогу |
| Контейнер unhealthy или завершился при расчёте | Прочитать `logs --tail=100 app` с теми же `-f`; проверить данные, права и диагностическую ошибку |
| Код завершения 137 / OOMKilled | Увеличить `APP_MEMORY_LIMIT` и доступную Docker Desktop память |
| После изменения исходников осталась старая версия | Повторить `up --build --wait --wait-timeout 90` с теми же `-f` |
| После изменения AI в `.env` параметры не применились | Повторить `up`, чтобы контейнер был пересоздан; одного `restart` недостаточно |

## Проверки кода и CI

```sh
docker build --target backend-test -t money-graph:backend-test .
docker build --target frontend-test -t money-graph:frontend-test .
```

Первый target запускает backend-тесты, включая аналитику и AI, и сверяет OpenAPI. Второй запускает frontend-тесты и сборку TypeScript/Vite. Сетевые ответы AI в тестах имитируются; кредиты не расходуются. Зависимости фиксированы в `requirements.lock` и `frontend/package-lock.json`. Конечный образ содержит полный аналитический pipeline и конфигурацию; исходные Parquet и готовые результаты в образ не включаются.

Workflow `.github/workflows/docker.yml` запускает Docker-проверки на Linux x86_64, fixture smoke без настроенных ключей и отчёт Trivy об исправляемых HIGH/CRITICAL уязвимостях. Сканирование отчётное, без блокировки по найденным уязвимостям; наличие отчёта не означает их отсутствия. Образы workflow не публикует. GitHub Actions для PR №2 не смог начать работу из-за billing-блокировки аккаунта; локальные проверки не выдаются за успешный запуск CI.

Фактические результаты текущей версии, воспроизводимость и время расчёта приведены в [отчёте реального запуска](real-data-verification.md). Ранние числа 206 API/AI-тестов и 17 frontend-тестов относятся к первой Docker-интеграции на синтетике. Работа AI с API-ключами подтверждена пользователем; отдельный измеренный протокол живого провайдера не записывался.

Сборка использует Node 24 и Python 3.12 из Debian slim образов. Node и `node_modules` остаются в стадии сборки. Приложение работает без root; файловая система контейнера доступна только для чтения, кроме временного `/tmp` и volume результатов в реальном режиме.

## Передача готового образа

На машине с той же архитектурой CPU сохранить образ:

```sh
docker save -o money-graph-image.tar money-graph:local
```

Передать архив образа, `compose.yaml`, `compose.real.yaml` и отдельно согласованные исходные Parquet. Получатель загружает образ, размещает данные и запускает:

```sh
docker load -i money-graph-image.tar
docker compose -f compose.yaml -f compose.real.yaml up --no-build --wait --wait-timeout 90
```

Для готового расчёта вместо raw-данных передать каталог результата и `compose.artifacts.yaml`, настроить `AML_ARTIFACTS_DIR` и использовать соответствующий override. Локальный `.env` с ключами в передачу не входит. Образ Apple Silicon имеет архитектуру `linux/arm64`, обычного Windows/Linux x86_64 — `linux/amd64`; для другой архитектуры собрать образ на машине получателя.

## Обновление и откат

Перед пересборкой сохранить рабочий образ и путь к существующему завершённому набору:

```sh
docker image tag money-graph:local money-graph:previous
docker compose -f compose.yaml -f compose.real.yaml up --build --wait --wait-timeout 90
```

Для отката на предыдущий образ с ранее проверенными артефактами задать в `.env` `MONEY_GRAPH_IMAGE_TAG=previous` и `AML_ARTIFACTS_DIR` с путём к этому набору на хосте. Затем:

```sh
docker compose -f compose.yaml -f compose.artifacts.yaml up --no-build --wait --wait-timeout 90
```

Проверить health, поиск известного gid и выгрузки. Этот путь не пересчитывает данные кодом предыдущего образа. Если исходный результат хранился только в volume, заранее сохранить каталог на хост командой `docker compose -f compose.yaml -f compose.real.yaml cp "app:/app/artifacts/real-<hash>" ./artifacts/`, подставив настоящий `run_id` и заключив пути с пробелами в кавычки.

Для возврата к обновлениям убрать `MONEY_GRAPH_IMAGE_TAG` из `.env` и выполнить основной запуск с `compose.real.yaml`. `down` сохраняет volume, образы, исходники и подключённые артефакты; архив образа и копия прежнего результата позволяют воспроизвести прежнюю версию.
