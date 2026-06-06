# Пайплайн

Этот файл описывает запуск проекта и правила воспроизводимости. Короткая инструкция для нового пользователя лежит в `README.md`.

## Установка

Обычная установка:

```bash
bash scripts/setup_env.sh
```

Скрипт создаёт структуру папок и устанавливает зависимости из `requirements.txt`.

Если conda установлена, будет создано окружение `doc-graph`. Если conda нет, будет создано локальное `.venv`.

Активировать conda-окружение:

```bash
conda activate doc-graph
export PYTHON_BIN="$(which python)"
```

Активировать `.venv`:

```bash
source .venv/bin/activate
export PYTHON_BIN="$(which python)"
```

Установка без скачивания SpaCy-моделей:

```bash
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

Лёгкая установка только с маленькой английской моделью:

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
```

## Полный Запуск

Для воспроизводимого демо сначала создай входной DOCX:

```bash
python scripts/create_teacher_demo_input.py
```

Запусти полный пайплайн:

```bash
bash scripts/run_pipeline.sh data/raw_teacher_demo pipeline 512 1 12
```

Для своих документов положи файлы в `data/raw/` и запусти:

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Если HuggingFace или загрузка моделей MinerU нестабильны, можно попробовать ModelScope:

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Phase 1 вызывает MinerU через активный Python-интерпретатор. Перед запуском полезно проверить окружение:

```bash
which python
python --version
```

Ожидается Python 3.11 из активного окружения.

## Что Сохраняет Каждая Фаза

Phase 1 сохраняет два слоя в каждом `*_parsed.json`:

- `blocks` — текстовый слой для Phase 2;
- `elements` — структурный слой MinerU: `figure`, `caption`, `table`, `title`, ссылки на изображения, HTML таблиц и связи подписей.

Phase 2 сохраняет provenance в каждом `*_chunked.json`:

- `source_blocks` — краткое описание блоков, из которых собран чанк;
- `source_elements` / `source_element_ids` — элементы Phase 1, которые напрямую дали текст;
- `related_elements` / `related_element_ids` — близкие или секционно связанные `figure`, `caption`, `table`, `formula`.

Phase 3 переносит этот контекст на сущности:

- `page_start` / `page_end`;
- `section_title`;
- `section_hierarchy`;
- `block_indices`;
- `source_element_ids`;
- `related_element_ids`.

Phase 5 строит linking-граф после NER:

```bash
python src/phase5_linking.py \
  -e data/entities/ \
  -c data/chunked/ \
  -p data/parsed/ \
  -o outputs/ \
  --max-entity-links-per-element 12
```

Он экспортирует:

- `outputs/document_links.html`
- `outputs/document_links.graphml`
- `outputs/document_links.json`
- `outputs/linking_metrics.json`

Для больших документов Phase 5 ограничивает связи `DISCUSSED_NEAR`, оставляя top-N сущностей на каждый структурный элемент. По умолчанию `12`. Значение `0` отключает ограничение.

HTML-визуализация открывается в отфильтрованном режиме `Core`. Для локального просмотра окружения можно использовать режимы `Figures`, `Tables`, фильтры по секции/странице и селектор конкретного рисунка или таблицы.

## Параметры Полного Скрипта

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Аргументы:

```text
1. input_dir       папка с исходными документами
2. backend         backend MinerU: pipeline, vlm, hybrid, auto
3. max_tokens      максимальный размер чанка
4. min_edge_weight минимальный вес ребра в entity-графе
5. max_entity_links_per_element top-N связей Entity -> Figure/Table/Caption
```

По умолчанию `run_pipeline.sh` использует `CLEAN_RUN=1` и удаляет старые артефакты из:

```text
data/parsed/
data/chunked/
data/entities/
data/graph/
outputs/
```

Исходные документы в `data/raw/` не удаляются.

Чтобы сохранить старые артефакты:

```bash
CLEAN_RUN=0 bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

## Пошаговый Запуск

```bash
python src/phase1_parsing.py -i data/raw/ -o data/parsed/ -b pipeline
python src/phase2_chunking.py -i data/parsed/ -o data/chunked/ --max-tokens 512
python src/phase3_ner.py -i data/chunked/ -o data/entities/ --engine spacy
python src/phase_cleanup_rebuild.py -e data/entities/ -c data/chunked/ -o outputs/ --min-edge-weight 1
python src/phase5_linking.py -e data/entities/ -c data/chunked/ -p data/parsed/ -o outputs/ --max-entity-links-per-element 12
```

## Чеклист Воспроизводимости

- Зафиксировать входную папку документов.
- Зафиксировать backend MinerU.
- Зафиксировать `max_tokens`.
- Зафиксировать NER engine.
- Зафиксировать `min_edge_weight`.
- Зафиксировать `max_entity_links_per_element`, потому что он меняет плотность связей `DISCUSSED_NEAR`.
- Хранить исходные документы и сгенерированные артефакты вне git.
- Сохранять метрики из `outputs/graph_metrics_clean.json`.
- Сохранять метрики из `outputs/linking_metrics.json`.
- Перед отчётом запускать `python -B -m unittest discover -s tests`.
- После полного запуска проверять артефакты через `bash scripts/check_outputs.sh`.
