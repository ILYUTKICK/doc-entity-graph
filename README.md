# Построение графа сущностей документов (MinerU)

Проект строит граф сущностей из неструктурированных документов. На вход подаются PDF/DOCX/PPTX, на выходе получается интерактивная HTML-визуализация, GraphML для Gephi и JSON с метриками.

Основной сценарий: MinerU парсит документ в Markdown/JSON, Phase 1 сохраняет текстовые блоки и структурные элементы документа, текст режется на семантические чанки, NER извлекает сущности, затем строятся два графа: baseline-граф сущностей по совместной встречаемости и linking-граф, связывающий сущности с чанками, графиками, подписями и таблицами.

## Архитектура

```text
PDF/DOCX/PPTX
      |
      v
MinerU parsing -> Markdown + JSON blocks + structured elements
      |
      v
Semantic chunking -> *_chunked.json
      |
      v
NER: SpaCy baseline + optional GLiNER
      |
      v
Entity resolution + noise filtering
      |
      v
NetworkX graph -> HTML / GraphML / JSON
      |
      v
Document linking graph -> Entity / Chunk / Figure / Caption / Table
```

## Быстрый старт

```bash
bash scripts/setup_env.sh
```

Скрипт создаёт окружение `doc-graph` через conda. Если conda не установлена, будет создана локальная `.venv`.

Если нужно только проверить структуру без скачивания SpaCy-моделей:

```bash
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

Для короткого англоязычного smoke-прогона можно скачать только маленькую английскую модель:

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
```

## Входные данные

Положи документы в `data/raw/`:

```text
data/raw/
├── report.pdf
├── lecture.docx
└── slides.pptx
```

Содержимое `data/raw/`, `data/parsed/`, `data/chunked/`, `data/entities/` и `data/graph/` игнорируется git-ом. В репозитории остаются только `.gitkeep`, чтобы структура директорий была воспроизводимой.

## Запуск

Полный пайплайн:

```bash
bash scripts/run_pipeline.sh
```

Если HuggingFace нестабилен или MinerU падает на скачивании `opendatalab/PDF-Extract-Kit-1.0`, переключи источник моделей на ModelScope:

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh
```

Фаза 1 запускает MinerU через текущий Python (`python -m mineru.cli.client`), поэтому важно активировать правильное окружение:

```bash
conda activate doc-graph
which python
python --version
```

Ожидаемый Python:

```text
/Users/ilyutkinn/anaconda3/envs/doc-graph/bin/python
Python 3.11.x
```

Если нужно явно указать бинарник MinerU, можно использовать `MINERU_BIN`:

```bash
MINERU_BIN="/Users/ilyutkinn/anaconda3/envs/doc-graph/bin/python -m mineru.cli.client" \
MINERU_MODEL_SOURCE=modelscope \
bash scripts/run_pipeline.sh
```

С параметрами:

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1
```

Аргументы:

```text
1. input_dir       папка с исходными документами, по умолчанию data/raw
2. backend         backend MinerU: pipeline, vlm, hybrid, auto
3. max_tokens      максимальный размер чанка
4. min_edge_weight минимальный вес ребра в очищенном графе. Для одного документа или smoke-запуска ставь `1`; для большого корпуса можно пробовать `2+`.
```

NER-движок можно выбрать через переменную окружения:

```bash
NER_ENGINE=spacy bash scripts/run_pipeline.sh
NER_ENGINE=gliner bash scripts/run_pipeline.sh
```

По умолчанию `run_pipeline.sh` делает clean run: очищает старые промежуточные файлы в `data/parsed/`, `data/chunked/`, `data/entities/`, `data/graph/` и `outputs/`, но не трогает `data/raw/`. Чтобы сохранить старые артефакты:

```bash
CLEAN_RUN=0 bash scripts/run_pipeline.sh
```

Пошаговый запуск:

```bash
python src/phase1_parsing.py -i data/raw/ -o data/parsed/ -b pipeline
python src/phase2_chunking.py -i data/parsed/ -o data/chunked/ --max-tokens 512
python src/phase3_ner.py -i data/chunked/ -o data/entities/ --engine spacy
python src/phase_cleanup_rebuild.py -e data/entities/ -c data/chunked/ -o outputs/ --min-edge-weight 2
python src/phase5_linking.py -e data/entities/ -c data/chunked/ -p data/parsed/ -o outputs/
```

## Результаты

После успешного запуска основные артефакты появятся в `outputs/`:

```text
outputs/
├── entity_graph_clean.html
├── entity_graph_clean.graphml
├── entity_graph_clean.json
├── document_links.html
├── document_links.graphml
├── document_links.json
├── linking_metrics.json
├── resolved_entities_clean.json
└── graph_metrics_clean.json
```

