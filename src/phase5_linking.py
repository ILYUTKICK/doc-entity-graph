"""
═══════════════════════════════════════════════════════════════
  Фаза 5: Linking-граф документа
  Проект: Построение графа сущностей документов
═══════════════════════════════════════════════════════════════

Вход:
  - data/entities/*_entities.json  — сущности с provenance из Фазы 3
  - data/chunked/*_chunked.json    — чанки с source/related elements
  - data/parsed/*_parsed.json      — структурные элементы MinerU

Выход:
  - outputs/document_links.html
  - outputs/document_links.graphml
  - outputs/document_links.json
  - outputs/linking_metrics.json

Идея:
  Строим явный документный граф:
    Entity -> MENTIONED_IN -> Chunk
    Chunk  -> RELATED_TO   -> Figure/Table/Caption
    Entity -> DISCUSSED_NEAR -> Figure/Table/Caption
    Figure -> HAS_CAPTION  -> Caption
"""

import json
import logging
from pathlib import Path
from collections import Counter, defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase5")


NODE_COLORS = {
    "document": "#64748b",
    "entity": "#1d9e75",
    "chunk": "#8b8b98",
    "figure": "#d85a30",
    "caption": "#d4a62a",
    "table": "#7f77dd",
    "formula": "#ba7517",
    "title": "#378add",
    "text": "#94a3b8",
    "list": "#5dcaa5",
    "unknown": "#777777",
}

EDGE_COLORS = {
    "CONTAINS_CHUNK": "#5b6472",
    "CONTAINS_ELEMENT": "#5b6472",
    "MENTIONED_IN": "#1d9e75",
    "EXTRACTED_FROM": "#4f9edc",
    "RELATED_TO": "#d85a30",
    "DISCUSSED_NEAR": "#f97316",
    "HAS_CAPTION": "#d4a62a",
    "CAPTION_OF": "#d4a62a",
}

STRUCTURED_TYPES = {"figure", "caption", "table", "formula"}


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: ЗАГРУЗКА
# ══════════════════════════════════════════════════════════════

def load_json_files(input_dir: str, pattern: str) -> list[dict]:
    """Загрузка JSON-файлов по паттерну."""
    path = Path(input_dir)
    files = sorted(path.glob(pattern))
    if not files:
        log.warning(f"Не найдено {pattern} в {input_dir}")
        return []

    result = []
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["_file_path"] = str(file_path)
        data["_doc_name"] = Path(data.get("source", file_path.stem)).stem
        result.append(data)

    log.info(f"Загружено {len(result)} файлов {pattern} из {input_dir}")
    return result


