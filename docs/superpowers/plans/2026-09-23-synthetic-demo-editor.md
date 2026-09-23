# Synthetic Demo Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Запустить локальное демо на 500 узлах с человеческими именами, добавлением узлов и переводов через UI, согласованным пересчётом и сохранением данных между перезапусками.

**Architecture:** Канонический источник — замороженные записи узлов, групп и отдельных переводов. Генератор артефактов создаёт полный проверенный `ResultStore`; репозиторий публикует неизменяемые версии атомарным переключением указателя. API и frontend закрепляют одну версию данных, включая имена; существующий контракт аналитических записей 1.0 сохраняется.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, стандартная библиотека для файлов/денег/блокировок; React 19, TypeScript, Cytoscape, Vitest, Testing Library; существующий Docker Compose. Новые зависимости не требуются.

**Spec:** `docs/superpowers/specs/2026-09-23-synthetic-demo-editor-design.md` (согласована пользователем 2026-09-23 после дополнения об именах).

## Global Constraints

- 500 узлов, 16 слабосвязных компонент, одна компонента на 320 узлов, 8 изолированных узлов; остальные 172 узла распределены между 7 меньшими компонентами.
- Несколько тысяч отдельных переводов за июль 2026 в KZT, каждый не меньше 5000 KZT.
- Деньги суммируются в целых тиынах, наружу выдаются десятичными строками.
- Сумма имеет не более двух десятичных знаков; диапазон — от 5000 до 1 000 000 000 000 KZT включительно.
- `gid` остаётся неизменным уникальным ключом узла, а `display_name` — отдельным отображаемым именем.
- Вручную для ID принимаются 1–64 символа из латинских букв, цифр, `_` и `-`; начальные нули сохраняются.
- Имя после удаления пробелов по краям содержит 1–120 символов Unicode; управляющие символы запрещены.
- Схемы `NodeRecord`, существующих `nodes.json`, `roles.csv` и `priorities.csv` контракта 1.0 не меняются.
- Все ответы сохраняют `X-Data-Source: fixtures` в demo-режиме. Обычные фикстуры и настоящие артефакты не становятся редактируемыми.
- `config/rules.json` остаётся `pending_approval`; демонстрационные правила хранятся отдельно.
- Удаление, массовый импорт, редактирование уже сохранённых записей и сброс всех пользовательских добавлений в этот объём не входят.
- Области нажатия не меньше 44 px. Следовать текущему стилю интерфейса.

## Review Focus

1. Два одинаковых имени и строковые `0007`/`7`: интерфейс выбирает точный ID, а не первое имя; тесты задач 1 и 6.
2. Ответ потерялся после успешной записи, затем сервер перезапущен: повтор не добавляет второй перевод; тест задачи 3.
3. Добавление пересекается со старым обычным/AI-запросом: тело, имена и заголовки относятся к одному снимку; тест задачи 4.
4. Диск недоступен или каталог открыт вторым процессом: сохранённая версия не теряется и не заменяется новым демо; тест задачи 3.
5. Во время обновления формы приходит старая загрузка или 409: ввод и исходный запрос сохраняются, устаревшие имена/AI не появляются; тесты задач 5 и 6.

---

## Границы модулей и общие интерфейсы

Рабочий каталог — корень репозитория. Менять только файлы задачи; не включать чужие изменения в коммиты. Текущие изменения Docker и определения источника по `X-Data-Source` уже входят в `main` и сохраняются.

| Файл | Ответственность |
|---|---|
| `backend/app/demo/models.py` | Замороженные исходные записи и строго проверяемые команды/ответы demo API |
| `backend/app/demo/generator.py` | Детерминированная генерация исходных узлов, имён и переводов |
| `backend/app/demo/derive.py`, `rules.json` | Агрегация, компоненты, демокластеры, роли и приоритет |
| `backend/app/demo/build.py` | Каноническая сериализация исходных данных, артефактов и manifest |
| `backend/app/demo/locking.py`, `repository.py` | Единственный писатель, журнал принятых запросов, публикация версий |
| `backend/app/snapshots.py` | Общий снимок и выбор версии ответа без циклических импортов |
| `backend/app/api/demo.py` | Сведения о демо и две операции записи |
| `frontend/src/types/demo.ts`, `api/demo.ts` | Типы и HTTP-клиент демо |
| `frontend/src/state/useDemoMutation.ts` | Отправка, неопределённый исход, повтор и конфликт |
| `frontend/src/components/NodePicker.tsx`, `DemoEditor.tsx` | Общий выбор человека и формы добавления |
| `frontend/src/names.ts` | Поиск и подписи по справочнику текущей версии |
| `compose.demo.yaml`, `docs/demo.md` | Запуск с постоянным томом и краткая инструкция ручной проверки |

