# Document Entity Graph

A document intelligence pipeline that preserves document structure across **parsing, semantic chunking, entity extraction, and graph construction**.

The project addresses a common limitation of text-only chunking: relationships between text, tables, figures, captions, and document sections can be lost when a document is split into isolated chunks.

The pipeline keeps document provenance across processing stages and uses it to build both an entity graph and a document linking graph.

## Pipeline

```text
PDF / DOCX / PPTX
        ↓
Structured Parsing
        ↓
Semantic Chunking
        ↓
Named Entity Recognition
        ↓
Entity Resolution
        ↓
┌──────────────────────┬────────────────────────┐
↓                      ↓
Entity Graph      Document Linking Graph
```
## Example Output

### Document Linking Graph

![Document linking graph](docs/images/document-linking-graph.png)

### Entity Graph

![Entity graph](docs/images/entity-graph.png)

## Key Features

- Structure-aware parsing of PDF, DOCX, and PPTX documents
- Semantic chunking with document provenance
- Named entity extraction with SpaCy and optional GLiNER
- Entity normalization and duplicate resolution
- Entity co-occurrence graph construction
- Document linking graph connecting entities, chunks, tables, figures, and captions
- Interactive HTML visualization
- GraphML and JSON exports
- Automated smoke tests and output validation

## Outputs

The pipeline generates two main graph representations:

### Entity Graph

A baseline graph where entities are connected based on co-occurrence within document chunks.

```text
Entity ↔ Entity
```

### Document Linking Graph

A provenance-aware graph preserving relationships between extracted knowledge and document structure.

```text
Entity → MENTIONED_IN → Chunk
Entity → DISCUSSED_NEAR → Figure / Table / Caption
Chunk → RELATED_TO → Figure / Table / Caption
Figure → HAS_CAPTION → Caption
```

## Quick Start

```bash
git clone https://github.com/ILYUTKICK/doc-entity-graph.git
cd doc-entity-graph

bash scripts/setup_env.sh
```

Activate the created environment and generate a reproducible demo document:

```bash
python scripts/create_demo_input.py
```

Run the full pipeline:

```bash
bash scripts/run_pipeline.sh data/raw_demo pipeline 512 1 12
```

Run validation:

```bash
python -B -m unittest discover -s tests
bash scripts/check_outputs.sh
```

## Tech Stack

**Document processing:** MinerU  
**NLP:** SpaCy, optional GLiNER  
**Graphs:** NetworkX  
**Visualization:** D3.js  
**Language:** Python 3.11

## Project Structure

```text
doc-entity-graph/
├── configs/
├── data/
├── docs/
├── outputs/
├── scripts/
├── src/
├── tests/
├── requirements.txt
└── README.md
```

## Documentation

Detailed documentation is available in [`docs/`](docs/):

- [Architecture](docs/architecture.md)
- [Pipeline](docs/pipeline.md)
- [Demo](docs/demo.md)
- [Results](docs/results.md)
- [Troubleshooting](docs/troubleshooting.md)

## Limitations

- The entity graph currently uses co-occurrence as a baseline relationship signal rather than full semantic relation extraction.
- NER quality depends on the selected model and document domain.
- Document parsing quality depends on the source file structure and MinerU output.
- Optional GLiNER enrichment may require additional model downloads and network access.