def load_inputs(
    entities_dir: str,
    chunked_dir: str,
    parsed_dir: str,
) -> tuple[list[dict], dict, dict, dict]:
    """
    Загружает входы Phase 5.

    Returns:
        entities: все сущности
        chunks_by_id: chunk_id -> chunk
        elements_by_id: element_id -> element
        documents: doc_name -> metadata
    """
    entity_docs = load_json_files(entities_dir, "*_entities.json")
    chunked_docs = load_json_files(chunked_dir, "*_chunked.json")
    parsed_docs = load_json_files(parsed_dir, "*_parsed.json")

    documents = {}
    entities = []
    chunks_by_id = {}
    elements_by_id = {}

    for doc in parsed_docs:
        doc_name = doc["_doc_name"]
        documents.setdefault(doc_name, {
            "doc_name": doc_name,
            "source": doc.get("source", ""),
            "source_hash": doc.get("source_hash", ""),
        })

        for element in doc.get("elements", []):
            element = dict(element)
            element["_doc_name"] = doc_name
            element["_source"] = doc.get("source", "")
            if element.get("element_id"):
                elements_by_id[element["element_id"]] = element

    for doc in chunked_docs:
        doc_name = doc["_doc_name"]
        documents.setdefault(doc_name, {
            "doc_name": doc_name,
            "source": doc.get("source", ""),
            "source_hash": doc.get("source_hash", ""),
        })

        for chunk in doc.get("chunks", []):
            chunk = dict(chunk)
            chunk["_doc_name"] = doc_name
            chunk["_source"] = doc.get("source", "")
            chunks_by_id[chunk["chunk_id"]] = chunk

            for summary in chunk.get("source_elements", []) + chunk.get("related_elements", []):
                element_id = summary.get("element_id")
                if element_id and element_id not in elements_by_id:
                    summary = dict(summary)
                    summary["_doc_name"] = doc_name
                    summary["_source"] = doc.get("source", "")
                    elements_by_id[element_id] = summary

    for doc in entity_docs:
        doc_name = doc["_doc_name"]
        documents.setdefault(doc_name, {
            "doc_name": doc_name,
            "source": doc.get("source", ""),
            "source_hash": doc.get("source_hash", ""),
        })

        for entity in doc.get("entities", []):
            entity = dict(entity)
            entity["_doc_name"] = doc_name
            entity["_source"] = doc.get("source", "")
            entities.append(entity)

            for summary in entity.get("source_elements", []) + entity.get("related_elements", []):
                element_id = summary.get("element_id")
                if element_id and element_id not in elements_by_id:
                    summary = dict(summary)
                    summary["_doc_name"] = doc_name
                    summary["_source"] = doc.get("source", "")
                    elements_by_id[element_id] = summary

    log.info(
        "Входы Phase 5: "
        f"{len(documents)} документов, {len(chunks_by_id)} чанков, "
        f"{len(elements_by_id)} элементов, {len(entities)} сущностей"
    )
    return entities, chunks_by_id, elements_by_id, documents


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: ПОСТРОЕНИЕ ГРАФА
# ══════════════════════════════════════════════════════════════

