# Docker для проверки «Графа денег»

## Первый запуск

Установить и запустить Docker Desktop с Linux-контейнерами на Windows/macOS либо Docker Engine и Compose v2+ на Linux. Все команды выполнять из `hack-686ce1df-id`, где находятся `Dockerfile` и `compose.yaml`. На Windows подходит PowerShell. Нужен интернет для первой сборки; после неё тестовый интерфейс, API и локальные AI-объяснения работают без сети.

```sh
docker compose up --build --wait --wait-timeout 90
```

Приложение: <http://localhost:8000>. Готовность: <http://localhost:8000/api/health>. Swagger: <http://localhost:8000/docs> (его оформление использует CDN).

`--wait` ждёт готовности контейнера и оставляет его работать в фоне. Лимит 90 секунд относится к запуску, а не скачиванию и сборке. Проверка готовности требует `data_ready=true` и доступный HTML-интерфейс; одного HTTP 200 от health недостаточно.

По умолчанию Compose явно включает фикстуры и отключает внешний AI независимо от локального `.env`. Набор `fixture-contract-v1` содержит только четыре искусственных узла. Проверить `0007` и `7` как разные идентификаторы, `0012` как границу наблюдения, `isolated` как узел без рёбер. В интерфейсе должен быть баннер искусственных данных. Нажатия AI возвращают локальный `fallback`, а не ответ модели.

```sh
docker compose exec -T app python scripts/docker_smoke.py
docker compose ps
docker compose logs --tail=100 app
docker compose down
```

Smoke проверяет HTML и JS/CSS, SPA-маршрут, готовность, точные идентификаторы, граф, выгрузку, локальный AI и JSON 404 для неизвестного API. Он предназначен для стандартного режима фикстур; на настоящем наборе использовать проверку готовности и предметные сценарии этого набора.

## Настоящий завершённый набор

Нужен каталог с `manifest.json` со статусом `complete` и всеми перечисленными в нём артефактами. Предварительный отчёт `audit_only` не подходит. Контейнер не создаёт настоящие результаты из Parquet: полный аналитический pipeline ещё не реализован.

Создать `.env` (или добавить в существующий) одну строку с путём к готовому каталогу:

```dotenv
AML_ARTIFACTS_DIR=./artifacts/my-run
```

Путь может быть абсолютным; в Windows удобно использовать `C:/datasets/my-run`. Для пути с пробелами заключить значение в двойные кавычки. Каталог должен существовать и быть доступен Docker Desktop; приложение внутри работает с UID/GID `10001:10001`, которому на Linux нужны права чтения файлов и прохода по каталогам.

```sh
docker compose -f compose.yaml -f compose.artifacts.yaml up --build --wait --wait-timeout 90
docker compose -f compose.yaml -f compose.artifacts.yaml logs --tail=100 app
```

Каталог монтируется **только для чтения** в `/app/artifacts/run`; в образ и build context исходные данные не включаются. Override выключает фикстуры. Отсутствующий каталог вызывает ошибку монтирования, незаконченный или некорректный набор — `unhealthy` и причину в логах; подмены фикстурами нет. Один образ обслуживает оба режима, баннер определяется заголовком `X-Data-Source` API.

После изменения файлов загруженного набора нужен перезапуск: сервер хранит один снимок в памяти.

```sh
docker compose -f compose.yaml -f compose.artifacts.yaml restart app
```

Вернуться к искусственному набору:

```sh
docker compose up --wait --wait-timeout 90
```

## Настройки и диагностика

Необязательные значения в `.env`:

```dotenv
APP_PORT=8001
APP_MEMORY_LIMIT=2g
APP_CPUS=2.0
```

После изменения выполнить `docker compose up --wait --wait-timeout 90` (добавить оба `-f` для настоящего набора). В примере адрес станет <http://localhost:8001>. Порт публикуется только на `127.0.0.1`; это локальный стенд тестировщика. По умолчанию контейнер ограничен 1 ГБ памяти, 2 CPU и 128 процессами. Store держит набор в памяти, поэтому крупным артефактам может понадобиться больше памяти; производительность на них ещё не измерена.

| Симптом | Действие |
|---|---|
| Cannot connect to the Docker daemon | Запустить Docker Desktop/Engine; на Windows включить Linux containers |
| Port is already allocated | Задать свободный `APP_PORT` в `.env` и повторить `up` |
| Ошибка скачивания образа/npm/pip | Проверить интернет, прокси и доступ к реестрам; повторить сборку |
| Контейнер unhealthy | `docker compose logs --tail=100 app`; проверить артефакты и права чтения |
| Код завершения 137 / OOMKilled | Увеличить `APP_MEMORY_LIMIT` и доступную Docker Desktop память |
| После изменения исходников видна старая версия | Повторить `docker compose up --build --wait --wait-timeout 90` |

