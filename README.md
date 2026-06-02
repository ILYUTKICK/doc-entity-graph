# Построение графа сущностей документов (MinerU)

Проект строит граф сущностей из неструктурированных документов. На вход подаются PDF/DOCX/PPTX, на выходе получается интерактивная HTML-визуализация, GraphML для Gephi и JSON с метриками.

Основной сценарий: MinerU парсит документ в Markdown/JSON, текст режется на семантические чанки, NER извлекает сущности, затем NetworkX строит граф по совместной встречаемости сущностей в чанках.

## Архитектура

```text
PDF/DOCX/PPTX
      |
      v
MinerU parsing -> Markdown + JSON blocks
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
```

## Результаты

После успешного запуска основные артефакты появятся в `outputs/`:

```text
outputs/
├── entity_graph_clean.html
├── entity_graph_clean.graphml
├── entity_graph_clean.json
├── resolved_entities_clean.json
└── graph_metrics_clean.json
```

Открыть граф:

```bash
open outputs/entity_graph_clean.html
```

Метрики для отчёта лучше брать из `outputs/graph_metrics_clean.json`; README не фиксирует числа, потому что они зависят от набора документов, backend MinerU, NER-движка и порогов фильтрации.

## Проверка

Минимальные smoke-тесты не требуют скачанных моделей:

```bash
python -B -m unittest discover -s tests
```

Проверка синтаксиса:

```bash
python -B -m py_compile src/phase1_parsing.py src/phase2_chunking.py src/phase3_ner.py src/phase4_graph.py src/phase_cleanup_rebuild.py
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
│   └── run_pipeline.sh
└── tests/
```

## Ограничения

Связи в графе сейчас являются baseline-связями по совместной встречаемости сущностей в одном чанке. Это не полноценный relation extraction. Для учебного проекта это хороший воспроизводимый baseline, но в отчёте стоит явно отделять co-occurrence graph от графа фактических отношений.

## Лицензия

MIT
