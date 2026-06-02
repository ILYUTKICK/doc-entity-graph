# Architecture

The project is organized as a five-phase pipeline.

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
full_markdown
```

Each block keeps text, page number, bbox and block index. This provenance is important because later phases can trace graph nodes back to document regions.

## Phase 2: chunking

Entry point: `src/phase2_chunking.py`

Input: `data/parsed/*_parsed.json`.

Output: `data/chunked/*_chunked.json`.

The default strategy groups blocks by document sections and splits long sections by sentences. Chunks preserve section titles, page ranges, block indices and overlap flags.

## Phase 3: NER

Entry point: `src/phase3_ner.py`

Input: `data/chunked/*_chunked.json`.

Output: `data/entities/*_entities.json`.

Available engines:

- `spacy`: fast baseline for standard entity types.
- `gliner`: zero-shot extraction for domain-specific labels.
- `llm`: optional API-based extraction path.

## Phase 4: graph baseline

Entry point: `src/phase4_graph.py`

Input: `data/entities/*_entities.json`.

Output: graph files in `data/graph/`.

This phase builds a co-occurrence graph: two entities are connected when they occur in the same chunk.

## Phase 5: clean rebuild

Entry point: `src/phase_cleanup_rebuild.py`

Input: `data/entities/` and `data/chunked/`.

Output: final report artifacts in `outputs/`.

This phase aggressively filters noisy SpaCy entities, optionally enriches entities with GLiNER, resolves duplicate entities and exports the clean graph.

