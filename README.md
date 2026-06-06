# Doc Entity Graph

Проект строит граф сущностей из неструктурированных документов. На вход подаются `PDF`, `DOCX` или `PPTX`, на выходе получаются интерактивные HTML-графы, `GraphML` для Gephi и JSON-файлы с метриками.

Главная идея: при обычном чанкинге документа легко потерять связь между текстом, таблицами, графиками и подписями. Здесь MinerU используется не только как OCR/парсер текста, но и как источник структуры документа. Эта структура сохраняется в чанках, переносится на сущности и затем используется для построения linking-графа.

## Что получится

После запуска проект строит два основных графа:

- `outputs/entity_graph_clean.html` — baseline-граф сущностей по совместной встречаемости в чанках.
- `outputs/document_links.html` — linking-граф документа: сущности, чанки, рисунки, таблицы и подписи.

В linking-графе есть связи:

- `Entity -> MENTIONED_IN -> Chunk`
- `Entity -> DISCUSSED_NEAR -> Figure/Table/Caption`
- `Chunk -> RELATED_TO -> Figure/Table/Caption`
- `Figure -> HAS_CAPTION -> Caption`

## Архитектура

```text
PDF/DOCX/PPTX
      |
      v
MinerU parsing -> text blocks + structured elements
      |
      v
Semantic chunking -> chunks with provenance
      |
      v
NER -> entities with provenance
      |
      +-----------------------------+
      |                             |
      v                             v
Entity co-occurrence graph     Document linking graph
```

## Требования

Минимально:

- macOS или Linux. На Windows лучше запускать через WSL.
- `git`
- `bash`
- Python 3.11
- доступ в интернет для первой установки зависимостей и моделей

Рекомендуется:

- `conda`, чтобы создать окружение `doc-graph`
- 4+ GB свободного места под зависимости и модели

Важно: MinerU может запускать локальный сервис на `127.0.0.1`. Если окружение запрещает локальные порты, Phase 1 может упасть с ошибкой вроде `PermissionError: bind`.

## Быстрый старт

Склонируй проект и перейди в папку:

```bash
git clone https://github.com/ILYUTKICK/doc-entity-graph.git
cd doc-entity-graph
```

Установи окружение. Обычный вариант:

```bash
bash scripts/setup_env.sh
```

Если `conda` установлена, скрипт создаст окружение `doc-graph`. Активируй его:

```bash
conda activate doc-graph
export PYTHON_BIN="$(which python)"
```

Если `conda` не установлена, скрипт создаст локальное `.venv`. Активируй его:

```bash
source .venv/bin/activate
export PYTHON_BIN="$(which python)"
```

Для более лёгкой установки можно вместо предыдущей команды скачать только маленькую английскую SpaCy-модель:

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
```

Если нужно поставить зависимости вообще без скачивания SpaCy-моделей:

```bash
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

## Воспроизводимое Демо

В репозитории не хранятся входные документы, потому что `data/raw/` и сгенерированные данные игнорируются git-ом. Для демонстрации есть генератор небольшого DOCX-документа с текстом, таблицами, графиками и подписями.

Создай демо-документ:

```bash
python scripts/create_teacher_demo_input.py
```

Скрипт создаст:

```text
data/raw_teacher_demo/demo_teacher_pipeline.docx
data/raw_teacher_demo/demo_teacher_pipeline_source.txt
```

Запусти полный пайплайн на демо-документе:

```bash
bash scripts/run_pipeline.sh data/raw_teacher_demo pipeline 512 1 12
```

Открой результаты:

```bash
open outputs/document_links.html
open outputs/entity_graph_clean.html
```

На Linux вместо `open` используй:

```bash
xdg-open outputs/document_links.html
xdg-open outputs/entity_graph_clean.html
```

Проверь артефакты:

```bash
bash scripts/check_outputs.sh
```

Подробный сценарий демонстрации лежит в `docs/teacher_demo.md`.

## Запуск на своих документах

Положи документы в `data/raw/`:

```text
data/raw/
├── report.pdf
├── lecture.docx
└── slides.pptx
```

Запусти пайплайн:

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Аргументы `run_pipeline.sh`:

```text
1. input_dir       папка с исходными документами, по умолчанию data/raw
2. backend         backend MinerU: pipeline, vlm, hybrid, auto
3. max_tokens      максимальный размер чанка
4. min_edge_weight минимальный вес ребра в entity-графе
5. max_entity_links_per_element top-N связей Entity -> Figure/Table/Caption в Phase 5
```

Для одного документа или короткого демо обычно подходит `min_edge_weight=1`. Для большого корпуса можно пробовать `2` и выше.

По умолчанию запуск очищает старые промежуточные файлы в `data/parsed/`, `data/chunked/`, `data/entities/`, `data/graph/` и `outputs/`, но не трогает исходные документы. Чтобы сохранить старые артефакты:

```bash
CLEAN_RUN=0 bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

## Переменные окружения

Можно менять поведение пайплайна через переменные:

```bash
PYTHON_BIN="$(which python)"
NER_ENGINE=spacy
CLEAN_RUN=1
MAX_ENTITY_LINKS_PER_ELEMENT=12
MINERU_MODEL_SOURCE=modelscope
```

Примеры:

```bash
NER_ENGINE=spacy bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Если нужно явно указать команду MinerU:

```bash
MINERU_BIN="$(which python) -m mineru.cli.client" \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

## Пошаговый запуск

Полный пайплайн можно запускать по фазам. Это удобно для отладки и демонстрации.

Подготовь входную папку:

```bash
export PYTHON_BIN="$(which python)"
python scripts/create_teacher_demo_input.py
export INPUT_DIR=data/raw_teacher_demo
```

### Phase 1: Parsing

```bash
$PYTHON_BIN src/phase1_parsing.py \
  -i "$INPUT_DIR" \
  -o data/parsed/ \
  -b pipeline
