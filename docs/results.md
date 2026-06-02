# Results

This file records the latest reproducible run on the current document set.

## Run configuration

```text
MinerU backend: pipeline
Chunk max tokens: 512
NER engine: SpaCy + GLiNER enrichment
GLiNER model: urchade/gliner_multi-v2.1
Minimum edge weight: 1
Documents: 2
```

## Graph metrics

Use `outputs/graph_metrics_clean.json`.

```text
Nodes: 41
Edges: 173
Density: 0.211
Average degree: 8.44
Connected components: 2
Largest component: 33
Communities: 5
```

## Linking metrics

Use `outputs/linking_metrics.json`.

```text
Documents: 2
Entities: 32
Chunks: 23
Figures: 14
Captions: 14
Tables: 1
Figure-caption links: 14
Entity-figure links: 61
Entity-table links: 2
Chunk-related links: 41
```

## Entity distribution

Use `outputs/resolved_entities_clean.json` or `outputs/entity_graph_clean.json`.

```text
ORG: 12
CONCEPT: 10
INSTRUMENT: 10
PERSON: 5
LOCATION: 2
WORK: 2
FORMULA: 0
```

`outputs/resolved_entities_clean.json` contains 42 resolved entities before graph pruning. The final graph has 41 nodes because one isolated `FORMULA` node was removed.

## Notes

The entity graph is based on entity co-occurrence inside chunks. It is a reproducible baseline and should not be described as full semantic relation extraction.

The linking graph is a structural graph. Its edges come from MinerU layout elements and chunk/entity provenance, not from learned relation extraction.

Latest structural linking result: 14 figures, 14 captions, 14 figure-caption links and 61 entity-figure links.