def build_linking_graph(
    entities_dir: str,
    chunked_dir: str,
    parsed_dir: str,
):
    """Строит документный linking-граф."""
    import networkx as nx

    entities, chunks_by_id, elements_by_id, documents = load_inputs(
        entities_dir=entities_dir,
        chunked_dir=chunked_dir,
        parsed_dir=parsed_dir,
    )

    G = nx.MultiDiGraph()

    # Документы
    for doc_name, doc in documents.items():
        doc_node = document_node_id(doc_name)
        G.add_node(
            doc_node,
            node_type="document",
            label=doc_name,
            doc_name=doc_name,
            source=doc.get("source", ""),
            source_hash=doc.get("source_hash", ""),
        )

    # Чанки
    for chunk_id, chunk in chunks_by_id.items():
        doc_name = chunk.get("_doc_name", Path(chunk.get("_source", "")).stem)
        c_node = chunk_node_id(chunk_id)
        G.add_node(
            c_node,
            node_type="chunk",
            label=chunk_label(chunk),
            chunk_id=chunk_id,
            doc_name=doc_name,
            section_title=chunk.get("section_title", ""),
            page_start=chunk.get("page_start", 0),
            page_end=chunk.get("page_end", 0),
            text_preview=chunk.get("text", "")[:260],
        )
        add_edge(G, document_node_id(doc_name), c_node, "CONTAINS_CHUNK")

    # Элементы MinerU
    for element_id, element in elements_by_id.items():
        doc_name = element.get("_doc_name", "")
        e_node = element_node_id(element_id)
        element_type = element.get("element_type", "unknown")
        G.add_node(
            e_node,
            node_type=element_type,
            label=element_label(element),
            element_id=element_id,
            doc_name=doc_name,
            ref_label=element.get("ref_label", ""),
            section_title=element.get("section_title", ""),
            page_number=element.get("page_number", 0),
            image_path=element.get("image_path", ""),
            caption=element.get("caption", ""),
            text_preview=element.get("text", element.get("text_preview", ""))[:260],
        )
        if doc_name:
            add_edge(G, document_node_id(doc_name), e_node, "CONTAINS_ELEMENT")

    # Caption links: Figure/Table -> Caption
    for element_id, element in elements_by_id.items():
        metadata = element.get("metadata", {}) if isinstance(element.get("metadata", {}), dict) else {}
        caption_id = metadata.get("caption_element_id") or element.get("caption_element_id")
        linked_id = metadata.get("linked_element_id") or element.get("linked_element_id")

        if caption_id and caption_id in elements_by_id:
            add_edge(
                G,
                element_node_id(element_id),
                element_node_id(caption_id),
                "HAS_CAPTION",
            )

        if linked_id and linked_id in elements_by_id:
            add_edge(
                G,
                element_node_id(linked_id),
                element_node_id(element_id),
                "HAS_CAPTION",
            )
            add_edge(
                G,
                element_node_id(element_id),
                element_node_id(linked_id),
                "CAPTION_OF",
            )

    # Chunk -> source/related elements
    for chunk_id, chunk in chunks_by_id.items():
        c_node = chunk_node_id(chunk_id)
        for element_id in chunk.get("source_element_ids", []):
            if element_id in elements_by_id:
                add_edge(G, c_node, element_node_id(element_id), "EXTRACTED_FROM")
        for element_id in chunk.get("related_element_ids", []):
            if element_id in elements_by_id:
                add_edge(G, c_node, element_node_id(element_id), "RELATED_TO")

    # Entity -> Chunk / Element
    for entity in entities:
        ent_node = entity_node_id(entity)
        doc_name = entity.get("_doc_name", "")
        G.add_node(
            ent_node,
            node_type="entity",
            label=entity.get("text", ""),
            normalized=entity.get("normalized", ""),
            entity_type=entity.get("entity_type", ""),
            confidence=entity.get("confidence", 0),
            doc_name=doc_name,
            frequency=len(split_csv(entity.get("chunk_id", ""))),
            section_title=entity.get("section_title", ""),
            context=entity.get("context", "")[:260],
        )

        for chunk_id in split_csv(entity.get("chunk_id", "")):
            if chunk_id in chunks_by_id:
                add_edge(
                    G,
                    ent_node,
                    chunk_node_id(chunk_id),
                    "MENTIONED_IN",
                    confidence=entity.get("confidence", 0),
                )

        for element_id in entity.get("source_element_ids", []):
            if element_id in elements_by_id:
                add_edge(G, ent_node, element_node_id(element_id), "EXTRACTED_FROM")

        for element_id in entity.get("related_element_ids", []):
            if element_id in elements_by_id:
                add_edge(G, ent_node, element_node_id(element_id), "DISCUSSED_NEAR")

    metrics = compute_linking_metrics(G)
    log.info(f"Linking-граф построен: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    return G, metrics


def add_edge(G, source: str, target: str, relation: str, **attrs) -> None:
    """Добавляет или усиливает directed edge с ключом relation."""
    if source == target:
        return

    if G.has_edge(source, target, key=relation):
        edge = G[source][target][relation]
        edge["weight"] = edge.get("weight", 1) + 1
        edge["evidence_count"] = edge.get("evidence_count", 1) + 1
        for key, value in attrs.items():
            if value not in ("", None):
                edge[key] = value
        return

    G.add_edge(
        source,
        target,
        key=relation,
        relation=relation,
        weight=1,
        evidence_count=1,
        **attrs,
    )


def compute_linking_metrics(G) -> dict:
    """Метрики linking-графа для отчёта."""
    node_types = Counter(data.get("node_type", "unknown") for _, data in G.nodes(data=True))
    edge_types = Counter(data.get("relation", "UNKNOWN") for _, _, data in G.edges(data=True))

    entity_figure_links = 0
    entity_table_links = 0
    caption_links = 0
    chunk_related_links = 0

    for source, target, data in G.edges(data=True):
        relation = data.get("relation", "")
        target_type = G.nodes[target].get("node_type", "")
        if relation == "DISCUSSED_NEAR" and target_type == "figure":
            entity_figure_links += 1
        if relation == "DISCUSSED_NEAR" and target_type == "table":
            entity_table_links += 1
        if relation == "HAS_CAPTION":
            caption_links += 1
        if relation == "RELATED_TO":
            chunk_related_links += 1

    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "node_type_counts": dict(node_types),
        "edge_relation_counts": dict(edge_types),
        "documents": node_types.get("document", 0),
        "entities": node_types.get("entity", 0),
        "chunks": node_types.get("chunk", 0),
        "figures": node_types.get("figure", 0),
        "captions": node_types.get("caption", 0),
        "tables": node_types.get("table", 0),
        "formulas": node_types.get("formula", 0),
        "caption_links": caption_links,
        "chunk_related_links": chunk_related_links,
        "entity_figure_links": entity_figure_links,
        "entity_table_links": entity_table_links,
    }


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: ЭКСПОРТ
# ══════════════════════════════════════════════════════════════

