# Контракт данных и функций v1

Предложение для общего старта команды. Владелец: участник B (backend). Изменение поля согласуется с его поставщиком и потребителями до изменения кода. Пути ниже относительны корню командного репозитория.

## Общие соглашения

- `gid`, `cluster_id`, `component_id`, `run_id` — строки. Исходный строковый gid сохраняется без потери ведущих нулей.
- Суммы — десятичные строки в KZT, например `"15000.00"`; в Python расчёт в точном десятичном представлении или целых минимальных денежных единицах после проверки исходного масштаба. Двоичный float не является эталоном сверки денежных сумм.
- Количества — целые неотрицательные числа. Отношения — конечные числа либо `null`, когда знаменатель нулевой/показатель неприменим. JSON NaN и Infinity запрещены.
- Все score в диапазоне 0–100. `role_score` — определённая правилами мера соответствия роли; `priority_score` — определённый формулой порядок проверки. Это не вероятности.
- `null` означает неизвестно/неприменимо; 0 — известный ноль. Пустой массив означает известное отсутствие элементов.
- Обязательные ключи присутствуют даже при `null`. Дополнительные поля добавляются через согласование контракта.
- Любой ответ с результатами содержит `contract_version: "1.0"` и `run_id`. Пагинация и сортировка детерминированы; при равенстве score используется gid по возрастанию.

## NodeRecord

| Поле | Тип и смысл |
|---|---|
| `gid` | string |
| `component_id`, `cluster_id` | string |
| `role` | одна из шести ролей из брифа |
| `role_score`, `priority_score` | number 0–100 |
| `assignment_status` | `rule_matched` или `insufficient_evidence` |
| `metrics` | объект фиксированных метрик ниже |
| `quality` | объект качества ниже |
| `role_evidence` | массив Evidence |
| `priority_evidence` | массив Evidence |
| `role_explanation`, `priority_explanation` | string: локальные объяснения по шаблонам |

`metrics`: `in_degree_unique`, `out_degree_unique`, `tx_in_count`, `tx_out_count` — integer; `in_amount_kzt`, `out_amount_kzt` — decimal string; `out_in_ratio` — number или null. `out_in_ratio = out_amount / in_amount` при положительном in_amount; может быть больше 1. Он не доказывает пересылку тех же денег. Дополнительные метрики добавляет A через B до использования в правилах.

`quality`: `is_seed` boolean|null; `hop_depth` integer|null; `outbound_censored` boolean|null; `inbound_incomplete` boolean|null; `reasons` string[]. `outbound_censored` означает ограничение наблюдения обходом, а не установленное отсутствие исходящих в реальности.

`Evidence`: `rule_id` string; `metric` string; `operator` одно из `gt/gte/lt/lte/eq`; `actual` number|string|boolean|null; `threshold` number|string|boolean; `passed` boolean|null. Денежные actual/threshold передаются десятичными строками. Порядок AND/OR и связи между условиями задаёт конфигурация правила, идентифицируемая `rule_id`.

При недостаточных данных договорённость для контракта: `role="peripheral"`, `assignment_status="insufficient_evidence"`, `role_score=0`, объяснение «Недостаточно наблюдаемых данных для более специфической роли». Это техническая остаточная категория, не доказанная периферийность. Другие применимые правила могут назначать специфическую роль; окончательное разрешение конфликтов определяет утверждённая конфигурация.

## Остальные записи

- `EdgeRecord`: `source` string, `target` string, `amount_kzt` decimal string, `tx_count` integer. Один агрегат на направленную пару. Суммы разных исходных таблиц не складываются повторно.
- `ClusterRecord`: `cluster_id`, `component_id` string; `gids` string[]; `hypothesis` объект `{text: string, basis_rule_ids: string[], limitations: string[]}`. Hypothesis создаётся ядром по утверждённым признакам. Слабосвязная компонента и кластер — отдельные понятия.
- `RunManifest`: `contract_version`, `run_id` string; `status="complete"`; `input_hashes` map filename→sha256; `rules_hash` string; `counts` map string→integer; `elapsed_seconds` number; `stage_seconds` map string→number; `random_seed` integer; `versions` map package→version; `files` map logical_name→relative_path; `warnings` string[].

## Обязательные артефакты одного запуска

Каталог `artifacts/<run_id>/`:

- `nodes.json` — массив всех NodeRecord, канонический источник карточек.
- `edges.json` — массив всех EdgeRecord для наблюдаемого графа.
- `clusters.json` — массив всех ClusterRecord.
- `priorities.csv` — все узлы в порядке priority_score, колонки `rank,gid,role,role_score,priority_score,priority_explanation`; первые 20 — обязательный топ.
- `roles.csv` — `gid,role,role_score,assignment_status,role_explanation` для всех узлов.
- `rules.json` — точная конфигурация использованных предикатов, score, конфликтов, кластерных гипотез и приоритета.
- `audit.json` — проверки схемы, ссылочной целостности, сверки таблиц, ограничений выборки и фактические количества.
- `manifest.json` — RunManifest. Записывается последним и отмечает завершённый набор.

Измерение времени начинается до чтения parquet и заканчивается после закрытия manifest.json; окончательное значение времени фиксируется в stdout и отчёте бенчмарка. `elapsed_seconds` в manifest отражает время до его финальной записи. Идентификатор запуска связан с входом и конфигурацией; один законченный набор результатов неизменяем.

