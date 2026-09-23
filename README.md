# Граф денег

Локальное приложение для исследования наблюдаемой транзакционной сети: роли, кластеры, приоритеты, граф и объяснения. Роли и score — аналитические гипотезы, не вероятности виновности; ограничения исходной выборки сохраняются в карточках.

**Состояние направления B:** реализованы FastAPI/Pydantic-контракт, проверка и загрузка законченных артефактов, API, выгрузки, точки подключения A/D и явный режим искусственных данных. Реальные parquet, аналитическое ядро A, интерфейс C и AI-сервис D в этой ветке пока отсутствуют. Полный расчёт и его время ≤300 секунд ещё не проверены.

## Установка и быстрый запуск

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

Linux/macOS (команды предусмотрены, здесь не проверялись):

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

Сервер проверяет все перечисленные файлы, модели, уникальность и ссылки, принадлежность кластеров, количества, SHA256 точных байтов `rules.json` и соответствие CSV карточкам. Некорректный или незаконченный каталог даёт `data_ready=false`; маршруты данных возвращают 503, причина записывается в лог. Запуск без конфигурации также остаётся неготовым и не подставляет фикстуры.

Карточки и выгрузки обслуживаются из одного снимка в памяти. Чтобы перейти на другой расчёт, указать новый каталог и перезапустить сервер. Изменение файлов после загрузки не изменяет текущие ответы.

Можно скопировать `.env.example` в `.env` и задать `AML_RUN_DIR`, затем запустить:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Существующие переменные окружения имеют приоритет над `.env`. `AML_FIXTURE_MODE=true` и непустой `AML_RUN_DIR` одновременно запрещены. `.env`, ключи, `data/`, `artifacts/`, Parquet и виртуальные окружения исключены из Git.

**Команда полного пересчёта пока не реализована.** Её владелец A; планируемый интерфейс указан в [плане аналитики](docs/superpowers/plans/2026-09-23-money-graph-a-analytics.md). До получения трёх настоящих parquet, согласования правил и интеграции A воспроизводимость расчёта и ограничения времени не заявляются.

## API и интеграция команды

| Маршрут | Назначение |
|---|---|
| `GET /api/health` | Готовность текущего запуска |
| `GET /api/nodes/{gid}` | Точная карточка строкового gid |
| `GET /api/priorities?limit=20&offset=0` | Приоритеты, score убывает, затем gid возрастает |
| `GET /api/clusters` и `/api/clusters/{cluster_id}` | Список и карточка кластера |
| `GET /api/graph` | Ровно один `gid`, `cluster_id` или `component_id`; radius 1/2, limit 1–300 |
| `GET /api/exports/{name}` | Только ключ из `manifest.files`, например `roles`, `priorities`, `clusters` |
| `POST /api/ai/explain` | Объяснение выбранного узла/кластера или локальная справка |
| `POST /api/ai/investigate` | Вопрос по выбранному узлу/кластеру или локальная справка |

Граф обходит связи в обе стороны, сохраняет направление рёбер и выбранный узел. Усечение сортируется по расстоянию, priority_score и gid; ответ сообщает `truncated`, `total_nodes`, `shown_nodes`. Метрики карточек берутся из полного расчёта. `null` сохраняется отдельно от нуля, денежные строки не преобразуются в float.

Все результаты связаны `contract_version="1.0"` и `run_id`. Ошибки имеют форму `{error:{code,message},run_id}`: 404 — неизвестный id/экспорт, 422 — параметры, 503 — нет завершённого расчёта, 409 — устаревший run_id AI-запроса. Заголовки API: `X-Data-Source`, `X-Contract-Version`, `X-Run-Id`, `Cache-Control: no-store`. В `X-Run-Id` специальные и Unicode-символы percent-encoded; JSON содержит исходную строку без изменения.

- **A:** модели в `backend/app/contracts.py`; `ResultStore.check_concentration` вызывает `backend.app.analytics.checks.check_concentration(gid, edges)`. Формула не продублирована. Пока A отсутствует, Python-вызов даёт `AnalyticsUnavailableError`.
- **C:** [снимок OpenAPI](contracts/openapi.json) и [общие примеры](tests/fixtures/contract-v1/). Dev proxy: `/api` → `http://127.0.0.1:8000`. Разрешён CORS-origin `http://localhost:5173`; другой локальный origin задаётся `AML_FRONTEND_ORIGIN`. После сборки `frontend/dist/index.html` FastAPI автоматически раздаёт этот каталог и SPA-маршруты. Реальной сборки C сейчас нет.
- **D:** реализовать `backend.app.ai.service.explain(request, store)` и `investigate(request, store)` как async-функции, возвращающие `AIResponse`. HTTP-слой проверяет существование target и текущий run_id до вызова. Доступны методы Store из контракта, типы `ExplainRequest`, `InvestigateRequest`, `AIResponse`.

Пока D не подключён, оба AI-маршрута возвращают `status="fallback"`, готовые локальные объяснения, ограничения и пустые `claims/checks`; внешних запросов нет. Ошибка сервиса, неверный ответ, чужой run_id и тайм-аут также дают локальную справку. Общий срок ожидания задаётся `AI_TIMEOUT_SECONDS` (не более 30). Провайдер, проверка evidence_ids, чисел и лимита трёх функций остаются ответственностью D. `AI_PROVIDER`, `AI_MODEL`, ключи и `AI_MAX_TOOL_CALLS` в `.env.example` зарезервированы для его интеграции; сейчас они сами по себе провайдера не включают.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pytest tests/api -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m backend.app.openapi --check
```

Для обновления снимка после согласованного изменения моделей: `.\.venv\Scripts\python.exe -m backend.app.openapi`. API-тесты используют только искусственные данные и имитации сервиса, без платных запросов. Применены [официальные рекомендации FastAPI по TestClient/lifespan](https://fastapi.tiangolo.com/advanced/testing-events/) и [строгой конфигурации Pydantic](https://docs.pydantic.dev/latest/api/config/).

Фактическая проверка 23.09.2026: установка из `requirements.lock` во второе чистое окружение `.venv-verify`; 102 API-теста прошли, `pip check` не нашёл конфликтов, OpenAPI совпал. Есть одно предупреждение устаревшего alias в Starlette/AnyIO, на проверки не влияет. Это проверка backend на синтетике, не бенчмарк аналитики.

Проверки настоящих counts, топа ≥20, интеграции реального frontend/AI и полного расчёта остаются до получения частей A/C/D. Рост до порядка миллиона узлов требует пакетной агрегации и отдельного компактного хранилища графа; текущий Store держит один небольшой набор и байты выгрузок в памяти, такая масштабируемость не измерялась.

## Контекст и планы

- [Архитектура](docs/superpowers/specs/2026-09-23-money-graph-design.md)
- [Контракт данных и API v1](docs/contracts/money-graph-v1.md)
- [Общий план команды](docs/superpowers/plans/2026-09-23-money-graph-parallel.md)
- [A — аналитика](docs/superpowers/plans/2026-09-23-money-graph-a-analytics.md)
- [B — backend](docs/superpowers/plans/2026-09-23-money-graph-b-backend.md)
- [C — frontend](docs/superpowers/plans/2026-09-23-money-graph-c-frontend.md)
- [D — AI](docs/superpowers/plans/2026-09-23-money-graph-d-ai.md)
