# Результаты

Этот файл фиксирует пример воспроизводимого запуска. Если меняются входные документы, backend, NER-движок или пороги фильтрации, метрики нужно пересчитать.

## Конфигурация Запуска

Пример ниже относится к демо-документу, который создаётся командой:

```bash
python scripts/create_teacher_demo_input.py
```

Запуск:

```bash
bash scripts/run_pipeline.sh data/raw_teacher_demo pipeline 512 1 12
```

Конфигурация:

```text
MinerU backend: pipeline
Chunk max tokens: 512
NER engine: SpaCy + optional GLiNER enrichment
Minimum edge weight: 1
Max entity links per structured element: 12
Documents: 1
```

## Метрики Entity-графа

Источник: `outputs/graph_metrics_clean.json`.

```text
Nodes: 21
Edges: 167
Density: 0.7952
Average degree: 15.9
Connected components: 1
Largest component: 21
Communities: 3
```

Entity-граф строится по совместной встречаемости сущностей внутри чанков. Это воспроизводимый baseline, а не полноценный relation extraction.

## Метрики Linking-графа

Источник: `outputs/linking_metrics.json`.

```text
Documents: 1
Entities: 24
Chunks: 4
Figures: 2
Captions: 4
Tables: 2
Nodes: 48
Edges: 387
Figure-caption links: 4
Entity-figure links: 24
Entity-table links: 24
Chunk-related links: 8
DISCUSSED_NEAR candidates: 112
DISCUSSED_NEAR kept: 96
DISCUSSED_NEAR pruned: 16
Max entity links per structured element: 12
```

Linking-граф является структурным графом. Его связи строятся из MinerU layout elements и provenance чанков/сущностей, а не из обученного relation extraction.

## Распределение Сущностей

Источник: `outputs/resolved_entities_clean.json` или `outputs/entity_graph_clean.json`.

Пример для демо-запуска:

```text
PERSON: 8
ORG: 7
INSTRUMENT: 3
CONCEPT: 1
LOCATION: 1
FORMULA: 1
```

Точные числа могут отличаться, если GLiNER недоступен или версии моделей изменились.

## Что Важно Указывать В Отчёте

- Входные документы.
- Backend MinerU.
- Размер чанка.
- NER engine.
- Минимальный вес ребра в entity-графе.
- `max_entity_links_per_element`.
- Метрики entity-графа из `outputs/graph_metrics_clean.json`.
- Метрики linking-графа из `outputs/linking_metrics.json`.

## Интерпретация

Entity-граф отвечает на вопрос:

```text
Какие сущности часто встречаются в одном текстовом контексте?
```

Linking-граф отвечает на другой вопрос:

```text
Какие сущности связаны с конкретными чанками, рисунками, таблицами и подписями?
```

Именно linking-граф показывает основную идею проекта: при обработке документов важно сохранять структурный контекст, а не только текст.