Python-сигнатуры, на которых строятся задачи:

```python
generate_source(seed: int = 20260923) -> DemoSource
derive(source: DemoSource) -> DerivedRecords
write_run(source: DemoSource, directory: Path, run_id: str) -> None

class DemoRepository:
    def __init__(self, directory: Path): ...
    def open(self) -> PublishedSnapshot: ...
    def close(self) -> None: ...
    @property
    def snapshot(self) -> PublishedSnapshot: ...
    def add_node(self, request: AddNodeRequest) -> MutationResult: ...
    def add_transfer(self, request: AddTransferRequest) -> MutationResult: ...
```

`PublishedSnapshot` — frozen dataclass в `snapshots.py`: `store: ResultStore`, `source: DemoSource | None`. Исходные модели заморожены рекурсивно, коллекции представлены tuples. `source=None` используется для обычных артефактов/фикстур. `MutationResult` содержит `response: MutationResponse` и `current: PublishedSnapshot`; повтор возвращает старый response, но current всегда указывает на актуальный набор. `DemoError` содержит `status_code`, `code`, `message`, `run_id` и не зависит от HTTP.

JSON-ответы API:

```json
{
  "contract_version": "1.0", "run_id": "fixture-demo-<uuid>",
  "enabled": true, "node_count": 500, "transfer_count": 6000,
  "nodes": [{"gid": "100001", "display_name": "Айдана Садыкова"}],
  "groups": [{"id": "group-main", "name": "Основная сеть"}],
  "limits": {"date_from": "2026-07-01", "date_to": "2026-07-31",
             "min_amount_kzt": "5000.00", "max_amount_kzt": "1000000000000.00"}
}
```

В примере сокращены массивы, но настоящий ответ `/api/demo` включает весь справочник. В read-only-режиме `enabled=false`, `nodes=[]`, `groups=[]`, `transfer_count=null`; при отсутствии загруженного набора `run_id=null`, `node_count=0`. Ответ операции: `{contract_version,run_id,created_id,select_gid,node_count,transfer_count}`. POST-поля точно соответствуют спецификации. Успех обеих операций — HTTP 200, запрещённый режим — 403 `demo_read_only`, неверное тело — 422 `invalid_request`, неизвестный узел/группа — 404 `not_found`, дубли — 409 `duplicate_node`/`request_id_conflict`, устаревший набор — 409 `stale_run`, ошибка диска — 503 `demo_write_failed`.

### Task 1: Исходные модели и правдоподобный генератор

**Files:** Create `backend/app/demo/{__init__,models,generator}.py`, `tests/demo/{conftest,test_generator,test_models}.py`; modify `pyproject.toml` testpaths.

**Interfaces:** Produces `DemoSource`, `SourceNode`, `SourceTransfer`, `SourceGroup`, `AcceptedRequest`, `AddNodeRequest`, `AddTransferRequest`, `DemoInfoResponse`, `MutationResponse`, `generate_source`.

- [ ] **Step 1: Зафиксировать модели и проверки тестами.** Исходный узел: gid, display_name, group_id, quality. Перевод: id, source, target, amount_kzt, date. DemoSource: seed, nodes, transfers, groups, accepted_requests; `AcceptedRequest` хранит request_id, request_hash и `MutationResponse`. Разделить frozen source-quality с reasons tuple и входной quality без reasons. Для команд применить строгую валидацию UUID-строки, даты `YYYY-MM-DD`, имени, суммы и неизвестных полей.

```python
def test_seed_reproduces_names_and_ledger():
    first = generate_source()
    assert first == generate_source()
    assert len(first.nodes) == 500
    assert len(first.transfers) == 6000
    assert {"0007", "7", "100001", "100010", "100020", "100030", "100040", "900001"} <= {n.gid for n in first.nodes}
    assert all(n.display_name.strip() for n in first.nodes)
    assert len({n.display_name for n in first.nodes}) < len(first.nodes)

@pytest.mark.parametrize("amount", ["4999.99", "1e10", "NaN", "5000.001", "1000000000000.01"])
def test_invalid_money_is_rejected(amount):
    with pytest.raises(ValidationError):
        AddTransferRequest.model_validate({
            "run_id": "fixture-demo-test", "request_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "source": "0007", "target": "7", "amount_kzt": amount, "date": "2026-07-01",
        })
```

