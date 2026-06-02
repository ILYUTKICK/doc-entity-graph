#!/usr/bin/env bash
# Быстрая проверка артефактов после запуска пайплайна.

set -Eeuo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"

"$PYTHON_BIN" - <<'PY'
import json
import sys
from pathlib import Path


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    sys.exit(1)


def ok(message: str) -> None:
    print(f"OK:   {message}")


def load(path: Path):
    if not path.exists():
        fail(f"missing {path}")
    return json.loads(path.read_text(encoding="utf-8"))


parsed_files = sorted(Path("data/parsed").glob("*_parsed.json"))
chunked_files = sorted(Path("data/chunked").glob("*_chunked.json"))
entity_files = sorted(Path("data/entities").glob("*_entities.json"))

if not parsed_files:
    fail("no data/parsed/*_parsed.json files")
if not chunked_files:
    fail("no data/chunked/*_chunked.json files")
if not entity_files:
    fail("no data/entities/*_entities.json files")

ok(f"parsed files: {len(parsed_files)}")
ok(f"chunked files: {len(chunked_files)}")
ok(f"entity files: {len(entity_files)}")

parsed_with_elements = 0
figures = captions = tables = caption_links = 0
for path in parsed_files:
    data = load(path)
    elements = data.get("elements", [])
    if elements:
        parsed_with_elements += 1
    figures += sum(1 for e in elements if e.get("element_type") == "figure")
    captions += sum(1 for e in elements if e.get("element_type") == "caption")
    tables += sum(1 for e in elements if e.get("element_type") == "table")
    caption_links += sum(
        1 for e in elements
        if e.get("element_type") == "caption"
        and e.get("metadata", {}).get("linked_element_id")
    )

if parsed_with_elements == 0:
    fail("parsed JSON files do not contain elements")
ok(f"parsed with elements: {parsed_with_elements}/{len(parsed_files)}")
ok(f"figures/captions/tables: {figures}/{captions}/{tables}")
ok(f"caption links: {caption_links}")

chunks = chunks_with_source = chunks_with_related = 0
for path in chunked_files:
    data = load(path)
    for chunk in data.get("chunks", []):
        chunks += 1
        if chunk.get("source_element_ids"):
            chunks_with_source += 1
        if chunk.get("related_element_ids"):
            chunks_with_related += 1

if chunks == 0:
    fail("chunked JSON files contain no chunks")
if chunks_with_source == 0:
    fail("chunks do not contain source_element_ids")
ok(f"chunks: {chunks}")
ok(f"chunks with source elements: {chunks_with_source}")
ok(f"chunks with related elements: {chunks_with_related}")

entities = entities_with_source = entities_with_related = 0
for path in entity_files:
    data = load(path)
    for entity in data.get("entities", []):
        entities += 1
        if entity.get("source_element_ids"):
            entities_with_source += 1
        if entity.get("related_element_ids"):
            entities_with_related += 1

if entities == 0:
    fail("entity JSON files contain no entities")
if entities_with_source == 0:
    fail("entities do not contain source_element_ids")
ok(f"entities: {entities}")
ok(f"entities with source elements: {entities_with_source}")
ok(f"entities with related elements: {entities_with_related}")

required_outputs = [
    "outputs/entity_graph_clean.html",
    "outputs/entity_graph_clean.json",
    "outputs/graph_metrics_clean.json",
    "outputs/document_links.html",
    "outputs/document_links.json",
    "outputs/linking_metrics.json",
]
for item in required_outputs:
    if not Path(item).exists():
        fail(f"missing {item}")
ok("required output files exist")

linking_metrics = load(Path("outputs/linking_metrics.json"))
if linking_metrics.get("caption_links", 0) < caption_links:
    fail("linking_metrics caption_links is lower than parsed caption links")
if linking_metrics.get("entity_figure_links", 0) == 0 and figures:
    fail("figures exist but no entity_figure_links were produced")

ok(f"linking graph nodes/edges: {linking_metrics.get('nodes')}/{linking_metrics.get('edges')}")
print("SUCCESS: pipeline artifacts look consistent")
PY
