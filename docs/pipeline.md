# Pipeline

This document describes the complete processing pipeline, including commands, inputs, outputs, and the main JSON fields passed between phases.

For the shortest end-to-end example, see [`demo.md`](demo.md).

---

## Environment Setup

```bash
bash scripts/setup_env.sh
```

If Conda is available:

```bash
conda activate doc-graph
export PYTHON_BIN="$(which python)"
```

Without Conda:

```bash
source .venv/bin/activate
export PYTHON_BIN="$(which python)"
```

Optional setup modes:

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

Python 3.11 is the expected runtime.

---

# Phase 1 — Structured Parsing

**Entry point:** `src/phase1_parsing.py`

### Command

```bash
$PYTHON_BIN src/phase1_parsing.py \
  -i data/raw/ \
  -o data/parsed/ \
  -b pipeline
```

### Input

```text
.pdf
.docx
.pptx
```

### Output

```text
data/parsed/*_parsed.json
```

### Key JSON fields

```text
source
source_hash
total_pages
backend
parse_time_sec
metadata
blocks
elements
full_markdown
```

`blocks` is the text-oriented representation used by Phase 2.

`elements` is the structural representation. Element types may include:

```text
title
text
figure
caption
table
formula
list
```

---

# Phase 2 — Semantic Chunking

**Entry point:** `src/phase2_chunking.py`

### Command

```bash
$PYTHON_BIN src/phase2_chunking.py \
  -i data/parsed/ \
  -o data/chunked/ \
  --max-tokens 512
```

### Input

```text
data/parsed/*_parsed.json
```

### Output

```text
data/chunked/*_chunked.json
```

### Key chunk fields

```text
chunk_id
text
section_title
section_hierarchy
page_start
page_end
block_indices
source_blocks
source_element_ids
source_elements
related_element_ids
related_elements
```

`source_elements` directly contributed text to the chunk. `related_elements` provide nearby or section-level structural context.

---

# Phase 3 — Named Entity Recognition

**Entry point:** `src/phase3_ner.py`

### Command

```bash
$PYTHON_BIN src/phase3_ner.py \
  -i data/chunked/ \
  -o data/entities/ \
  --engine spacy
```

### Input

```text
data/chunked/*_chunked.json
```

### Output

```text
data/entities/*_entities.json
```

### NER engines

```text
spacy
gliner
llm
```

### Key entity fields

```text
text
normalized
entity_type
confidence
chunk_id
section_title
section_hierarchy
page_start
page_end
block_indices
source_element_ids
related_element_ids
context
```

Entities inherit the provenance of the chunk from which they were extracted.

---

# Phase 4 — Clean Entity Graph

The repository also contains `src/phase4_graph.py` as a simple co-occurrence baseline. The portfolio-facing entity graph is produced by `src/phase_cleanup_rebuild.py`.

### Command

```bash
$PYTHON_BIN src/phase_cleanup_rebuild.py \
  -e data/entities/ \
  -c data/chunked/ \
  -o outputs/ \
  --min-edge-weight 1
```

### Input

```text
data/entities/
data/chunked/
```

### Output

```text
outputs/entity_graph_clean.html
outputs/entity_graph_clean.graphml
outputs/entity_graph_clean.json
outputs/resolved_entities_clean.json
outputs/graph_metrics_clean.json
```

### Entity graph JSON

```json
{
  "nodes": [],
  "edges": [],
  "metrics": {}
}
```

Important node fields:

```text
id
label
type
frequency
community
degree
source_docs
sections
related_refs
related_figures
related_tables
source_element_ids
related_element_ids
source_element_count
related_element_count
```

Edge fields:

```text
source
target
weight
```

The graph is a weighted entity co-occurrence graph, not semantic relation extraction.

### Metrics

`graph_metrics_clean.json` may contain:

```text
nodes
edges
density
avg_degree
max_degree_node
max_degree
top_pagerank
connected_components
largest_component
communities
community_sizes
```

---

# Phase 5 — Document Linking Graph

**Entry point:** `src/phase5_linking.py`

### Command

```bash
$PYTHON_BIN src/phase5_linking.py \
  -e data/entities/ \
  -c data/chunked/ \
  -p data/parsed/ \
  -o outputs/ \
  --max-entity-links-per-element 12
```

### Input

```text
data/entities/
data/chunked/
data/parsed/
```

### Output

```text
outputs/document_links.html
outputs/document_links.graphml
outputs/document_links.json
outputs/linking_metrics.json
```

### Linking graph JSON

```json
{
  "nodes": [],
  "edges": [],
  "metrics": {}
}
```

Nodes include:

```text
id
node_type
color
```

Depending on node type, additional fields may include:

```text
label
doc_name
section_title
page_start
page_end
text_preview
entity_type
confidence
frequency
context
ref_label
caption
```

Edges include:

```text
source
target
key
relation
color
```

Supported relation labels:

```text
CONTAINS_CHUNK
CONTAINS_ELEMENT
MENTIONED_IN
EXTRACTED_FROM
RELATED_TO
DISCUSSED_NEAR
HAS_CAPTION
CAPTION_OF
```

### Linking metrics

`linking_metrics.json` may include:

```text
documents
entities
chunks
figures
captions
tables
formulas
nodes
edges
caption_links
chunk_related_links
entity_figure_links
entity_table_links
discussed_near_candidates
discussed_near_kept
discussed_near_pruned
max_entity_links_per_element
```

---

# Full Pipeline

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Arguments:

```text
1. input_dir
2. MinerU backend: pipeline, vlm, hybrid, auto
3. max_tokens
4. min_edge_weight
5. max_entity_links_per_element
```

The wrapper normally clears old generated artifacts from:

```text
data/parsed/
data/chunked/
data/entities/
data/graph/
outputs/
```

Raw source documents are preserved.

Keep old artifacts with:

```bash
CLEAN_RUN=0 bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

---

## Environment Variables

```bash
PYTHON_BIN="$(which python)"
NER_ENGINE=spacy
CLEAN_RUN=1
MAX_ENTITY_LINKS_PER_ELEMENT=12
MINERU_MODEL_SOURCE=modelscope
```

Examples:

```bash
NER_ENGINE=spacy bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Explicit MinerU command:

```bash
MINERU_BIN="$(which python) -m mineru.cli.client" \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

---

## Validation

```bash
python -B -m unittest discover -s tests
bash scripts/check_outputs.sh
```

Optional syntax validation:

```bash
python -B -m py_compile \
  src/phase1_parsing.py \
  src/phase2_chunking.py \
  src/phase3_ner.py \
  src/phase4_graph.py \
  src/phase_cleanup_rebuild.py \
  src/phase5_linking.py
```

---

## Reproducibility Checklist

Record:

- input document set;
- MinerU backend;
- chunk `max_tokens`;
- NER engine;
- entity graph `min_edge_weight`;
- `max_entity_links_per_element`;
- `outputs/graph_metrics_clean.json`;
- `outputs/linking_metrics.json`.

Model availability and versions may change exact entity counts.