Добавить отдельные параметризованные случаи пустого/121-символьного имени, управляющих символов, кириллицы/апострофа, даты `2026-07-32`, boolean вместо hop_depth, лишнего поля и самоперевода.

- [ ] **Step 2: Запустить тесты до реализации.** `.venv/bin/python -m pytest tests/demo/test_generator.py tests/demo/test_models.py -q`; ожидается ошибка отсутствующих новых модулей.
- [ ] **Step 3: Реализовать генератор из отдельных переводов.** Использовать `random.Random(seed)` и локальные списки имён/фамилий. Размеры компонент: `[320,32,28,26,24,22,20,20] + [1]*8`. Построить связный каркас каждой несинглетной компоненты, затем добавить переводы до 6000 только внутри неё. Защитить диагностические мотивы от случайных рёбер, меняющих их роль: `100010` получает от ≥3 контрагентов и отдаёт малую долю; `100020` передаёт полученную сумму `100030`; `100030` распределяет ≥3 адресатам с отношением ≥1.5; `100040` получает, не отправляет и имеет подтверждённые исходящие; `100001` имеет ≥3 входа и выхода; `900001` изолирован. Мотивы соединить с фоновым каркасом через разрешённые концы. Заполняющие переводы связывают обычные узлы. Суммы генерировать целыми тиынами:

```python
def money_text(tiyn: int) -> str:
    return f"{tiyn // 100}.{tiyn % 100:02d}"

rng = random.Random(seed)
amount = money_text(rng.randint(500_000, 250_000_000))
date = f"2026-07-{rng.randint(1, 31):02d}"
```

Все дополнительные поля качества генерируются осмысленно: есть true/false/null, seed и граница обхода. `gid` не преобразовывать в int. Автоматические ID новых узлов не создаются генератором — это ответственность репозитория.
- [ ] **Step 4: Прогнать тесты моделей/генератора.** Включить `tests/demo` в pytest testpaths, не добавляя PyArrow к основному окружению.
- [ ] **Step 5: Проверить diff и сохранить только файлы задачи.** Коммит `feat: generate named synthetic transaction sources`.

### Task 2: Пересчёт и полные артефакты

**Files:** Create `backend/app/demo/{derive,build}.py`, `backend/app/demo/rules.json`, `tests/demo/test_build.py`.

**Interfaces:** Consumes `DemoSource`; produces `DerivedRecords(nodes: tuple[NodeRecord,...], edges: tuple[EdgeRecord,...], clusters: tuple[ClusterRecord,...])`, `derive`, `write_run`. Existing `analytics.features.build_features` supplies the seven metrics.

- [ ] **Step 1: Проверить экономическую и структурную согласованность.** Записывать временный run с генератором, открывать его существующим Store, независимо складывать исходные суммы и счётчики. Проверить 16 компонент, максимум 320, 8 изолятов, все роли и правильные диагностические gid.

```python
def test_exported_run_matches_source(tmp_path):
    source = generate_source()
    write_run(source, tmp_path, "fixture-demo-test")
    store = ResultStore.from_directory(tmp_path)
    records = derive(source)
    assert len(records.nodes) == 500
    assert {n.role for n in records.nodes} == {"coordinator", "consolidator", "transit", "distributor", "terminal", "peripheral"}
    sizes = Counter(n.component_id for n in records.nodes)
    assert len(sizes) == 16 and max(sizes.values()) == 320
    assert sum(size == 1 for size in sizes.values()) == 8
    ledger_total = sum(Decimal(t.amount_kzt) for t in source.transfers)
    assert sum(Decimal(e.amount_kzt) for e in records.edges) == ledger_total
    assert sum(Decimal(n.metrics.in_amount_kzt) for n in records.nodes) == ledger_total
    assert sum(n.metrics.tx_in_count for n in records.nodes) == 6000
    assert store.run_id == "fixture-demo-test"
```