AI-ключи и локальные `.env` не копируются в образ. Стандартный стенд намеренно работает с локальными объяснениями; живой провайдер требует отдельной конфигурации из [инструкции AI](ai.md), передачи ключа при запуске и выбранной модели. Не добавлять ключ в Dockerfile или build arguments.

## Проверки внутри Docker и CI

```sh
docker build --target backend-test -t money-graph:backend-test .
docker build --target frontend-test -t money-graph:frontend-test .
docker compose up --build --wait --wait-timeout 90
docker compose exec -T app python scripts/docker_smoke.py
```

Первый target запускает API/AI-тесты и проверку OpenAPI. Второй запускает frontend-тесты и сборку TypeScript/Vite. Тесты не вызывают платный AI. Зависимости берутся из `requirements.lock` и `frontend/package-lock.json`. Аналитический CLI использует отдельные зависимости и в этот веб-образ не включён как готовый pipeline.

Workflow `.github/workflows/docker.yml` повторяет эти проверки на Linux x86_64 и формирует отчёт Trivy об исправляемых HIGH/CRITICAL уязвимостях конечного образа. Пока сканирование отчётное: существующий lock содержит Starlette 0.47.3 с известными advisory (например, [CVE-2025-62727](https://github.com/Kludex/starlette/security/advisories/GHSA-7f5h-v6xp-fcq8)); обновление совместимой пары FastAPI/Starlette требует отдельной проверки. Наличие отчёта не означает отсутствие уязвимостей. Образы никуда не публикуются. Результаты workflow появляются после отправки изменений в GitHub; локальная проверка не является запуском CI.

Сборка использует Node 24 и Python 3.12 из официальных Debian slim образов. Node и `node_modules` остаются в стадии сборки; конечный образ работает без root, с файловой системой только для чтения и временным `/tmp`. Базовые теги получают обновления внутри выбранной ветки; для передачи строго одинакового образа сохранить его в архив.

## Фактическая проверка 23.09.2026

Docker Desktop, Linux arm64: образ собран из lock-файлов, **206 API/AI-тестов**, **17 frontend-тестов**, сверка OpenAPI и **16 HTTP smoke-проверок** прошли. Контейнер достиг `healthy`, работает с UID/GID `10001:10001` и read-only файловой системой. В браузере проверены баннер фикстур, поиск `0007`, граф, карточка и локальное AI-объяснение.

Отдельный временный контейнер проверил загрузку read-only каталога артефактов на искусственной копии набора с тестовым run_id. Неполный каталог оставил `data_ready=false`, без подстановки фикстур, а healthcheck завершился ошибкой. Проверка готовности также прошла при заведомо недоступном HTTP-прокси в окружении. Настоящие данные, живой AI, Linux amd64 и удалённый CI/Trivy в этой локальной проверке не проверялись.

## Передача готового образа без повторной сборки

На машине с той же архитектурой CPU:

```sh
docker save -o money-graph-image.tar money-graph:local
```

Передать архив и `compose.yaml`. Получатель выполняет:

```sh
docker load -i money-graph-image.tar
docker compose up --no-build --wait --wait-timeout 90
```

Сборка на Apple Silicon создаёт `linux/arm64`, на обычном Windows/Linux x86_64 — `linux/amd64`. Для другой архитектуры получателю проще собрать из исходников; один локальный архив не объявляется универсальным. Для работы с настоящими артефактами также передать `compose.artifacts.yaml` и отдельно согласованный набор данных.

## Обновление и откат

Перед пересборкой сохранить текущий рабочий образ:

```sh
docker image tag money-graph:local money-graph:previous
docker compose up --build --wait --wait-timeout 90
```

Если новая сборка не работает, задать `MONEY_GRAPH_IMAGE_TAG=previous` в `.env`, затем:

```sh
docker compose up --no-build --wait --wait-timeout 90
docker compose exec -T app python scripts/docker_smoke.py
```

Для настоящих артефактов добавить оба `-f` и проверять собственный набор вместо fixture smoke. Для возврата к обновлениям убрать `MONEY_GRAPH_IMAGE_TAG` из `.env`. `docker compose down` удаляет контейнер и его сеть, но сохраняет образы, исходники и подключённые артефакты.

Основания: [multi-stage Docker builds](https://docs.docker.com/build/building/multi-stage/), [Compose up и --wait](https://docs.docker.com/reference/cli/docker/compose/up/), [настройки сервисов Compose](https://docs.docker.com/reference/compose-file/services/), [Trivy Action](https://github.com/aquasecurity/trivy-action).
