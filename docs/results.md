# Results

This file is a report template. Fill it after running the pipeline on the final document set.

## Run configuration

```text
MinerU backend:
Chunk max tokens:
NER engine:
GLiNER model:
Minimum edge weight:
Documents:
```

## Graph metrics

Use `outputs/graph_metrics_clean.json`.

```text
Nodes:
Edges:
Density:
Average degree:
Connected components:
Largest component:
Communities:
```

## Entity distribution

Use `outputs/resolved_entities_clean.json` or `outputs/entity_graph_clean.json`.

```text
PERSON:
ORG:
CONCEPT:
INSTRUMENT:
LOCATION:
WORK:
FORMULA:
DATE:
```

## Notes

The current graph is based on entity co-occurrence inside chunks. It is a reproducible baseline and should not be described as full semantic relation extraction.