Для повторных рёбер создать source из узлов `0007`, `7` и переводов `5000.01`, `6000.02` в одном направлении, `7000.03` обратно; ожидать два ребра с суммами `11000.03`, `7000.03`, counts 2 и 1. На каждом отдельном наборе независимо проверить метрики обоих узлов. Для terminal проверить одинаковую структуру с outbound false/true/null; только false разрешает terminal.
- [ ] **Step 2: Запустить тесты.** `.venv/bin/python -m pytest tests/demo/test_build.py -q`; до реализации они должны падать.
- [ ] **Step 3: Реализовать производные записи и сериализацию.** Суммировать пары `(source,target)` целыми тиынами; передать агрегаты в build_features. Слабые компоненты — обход по неориентированным соседям в порядке gid; ID компоненты — SHA-256 от JSON отсортированных gid. Кластеры — пересечение group_id и component_id, с отдельным стабильным ID и честным предупреждением о демогруппировке.

```python
by_pair: dict[tuple[str, str], tuple[int, int]] = {}
for transfer in source.transfers:
    whole, _, fraction = transfer.amount_kzt.partition(".")
    tiyn = int(whole) * 100 + int(fraction.ljust(2, "0"))
    key = (transfer.source, transfer.target)
    amount, count = by_pair.get(key, (0, 0))
    by_pair[key] = (amount + tiyn, count + 1)
```

В `rules.json` скопировать согласованные для демо пороги из спецификации/методологии, порядок `coordinator > consolidator > distributor > transit > terminal > peripheral`, формулу Q/N и isolated override; derive читает именно эти параметры. Evidence содержит фактические значения и `demo.*` ID, а все тексты называют расчёт демонстрационным. Гипотезы кластеров описывают наблюдаемую структуру и ограничения.

`write_run` создаёт source.json, nodes.json, edges.json, clusters.json, roles.csv, priorities.csv, node_names.json, transfers.json, rules.json, audit.json, затем manifest.json. CSV покрывает всех узлов, приоритеты сортируются `(-priority_score,gid)`. `input_hashes` содержит SHA-256 source.json; rules_hash вычисляется по фактическим bytes. Manifest.files использует короткие логические ключи, включая manifest/node_names/source/transfers. Время этапов измеряется perf_counter, а генератор/правила имеют явную версию. Для воспроизводимости сравнивать source и содержательные записи, не UUID run_id/замеры времени.
- [ ] **Step 4: Проверить загрузку всех экспортов Store и тесты задачи.** Проверить leading-zero gid, clusters без пересечения компонент, rankings CSV, полный справочник имён, hash rules, роли всех шести примеров.
- [ ] **Step 5: Сохранить коммит `feat: derive consistent demo graph artifacts`.**

### Task 3: Сохранение, блокировки и идемпотентность

**Files:** Create `backend/app/demo/{locking,repository}.py`, `backend/app/snapshots.py`, `tests/demo/test_repository.py`.

**Interfaces:** Consumes source/build/models; produces `PublishedSnapshot`, `MutationResult`, `DemoError`, `DemoRepository` with signatures above. `SourceNode.quality` is always immutable. No repository function imports FastAPI.

- [ ] **Step 1: Написать тесты перезапуска и потерянного ответа.** Для тестовых запросов использовать uuid4; response — модель, не dict.

```python
def test_replay_after_restart_does_not_duplicate_transfer(tmp_path):
    repo = DemoRepository(tmp_path)
    snapshot = repo.open()
    command = AddTransferRequest.model_validate({
        "run_id": snapshot.store.run_id, "request_id": str(uuid4()),
        "source": "0007", "target": "7", "amount_kzt": "5000.01", "date": "2026-07-12",
    })
    accepted = repo.add_transfer(command).response
    repo.close()
    reopened = DemoRepository(tmp_path)
    try:
        reopened.open()
        assert reopened.add_transfer(command).response == accepted
        assert len(reopened.snapshot.source.transfers) == 6001
    finally:
        reopened.close()
```

Добавить реальные subprocess-тесты второго писателя и освобождения lock после завершения процесса. При двух thread-запросах на одном run_id с разными request_id ожидать ровно один успех и один stale_run. Через monkeypatch `os.replace` отказать только при переключении `current.json`, проверить старый run_id и состояние после повторного open. Повреждённый current/source/артефакт и непустой каталог без current дают ошибку, не новую генерацию. Тест тёзок добавляет другое gid с тем же display_name; тест request_id_conflict меняет сумму исходной команды.
- [ ] **Step 2: Запустить `.venv/bin/python -m pytest tests/demo/test_repository.py -q`.**
- [ ] **Step 3: Реализовать единственного писателя.** Держать process lock открытым весь lifespan: Unix `fcntl.flock(LOCK_EX|LOCK_NB)`, Windows `msvcrt.locking(LK_NBLCK)` на заранее существующем одном байте. Локальный `threading.Lock` сериализует операции. Проверка request_id/hash идёт до stale_run; hash включает тип операции и каноническое тело команды. Авто-ID `custom-000001` выбирается под lock. Новые узлы не имеют переводов, качество по умолчанию неизвестно.