Необходимые поля явно сопоставлены в `config/input_mapping.json` по полученным Parquet: `gid/depth/is_seed`, `src/dst/sum_kzt/n_tx`, `src/dst/sum_kzt/date`. Отсутствующая требуемая колонка останавливает запуск с именем файла и поля. Исходные float64 переводятся по явной политике `decimal_string` без округления в целые тиыны; точность и сверка агрегатов проверяются.

Дополнительные экспорты реального расчёта: `nodes_roles` → `nodes_roles.csv`, `clusters_csv` → `clusters.csv`, `top_nodes` → `top_nodes.csv` в схеме starter; score в этих CSV масштабирован в 0–1, внутренние модели сохраняют 0–100. `temporal` и `diagnostics` содержат дополнительные признаки и промежуточные значения формулы. `manifest` разрешён самим `manifest.files`.

## Интерфейс Python: владелец A

```python
def run_pipeline(input_dir: Path, output_dir: Path, rules_path: Path,
                 mapping_path: Path) -> RunManifest: ...

def check_concentration(gid: str, edges: list[EdgeRecord]) -> dict: ...
```

`check_concentration` возвращает `gid`, `total_out_kzt` decimal string, `top_receiver_gid` string|null, `top_receiver_share` number|null, `receiver_count` integer. Доля = сумма максимального получателя / общая исходящая сумма при положительном знаменателе. При равенстве сумм выбирается лексикографически первый gid. Самопереводы отдельно помечаются аудитом; политика их включения фиксируется в rules.json и одинаково применяется к метрикам и проверке.

## Интерфейс HTTP: владелец B

FastAPI/Pydantic-модели — единый источник схем HTTP; `contracts/openapi.json` фиксирует согласованный снимок. Frontend хранит производные типы в `frontend/src/types/api.ts`.

| Метод и путь | Параметры | Ответ |
|---|---|---|
| `GET /api/health` | — | `{status:"ok", contract_version:"1.0", run_id:string|null, data_ready:boolean}` |
| `GET /api/nodes/{gid}` | точный gid | `{contract_version,run_id,node:NodeRecord}` |
| `GET /api/priorities` | `limit=20`, `offset=0` | `{contract_version,run_id,items:NodeRecord[],total:integer}` |
| `GET /api/clusters` | — | `{contract_version,run_id,items:ClusterRecord[]}` |
| `GET /api/clusters/{cluster_id}` | — | `{contract_version,run_id,cluster:ClusterRecord}` |
| `GET /api/graph` | ровно один из `gid/cluster_id/component_id`; `radius=1` или 2 для gid; `limit=300` (1–300) | `{contract_version,run_id,nodes:NodeRecord[],edges:EdgeRecord[],truncated:boolean,total_nodes:integer,shown_nodes:integer}` |
| `GET /api/exports/{name}` | разрешённое имя из manifest.files | файл текущего запуска |
| `POST /api/ai/explain` | `{run_id,target:{kind:"node"|"cluster",id:string}}` | AIResponse |
| `POST /api/ai/investigate` | тот же target и `question:string` | AIResponse |

Граф окружения обходит наблюдаемые связи в обе стороны для навигации и сохраняет направления отображаемых рёбер. Радиус относится к выбранному узлу, а не к исходным четырём коленам. При отсечении выбранный узел сохраняется; остальные выбираются по расстоянию, priority_score убыванию, gid. Показываются только рёбра между показанными узлами. Карточные метрики всегда взяты из полного расчёта, а не пересчитаны на отфильтрованном экране. `total_nodes` — размер выбранной области до ограничения отображения.

Ошибка JSON: `{error:{code:string,message:string},run_id:string|null}`. Неизвестный gid/cluster — 404; нет законченного запуска — 503; некорректные параметры — 422; устаревший run_id AI-запроса — 409. Экспорт принимает имя из списка, а не произвольный путь.

## AIResponse и функции: владелец D, интеграция B

`AIResponse`: `contract_version`, `run_id`; `status="ok"|"fallback"`; `summary` string; `claims` массив `{text:string,evidence_ids:string[]}`; `limitations` string[]; `checks` массив `{tool:string,args:object,result:object,evidence_id:string}`; `fallback_reason` string|null.

Сервер выдаёт идентификаторы оснований из текущей карточки/проверки. Ссылки модели на несуществующие основания отклоняются. Обязательные объяснения показываются независимо от AI. Проверка ссылок не доказывает истинность всего свободного текста; числа сверяются с результатами, спорные причинные выводы помечаются гипотезами.

Разрешённые имена и аргументы:

```text
get_node(gid: string)
get_neighbors(gid: string, radius: integer=1, limit: integer=50)
get_cluster(cluster_id: string)
check_concentration(gid: string)
```

Первые три функции используют те же модели и хранилище, что HTTP. `check_concentration` вызывает функцию A. На вопрос выполняется максимум три функции, включая вызовы из одного пакета модели; четвёртая не исполняется. Общий предел AI-запроса предлагается 30 секунд. Сервер проверяет имена функций, типы аргументов, существование идентификаторов и run_id до исполнения. Для explain внешний вызов один, без цикла функций. При отсутствии ключа, тайм-ауте, ошибке провайдера или невалидном ответе используется локальная справка с `status="fallback"`.

## Совместные тестовые примеры

B создаёт `tests/fixtures/contract-v1/`: небольшие искусственные NodeRecord/EdgeRecord/ClusterRecord, manifest и AIResponse, включая нулевой вход, обрезанный узел, строковый gid `"0007"` и один изолированный узел. Это данные разработки, явно помеченные искусственными. Фикстуры не используются как результаты production-пайплайна. Действительные gid для демо выбираются после расчёта.