```

Что делает: запускает MinerU и сохраняет текстовые блоки и структурные элементы документа.

Выход:

```text
data/parsed/*_parsed.json
```

Важные поля:

- `blocks` — текстовый слой для чанкинга.
- `elements` — структурный слой: `title`, `text`, `figure`, `caption`, `table`, `formula`, `list`.

### Phase 2: Chunking

```bash
$PYTHON_BIN src/phase2_chunking.py \
  -i data/parsed/ \
  -o data/chunked/ \
  --max-tokens 512
```

Что делает: режет текст на чанки и сохраняет provenance.

Выход:

```text
data/chunked/*_chunked.json
```

Важные поля чанка:

- `source_blocks`
- `source_element_ids`
- `source_elements`
- `related_element_ids`
- `related_elements`

### Phase 3: NER

```bash
$PYTHON_BIN src/phase3_ner.py \
  -i data/chunked/ \
  -o data/entities/ \
  --engine spacy
```

Что делает: извлекает сущности и переносит provenance с чанков на сущности.

Выход:

```text
data/entities/*_entities.json
```

Важные поля сущности:

- `text`
- `normalized`
- `entity_type`
- `chunk_id`
- `section_title`
- `source_element_ids`
- `related_element_ids`

### Phase 4: Clean Entity Graph

```bash
$PYTHON_BIN src/phase_cleanup_rebuild.py \
  -e data/entities/ \
  -c data/chunked/ \
  -o outputs/ \
  --min-edge-weight 1
```

Что делает: чистит шумные сущности, опционально добавляет GLiNER, объединяет дубли и строит baseline-граф сущностей по совместной встречаемости.

Выход:

```text
outputs/entity_graph_clean.html
outputs/entity_graph_clean.graphml
outputs/entity_graph_clean.json
outputs/resolved_entities_clean.json
outputs/graph_metrics_clean.json
```

Если GLiNER или HuggingFace недоступны, эта фаза продолжит работу на очищенных SpaCy-сущностях.

### Phase 5: Document Linking Graph

```bash
$PYTHON_BIN src/phase5_linking.py \
  -e data/entities/ \
  -c data/chunked/ \
  -p data/parsed/ \
  -o outputs/ \
  --max-entity-links-per-element 12
```

Что делает: строит документный linking-граф, где сущности связаны с чанками, рисунками, таблицами и подписями.

Выход:

```text
outputs/document_links.html
outputs/document_links.graphml
outputs/document_links.json
outputs/linking_metrics.json
```

## Результаты

После успешного запуска основные файлы будут в `outputs/`:

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

Метрики для отчёта:

- `outputs/graph_metrics_clean.json`
- `outputs/linking_metrics.json`

HTML-визуализации используют D3.js через CDN. Для просмотра HTML нужен доступ к CDN или локально подключённый D3.

## Проверка

Smoke-тесты:

```bash
python -B -m unittest discover -s tests
```

Проверка артефактов после запуска:

```bash
bash scripts/check_outputs.sh
```

Проверка синтаксиса основных модулей:

```bash
python -B -m py_compile \
  src/phase1_parsing.py \
  src/phase2_chunking.py \
  src/phase3_ner.py \
  src/phase4_graph.py \
  src/phase_cleanup_rebuild.py \
  src/phase5_linking.py
```

## Troubleshooting

### В `data/raw/` нет документов

Пайплайн обрабатывает только поддерживаемые форматы:

```text
pdf, docx, pptx
```

Для демо сначала создай входной DOCX:

```bash
python scripts/create_teacher_demo_input.py
```

### MinerU не скачивает модели

Попробуй ModelScope:

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

### GLiNER или HuggingFace недоступны

Это не блокирует весь проект. Phase 4 продолжит работу на SpaCy-сущностях. Для базового запуска можно оставить:

```bash
NER_ENGINE=spacy bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

### Ошибка локального порта MinerU

Если Phase 1 падает с ошибкой про `127.0.0.1` или `PermissionError: bind`, значит окружение запрещает локальный сервис MinerU. Запусти проект в обычном терминале, WSL или окружении, где разрешены локальные порты.

### HTML открылся, но граф пустой

Сначала проверь артефакты:

```bash
bash scripts/check_outputs.sh
```

Если `document_links.html` пустой, скорее всего Phase 5 не увидела файлы в `data/parsed/`, `data/chunked/` или `data/entities/`. Перезапусти полный пайплайн с `CLEAN_RUN=1`.

## Структура

```text
doc-entity-graph/
├── README.md
├── requirements.txt
├── requirements-vlm.txt
├── src/
│   ├── phase1_parsing.py
│   ├── phase2_chunking.py
│   ├── phase3_ner.py
│   ├── phase4_graph.py
│   ├── phase5_linking.py
│   └── phase_cleanup_rebuild.py
├── configs/
├── docs/
├── data/
│   ├── raw/
│   ├── parsed/
│   ├── chunked/
│   ├── entities/
│   └── graph/
├── outputs/
├── scripts/
│   ├── setup_env.sh
│   ├── run_pipeline.sh
│   ├── check_outputs.sh
│   └── create_teacher_demo_input.py
└── tests/
```

## Ограничения

- Baseline-граф строится по совместной встречаемости сущностей в чанках. Это не полноценный relation extraction.
- Качество NER зависит от языка документа и доступных моделей.
- Для больших документов linking-граф может быть очень плотным, поэтому Phase 5 ограничивает число связей `DISCUSSED_NEAR` параметром `--max-entity-links-per-element`.
- Входные документы и результаты игнорируются git-ом. Для воспроизводимого демо используется генератор `scripts/create_teacher_demo_input.py`.
