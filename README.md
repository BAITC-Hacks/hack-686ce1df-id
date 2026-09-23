# Граф денег

Локальное приложение для исследования наблюдаемой транзакционной сети: роли, кластеры, приоритеты, граф и объяснения. Роли и score — аналитические гипотезы, не вероятности виновности; ограничения исходной выборки сохраняются в карточках.

**Состояние приложения:** полный локальный расчёт из трёх Parquet, роли с проверяемыми основаниями, воспроизводимые кластеры Louvain, приоритеты, признаки активности по датам, API, React-интерфейс и CSV задания. Исходные данные получены и проверены: 2248 узлов, 3119 связей, 4840 переводов. Правила `observed-graph-v1` — явные инженерные гипотезы, а не статистически валидированная модель риска. AI необязателен; внешний провайдер не проверялся. [Методология](docs/methodology.md), [приём данных](docs/data-intake-2026-09-23.md).

## Реальные данные: расчёт и приложение

Нужны Python 3.12+ и Node.js 22.12+ для сборки интерфейса. Три исходных файла должны лежать в `data/raw/`: `nodes.parquet`, `edges.parquet`, `transactions.parquet`. Из корня проекта на macOS/Linux:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m backend --data-dir data/raw
```

Открыть [http://127.0.0.1:8000](http://127.0.0.1:8000). Если порт занят, добавить `--port 8002`. На Windows заменить `.venv/bin/python` на `.venv\Scripts\python.exe`. При старте выполняется полный расчёт; ошибка входа останавливает запуск с диагностикой. Повторный расчёт сверяет содержимое существующего неизменяемого набора. В режиме настоящих данных редактор демонстрационных записей отключён.

Только расчёт, без запуска веб-сервера:

```sh
.venv/bin/python -m backend.app.analytics.cli run --input-dir data/raw --output-dir artifacts --rules config/rules.json --mapping config/input_mapping.json
```

Команда выводит `run_dir`, фактические количества и полное время. Результат — `artifacts/real-<hash>/`. В меню «Выгрузки» доступны `nodes_roles.csv` (все узлы), `clusters.csv` и `top_nodes.csv` (первые 20). Эти три CSV соответствуют starter; оценки в них 0–1. В приложении и внутренних файлах `roles.csv`/`priorities.csv` шкала 0–100. Артефакты также содержат `temporal.json`, `diagnostics.json`, точные правила и mapping, контрольные суммы и паспорт расчёта.

Исходные Parquet не переписываются; `data/` и `artifacts/` исключены из Git. Данные из архивов не отправляются внешним сервисам при расчёте. Суммы float64 принимаются только с явной политикой преобразования без округления; далее агрегируются в целых тиынах. Все 19 изолятов сохранены: полный граф имеет 35 компонент, из них 16 с рёбрами.

Отдельные искусственные режимы: `.venv/bin/python -m backend --fixtures` для четырёх контрактных примеров и `.venv/bin/python -m backend --demo` для редактируемого синтетического набора.

## Docker: быстрый запуск для тестировщика

Нужен запущенный **Docker Desktop с Linux-контейнерами** (Windows/macOS) или Docker Engine с Compose v2+ (Linux). Python и Node.js на компьютере не требуются. Из корня этого репозитория:

```sh
docker compose up --build --wait --wait-timeout 90
```

Открыть **[http://localhost:8000](http://localhost:8000)**. Первая сборка скачивает образы и зависимости; последующие используют кеш. `.env` и AI-ключ для запуска не нужны. По умолчанию загружается **искусственный набор** `fixture-contract-v1`: четыре узла, предупреждение в интерфейсе, локальные AI-объяснения без внешних запросов. Проверить поиск `0007`, `7`, `0012`, `isolated`, граф и CSV-выгрузки. Контейнер становится `healthy`, только когда загружены данные и доступен интерфейс.

```sh
# Автоматическая проверка собранного приложения
docker compose exec -T app python scripts/docker_smoke.py
# Состояние и логи
docker compose ps
docker compose logs --tail=100 app
# Остановка и удаление контейнера (исходники и данные сохраняются)
docker compose down
```

Один контейнер содержит FastAPI, собранный React-интерфейс, аналитику и фикстуры. [Инструкция Docker](docs/docker.md) описывает подключение рассчитанного набора через `compose.artifacts.yaml`, другой порт, тесты, обновление и откат. Исходные данные в образ не включаются.

## Установка и быстрый запуск без Docker

Команды выполнять из корня этого репозитория, где находится `pyproject.toml`. Проверенное окружение: Windows, Python 3.12.14. `requirements.lock` фиксирует все зависимости сервера и API-тестов; активация окружения не нужна.

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m backend --fixtures
```

На этом компьютере `python` отсутствует в PATH. Для **первой** команды доступен Python из установленного Codex:

```powershell
& 'C:\Users\Adina\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv .venv
```

