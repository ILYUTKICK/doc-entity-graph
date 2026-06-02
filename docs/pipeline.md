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
```

## Reproducibility checklist

- Keep source documents in `data/raw/`.
- Keep generated intermediate files out of git.
- Record backend, chunk size, NER engine and edge threshold.
- Use `min_edge_weight=1` for one-document smoke runs; higher thresholds can remove every edge when each co-occurrence appears only once.
- Save final metrics from `outputs/graph_metrics_clean.json`.
- Use `python -B -m unittest discover -s tests` before reporting results.
