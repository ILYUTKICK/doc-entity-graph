"""Фаза 5: построение linking-графа документа.

Вход:
    Структурные элементы из Фазы 1, чанки из Фазы 2 и сущности из Фазы 3.

Выход:
    ``outputs/document_links.*`` и ``outputs/linking_metrics.json``.

Граф связывает сущности с чанками, рисунками, таблицами и подписями, чтобы
структура документа оставалась видимой после чанкинга текста.
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
    max_entity_links_per_element: int = 12,
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
    discussed_near_candidates = defaultdict(dict)

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
            page_start=entity.get("page_start", 0),
            page_end=entity.get("page_end", 0),
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
                collect_discussed_near_candidate(
                    discussed_near_candidates,
                    ent_node,
                    entity,
                    element_id,
                    elements_by_id,
                    chunks_by_id,
                    base_score=2.0,
                    reason="source_element",
                )

        for element_id in entity.get("related_element_ids", []):
            collect_discussed_near_candidate(
                discussed_near_candidates,
                ent_node,
                entity,
                element_id,
                elements_by_id,
                chunks_by_id,
                base_score=1.0,
                reason="related_element",
            )

    discussion_stats = add_ranked_discussed_near_edges(
        G,
        discussed_near_candidates,
        elements_by_id,
        max_entity_links_per_element=max_entity_links_per_element,
    )

    metrics = compute_linking_metrics(G)
    metrics.update(discussion_stats)
    log.info(f"Linking-граф построен: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    return G, metrics


def add_edge(
    G,
    source: str,
    target: str,
    relation: str,
    weight: float = 1,
    evidence_count: int = 1,
    **attrs,
) -> None:
    """Добавляет или усиливает directed edge с ключом relation."""
    if source == target:
        return

    if G.has_edge(source, target, key=relation):
        edge = G[source][target][relation]
        edge["weight"] = round(edge.get("weight", 1) + weight, 4)
        edge["evidence_count"] = edge.get("evidence_count", 1) + evidence_count
        for key, value in attrs.items():
            if value not in ("", None):
                edge[key] = value
        return

    G.add_edge(
        source,
        target,
        key=relation,
        relation=relation,
        weight=round(weight, 4),
        evidence_count=evidence_count,
        **attrs,
    )


def collect_discussed_near_candidate(
    candidates: dict,
    ent_node: str,
    entity: dict,
    element_id: str,
    elements_by_id: dict,
    chunks_by_id: dict,
    base_score: float,
    reason: str,
) -> None:
    """Кандидат Entity -> structured element до top-N фильтрации."""
    element = elements_by_id.get(element_id)
    if not element or element.get("element_type") not in STRUCTURED_TYPES:
        return

    chunk_ids = split_csv(entity.get("chunk_id", ""))
    evidence_chunks = []
    score = base_score + min(len(chunk_ids), 8) * 0.15

    for chunk_id in chunk_ids:
        chunk = chunks_by_id.get(chunk_id, {})
        if element_id in chunk.get("source_element_ids", []):
            score += 2.0
            evidence_chunks.append(chunk_id)
        if element_id in chunk.get("related_element_ids", []):
            score += 1.0
            evidence_chunks.append(chunk_id)

    if _same_section(entity, element):
        score += 0.4
    if _same_page(entity, element):
        score += 0.6

    confidence = entity.get("confidence", 0) or 0
    score += min(float(confidence), 1.0) * 0.35

    bucket = candidates[element_id]
    candidate = bucket.setdefault(
        ent_node,
        {
            "score": 0.0,
            "entity_label": entity.get("text", ""),
            "entity_type": entity.get("entity_type", ""),
            "confidence": confidence,
            "evidence_chunks": set(),
            "reasons": set(),
        },
    )
    candidate["score"] += score
    candidate["confidence"] = max(candidate.get("confidence", 0), confidence)
    candidate["evidence_chunks"].update(evidence_chunks or chunk_ids[:3])
    candidate["reasons"].add(reason)


def add_ranked_discussed_near_edges(
    G,
    candidates: dict,
    elements_by_id: dict,
    max_entity_links_per_element: int,
) -> dict:
    """Оставляет top-N Entity -> Figure/Table/Caption связей для каждого элемента."""
    total_candidates = sum(len(bucket) for bucket in candidates.values())
    kept = 0

    for element_id, bucket in candidates.items():
        element = elements_by_id.get(element_id, {})
        sorted_candidates = sorted(
            bucket.items(),
            key=lambda item: (
                -item[1].get("score", 0),
                -len(item[1].get("evidence_chunks", [])),
                item[1].get("entity_label", ""),
            ),
        )
        if max_entity_links_per_element > 0:
            sorted_candidates = sorted_candidates[:max_entity_links_per_element]

        for ent_node, candidate in sorted_candidates:
            evidence_chunks = sorted(candidate.get("evidence_chunks", []))
            score = round(candidate.get("score", 0), 4)
            add_edge(
                G,
                ent_node,
                element_node_id(element_id),
                "DISCUSSED_NEAR",
                weight=max(score, 1),
                evidence_count=max(1, len(evidence_chunks)),
                score=score,
                reasons=",".join(sorted(candidate.get("reasons", []))),
                evidence_chunks=",".join(evidence_chunks[:12]),
                element_type=element.get("element_type", ""),
            )
            kept += 1

    return {
        "max_entity_links_per_element": max_entity_links_per_element,
        "discussed_near_candidates": total_candidates,
        "discussed_near_kept": kept,
        "discussed_near_pruned": max(0, total_candidates - kept),
    }


def _same_section(entity: dict, element: dict) -> bool:
    entity_section = entity.get("section_title") or ""
    element_section = element.get("section_title") or ""
    return bool(entity_section and element_section and entity_section == element_section)


def _same_page(entity: dict, element: dict) -> bool:
    page = element.get("page_number")
    if page is None:
        return False
    try:
        page = int(page)
        start = int(entity.get("page_start", page))
        end = int(entity.get("page_end", start))
    except (TypeError, ValueError):
        return False
    return start <= page <= end


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
#panel{{position:fixed;top:14px;left:14px;background:#171a1f;border:1px solid #303642;border-radius:10px;padding:12px 14px;z-index:10;max-width:390px}}
#panel h1{{font-size:15px;font-weight:650;margin-bottom:8px;color:#fff}}
.stat-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-size:12px;color:#b8c0cc}}
.stat-grid b{{font-size:18px;color:#fff;font-weight:650}}
.hint{{font-size:11px;line-height:1.35;color:#94a3b8;margin-top:8px}}
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
button.active{{background:#2563eb;border-color:#3b82f6;color:#fff}}
.filter-row{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
.mini{{font-size:12px;padding:5px 8px}}
.scope-row{{display:grid;grid-template-columns:1fr 105px;gap:8px;margin-top:8px}}
.focus-row{{margin-top:8px}}
.focus-row select{{width:100%}}
select{{min-width:0;background:#1c2026;color:#ddd;border:1px solid #3b414b;border-radius:7px;padding:6px 8px;font-size:12px;outline:none}}
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
    <div><b id="nVisible">0</b><br>видно</div>
    <div><b id="nPruned">0</b><br>скрыто связей</div>
  </div>
  <div class="hint" id="pruneInfo"></div>
  <div class="filter-row">
    <button class="mini" data-mode-button="core" onclick="showOnlyCore()">Core</button>
    <button class="mini" data-mode-button="figures" onclick="showFigureFocus()">Figures</button>
    <button class="mini" data-mode-button="tables" onclick="showTableFocus()">Tables</button>
    <button class="mini" data-mode-button="all" onclick="showAllTypes()">Show all</button>
  </div>
  <div class="scope-row">
    <select id="sectionFilter" onchange="setSectionFilter(this.value)"></select>
    <select id="pageFilter" onchange="setPageFilter(this.value)"></select>
  </div>
  <div class="focus-row">
    <select id="elementFilter" onchange="setElementFocus(this.value)"></select>
  </div>
</div>
<div id="search"><input placeholder="Поиск entity / figure / chunk..." oninput="searchGraph(this.value)"></div>
<div id="controls">
  <button onclick="zoomBy(1.35)">+</button>
  <button onclick="zoomBy(0.75)">-</button>
  <button onclick="resetZoom()">Reset</button>
  <button id="labelButton" onclick="toggleLabels()">Labels</button>
</div>
<div id="legend"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const graph = {graph_data};
const nodes = graph.nodes;
const edges = graph.edges;
const metrics = graph.metrics;
const CORE_TYPES = ["document", "entity", "figure", "table", "title"];
const FIGURE_TYPES = ["entity", "figure", "caption"];
const TABLE_TYPES = ["entity", "table", "caption"];
let currentMode = "core";
let activeTypes = new Set(CORE_TYPES.filter(t => nodes.some(d => d.node_type === t)));
let currentQuery = "";
let currentSection = "";
let currentPage = "";
let currentElementFocus = "";
let activeElementFocusIds = null;
let labelsVisible = false;

document.getElementById("nNodes").textContent = metrics.nodes || nodes.length;
document.getElementById("nEdges").textContent = metrics.edges || edges.length;
document.getElementById("nEntities").textContent = metrics.entities || 0;
document.getElementById("nFigures").textContent = metrics.figures || 0;
document.getElementById("nPruned").textContent = metrics.discussed_near_pruned || 0;
document.getElementById("pruneInfo").textContent =
  "DISCUSSED_NEAR top-N: " + (metrics.max_entity_links_per_element || "all") +
  " на элемент; кандидатов " + (metrics.discussed_near_candidates || 0) +
  ", оставлено " + (metrics.discussed_near_kept || 0) + ".";

const width = window.innerWidth;
const height = window.innerHeight;
const svg = d3.select("#graph").append("svg").attr("width", width).attr("height", height);
const g = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.08, 6]).on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);

const nodeById = new Map(nodes.map(d => [d.id, d]));
edges.forEach(e => {{ e.sourceNode = nodeById.get(e.source); e.targetNode = nodeById.get(e.target); }});
const idOf = value => typeof value === "object" ? value.id : value;
const figureFocusIds = new Set();
const tableFocusIds = new Set();
const coreFocusIds = new Set();
edges.forEach(e => {{
  const s = idOf(e.source);
  const t = idOf(e.target);
  const sourceNode = nodeById.get(s);
  const targetNode = nodeById.get(t);
  if (!sourceNode || !targetNode) return;
  if (e.relation === "DISCUSSED_NEAR" && targetNode.node_type === "figure") {{
    figureFocusIds.add(s); figureFocusIds.add(t);
  }}
  if (e.relation === "DISCUSSED_NEAR" && targetNode.node_type === "table") {{
    tableFocusIds.add(s); tableFocusIds.add(t);
  }}
}});
edges.forEach(e => {{
  const s = idOf(e.source);
  const t = idOf(e.target);
  const sourceNode = nodeById.get(s);
  const targetNode = nodeById.get(t);
  if (!sourceNode || !targetNode) return;
  if (["HAS_CAPTION", "CAPTION_OF"].includes(e.relation)) {{
    if (figureFocusIds.has(s) || figureFocusIds.has(t)) {{
      figureFocusIds.add(s); figureFocusIds.add(t);
    }}
    if (tableFocusIds.has(s) || tableFocusIds.has(t)) {{
      tableFocusIds.add(s); tableFocusIds.add(t);
    }}
  }}
}});
figureFocusIds.forEach(id => coreFocusIds.add(id));
tableFocusIds.forEach(id => coreFocusIds.add(id));
nodes.forEach(d => {{
  if (["document", "title"].includes(d.node_type)) coreFocusIds.add(d.id);
}});

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
  document.getElementById("labelButton").classList.toggle("active", labelsVisible);
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
function nodePages(d) {{
  if (d.page_number !== undefined && d.page_number !== null && d.page_number !== "") {{
    const page = Number(d.page_number);
    return Number.isFinite(page) ? [page] : [];
  }}
  if (d.page_start !== undefined && d.page_end !== undefined && d.page_start !== "" && d.page_end !== "") {{
    const start = Number(d.page_start);
    const end = Number(d.page_end);
    if (!Number.isFinite(start) || !Number.isFinite(end)) return [];
    const pages = [];
    for (let p = start; p <= end; p++) pages.push(p);
    return pages;
  }}
  return [];
}}
function nodeMatchesScope(d) {{
  if (d.node_type === "document") return true;
  if (currentSection && (d.section_title || "") !== currentSection) return false;
  if (currentPage !== "") {{
    const page = Number(currentPage);
    const pages = nodePages(d);
    if (!pages.includes(page)) return false;
  }}
  return true;
}}
function nodeMatchesMode(d) {{
  if (currentElementFocus) return activeElementFocusIds && activeElementFocusIds.has(d.id);
  if (currentMode === "all") return true;
  if (currentMode === "core") return coreFocusIds.has(d.id);
  if (currentMode === "figures") return figureFocusIds.has(d.id);
  if (currentMode === "tables") return tableFocusIds.has(d.id);
  return true;
}}
function elementFocusIds(focusId) {{
  if (!focusId) return null;
  const focus = new Set([focusId]);
  edges.forEach(e => {{
    const s = idOf(e.source);
    const t = idOf(e.target);
    if (s === focusId || t === focusId) {{
      focus.add(s); focus.add(t);
    }}
  }});
  edges.forEach(e => {{
    const s = idOf(e.source);
    const t = idOf(e.target);
    if (["HAS_CAPTION", "CAPTION_OF"].includes(e.relation) && (focus.has(s) || focus.has(t))) {{
      focus.add(s); focus.add(t);
    }}
  }});
  return focus;
}}
function nodeVisible(d) {{
  return activeTypes.has(d.node_type || "unknown") &&
    nodeMatchesMode(d) &&
    nodeMatchesScope(d) &&
    nodeMatchesQuery(d);
}}
function edgeAllowedByMode(d) {{
  if (currentMode === "all") return true;
  if (currentElementFocus) {{
    return ["DISCUSSED_NEAR", "HAS_CAPTION", "CAPTION_OF", "RELATED_TO", "EXTRACTED_FROM", "MENTIONED_IN"].includes(d.relation || "");
  }}
  const relation = d.relation || "";
  if (currentMode === "figures") return ["DISCUSSED_NEAR", "HAS_CAPTION", "CAPTION_OF"].includes(relation);
  if (currentMode === "tables") return relation === "DISCUSSED_NEAR" || ["HAS_CAPTION", "CAPTION_OF"].includes(relation);
  return ["DISCUSSED_NEAR", "HAS_CAPTION", "CAPTION_OF"].includes(relation);
}}
function updateVisibility() {{
  const visibleIds = new Set(nodes.filter(nodeVisible).map(d => d.id));
  node.style("display", d => visibleIds.has(d.id) ? null : "none")
    .attr("opacity", d => visibleIds.has(d.id) ? 1 : 0);
  label.style("display", d => visibleIds.has(d.id) && labelsVisible ? null : "none")
    .attr("opacity", d => visibleIds.has(d.id) && labelsVisible ? 1 : 0);
  link.style("display", d => {{
    const s = idOf(d.source);
    const t = idOf(d.target);
    return visibleIds.has(s) && visibleIds.has(t) && edgeAllowedByMode(d) ? null : "none";
  }}).attr("opacity", d => {{
    const relation = d.relation || "";
    if (relation === "DISCUSSED_NEAR") return 0.48;
    if (["HAS_CAPTION", "CAPTION_OF"].includes(relation)) return 0.72;
    return currentMode === "all" ? 0.28 : 0.18;
  }});
  document.getElementById("nVisible").textContent = visibleIds.size;
  syncLegend();
  syncModeButtons();
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
  currentMode = "core";
  currentElementFocus = "";
  activeElementFocusIds = null;
  labelsVisible = false;
  activeTypes = new Set(CORE_TYPES.filter(t => nodes.some(d => d.node_type === t)));
  updateVisibility();
}}
function showFigureFocus() {{
  currentMode = "figures";
  currentElementFocus = "";
  activeElementFocusIds = null;
  labelsVisible = false;
  activeTypes = new Set(FIGURE_TYPES.filter(t => nodes.some(d => d.node_type === t)));
  updateVisibility();
}}
function showTableFocus() {{
  currentMode = "tables";
  currentElementFocus = "";
  activeElementFocusIds = null;
  labelsVisible = false;
  activeTypes = new Set(TABLE_TYPES.filter(t => nodes.some(d => d.node_type === t)));
  updateVisibility();
}}
function showAllTypes() {{
  currentMode = "all";
  currentElementFocus = "";
  activeElementFocusIds = null;
  labelsVisible = false;
  activeTypes = new Set(nodes.map(d => d.node_type || "unknown"));
  updateVisibility();
}}
function setSectionFilter(value) {{
  currentSection = value || "";
  updateVisibility();
}}
function setPageFilter(value) {{
  currentPage = value || "";
  updateVisibility();
}}
function setElementFocus(value) {{
  currentElementFocus = value || "";
  if (currentElementFocus) {{
    currentMode = "element";
    activeElementFocusIds = elementFocusIds(currentElementFocus);
    currentSection = "";
    currentPage = "";
    document.getElementById("sectionFilter").value = "";
    document.getElementById("pageFilter").value = "";
    labelsVisible = false;
    activeTypes = new Set(["document", "entity", "figure", "caption", "table", "chunk"].filter(t => nodes.some(d => d.node_type === t)));
  }} else {{
    activeElementFocusIds = null;
  }}
  updateVisibility();
}}
function syncLegend() {{
  document.querySelectorAll("[data-node-type]").forEach(cb => cb.checked = activeTypes.has(cb.dataset.nodeType));
  document.getElementById("labelButton").classList.toggle("active", labelsVisible);
}}
function syncModeButtons() {{
  document.querySelectorAll("[data-mode-button]").forEach(btn => {{
    btn.classList.toggle("active", btn.dataset.modeButton === currentMode);
  }});
  const elementSelect = document.getElementById("elementFilter");
  if (elementSelect && elementSelect.value !== currentElementFocus) elementSelect.value = currentElementFocus;
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
    .property("checked", activeTypes.has(type))
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

const sectionSelect = document.getElementById("sectionFilter");
const pageSelect = document.getElementById("pageFilter");
const elementSelect = document.getElementById("elementFilter");
sectionSelect.innerHTML = "<option value=''>Все разделы</option>";
Array.from(new Set(nodes.map(d => d.section_title).filter(Boolean)))
  .sort((a, b) => a.localeCompare(b, "ru"))
  .forEach(section => {{
    const opt = document.createElement("option");
    opt.value = section;
    opt.textContent = section.length > 52 ? section.slice(0, 49) + "..." : section;
    sectionSelect.appendChild(opt);
  }});
pageSelect.innerHTML = "<option value=''>Все стр.</option>";
Array.from(new Set(nodes.flatMap(nodePages)))
  .sort((a, b) => a - b)
  .forEach(page => {{
    const opt = document.createElement("option");
    opt.value = String(page);
    opt.textContent = "стр. " + (page + 1);
    pageSelect.appendChild(opt);
  }});
elementSelect.innerHTML = "<option value=''>Фокус: график/таблица</option>";
nodes
  .filter(d => ["figure", "table"].includes(d.node_type))
  .sort((a, b) => {{
    const pageA = Number(a.page_number ?? 999999);
    const pageB = Number(b.page_number ?? 999999);
    if (pageA !== pageB) return pageA - pageB;
    return (a.label || a.id).localeCompare(b.label || b.id, "ru");
  }})
  .forEach(d => {{
    const opt = document.createElement("option");
    const page = d.page_number !== undefined && d.page_number !== "" ? "стр. " + (Number(d.page_number) + 1) + " · " : "";
    opt.value = d.id;
    opt.textContent = page + (d.ref_label || d.label || d.id).slice(0, 72);
    elementSelect.appendChild(opt);
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
    if "discussed_near_candidates" in metrics:
        print(f"  DISCUSSED kept:    {metrics['discussed_near_kept']}")
        print(f"  DISCUSSED pruned:  {metrics['discussed_near_pruned']}")
        print(f"  Top-N per element: {metrics['max_entity_links_per_element']}")
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
    max_entity_links_per_element: int = 12,
):
    """Полный запуск Phase 5."""
    G, metrics = build_linking_graph(
        entities_dir=entities_dir,
        chunked_dir=chunked_dir,
        parsed_dir=parsed_dir,
        max_entity_links_per_element=max_entity_links_per_element,
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
    parser.add_argument(
        "--max-entity-links-per-element",
        type=int,
        default=12,
        help="Top-N связей Entity -> Figure/Table/Caption на каждый структурный элемент; 0 = без ограничения",
    )
    args = parser.parse_args()

    run_phase5(
        entities_dir=args.entities,
        chunked_dir=args.chunked,
        parsed_dir=args.parsed,
        output_dir=args.output,
        max_entity_links_per_element=args.max_entity_links_per_element,
    )

    print("Готово!")
    print(f"   HTML:    {args.output}/document_links.html")
    print(f"   GraphML: {args.output}/document_links.graphml")
    print(f"   JSON:    {args.output}/document_links.json")