def export_linking_graph(G, metrics: dict, output_dir: str) -> None:
    """Экспорт GraphML, JSON, HTML и metrics."""
    import networkx as nx

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    graph_json = graph_to_json(G, metrics)

    json_path = output_path / "document_links.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph_json, f, ensure_ascii=False, indent=2)
    log.info(f"JSON: {json_path}")

    metrics_path = output_path / "linking_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    log.info(f"Metrics: {metrics_path}")

    graphml_path = output_path / "document_links.graphml"
    nx.write_graphml(sanitized_graph(G), str(graphml_path))
    log.info(f"GraphML: {graphml_path}")

    html_path = output_path / "document_links.html"
    visualize_html(graph_json, str(html_path))


def graph_to_json(G, metrics: dict) -> dict:
    """Graph object -> JSON-friendly dict."""
    nodes = []
    edges = []

    for node_id, data in G.nodes(data=True):
        payload = dict(data)
        payload["id"] = node_id
        payload["color"] = NODE_COLORS.get(payload.get("node_type", "unknown"), NODE_COLORS["unknown"])
        nodes.append(payload)

    for source, target, key, data in G.edges(keys=True, data=True):
        payload = dict(data)
        payload["source"] = source
        payload["target"] = target
        payload["key"] = key
        payload["color"] = EDGE_COLORS.get(payload.get("relation", ""), "#666666")
        edges.append(payload)

    return {
        "nodes": nodes,
        "edges": edges,
        "metrics": metrics,
    }


def sanitized_graph(G):
    """Копия графа с атрибутами, совместимыми с GraphML."""
    import networkx as nx

    H = nx.MultiDiGraph()
    for node_id, data in G.nodes(data=True):
        H.add_node(node_id, **sanitize_attrs(data))
    for source, target, key, data in G.edges(keys=True, data=True):
        H.add_edge(source, target, key=key, **sanitize_attrs(data))
    return H


def sanitize_attrs(attrs: dict) -> dict:
    """GraphML поддерживает только простые типы."""
    result = {}
    for key, value in attrs.items():
        if value is None:
            result[key] = ""
        elif isinstance(value, (str, int, float, bool)):
            result[key] = value
        else:
            result[key] = json.dumps(value, ensure_ascii=False)
    return result