```python
canonical = json.dumps(command.model_dump(mode="json"), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"))
request_hash = hashlib.sha256((kind + "\n" + canonical).encode()).hexdigest()
```

Каждая принятая команда копирует source, добавляет запись и receipt с будущим run_id `fixture-demo-{uuid4().hex}`; новый response содержит уже вычисленные counts. `_publish(source,run_id)` записывает `revisions/.staging-<uuid>`, закрывает/синхронизирует файлы, проверяет Store и соответствие source производным данным, переименовывает каталог и атомарно заменяет временный current.json. После pointer commit не выполнять действия, способные ошибочно вернуть «не сохранено»; замена in-memory snapshot выполняется синхронно до освобождения lock. Инициализировать только пустой каталог (lock-файл не считается данными). Ничего автоматически не удалять из старых revisions. Повтор возвращает сохранённый response и актуальный snapshot, не публикует старую версию.
- [ ] **Step 4: Прогнать все tests/demo.** Проверить, что все отклонённые команды сохраняют counts/run_id и что source/metadata нельзя мутировать через публичные ссылки.
- [ ] **Step 5: Коммит `feat: persist editable demo revisions atomically`.**

### Task 4: Demo API, запуск и закрепление снимка

**Files:** Create `backend/app/api/demo.py`, `tests/api/test_demo.py`, `tests/api/test_snapshot_publication.py`; modify `backend/{__main__.py,app/config.py,app/main.py,app/errors.py,app/api/dependencies.py}`, `contracts/openapi.json`.

**Interfaces:** GET /api/demo and two POST routes use models from Task 1. Settings adds `demo_mode: bool=False`, `demo_dir: Path|None=None`; environment `AML_DEMO_MODE`, `AML_DEMO_DIR`. `--demo` defaults directory to PROJECT_ROOT/data/demo; `--demo-dir` alone is invalid. CLI clears alternate fields when explicitly selecting fixtures/artifacts/demo. Config forbids combined modes.

- [ ] **Step 1: Написать HTTP-тесты.** Reuse `make_client` from tests/api/conftest.py. Validate all documented error codes, capabilities of ordinary fixtures, user names and leading zeros.

```python
def test_add_named_node(make_client, tmp_path):
    client = make_client(demo_mode=True, demo_dir=tmp_path / "demo")
    info = client.get("/api/demo").json()
    response = client.post("/api/demo/nodes", json={
        "run_id": info["run_id"], "request_id": str(uuid4()),
        "gid": "0000007", "display_name": "Айдана Садыкова",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["node_count"] == 501 and body["select_gid"] == "0000007"
    assert response.headers["x-run-id"] == body["run_id"]
    assert response.headers["x-data-source"] == "fixtures"
    assert {"gid": "0000007", "display_name": "Айдана Садыкова"} in client.get("/api/demo").json()["nodes"]
    assert "display_name" not in client.get("/api/nodes/0000007").json()["node"]
```

Для гонки закрепить старый снимок запросом с Event-барьером, опубликовать новый узел параллельным POST, затем отпустить старый запрос: его body/header/names остаются старыми. Повторить с задержанным AI service, используя паттерны `tests/api/test_merged_ai.py`. Отдельно replay после последующей записи возвращает старый response/header, но следующий health остаётся новым.
- [ ] **Step 2: Запустить новые API-тесты и увидеть отказ до подключения маршрутов.**
- [ ] **Step 3: Подключить репозиторий и единый снимок.** Lifespan открывает/закрывает repo, сохраняет совместимость app.state.store для существующих AI-тестов. Для demo авторитетный текущий snapshot берётся из repo.snapshot; для read-only хранится в app.state.snapshot. Middleware закрепляет snapshot до call_next. Health, get_store, errors читают request.state.snapshot; глобальный Store после ответа не читают. POST задаёт `request.state.response_run_id` из результата/ошибки под lock, middleware использует override, включая идемпотентный старый run.

```python
request.state.snapshot = (
    request.app.state.demo_repository.snapshot
    if request.app.state.demo_repository is not None
    else request.app.state.snapshot
)
request.state.response_run_id = None
```