Открыть граф:

```bash
open outputs/entity_graph_clean.html
open outputs/document_links.html
```

Метрики для отчёта лучше брать из `outputs/graph_metrics_clean.json`; README не фиксирует числа, потому что они зависят от набора документов, backend MinerU, NER-движка и порогов фильтрации.

Phase 1 сохраняет в `data/parsed/*_parsed.json` два слоя:

- `blocks` — совместимый текстовый слой для чанкинга.
- `elements` — структурный слой MinerU: `title`, `text`, `figure`, `caption`, `table`, `formula`, `list` с `page_number`, `bbox`, `image_path`, `table_html`, `ref_label` и простыми связями `caption_of`.

Phase 2 сохраняет provenance в `data/chunked/*_chunked.json`: каждый чанк содержит `source_blocks`, `source_element_ids`, `source_elements`, `related_element_ids` и `related_elements`. Это позволяет связать текст чанка с ближайшими графиками, подписями и таблицами.

Phase 3 переносит provenance на сущности в `data/entities/*_entities.json`: каждая сущность хранит `page_start/page_end`, `section_title`, `block_indices`, `source_element_ids` и `related_element_ids`. Благодаря этому можно проверить, что сущность была извлечена из текста, связанного с конкретным графиком или таблицей.

Phase 5 строит linking-граф документа в `outputs/document_links.*`. В нём есть явные связи `Entity -> MENTIONED_IN -> Chunk`, `Entity -> DISCUSSED_NEAR -> Figure/Table`, `Chunk -> RELATED_TO -> Figure/Caption/Table` и `Figure -> HAS_CAPTION -> Caption`.

## Как это отвечает задаче

Проблема обычного RAG/чанкинга в том, что текстовый чанк может оказаться далеко от связанного графика, подписи или таблицы. Тогда модель видит кусок текста, но теряет структурное окружение документа.

В этом проекте MinerU используется не только как OCR/Markdown-парсер, а как источник структуры документа:

- Phase 1 извлекает `figure`, `caption`, `table`, `title` и связывает подписи с графиками.
- Phase 2 сохраняет, какие блоки и элементы породили каждый чанк.
- Phase 3 переносит этот provenance на найденные сущности.
- Phase 5 строит linking-граф, где видно, какая сущность обсуждается рядом с каким рисунком, подписью или таблицей.

На текущем документе найдено 14 графиков, 14 подписей и 14 связей `Figure -> HAS_CAPTION -> Caption`; linking-граф содержит 61 связь `Entity -> DISCUSSED_NEAR -> Figure`.

## Проверка

Минимальные smoke-тесты не требуют скачанных моделей:

```bash
python -B -m unittest discover -s tests
```

Проверка, что после запуска пайплайна появились все ключевые артефакты и что provenance не потерялся:

```bash
bash scripts/check_outputs.sh
```

Проверка синтаксиса:

```bash
python -B -m py_compile src/phase1_parsing.py src/phase2_chunking.py src/phase3_ner.py src/phase4_graph.py src/phase_cleanup_rebuild.py src/phase5_linking.py
```

## Конфигурация

Базовые параметры эксперимента лежат в `configs/default.yaml`, GLiNER-labels - в `configs/ner_labels.yaml`. Сейчас CLI-скрипты принимают параметры напрямую; конфиги добавлены как единая точка правды для отчёта и будущей автоматизации.

## Структура

```text
doc-entity-graph/
├── README.md
├── requirements.txt
├── requirements-vlm.txt
├── .gitignore
├── src/
│   ├── __init__.py
│   ├── phase1_parsing.py
│   ├── phase2_chunking.py
│   ├── phase3_ner.py
│   ├── phase4_graph.py
│   ├── phase5_linking.py
│   └── phase_cleanup_rebuild.py
├── configs/
│   ├── default.yaml
│   └── ner_labels.yaml
├── docs/
│   ├── architecture.md
│   ├── pipeline.md
│   └── results.md
├── notebooks/
├── data/
│   ├── raw/
│   ├── parsed/
│   ├── chunked/
│   ├── entities/
│   └── graph/
├── outputs/
│   └── figures/
├── scripts/
│   ├── setup_env.sh
│   ├── run_pipeline.sh
│   └── check_outputs.sh
└── tests/
```

## Ограничения

Связи в графе сейчас являются baseline-связями по совместной встречаемости сущностей в одном чанке. Это не полноценный relation extraction. Для учебного проекта это хороший воспроизводимый baseline, но в отчёте стоит явно отделять co-occurrence graph от графа фактических отношений.

## Лицензия

MIT
