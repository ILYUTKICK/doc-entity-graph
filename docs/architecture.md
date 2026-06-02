# Architecture

The project is organized as a reproducible document-to-graph pipeline.

## Phase 1: parsing

Entry point: `src/phase1_parsing.py`

Input: source documents from `data/raw/`.

Output: `data/parsed/*_parsed.json`.

The parser calls MinerU and normalizes the result into a stable JSON schema:

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

Each block keeps text, page number, bbox and block index. The `elements` layer stores structured MinerU output such as titles, figures, captions, tables and caption links. This provenance is important because later phases can trace graph nodes back to document regions.

## Phase 2: chunking

Entry point: `src/phase2_chunking.py`

Input: `data/parsed/*_parsed.json`.

Output: `data/chunked/*_chunked.json`.

The default strategy groups blocks by document sections and splits long sections by sentences. Chunks preserve section titles, page ranges, block indices and overlap flags.

Each chunk also keeps:

- `source_blocks`
- `source_element_ids` / `source_elements`
- `related_element_ids` / `related_elements`

This is the bridge between text chunks and structured document elements such as figures and captions.

## Phase 3: NER

Entry point: `src/phase3_ner.py`

Input: `data/chunked/*_chunked.json`.

Output: `data/entities/*_entities.json`.

Available engines:

- `spacy`: fast baseline for standard entity types.
- `gliner`: zero-shot extraction for domain-specific labels.
- `llm`: optional API-based extraction path.

Entities inherit chunk provenance, including page range, section, source blocks and related structured elements.

## Phase 4: graph baseline

Entry point: `src/phase4_graph.py`

Input: `data/entities/*_entities.json`.

Output: graph files in `data/graph/`.

This phase builds a co-occurrence graph: two entities are connected when they occur in the same chunk.

## Clean graph rebuild

Entry point: `src/phase_cleanup_rebuild.py`

Input: `data/entities/` and `data/chunked/`.

Output: final report artifacts in `outputs/`.

This phase aggressively filters noisy SpaCy entities, optionally enriches entities with GLiNER, resolves duplicate entities and exports the clean graph.

## Phase 5: document linking

Entry point: `src/phase5_linking.py`

Input: `data/entities/`, `data/chunked/` and `data/parsed/`.

Output: document linking artifacts in `outputs/`:

```text
document_links.html
document_links.graphml
document_links.json
linking_metrics.json
```

This phase builds a heterogeneous directed graph with explicit links:

```text
Entity -> MENTIONED_IN -> Chunk
Entity -> DISCUSSED_NEAR -> Figure/Table/Caption
Chunk  -> RELATED_TO -> Figure/Table/Caption
Figure -> HAS_CAPTION -> Caption
```

The linking graph is separate from the baseline co-occurrence graph. It demonstrates how structured parsing with MinerU can recover context around figures, tables and captions even when the relevant text is chunked separately.