Linux/macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m backend --fixtures
```

API: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). Swagger: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). Swagger загружает оформление с CDN; сам API и сохранённый `contracts/openapi.json` доступны без него. До сборки frontend по адресу `/` возвращается 404.

`--fixtures` означает **искусственные данные разработки**. Health возвращает `run_id="fixture-contract-v1"`, а заголовок `X-Data-Source: fixtures` и лог сервера обозначают источник. В наборе узлы `0007`, `7`, `0012` и `isolated`; `0007` и `7` различаются. Все роли/приоритеты — примеры формата, а не результат классификации. Канонический каталог фикстур и `run_id` с префиксом `fixture-` отвергаются в обычном режиме.

Быстрая проверка из второго PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/nodes/0007
Invoke-RestMethod 'http://127.0.0.1:8000/api/graph?gid=0007&radius=2&limit=300'
Invoke-RestMethod 'http://127.0.0.1:8000/api/graph?gid=isolated'
```

## Настоящий завершённый запуск

После передачи артефактов от A:

```powershell
.\.venv\Scripts\python.exe -m backend --run-dir artifacts/<run_id>
```

Вместо `<run_id>` подставить имя полученного каталога. Он содержит `manifest.json` со статусом `complete`, `nodes.json`, `edges.json`, `clusters.json`, `priorities.csv`, `roles.csv`, `rules.json`, `audit.json`. `manifest.files` сопоставляет логические имена с относительными путями; образец находится в [фикстурах](tests/fixtures/contract-v1/manifest.json). Для обязательных файлов поддерживаются ключи `nodes`, `edges`, `clusters`, `priorities`, `roles`, `rules`, `audit` или соответствующие полные имена файлов.

Выгрузки обязательных файлов всегда доступны по логическим именам, даже если в `manifest.files` указаны полные имена файлов. Все явно перечисленные ключи также сохраняются. `/api/exports/manifest` возвращает точные байты загруженного `manifest.json`; включать паспорт в собственный список `files` необязательно. Ключ `manifest` зарезервирован: если он указан, его путь должен вести к тому же `manifest.json`.

Сервер проверяет все перечисленные файлы, модели, уникальность и ссылки, принадлежность кластеров, количества, SHA256 точных байтов `rules.json` и соответствие CSV карточкам. Некорректный или незаконченный каталог даёт `data_ready=false`; маршруты данных возвращают 503, причина записывается в лог. Запуск без конфигурации также остаётся неготовым и не подставляет фикстуры.

Карточки и выгрузки обслуживаются из одного снимка в памяти. Чтобы перейти на другой расчёт, указать новый каталог и перезапустить сервер. Изменение файлов после загрузки не изменяет текущие ответы.

Можно скопировать `.env.example` в `.env` и задать `AML_RUN_DIR`, затем запустить:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Существующие переменные окружения имеют приоритет над `.env`. `AML_FIXTURE_MODE=true` и непустой `AML_RUN_DIR` одновременно запрещены. `.env`, ключи, `data/`, `artifacts/`, Parquet и виртуальные окружения исключены из Git.

Команда полного пересчёта `analytics.cli run` описана выше. Правила, трактовка неполноты и воспроизводимость описаны в [методологии](docs/methodology.md). Прежние планы отражают этап до получения данных.

## API и интеграция команды

| Маршрут | Назначение |
|---|---|
| `GET /api/health` | Готовность текущего запуска |
| `GET /api/node?gid=…` | Точная карточка строкового gid, включая `/`, Unicode и сегменты `.`/`..` |
| `GET /api/nodes/{gid}` | Совместимый маршрут карточки через путь; поддерживает `/` внутри gid |
| `GET /api/priorities?limit=20&offset=0` | Приоритеты, score убывает, затем gid возрастает |
| `GET /api/clusters` | Список кластеров |
| `GET /api/cluster?cluster_id=…` | Точная карточка кластера с непрозрачным строковым ID |
| `GET /api/clusters/{cluster_id}` | Совместимый маршрут карточки через путь; поддерживает `/` внутри ID |
| `GET /api/graph` | Ровно один `gid`, `cluster_id` или `component_id`; radius 1/2, limit 1–300 |
| `GET /api/exports/{name}` | Логическое имя обязательного файла, `manifest` или точный ключ из `manifest.files` |
| `POST /api/ai/explain` | Объяснение выбранного узла/кластера или локальная справка |
| `POST /api/ai/investigate` | Вопрос по выбранному узлу/кластеру или локальная справка |

Граф обходит связи в обе стороны, сохраняет направление рёбер и выбранный узел. Усечение сортируется по расстоянию, priority_score и gid; ответ сообщает `truncated`, `total_nodes`, `shown_nodes`. Метрики карточек берутся из полного расчёта. `null` сохраняется отдельно от нуля, денежные строки не преобразуются в float.

Для точного поиска frontend использует query-маршруты `/api/node` и `/api/cluster` с кодированием параметров через `URLSearchParams`. Это сохраняет ID с точками и разделителями без нормализации сегментов пути браузером. Пустой или отсутствующий ID даёт 422, неизвестный — 404.

