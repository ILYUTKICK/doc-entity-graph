# Pipeline

## Setup

```bash
bash scripts/setup_env.sh
```

This creates the required directory structure and installs `requirements.txt`.

For a lightweight setup without downloading SpaCy language models:

```bash
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

For a lightweight English-only smoke run:

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
```

## Full run

```bash
bash scripts/run_pipeline.sh
```

If HuggingFace model downloads fail, use ModelScope:

```bash
MINERU_MODEL_SOURCE=modelscope bash scripts/run_pipeline.sh
```

Phase 1 calls MinerU through the active Python interpreter with `python -m mineru.cli.client`. Check the environment before running:

```bash
conda activate doc-graph
which python
python --version
```

Expected interpreter:

```text
/Users/ilyutkinn/anaconda3/envs/doc-graph/bin/python
Python 3.11.x
```

Phase 1 output keeps two complementary layers in every `*_parsed.json` file:

- `blocks`: text-first blocks used by Phase 2 chunking.
- `elements`: structured MinerU elements used for document-graph context, including `figure`, `caption`, `table`, `title`, paths to extracted images, table HTML and nearby `caption_of` links.

Phase 2 preserves source context in every `*_chunked.json` chunk:

- `source_blocks`: compact provenance for text blocks used to build the chunk.
- `source_elements` / `source_element_ids`: Phase 1 elements that directly contributed text.
- `related_elements` / `related_element_ids`: nearby or section-linked `figure`, `caption`, `table` and `formula` elements.

Phase 3 preserves the same context on extracted entities:

- `page_start` / `page_end`, `section_title`, `section_hierarchy`, `block_indices`.
- `source_element_ids` and `related_element_ids` for linking entities to figures, captions and tables.

Phase 5 builds the document linking graph after NER and graph cleanup:

```bash
python src/phase5_linking.py -e data/entities/ -c data/chunked/ -p data/parsed/ -o outputs/
```

It exports:

- `outputs/document_links.html`
- `outputs/document_links.graphml`
- `outputs/document_links.json`
- `outputs/linking_metrics.json`

Equivalent explicit form:

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1
```

By default, `run_pipeline.sh` uses `CLEAN_RUN=1` and removes previous generated artifacts from `data/parsed/`, `data/chunked/`, `data/entities/`, `data/graph/` and `outputs/`. It does not remove source documents from `data/raw/`.

To keep old artifacts:

```bash
CLEAN_RUN=0 bash scripts/run_pipeline.sh
```

## Step-by-step run

```bash
python src/phase1_parsing.py -i data/raw/ -o data/parsed/ -b pipeline
python src/phase2_chunking.py -i data/parsed/ -o data/chunked/ --max-tokens 512
python src/phase3_ner.py -i data/chunked/ -o data/entities/ --engine spacy
python src/phase_cleanup_rebuild.py -e data/entities/ -c data/chunked/ -o outputs/ --min-edge-weight 2
python src/phase5_linking.py -e data/entities/ -c data/chunked/ -p data/parsed/ -o outputs/
```

## Reproducibility checklist

- Keep source documents in `data/raw/`.
- Keep generated intermediate files out of git.
- Record backend, chunk size, NER engine and edge threshold.
- Use `min_edge_weight=1` for one-document smoke runs; higher thresholds can remove every edge when each co-occurrence appears only once.
- Save final metrics from `outputs/graph_metrics_clean.json`.
- Save linking metrics from `outputs/linking_metrics.json`.
- Use `python -B -m unittest discover -s tests` before reporting results.
- Use `bash scripts/check_outputs.sh` after a full run to verify that parsed elements, chunk/entity provenance and exported graphs are present.