def visualize_html(graph_json: dict, output_path: str) -> None:
    """Самодостаточная D3-визуализация linking-графа."""
    graph_data = json.dumps(graph_json, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Document Linking Graph</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#101113;color:#ddd;font-family:system-ui,-apple-system,sans-serif;overflow:hidden}}
#graph{{width:100vw;height:100vh}}
.tooltip{{position:absolute;background:#1f2329;border:1px solid #3b414b;border-radius:8px;padding:10px 12px;max-width:360px;font-size:13px;line-height:1.45;pointer-events:none;opacity:0;transition:opacity .12s;z-index:20}}
.tooltip b{{color:#fff;font-size:14px}}
.muted{{color:#9ca3af}}
#panel{{position:fixed;top:14px;left:14px;background:#171a1f;border:1px solid #303642;border-radius:10px;padding:12px 14px;z-index:10;max-width:340px}}
#panel h1{{font-size:15px;font-weight:650;margin-bottom:8px;color:#fff}}
.stat-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:12px;color:#b8c0cc}}
.stat-grid b{{font-size:18px;color:#fff;font-weight:650}}
#search{{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:10}}
#search input{{width:320px;background:#1c2026;color:#eee;border:1px solid #3b414b;border-radius:8px;padding:8px 12px;font-size:14px;outline:none}}
#legend{{position:fixed;right:14px;bottom:14px;background:#171a1f;border:1px solid #303642;border-radius:10px;padding:12px 14px;z-index:10;max-width:270px;max-height:48vh;overflow:auto}}
.leg-title{{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#94a3b8;margin:8px 0 6px}}
.leg-item{{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:12px;color:#b8c0cc;cursor:pointer;user-select:none}}
.leg-dot{{width:10px;height:10px;border-radius:50%}}
.leg-line{{width:18px;height:0;border-top:2px solid #777}}
#controls{{position:fixed;right:14px;top:14px;display:flex;gap:8px;z-index:10}}
button{{background:#1c2026;color:#ddd;border:1px solid #3b414b;border-radius:7px;padding:7px 10px;cursor:pointer}}
button:hover{{background:#272c35}}
.filter-row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
.mini{{font-size:12px;padding:5px 8px}}
.hidden{{opacity:.08}}
</style>
</head>
<body>
<div id="graph"></div>
<div class="tooltip" id="tip"></div>
<div id="panel">
  <h1>Document Linking Graph</h1>
  <div class="stat-grid">
    <div><b id="nNodes">0</b><br>узлов</div>
    <div><b id="nEdges">0</b><br>рёбер</div>
    <div><b id="nEntities">0</b><br>сущностей</div>
    <div><b id="nFigures">0</b><br>рисунков</div>
  </div>
  <div class="filter-row">
    <button class="mini" onclick="hideChunks()">Hide chunks</button>
    <button class="mini" onclick="showOnlyCore()">Core view</button>
    <button class="mini" onclick="showAllTypes()">Show all</button>
  </div>
</div>
<div id="search"><input placeholder="Поиск entity / figure / chunk..." oninput="searchGraph(this.value)"></div>
<div id="controls">
  <button onclick="zoomBy(1.35)">+</button>
  <button onclick="zoomBy(0.75)">-</button>
  <button onclick="resetZoom()">Reset</button>
  <button onclick="toggleLabels()">Labels</button>
</div>
<div id="legend"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const graph = {graph_data};
const nodes = graph.nodes;
const edges = graph.edges;
const metrics = graph.metrics;
let activeTypes = new Set(nodes.map(d => d.node_type || "unknown"));
let currentQuery = "";
let labelsVisible = true;

document.getElementById("nNodes").textContent = metrics.nodes || nodes.length;
document.getElementById("nEdges").textContent = metrics.edges || edges.length;
document.getElementById("nEntities").textContent = metrics.entities || 0;
document.getElementById("nFigures").textContent = metrics.figures || 0;

const width = window.innerWidth;
const height = window.innerHeight;
const svg = d3.select("#graph").append("svg").attr("width", width).attr("height", height);
const g = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.08, 6]).on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);

const nodeById = new Map(nodes.map(d => [d.id, d]));
edges.forEach(e => {{ e.sourceNode = nodeById.get(e.source); e.targetNode = nodeById.get(e.target); }});

const sim = d3.forceSimulation(nodes)
  .force("link", d3.forceLink(edges).id(d => d.id).distance(d => linkDistance(d)).strength(0.26))
  .force("charge", d3.forceManyBody().strength(d => chargeStrength(d)))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide().radius(d => nodeSize(d) + 8));

const link = g.append("g").selectAll("line").data(edges).join("line")
  .attr("stroke", d => d.color || "#555")
  .attr("stroke-width", d => Math.min(4, 0.8 + (d.weight || 1) * 0.35))
  .attr("stroke-opacity", 0.34);

const node = g.append("g").selectAll("circle").data(nodes).join("circle")
  .attr("r", d => nodeSize(d))
  .attr("fill", d => d.color || "#777")
  .attr("stroke", "#111")
  .attr("stroke-width", 1.2)
  .attr("cursor", "pointer")
  .call(d3.drag().on("start", dragStart).on("drag", dragging).on("end", dragEnd));

const label = g.append("g").selectAll("text").data(nodes).join("text")
  .text(d => labelText(d))
  .attr("fill", "#d1d5db")
  .attr("font-size", d => d.node_type === "entity" ? 12 : 10)
  .attr("text-anchor", "middle")
  .attr("dy", d => nodeSize(d) + 13)
  .attr("pointer-events", "none");

const tip = d3.select("#tip");
node.on("mouseover", (event, d) => {{
  const html = [
    "<b>" + escapeHtml(d.label || d.id) + "</b>",
    "<span class='muted'>" + escapeHtml(d.node_type || "") + "</span>",
    d.entity_type ? "Entity type: " + escapeHtml(d.entity_type) : "",
    d.chunk_id ? "Chunk: " + escapeHtml(d.chunk_id) : "",
    d.element_id ? "Element: " + escapeHtml(d.element_id) : "",
    d.section_title ? "Section: " + escapeHtml(d.section_title) : "",
    d.ref_label ? "Ref: " + escapeHtml(d.ref_label) : "",
    d.caption ? "Caption: " + escapeHtml(d.caption).slice(0, 220) : "",
    d.image_path ? "Image: " + escapeHtml(d.image_path.split('/').slice(-1)[0]) : "",
    d.page_number !== undefined ? "Page: " + d.page_number : "",
    d.page_start !== undefined ? "Pages: " + d.page_start + "-" + d.page_end : "",
    d.text_preview ? "<span class='muted'>" + escapeHtml(d.text_preview).slice(0, 220) + "</span>" : "",
    d.context ? "<span class='muted'>" + escapeHtml(d.context).slice(0, 220) + "</span>" : ""
  ].filter(Boolean).join("<br>");
  tip.style("opacity", 1).html(html);
}}).on("mousemove", event => {{
  tip.style("left", (event.pageX + 14) + "px").style("top", (event.pageY - 10) + "px");
}}).on("mouseout", () => tip.style("opacity", 0));

sim.on("tick", () => {{
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  node.attr("cx", d => d.x).attr("cy", d => d.y);
  label.attr("x", d => d.x).attr("y", d => d.y);
}});

function nodeSize(d) {{
  if (d.node_type === "document") return 18;
  if (d.node_type === "entity") return Math.min(22, 8 + (d.frequency || 1) * 1.6);
  if (d.node_type === "chunk") return 8;
  if (d.node_type === "figure") return 13;
  if (d.node_type === "table") return 12;
  if (d.node_type === "caption") return 9;
  return 7;
}}
function linkDistance(d) {{
  const rel = d.relation || "";
  if (rel === "MENTIONED_IN") return 80;
  if (rel === "DISCUSSED_NEAR") return 95;
  if (rel === "HAS_CAPTION") return 45;
  return 70;
}}
function chargeStrength(d) {{
  if (d.node_type === "document") return -420;
  if (d.node_type === "entity") return -180;
  return -95;
}}
function labelText(d) {{
  if (d.node_type === "entity") return d.label || "";
  if (["figure", "caption", "table"].includes(d.node_type)) return d.ref_label || d.label || "";
  if (d.node_type === "document") return d.label || "";
  return "";
}}
function dragStart(e, d) {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }}
function dragging(e, d) {{ d.fx = e.x; d.fy = e.y; }}
function dragEnd(e, d) {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }}
function zoomBy(k) {{ svg.transition().duration(180).call(zoom.scaleBy, k); }}
function resetZoom() {{ svg.transition().duration(220).call(zoom.transform, d3.zoomIdentity); }}
function toggleLabels() {{
  labelsVisible = !labelsVisible;
  updateVisibility();
}}
function searchGraph(q) {{
  currentQuery = (q || "").toLowerCase();
  updateVisibility();
}}
function nodeMatchesQuery(d) {{
  if (!currentQuery) return true;
  return (d.label || "").toLowerCase().includes(currentQuery) ||
    (d.ref_label || "").toLowerCase().includes(currentQuery) ||
    (d.section_title || "").toLowerCase().includes(currentQuery) ||
    (d.caption || "").toLowerCase().includes(currentQuery) ||
    (d.entity_type || "").toLowerCase().includes(currentQuery) ||
    (d.id || "").toLowerCase().includes(currentQuery);
}}
function nodeVisible(d) {{
  return activeTypes.has(d.node_type || "unknown") && nodeMatchesQuery(d);
}}
function updateVisibility() {{
  const visibleIds = new Set(nodes.filter(nodeVisible).map(d => d.id));
  node.attr("opacity", d => visibleIds.has(d.id) ? 1 : 0.08);
  label.attr("opacity", d => visibleIds.has(d.id) && labelsVisible ? 1 : 0);
  link.attr("opacity", d => {{
    const s = typeof d.source === "object" ? d.source.id : d.source;
    const t = typeof d.target === "object" ? d.target.id : d.target;
    return visibleIds.has(s) && visibleIds.has(t) ? 0.42 : 0.018;
  }});
}}
function setType(type, enabled) {{
  if (enabled) activeTypes.add(type); else activeTypes.delete(type);
  updateVisibility();
}}
function hideChunks() {{
  activeTypes.delete("chunk");
  document.querySelectorAll("[data-node-type='chunk']").forEach(cb => cb.checked = false);
  updateVisibility();
}}
function showOnlyCore() {{
  activeTypes = new Set(["document", "entity", "figure", "caption", "table"]);
  document.querySelectorAll("[data-node-type]").forEach(cb => cb.checked = activeTypes.has(cb.dataset.nodeType));
  updateVisibility();
}}
function showAllTypes() {{
  activeTypes = new Set(nodes.map(d => d.node_type || "unknown"));
  document.querySelectorAll("[data-node-type]").forEach(cb => cb.checked = true);
  updateVisibility();
}}
function escapeHtml(text) {{
  return String(text).replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}}[c]));
}}

const typeCounts = metrics.node_type_counts || {{}};
const legend = d3.select("#legend");
legend.append("div").attr("class", "leg-title").text("Node types");
Object.entries(typeCounts).sort((a,b) => b[1]-a[1]).forEach(([type, count]) => {{
  const color = (nodes.find(n => n.node_type === type) || {{}}).color || "#777";
  const row = legend.append("label").attr("class", "leg-item");
  row.append("input")
    .attr("type", "checkbox")
    .attr("checked", true)
    .attr("data-node-type", type)
    .on("change", function() {{ setType(type, this.checked); }});
  row.append("div").attr("class", "leg-dot").style("background", color);
  row.append("span").text(type + " (" + count + ")");
}});

legend.append("div").attr("class", "leg-title").text("Relations");
Object.entries(metrics.edge_relation_counts || {{}}).sort((a,b) => b[1]-a[1]).forEach(([rel, count]) => {{
  const edge = edges.find(e => e.relation === rel) || {{}};
  const row = legend.append("div").attr("class", "leg-item");
  row.append("div").attr("class", "leg-line").style("border-color", edge.color || "#777");
  row.append("span").text(rel + " (" + count + ")");
}});
updateVisibility();
</script>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info(f"HTML: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: УТИЛИТЫ
# ══════════════════════════════════════════════════════════════

def document_node_id(doc_name: str) -> str:
    return f"doc::{doc_name}"


def chunk_node_id(chunk_id: str) -> str:
    return f"chunk::{chunk_id}"


def element_node_id(element_id: str) -> str:
    return f"element::{element_id}"


def entity_node_id(entity: dict) -> str:
    doc_name = entity.get("_doc_name", Path(entity.get("source_doc", "")).stem)
    etype = entity.get("entity_type", "UNKNOWN")
    normalized = entity.get("normalized") or entity.get("text", "").lower()
    return f"entity::{doc_name}::{etype}::{normalized}"


def split_csv(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in str(value).split(",") if part.strip()]


def chunk_label(chunk: dict) -> str:
    section = chunk.get("section_title", "")
    if section:
        return section[:80]
    return chunk.get("chunk_id", "chunk")


def element_label(element: dict) -> str:
    if element.get("ref_label"):
        return element["ref_label"]
    if element.get("caption"):
        return element["caption"][:80]
    text = element.get("text") or element.get("text_preview") or ""
    if text:
        return text[:80]
    return element.get("element_id", "element")


def print_stats(metrics: dict) -> None:
    """Консольная сводка для Phase 5."""
    print(f"\n{'═' * 60}")
    print("  DOCUMENT LINKING GRAPH")
    print(f"{'═' * 60}")
    print(f"  Узлов:             {metrics['nodes']}")
    print(f"  Рёбер:             {metrics['edges']}")
    print(f"  Документов:        {metrics['documents']}")
    print(f"  Сущностей:         {metrics['entities']}")
    print(f"  Чанков:            {metrics['chunks']}")
    print(f"  Рисунков:          {metrics['figures']}")
    print(f"  Подписей:          {metrics['captions']}")
    print(f"  Таблиц:            {metrics['tables']}")
    print(f"  Figure-caption:    {metrics['caption_links']}")
    print(f"  Entity-figure:     {metrics['entity_figure_links']}")
    print(f"  Entity-table:      {metrics['entity_table_links']}")
    print(f"{'─' * 60}")
    print("  Типы рёбер:")
    for relation, count in sorted(metrics["edge_relation_counts"].items(), key=lambda x: -x[1]):
        print(f"    {relation:16s} │ {count:4d}")
    print(f"{'═' * 60}\n")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: ГЛАВНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════════════════

def run_phase5(
    entities_dir: str,
    chunked_dir: str,
    parsed_dir: str,
    output_dir: str,
):
    """Полный запуск Phase 5."""
    G, metrics = build_linking_graph(
        entities_dir=entities_dir,
        chunked_dir=chunked_dir,
        parsed_dir=parsed_dir,
    )
    print_stats(metrics)
    export_linking_graph(G, metrics, output_dir)
    return G, metrics


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Фаза 5: Linking-граф документа")
    parser.add_argument("--entities", "-e", required=True, help="Папка с *_entities.json")
    parser.add_argument("--chunked", "-c", required=True, help="Папка с *_chunked.json")
    parser.add_argument("--parsed", "-p", required=True, help="Папка с *_parsed.json")
    parser.add_argument("--output", "-o", default="outputs", help="Папка для результатов")
    args = parser.parse_args()

    run_phase5(
        entities_dir=args.entities,
        chunked_dir=args.chunked,
        parsed_dir=args.parsed,
        output_dir=args.output,
    )

    print("Готово!")
    print(f"   HTML:    {args.output}/document_links.html")
    print(f"   GraphML: {args.output}/document_links.graphml")
    print(f"   JSON:    {args.output}/document_links.json")
