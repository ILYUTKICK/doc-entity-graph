# Архитектура

Проект устроен как воспроизводимый пайплайн `документ -> граф`.

## Phase 1: Парсинг

Точка входа: `src/phase1_parsing.py`

Вход: исходные документы из `data/raw/` или другой входной папки.

Выход: `data/parsed/*_parsed.json`.

Фаза вызывает MinerU и нормализует результат в устойчивую JSON-схему:

```text
source
source_hash
total_pages
backend
parse_time_sec
metadata
blocks[]
elements[]
full_markdown
```

`blocks` — текстовый слой документа. Каждый блок хранит текст, номер страницы, координаты `bbox` и индекс блока.

`elements` — структурный слой документа. В нём хранятся заголовки, текстовые элементы, рисунки, подписи, таблицы, формулы и связи подписей с рисунками/таблицами.

Этот provenance важен, потому что следующие фазы могут восстановить, из какой части документа появился чанк, сущность или связь в графе.

## Phase 2: Чанкинг

Точка входа: `src/phase2_chunking.py`

Вход: `data/parsed/*_parsed.json`.

Выход: `data/chunked/*_chunked.json`.

Фаза группирует блоки по разделам документа и режет длинные секции на чанки. Чанки сохраняют:

- заголовок секции;
- диапазон страниц;
- индексы исходных блоков;
- признак overlap;
- ссылки на исходные структурные элементы.

Каждый чанк также хранит:

- `source_blocks`
- `source_element_ids` / `source_elements`
- `related_element_ids` / `related_elements`

Это мост между обычными текстовыми чанками и структурой документа: рисунками, таблицами и подписями.

## Phase 3: NER

Точка входа: `src/phase3_ner.py`

Вход: `data/chunked/*_chunked.json`.

Выход: `data/entities/*_entities.json`.

Доступные NER-движки:

- `spacy` — быстрый baseline для стандартных типов сущностей;
- `gliner` — zero-shot извлечение доменных сущностей;
- `llm` — опциональный API-based путь для извлечения сущностей.

Сущности наследуют provenance чанка:

- диапазон страниц;
- секцию;
- исходные блоки;
- `source_element_ids`;
- `related_element_ids`.

Так можно отследить сущность обратно к тексту, таблице, графику или подписи.

## Phase 4: Baseline-граф

Точка входа: `src/phase4_graph.py`

Вход: `data/entities/*_entities.json`.

Выход: файлы графа в `data/graph/`.

Эта фаза строит граф совместной встречаемости: две сущности соединяются ребром, если они встретились в одном чанке.

Это baseline, а не полноценное извлечение смысловых отношений.

## Clean Graph Rebuild

Точка входа: `src/phase_cleanup_rebuild.py`

Вход: `data/entities/` и `data/chunked/`.

Выход: финальные артефакты в `outputs/`.

Фаза:

- фильтрует шумные SpaCy-сущности;
- опционально добавляет GLiNER-сущности;
- объединяет дубли;
- пересобирает чистый entity-граф;
- экспортирует HTML, GraphML, JSON и метрики.

## Phase 5: Linking-граф Документа

Точка входа: `src/phase5_linking.py`

Вход:

```text
data/entities/
data/chunked/
data/parsed/
```

Выход:

```text
outputs/document_links.html
outputs/document_links.graphml
outputs/document_links.json
outputs/linking_metrics.json
```

Фаза строит гетерогенный направленный граф с явными связями:

```text
Entity -> MENTIONED_IN -> Chunk
Entity -> DISCUSSED_NEAR -> Figure/Table/Caption
Chunk  -> RELATED_TO -> Figure/Table/Caption
Figure -> HAS_CAPTION -> Caption
```

Linking-граф отделён от baseline-графа совместной встречаемости. Он показывает, как структурный парсинг MinerU помогает восстановить контекст вокруг рисунков, таблиц и подписей даже после разбиения текста на чанки.