Все результаты связаны `contract_version="1.0"` и `run_id`. Ошибки имеют форму `{error:{code,message},run_id}`: 404 — неизвестный id/экспорт, 422 — параметры, 503 — нет завершённого расчёта, 409 — устаревший run_id AI-запроса. Заголовки API: `X-Data-Source`, `X-Contract-Version`, `X-Run-Id`, `Cache-Control: no-store`. В `X-Run-Id` специальные и Unicode-символы percent-encoded; JSON содержит исходную строку без изменения.

- **A:** `backend.app.analytics.pipeline.run_pipeline` выполняет чтение, аудит, роли, кластеры, приоритеты и экспорт. Модели — в `backend/app/contracts.py`; `ResultStore.check_concentration` вызывает общий расчёт концентрации. Зависимости аналитики включены в `requirements.lock` и обычную установку проекта.
- **C:** [снимок OpenAPI](contracts/openapi.json), [общие примеры](tests/fixtures/contract-v1/) и [запуск frontend](frontend/README.md). Dev proxy: `/api` → `http://127.0.0.1:8000`. Разрешён CORS-origin `http://localhost:5173`; другой локальный origin задаётся `AML_FRONTEND_ORIGIN`. После сборки `frontend/dist/index.html` FastAPI автоматически раздаёт этот каталог и SPA-маршруты. Docker-сборка и интеграция на общих искусственных данных проверены; результаты перечислены в [инструкции Docker](docs/docker.md).
- **D:** подключены async-функции `backend.app.ai.service.explain(request, store)` и `investigate(request, store)`, возвращающие `AIResponse`. HTTP-слой проверяет существование target и текущий run_id до вызова и сохраняет коды ошибок запросов D. Доступны методы Store из контракта, типы `ExplainRequest`, `InvestigateRequest`, `AIResponse`.

Без `OPENAI_API_KEY` и `AI_MODEL` оба AI-маршрута возвращают `status="fallback"` с локальными объяснениями, основаниями и ограничениями. Для провайдера используется `AI_PROVIDER=openai`; модель задаётся конфигурацией. Общий срок — `AI_TIMEOUT_SECONDS` (не более 30), бюджет функций — `AI_MAX_TOOL_CALLS` (0–3). D проверяет ссылки на основания и числовые утверждения; сбой даёт локальную справку с уже завершёнными проверками. Неверный формат ответа или несовместимый run_id на границе HTTP дают локальную справку B. Вопрос длиннее 4000 символов получает 422. Подробности и пределы проверки достоверности — в [инструкции AI](docs/ai.md). Реальный провайдер в рамках слияния не вызывался.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m backend.app.openapi --check
```

Для обновления снимка после согласованного изменения моделей: `.\.venv\Scripts\python.exe -m backend.app.openapi`. API-тесты используют только искусственные данные и имитации сервиса, без платных запросов. Применены [официальные рекомендации FastAPI по TestClient/lifespan](https://fastapi.tiangolo.com/advanced/testing-events/) и [строгой конфигурации Pydantic](https://docs.pydantic.dev/latest/api/config/).

Фактическая проверка 23.09.2026: установка из `requirements.lock` во второе чистое окружение `.venv-verify`; 102 API-теста прошли, `pip check` не нашёл конфликтов, OpenAPI совпал. Есть одно предупреждение устаревшего alias в Starlette/AnyIO, на проверки не влияет. Это проверка backend на синтетике, не бенчмарк аналитики.

После объединения B и D: **206 тестов API и AI прошли**, включая публичные функции D с ResultStore, объяснение и исследование с имитацией провайдера, обработку ошибок и сохранение выполненных проверок при тайм-ауте. OpenAPI совпадает, конфликтов зависимостей нет; платные API не вызывались.

После исправлений границ frontend/API добавлены регрессии для восьми выгрузок, filename-ключей manifest, неизменяемости выгружаемого паспорта и точного поиска ID со слешами, Unicode и сегментами `.`/`..`. Проверка API и AI: **233 теста прошли**, OpenAPI совпал; внешний провайдер не вызывался.

Текущая приёмка полного расчёта описана в [отчёте реального запуска](docs/real-data-verification.md). Для живого AI нужны настроенный аккаунт и выбранная модель. Рост до порядка миллиона узлов требует пакетной агрегации и компактного хранилища графа; текущий Store держит небольшой набор и выгрузки в памяти, такая масштабируемость не измерялась.

## Контекст и планы

- [Архитектура](docs/superpowers/specs/2026-09-23-money-graph-design.md)
- [Контракт данных и API v1](docs/contracts/money-graph-v1.md)
- [Общий план команды](docs/superpowers/plans/2026-09-23-money-graph-parallel.md)
- [A — аналитика](docs/superpowers/plans/2026-09-23-money-graph-a-analytics.md)
- [B — backend](docs/superpowers/plans/2026-09-23-money-graph-b-backend.md)
- [C — frontend](docs/superpowers/plans/2026-09-23-money-graph-c-frontend.md)
- [D — AI](docs/superpowers/plans/2026-09-23-money-graph-d-ai.md)