Маршруты sync `def` выполняют файловую работу в FastAPI thread pool; не блокировать event loop async-маршрутом со sync publication. GET /api/demo строится только из pinned source. В ordinary mode допускается отсутствие Store и run_id=None. Ошибки незагруженного/corrupt demo видны в health/log и не маскируются новым seed. Перед catch-all подключить demo router, добавить 403 в OpenAPI. Сохранить existing guards от выдачи fixture-run как реального.
- [ ] **Step 4: Обновить OpenAPI и выполнить регрессии.** `.venv/bin/python -m backend.app.openapi`; `.venv/bin/python -m pytest tests/demo tests/api tests/ai -q`; `.venv/bin/python -m backend.app.openapi --check`.
- [ ] **Step 5: Коммит `feat: expose versioned demo editing API`.**

### Task 5: Клиент, состояние и надёжная отправка

**Files:** Create `frontend/src/{types/demo.ts,api/demo.ts,state/useDemoMutation.ts}`, `frontend/tests/demo-state.test.tsx`; modify `frontend/src/{api/client.ts,state/useWorkspace.ts}`, mocks in `frontend/tests/{workspace.test.tsx,data-source.test.tsx,serve-fixtures.mjs}`.

**Interfaces:** Export existing request helper as `requestJson<T>` and retain header handling. `demoApi.info(signal?)`, `addNode(command,signal?)`, `addTransfer(command,signal?)`; `DemoInfo` mirrors HTTP shape. Workspace dataset adds `demo: DemoInfo`, names derive from dataset only. `refresh(message?) -> Promise<string|null>` returns loaded run; `refreshAndSelect(gid) -> Promise<boolean>` refreshes latest and selects gid through existing generation/abort guards.

- [ ] **Step 1: Проверить общий snapshot и refresh/select.** Existing api mocks must explicitly mock demo info with matching run_id, enabled=false by default. For new tests reuse the record/response factories from workspace tests by moving shared test-only builders to `frontend/tests/helpers.ts` if necessary; existing assertions stay intact.

```tsx
it('refreshes the current run before selecting a newly created node', async () => {
  vi.spyOn(api, 'health').mockResolvedValue(healthResponse(NEXT_RUN));
  vi.spyOn(demoApi, 'info').mockResolvedValue(demoInfo(NEXT_RUN));
  vi.spyOn(api, 'priorities').mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [], total: 501 });
  vi.spyOn(api, 'clusters').mockResolvedValue({ contract_version: '1.0', run_id: NEXT_RUN, items: [] });
  vi.spyOn(api, 'node').mockResolvedValue(nodeResponse('custom-000001', NEXT_RUN));
  vi.spyOn(api, 'graph').mockResolvedValue(graphResponse('custom-000001', NEXT_RUN));
  const { result } = renderHook(() => useWorkspace());
  await waitFor(() => expect(result.current.dataset?.runId).toBe(NEXT_RUN));
  await act(async () => { await result.current.refreshAndSelect('custom-000001'); });
  expect(result.current.selection.node?.gid).toBe('custom-000001');
});
```

`demoInfo(runId)` returns version 1.0, supplied run ID, enabled true, counts, nodes/groups and exact limits above. Keep factory definitions in helpers.ts, not production code. Add mismatch in info run ID test and late old info after refresh test: workspace never shows mixed names.
- [ ] **Step 2: Запустить `npm --prefix frontend test -- demo-state`.**
- [ ] **Step 3: Реализовать загрузку и состояние мутации.** GET /api/demo joins priorities/clusters in the boot batch; all must match health.run_id. A 404 from an older read-only backend may degrade to disabled editor/empty names; other failures show load error, not fabricated capability. Successful POST may return an old run ID on replay: refresh latest without treating this as an API error.

`useDemoMutation` stores `{kind,command}` in a ref plus `pending`, `error`, `uncertain`. First submit assigns crypto.randomUUID and current run. Network/timeout sets uncertain and preserves exact command; retry sends the same command and ID, without rebuilding from edited form. While uncertain, lock fields and provide explicit “Повторить сохранение” plus explanation that the result is unknown. Closing the panel only hides it: keep the hook mounted and retain the unresolved command; reopening offers retry of that operation before starting another one. Known validation failure permits editing. On stale_run retain draft, refresh dataset after the user's explicit action, require explicit save with new run and new request_id. A duplicate-node error must not trigger stale-run refresh. After confirmed success clear pending receipt and call refreshAndSelect(response.select_gid); if refresh fails report “Сохранено, обновить данные” and never submit again as a new record.
- [ ] **Step 4: Проверить meaningful retry cases.** Mock first POST as network failure, second success, assert bodies strictly equal; stale_run followed by explicit save changes request_id/run_id; success with failed refresh does not repeat POST; double click calls POST once. Run all frontend tests and typecheck.
- [ ] **Step 5: Коммит `feat: keep demo editing state consistent across retries`.**

