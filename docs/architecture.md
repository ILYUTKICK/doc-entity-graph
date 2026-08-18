# Architecture

`doc-entity-graph` is a reproducible document-intelligence pipeline that turns unstructured documents into graph representations while preserving provenance across every processing stage.

The system is split into five processing phases so that each transformation can be inspected, tested, and reproduced independently.

## High-Level Flow

```text
PDF / DOCX / PPTX
        |
        v
Phase 1 — Structured Parsing
        |
        v
Phase 2 — Semantic Chunking
        |
        v
Phase 3 — Named Entity Recognition
        |
        +-----------------------------+
        |                             |
        v                             v
Phase 4 — Entity Graph         Phase 5 — Document Linking Graph
```

The core architectural idea is **provenance preservation**: chunks and entities retain enough information to trace them back to the original document structure, including pages, sections, figures, tables, captions, and source blocks.

---

## Phase 1 — Structured Parsing

**Entry point:** `src/phase1_parsing.py`

**Input:** PDF, DOCX, or PPTX files.

**Output:** `data/parsed/*_parsed.json`

MinerU is used as the document parsing layer. The parser normalizes each input document into two complementary representations:

- `blocks` — the text-oriented layer used by the chunking phase;
- `elements` — the structural layer containing titles, text elements, figures, captions, tables, formulas, lists, and layout relationships.

Important document-level fields include:

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

The structured layer is important because text-only extraction would lose relationships between prose and nearby figures, tables, captions, and section boundaries.

---

## Phase 2 — Semantic Chunking

**Entry point:** `src/phase2_chunking.py`

**Input:** `data/parsed/*_parsed.json`

**Output:** `data/chunked/*_chunked.json`

Parsed text blocks are grouped by document section and split into semantic chunks with a configurable maximum token size.

Each chunk keeps both textual context and provenance information, including:

- section title;
- page range;
- source block indices;
- overlap information;
- `source_blocks`;
- `source_element_ids` / `source_elements`;
- `related_element_ids` / `related_elements`.

This phase creates the bridge between ordinary text chunks and the original document structure.

---

## Phase 3 — Named Entity Recognition

**Entry point:** `src/phase3_ner.py`

**Input:** `data/chunked/*_chunked.json`

**Output:** `data/entities/*_entities.json`

The project supports multiple NER paths:

- **SpaCy** — fast baseline NER;
- **GLiNER** — optional zero-shot/domain-oriented enrichment;
- **LLM** — optional API-based extraction path.

Entities inherit chunk provenance so they can be traced back to the document context where they were extracted.

Typical entity fields include:

```text
text
normalized
entity_type
confidence
chunk_id
section_title
page_start
page_end
source_element_ids
related_element_ids
context
```

---

## Phase 4 — Entity Graph

Two graph-building stages exist for the entity-only representation.

### Baseline graph

**Entry point:** `src/phase4_graph.py`

Entities are connected when they co-occur in the same chunk.

This is intentionally a **co-occurrence baseline**, not semantic relation extraction.

### Clean graph rebuild

**Entry point:** `src/phase_cleanup_rebuild.py`

**Input:**

```text
data/entities/
data/chunked/
```

**Output:**

```text
outputs/entity_graph_clean.html
outputs/entity_graph_clean.graphml
outputs/entity_graph_clean.json
outputs/resolved_entities_clean.json
outputs/graph_metrics_clean.json
```

The clean rebuild:

- filters noisy NER outputs;
- optionally enriches entities with GLiNER;
- resolves duplicate entities;
- rebuilds the co-occurrence graph;
- calculates graph metrics and communities;
- exports JSON, GraphML, and interactive HTML.

The exported entity graph JSON has the high-level structure:

```json
{
  "nodes": [],
  "edges": [],
  "metrics": {}
}
```

Entity nodes include attributes such as label, entity type, frequency, community, degree, source documents, sections, and provenance references. Edges contain `source`, `target`, and `weight`.

---

## Phase 5 — Document Linking Graph

**Entry point:** `src/phase5_linking.py`

**Input:**

```text
data/entities/
data/chunked/
data/parsed/
```

**Output:**

```text
outputs/document_links.html
outputs/document_links.graphml
outputs/document_links.json
outputs/linking_metrics.json
```

This phase builds a heterogeneous directed graph that preserves the structural context of the original document.

### Node types

The linking graph may contain the following node types:

- `document`
- `entity`
- `chunk`
- `figure`
- `caption`
- `table`
- `formula`
- `title`
- `text`
- `list`
- `unknown`

### Edge types

| Relation | Meaning |
|---|---|
| `CONTAINS_CHUNK` | Document contains a chunk |
| `CONTAINS_ELEMENT` | Document contains a structured element |
| `MENTIONED_IN` | Entity appears in a chunk |
| `EXTRACTED_FROM` | Chunk/entity comes directly from a structured element |
| `RELATED_TO` | Chunk is associated with a nearby or section-related structured element |
| `DISCUSSED_NEAR` | Entity is associated with a figure, table, or caption based on preserved provenance |
| `HAS_CAPTION` | Figure/table points to its caption |
| `CAPTION_OF` | Caption points back to its linked figure/table |

The JSON export follows:

```json
{
  "nodes": [],
  "edges": [],
  "metrics": {}
}
```

Each node keeps its graph attributes and `node_type`. Each edge keeps its `relation`, source, target, graph key, and visualization metadata.

---

## Provenance Model

Provenance flows through the pipeline rather than being reconstructed at the end.

```text
Document
  |
  +-- Block / Structured Element
          |
          v
        Chunk
          |
          v
        Entity
```

At the parsing stage, every structural element receives its document context. During chunking, source and related element references are copied into the chunk. During NER, this context is propagated to each entity.

As a result, Phase 5 can build relationships such as:

```text
Entity -> MENTIONED_IN -> Chunk
Chunk  -> EXTRACTED_FROM -> Figure/Table/Text
Entity -> DISCUSSED_NEAR -> Figure/Table/Caption
```

without relying only on textual similarity.

---

## Architectural Rationale

### Why preserve document structure?

Chunking is useful for downstream NLP and retrieval, but isolated chunks can lose the relationships between text and visual or tabular content. Keeping provenance makes those relationships recoverable.

### Why keep two graph representations?

- **Entity graph:** which entities frequently occur in the same textual context?
- **Document linking graph:** where did an entity come from, and which document elements surround it?

Keeping them separate avoids mixing a co-occurrence baseline with document-structure relationships.

### Why use co-occurrence as the entity-graph baseline?

Co-occurrence is deterministic, reproducible, easy to inspect, and does not pretend to be semantic relation extraction.

### Why use top-N pruning for `DISCUSSED_NEAR`?

Large documents can produce very dense linking graphs. `--max-entity-links-per-element` keeps the highest-ranked candidates while preserving readability.

### Why make GLiNER optional?

The core pipeline should remain usable when external model downloads are unavailable. SpaCy provides the baseline path, while GLiNER can enrich results when available.

---

## Design Principles

1. **Traceability** — graph nodes should be traceable to document context.
2. **Reproducibility** — each phase can run independently.
3. **Graceful degradation** — optional enrichment should not block the baseline pipeline.
4. **Inspectable outputs** — JSON, GraphML, metrics, and HTML are exported.
5. **Separation of concerns** — parsing, chunking, extraction, and graph building stay modular.
