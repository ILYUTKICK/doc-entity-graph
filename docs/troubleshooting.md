# Troubleshooting

This guide covers the most common failure modes in the document-to-graph pipeline.

---

## No Documents Found

The input directory must contain supported formats:

```text
.pdf
.docx
.pptx
```

For the reproducible demo:

```bash
python scripts/create_demo_input.py
bash scripts/run_pipeline.sh data/raw_demo pipeline 512 1 12
```

For your own documents, place files in:

```text
data/raw/
```

---

## MinerU Cannot Download Models

Try ModelScope:

```bash
MINERU_MODEL_SOURCE=modelscope \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Verify the environment:

```bash
which python
python --version
```

Python 3.11 is expected.

If needed, explicitly set the MinerU command:

```bash
MINERU_BIN="$(which python) -m mineru.cli.client" \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

---

## MinerU Local Port / Bind Error

### Symptoms

Phase 1 fails with an error involving:

```text
127.0.0.1
PermissionError: bind
```

### Cause

The environment blocks MinerU from starting a local service.

### Fix

Run the project in a normal local terminal, WSL, or another environment that allows local loopback ports.

---

## GLiNER or HuggingFace Is Unavailable

This should not block the baseline pipeline. The clean graph rebuild can continue with cleaned SpaCy entities.

Use:

```bash
NER_ENGINE=spacy \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Alternative setup options:

```bash
SKIP_SPACY_MODELS=1 bash scripts/setup_env.sh
```

```bash
SPACY_MODELS=en_core_web_sm bash scripts/setup_env.sh
```

---

## Empty Entity Graph

Check upstream artifacts:

```text
data/chunked/
data/entities/
```

Run:

```bash
python -B -m unittest discover -s tests
bash scripts/check_outputs.sh
```

Possible causes:

- NER returned no entities;
- entity filtering removed most candidates;
- `--min-edge-weight` is too high;
- the input contains too little repeated context.

For a small demo, use `--min-edge-weight 1`.

---

## Empty Document Linking Graph

Phase 5 depends on:

```text
data/parsed/
data/chunked/
data/entities/
```

If any are missing or inconsistent:

```bash
CLEAN_RUN=1 \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

Then validate:

```bash
bash scripts/check_outputs.sh
```

---

## Linking Graph Is Too Dense

Reduce the maximum number of `DISCUSSED_NEAR` edges retained per structured element:

```bash
$PYTHON_BIN src/phase5_linking.py \
  -e data/entities/ \
  -c data/chunked/ \
  -p data/parsed/ \
  -o outputs/ \
  --max-entity-links-per-element 6
```

The standard demo uses `12`.

A value of `0` disables top-N pruning.

---

## Old Artifacts Cause Inconsistent Results

The wrapper normally uses a clean run.

```bash
CLEAN_RUN=1 \
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

This clears:

```text
data/parsed/
data/chunked/
data/entities/
data/graph/
outputs/
```

Raw source documents are preserved.

Use `CLEAN_RUN=0` only when you intentionally want to keep prior artifacts.

---

## Interactive HTML Loads but the Graph Does Not Render

The HTML visualizations use D3.js from a CDN.

If the page opens but the graph does not render:

1. confirm network access to the D3 CDN;
2. inspect the browser console for JavaScript errors;
3. verify that JSON outputs were generated;
4. rerun `bash scripts/check_outputs.sh`.

For offline use, replace the CDN dependency with a local D3 asset.

---

## Output Validation

After troubleshooting:

```bash
python -B -m unittest discover -s tests
bash scripts/check_outputs.sh
```

Expected artifacts include:

```text
outputs/entity_graph_clean.html
outputs/entity_graph_clean.graphml
outputs/entity_graph_clean.json
outputs/graph_metrics_clean.json
outputs/document_links.html
outputs/document_links.graphml
outputs/document_links.json
outputs/linking_metrics.json
```