### Task 6: Формы, имена и поиск

**Files:** Create `frontend/src/{names.ts,components/NodePicker.tsx,components/DemoEditor.tsx}`, `frontend/tests/{demo-editor,node-names}.test.tsx`; modify `App.tsx`, `styles.css`, `components/{SearchPanel,PriorityTable,GraphPanel,NodeDetails,ClusterDetails,ExportMenu}.tsx`.

**Interfaces:** `NodeName = {gid:string;display_name:string}`; `searchNames(nodes,query) -> NodeName[]` exact gid first, then case-insensitive name/gid substring, stable tie order by display_name/gid. `NodePicker({label,nodes,value,onChange,disabled})` always emits gid. `DemoEditor({mode,info,onClose,onSaved,onConflict})` uses useDemoMutation; `mode` is `'node'|'transfer'|null`, null hides the panel without unmounting; onSaved delegates refreshAndSelect, onConflict delegates refresh with preserved form.

- [ ] **Step 1: Написать тесты поиска и форм.**

```tsx
it('distinguishes namesakes and preserves zero-padded IDs', () => {
  const nodes = [
    { gid: '0007', display_name: 'Александр Иванов' },
    { gid: '7', display_name: 'Александр Иванов' },
  ];
  expect(searchNames(nodes, 'ивАН')).toHaveLength(2);
  expect(searchNames(nodes, '7')[0].gid).toBe('7');
  const onChange = vi.fn();
  render(<NodePicker label="Получатель" nodes={nodes} value="" onChange={onChange} disabled={false} />);
  fireEvent.change(screen.getByRole('combobox', { name: 'Получатель' }), { target: { value: 'Иванов' } });
  fireEvent.click(screen.getByRole('option', { name: 'Александр Иванов · 0007' }));
  expect(onChange).toHaveBeenCalledWith('0007');
});
```

Добавить сценарии обязательного имени, сохранения кириллицы, переноса фокуса, Escape/Отмена, ошибок рядом с полями, суммы с запятой, выбора контрагента вне топ-20, одинаковых имён, 409 с сохранением ввода. Имя `<img src=x>` должно быть текстом, не DOM-элементом. Read-only capability скрывает кнопки независимо от VITE_DATA_SOURCE/fixture banner.
- [ ] **Step 2: Запустить новые frontend-тесты до компонентов.**
- [ ] **Step 3: Реализовать формы и подписи.** Кнопки в header видны только enabled=true. Editor остаётся смонтированным вне dataset conditional и не получает key=runId, чтобы refresh не стирал draft. Панель использует семантику dialog, фокус и возврат фокуса, клавиатуру и доступные ошибки. На desktop занимает область карточки, на mobile ширину экрана. Дополнительные поля качества раскрываются отдельно; значения bool/null представлены тремя понятными вариантами. Исходный run формы меняется только после явного разрешения конфликта.

```tsx
const displayName = names.get(node.gid);
<div className="node-identity">
  <strong>{displayName ?? node.gid}</strong>
  {displayName ? <small>ID: {node.gid}</small> : null}
</div>
```

GraphPanel сохраняет внутренний id `node:${gid}` и события по gid; Cytoscape data.label обновляется при изменении справочника даже если topology тот же. Selected/hover labels use display_name with visual truncation; full title/accessible selector shows name and ID. PriorityTable, NodeDetails, ClusterDetails, scope title and search results use the same lookup. AI target stays gid. ExportMenu conditionally adds node_names/source/transfers for demo while preserving eight existing exports. Суммы формы нормализовать строковыми операциями (запятая → точка), диапазон проверять через BigInt тиыны; не использовать Number/parseFloat для денежных значений.
- [ ] **Step 4: Выполнить frontend tests/build и браузерную проверку desktop/mobile.** Проверить открытие клавиатурой, Tab внутри панели, фокус после закрытия, длинное имя, сохранённый ввод после ошибки и видимость синтетического баннера в обычной production build.
- [ ] **Step 5: Коммит `feat: add named nodes and transfer editor to the workspace`.**

### Task 7: Постоянный Docker-том, инструкция и полный запуск

