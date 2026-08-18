# Demo

This guide shows the shortest reproducible path from a generated input document to both interactive graph visualizations.

The commands below assume the public demo generator is named `scripts/create_demo_input.py`.

---

## 1. Set Up the Environment

```bash
bash scripts/setup_env.sh
```

Activate the environment.

With Conda:

```bash
conda activate doc-graph
export PYTHON_BIN="$(which python)"
```

With the local virtual environment:

```bash
source .venv/bin/activate
export PYTHON_BIN="$(which python)"
```

Confirm the runtime:

```bash
which python
python --version
```

Python 3.11 is expected.

---

## 2. Generate the Demo Input

```bash
python scripts/create_demo_input.py
```

Expected demo directory:

```text
data/raw_demo/
```

The generated DOCX is intended to contain text and structured elements such as tables, figures, and captions.

---

## 3. Run the Complete Pipeline

```bash
bash scripts/run_pipeline.sh data/raw_demo pipeline 512 1 12
```

Demo configuration:

```text
MinerU backend: pipeline
Chunk max tokens: 512
Minimum entity-graph edge weight: 1
Max entity links per structured element: 12
```

SpaCy is the baseline NER path. GLiNER enrichment is optional.

---

## 4. Open the Interactive Outputs

```text
outputs/document_links.html
outputs/entity_graph_clean.html
```

On macOS:

```bash
open outputs/document_links.html
open outputs/entity_graph_clean.html
```

On Linux:

```bash
xdg-open outputs/document_links.html
xdg-open outputs/entity_graph_clean.html
```

### Document Linking Graph

`document_links.html` visualizes the provenance-aware heterogeneous graph: documents, entities, chunks, figures, tables, captions, and explicit relations.

Useful controls include:

- `Core`
- `Figures`
- `Tables`
- `Show all`
- section/page filters
- element focus
- search
- label toggle

### Entity Graph

`entity_graph_clean.html` visualizes the cleaned entity co-occurrence graph with entity types, weighted edges, graph communities, search, and filtering controls.

---

## 5. Validate the Outputs

```bash
python -B -m unittest discover -s tests
bash scripts/check_outputs.sh
```

A successful run should produce:

```text
outputs/
├── entity_graph_clean.html
├── entity_graph_clean.graphml
├── entity_graph_clean.json
├── graph_metrics_clean.json
├── resolved_entities_clean.json
├── document_links.html
├── document_links.graphml
├── document_links.json
└── linking_metrics.json
```

---

## 6. Inspect Metrics

```bash
cat outputs/graph_metrics_clean.json
cat outputs/linking_metrics.json
```

---

## 7. Run on Your Own Documents

Place supported files in:

```text
data/raw/
```

For example:

```text
data/raw/
├── report.pdf
├── lecture.docx
└── slides.pptx
```

Then run:

```bash
bash scripts/run_pipeline.sh data/raw pipeline 512 1 12
```

If the demo fails, see [`troubleshooting.md`](troubleshooting.md).