**Files:** Create `compose.demo.yaml`, `docs/demo.md`; modify `Dockerfile`, `.env.example`, `README.md`, `docs/docker.md`, `.github/workflows/docker.yml` to include demo tests in its existing backend test stage.

**Interfaces:** `python -m backend --demo [--demo-dir PATH]`; Compose override sets AML_DEMO_MODE=true, AML_DEMO_DIR=/app/data/demo, AML_FIXTURE_MODE=false, AML_RUN_DIR="". Existing default and artifacts modes stay read-only.

- [ ] **Step 1: Добавить runtime-проверку прав и сохранения.** Dockerfile creates `/app/data/demo` owned by UID 10001 before USER so a fresh named volume is initialized with writable ownership. Do not chmod the whole app or run as root. Compose file:

```yaml
services:
  app:
    environment:
      AML_DEMO_MODE: "true"
      AML_DEMO_DIR: /app/data/demo
      AML_FIXTURE_MODE: "false"
      AML_RUN_DIR: ""
    volumes:
      - demo-data:/app/data/demo
volumes:
  demo-data:
```

- [ ] **Step 2: Проверить compose config и образ.** `docker compose -f compose.yaml -f compose.demo.yaml config`; build existing test/runtime stages with `tests/demo` added. Run demo on a free loopback port, add node/transfer, recreate container with the same volume, verify unchanged records. Do not delete the volume. If Docker is unavailable, report the missing execution check explicitly and complete local tests.
- [ ] **Step 3: Написать инструкцию ручной проверки и запустить локально.** docs/demo.md includes launch commands, synthetic limits, fields, six stable example gids with actual generated names/metrics, add-node/add-transfer walkthrough, duplicate/retry errors, two-tab stale-run behavior, persistence location, synthetic-grouping explanation. README links this guide. User additions live under ignored data/demo, never in git. Inspect current listeners before replacing only this task's known backend process; reuse frontend on 5173. Start backend at 127.0.0.1:8000 using `.venv/bin/python -m backend --demo`; restart frontend if needed with API_PROXY_TARGET=http://127.0.0.1:8000.
- [ ] **Step 4: Полный приёмочный проход.**

```sh
.venv/bin/python -m pytest tests/demo tests/api tests/ai -q
/private/tmp/money-graph-a-venv/bin/python -m pytest tests/analytics -q
.venv/bin/python -m backend.app.openapi --check
npm --prefix frontend test
npm --prefix frontend run build
git diff --check
```

Сначала browser-test write scenarios on a separate temporary demo directory, leaving the user's persistent initial directory clean. Through UI create a named node, transfer, inspect exact totals, names search, graph, eight existing exports and names export, restart backend, verify persistence. In two tabs create competing writes, ensure conflict preserves draft and retry works. Check desktop and narrow screen, old fixture UI, production banner and AI fallback. Then open the user's persistent local demo at 5173. Record test counts and actual browser/Docker results in docs/demo.md without claiming unrun checks.
- [ ] **Step 5: Проверить интеграционный diff свежим reviewer и устранить найденные дефекты.** Review publication/retry races and persisted user data first, then names/UI. Save focused commit `feat: run persistent synthetic demo locally and in Docker`; final answer contains working local URL, a short walkthrough and any remaining runtime limitation.

## Порядок выполнения и проверка плана

Зависимости: 1 → 2 → 3 → 4; после согласования общих DTO подготовка 5 может идти независимо от вычислений; 6 зависит от 5; 7 завершает интеграцию. Одна задача не меняет чужие незавершённые файлы. Генератор/хранилище/API, frontend и проверку интеграции удобно поручить отдельным исполнителям с общими интерфейсами этого документа.

План проверен по спецификации: роли/качество/граф — задачи 1–2; локальное хранение и журнал — 3; режимы/заголовки/API — 4; имена, поля, поиск, тёзки и UX ошибок — 5–6; Docker, руководство, регрессии и ручной запуск — 7. Проверки всех пяти Review Focus привязаны к задачам. Изменения production-правил, реальный parquet pipeline и удаление записей в план не включены.

Статус: план подготовлен для проверки; реализация продукта не начата. Предпочтительный способ — выполнение в текущей сессии с разделением независимых модулей и итоговой независимой проверкой. Два дополнительных superpowers-навыка, названные в стандартном заголовке, не найдены среди установленных навыков; обычные инструменты исполнения и подагенты доступны. Для исполнения без этих отсутствующих навыков нужен явный выбор этого обычного способа пользователем.
