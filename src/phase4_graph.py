"""Фаза 4: построение базового графа совместной встречаемости сущностей.

Вход:
    ``data/entities/*_entities.json`` из Фазы 3.

Выход:
    GraphML, JSON и HTML-артефакты графа.

Граф связывает сущности, которые встречаются в одном чанке. Это
воспроизводимая базовая версия, а не полноценное извлечение отношений.
"""

import json
import logging
import re
import math
from pathlib import Path
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict
from typing import Optional
from itertools import combinations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase4")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: ЗАГРУЗКА ДАННЫХ
# ══════════════════════════════════════════════════════════════

def load_all_entities(input_dir: str) -> list[dict]:
    """Загрузка сущностей из всех *_entities.json."""
    input_path = Path(input_dir)
    entity_files = sorted(input_path.glob("*_entities.json"))

    if not entity_files:
        raise FileNotFoundError(f"Не найдено *_entities.json в {input_dir}")

    all_entities = []
    sources = []

    for ef in entity_files:
        with open(ef, "r", encoding="utf-8") as f:
            data = json.load(f)

        doc_name = Path(data["source"]).stem
        sources.append(doc_name)

        for ent in data["entities"]:
            ent["_source_doc"] = doc_name
            all_entities.append(ent)

        log.info(f"Загружено из {doc_name}: {len(data['entities'])} сущностей")

    log.info(f"Всего сущностей: {len(all_entities)} из {len(sources)} документов")
    return all_entities


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: ФИЛЬТРАЦИЯ ШУМА
# ══════════════════════════════════════════════════════════════

# Паттерны шума: LaTeX, OCR-мусор, слишком короткие
NOISE_PATTERNS = [
    r"\\(end|begin|frac|sigma|alpha|beta|sum|left|right)\b",
    r"^[\d\s\+\-\*\/\=\{\}\(\)\[\]\\.,;:]+$",
    r"^[A-Z]{1,2}\d+$",
    r"Add\s*Sk",
    r"^[а-яa-z]{1,2}$",
]
NOISE_RE = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]

# Типы сущностей, которые не интересны для графа
SKIP_TYPES = {"NUMBER", "PERCENT", "QUANTITY", "MONEY"}


def filter_entities(entities: list[dict], min_confidence: float = 0.5) -> list[dict]:
    """Фильтрация шумовых сущностей перед построением графа."""
    filtered = []
    removed = Counter()

    for ent in entities:
        text = ent.get("text", "").strip()
        etype = ent.get("entity_type", "")

        # Пропуск неинтересных типов
        if etype in SKIP_TYPES:
            removed["skip_type"] += 1
            continue

        # Пропуск коротких
        if len(text) < 3:
            removed["too_short"] += 1
            continue

        # Пропуск шумовых паттернов
        if any(r.search(text) for r in NOISE_RE):
            removed["noise_pattern"] += 1
            continue

        # Пропуск слишком длинных
        if len(text) > 100:
            removed["too_long"] += 1
            continue

        filtered.append(ent)

    total_removed = sum(removed.values())
    log.info(f"Отфильтровано: {total_removed} сущностей ({dict(removed)})")
    log.info(f"Осталось: {len(filtered)} сущностей")
    return filtered


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: ENTITY RESOLUTION (ДЕДУПЛИКАЦИЯ)
# ══════════════════════════════════════════════════════════════

def resolve_entities(entities: list[dict]) -> tuple[list[dict], dict]:
    """
    Cross-document entity resolution.

    Объединяет:
      - Точные совпадения normalized формы
      - Fuzzy matching (Levenshtein < 3)
      - Аббревиатуры (CAPM = Capital Asset Pricing Model)

    Returns:
        resolved: список уникальных сущностей
        merge_map: {original_normalized → canonical_normalized}
    """
    # Группировка по normalized тексту
    groups = defaultdict(list)
    for ent in entities:
        norm = ent.get("normalized", ent["text"].lower().strip())
        groups[norm].append(ent)

    # Fuzzy merge: объединяем похожие ключи
    merge_map = {}
    canonical_keys = sorted(groups.keys(), key=lambda k: -len(groups[k]))

    for i, key_a in enumerate(canonical_keys):
        if key_a in merge_map:
            continue
        for key_b in canonical_keys[i + 1:]:
            if key_b in merge_map:
                continue
            if _should_merge(key_a, key_b):
                # Merge key_b → key_a (более частый)
                merge_map[key_b] = key_a
                groups[key_a].extend(groups[key_b])

    # Построение resolved сущностей
    resolved = []
    for norm, group in groups.items():
        if norm in merge_map:
            continue

        # Выбираем лучший текст (самый частый, не OCR-мусор)
        text_counts = Counter(e["text"] for e in group)
        best_text = text_counts.most_common(1)[0][0]

        # Тип — самый частый
        type_counts = Counter(e["entity_type"] for e in group)
        best_type = type_counts.most_common(1)[0][0]

        # Все chunk_id
        all_chunks = set()
        for e in group:
            for cid in e.get("chunk_id", "").split(","):
                if cid.strip():
                    all_chunks.add(cid.strip())

        # Все документы
        all_docs = set(e.get("_source_doc", "") for e in group)

        # Лучший контекст
        contexts = [e.get("context", "") for e in group if e.get("context")]
        best_context = max(contexts, key=len) if contexts else ""

        resolved.append({
            "text": best_text,
            "normalized": norm,
            "entity_type": best_type,
            "confidence": max(e.get("confidence", 0) for e in group),
            "frequency": len(group),
            "chunk_ids": sorted(all_chunks),
            "source_docs": sorted(all_docs),
            "context": best_context,
            "aliases": sorted(set(e["text"] for e in group) - {best_text}),
        })

    resolved.sort(key=lambda e: -e["frequency"])
    log.info(f"Entity resolution: {len(entities)} → {len(resolved)} уникальных")
    return resolved, merge_map


def _should_merge(a: str, b: str) -> bool:
    """Проверка: нужно ли объединять две сущности."""
    # Точное совпадение
    if a == b:
        return True

    # Одна содержит другую
    if a in b or b in a:
        if min(len(a), len(b)) >= 3:
            return True

    # Levenshtein distance
    if len(a) > 4 and len(b) > 4:
        dist = _levenshtein(a, b)
        max_len = max(len(a), len(b))
        if dist <= 2 and dist / max_len < 0.2:
            return True

    return False


def _levenshtein(s1: str, s2: str) -> int:
    """Расстояние Левенштейна."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: ПОСТРОЕНИЕ ГРАФА
# ══════════════════════════════════════════════════════════════

def build_graph(resolved_entities: list[dict]):
    """
    Построение графа сущностей.

    Узлы = сущности
    Рёбра = co-occurrence в одном чанке (чем больше, тем сильнее связь)
    """
    import networkx as nx

    G = nx.Graph()

    # ── Добавляем узлы ──
    for ent in resolved_entities:
        G.add_node(
            ent["normalized"],
            label=ent["text"],
            entity_type=ent["entity_type"],
            frequency=ent["frequency"],
            source_docs=",".join(ent["source_docs"]),
            aliases=",".join(ent["aliases"][:5]),
            context=ent["context"][:200],
        )

    # ── Строим индекс: chunk_id → [entities] ──
    chunk_to_entities = defaultdict(list)
    for ent in resolved_entities:
        for cid in ent["chunk_ids"]:
            chunk_to_entities[cid].append(ent["normalized"])

    # ── Добавляем рёбра (co-occurrence) ──
    edge_weights = Counter()
    edge_chunks = defaultdict(set)

    for cid, ent_list in chunk_to_entities.items():
        # Все пары сущностей в одном чанке
        unique_ents = sorted(set(ent_list))
        for a, b in combinations(unique_ents, 2):
            if a != b and G.has_node(a) and G.has_node(b):
                pair = (min(a, b), max(a, b))
                edge_weights[pair] += 1
                edge_chunks[pair].add(cid)

    for (a, b), weight in edge_weights.items():
        G.add_edge(
            a, b,
            weight=weight,
            co_occurrences=weight,
            chunks=",".join(sorted(edge_chunks[(a, b)])),
        )

    log.info(f"Граф построен: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    return G


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: МЕТРИКИ ГРАФА
# ══════════════════════════════════════════════════════════════

def compute_metrics(G) -> dict:
    """Расчёт метрик графа."""
    import networkx as nx

    metrics = {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "density": round(nx.density(G), 4),
    }

    if G.number_of_nodes() == 0:
        return metrics

    # Degree
    degrees = dict(G.degree())
    metrics["avg_degree"] = round(sum(degrees.values()) / len(degrees), 2)
    metrics["max_degree_node"] = max(degrees, key=degrees.get)
    metrics["max_degree"] = degrees[metrics["max_degree_node"]]

    # PageRank
    try:
        pagerank = nx.pagerank(G, weight="weight")
        top_pr = sorted(pagerank.items(), key=lambda x: -x[1])[:10]
        metrics["top_pagerank"] = [
            {"entity": node, "score": round(score, 4)} for node, score in top_pr
        ]
    except Exception:
        metrics["top_pagerank"] = []

    # Connected components
    components = list(nx.connected_components(G))
    metrics["connected_components"] = len(components)
    metrics["largest_component"] = len(max(components, key=len)) if components else 0

    # Communities (Louvain)
    try:
        communities = nx.community.louvain_communities(G, weight="weight")
        metrics["communities"] = len(communities)
        metrics["community_sizes"] = sorted([len(c) for c in communities], reverse=True)

        # Добавляем community_id в узлы
        for comm_id, members in enumerate(communities):
            for node in members:
                G.nodes[node]["community"] = comm_id
    except Exception:
        metrics["communities"] = 0

    return metrics


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 6: ВИЗУАЛИЗАЦИЯ (D3.js — без зависимостей)
# ══════════════════════════════════════════════════════════════

# Цвета для типов сущностей
TYPE_COLORS = {
    "PERSON": "#7F77DD",
    "ORG": "#1D9E75",
    "CONCEPT": "#D85A30",
    "LOCATION": "#378ADD",
    "DATE": "#888780",
    "WORK": "#D4537E",
    "FORMULA": "#BA7517",
    "INSTRUMENT": "#639922",
    "GROUP": "#E24B4A",
    "PRODUCT": "#5DCAA5",
    "FACILITY": "#97C459",
    "LAW": "#ED93B1",
}


def visualize_html(G, output_path: str, title: str = "Граф сущностей"):
    """
    Самодостаточная HTML визуализация на D3.js.
    Не требует PyVis или других зависимостей.
    Открывается в любом браузере: open entity_graph.html
    """
    import json as _json

    # Подготовка данных для D3
    nodes = []
    for node, data in G.nodes(data=True):
        nodes.append({
            "id": node,
            "label": data.get("label", node),
            "type": data.get("entity_type", "UNKNOWN"),
            "freq": data.get("frequency", 1),
            "comm": data.get("community", 0),
            "docs": data.get("source_docs", ""),
        })

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append({
            "source": u,
            "target": v,
            "weight": data.get("weight", 1),
        })

    graph_data = _json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)

    type_colors_json = _json.dumps(TYPE_COLORS)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#111;color:#ddd;font-family:system-ui,-apple-system,sans-serif;overflow:hidden}}
#graph{{width:100vw;height:100vh}}
.tooltip{{position:absolute;background:#222;border:1px solid #444;border-radius:8px;padding:10px 14px;font-size:13px;pointer-events:none;opacity:0;transition:opacity .15s;max-width:300px;line-height:1.5}}
.tooltip b{{color:#fff;font-size:14px}}
.tooltip .t-type{{color:#999;font-size:12px}}
#controls{{position:fixed;top:16px;left:16px;display:flex;flex-direction:column;gap:8px;z-index:10}}
#controls button{{background:#222;color:#ccc;border:1px solid #444;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px}}
#controls button:hover{{background:#333}}
#legend{{position:fixed;bottom:16px;left:16px;background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:12px 16px;z-index:10}}
.leg-item{{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px;color:#aaa}}
.leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
#stats{{position:fixed;top:16px;right:16px;background:#1a1a1a;border:1px solid #333;border-radius:10px;padding:12px 16px;font-size:13px;z-index:10}}
.stat-val{{font-size:20px;font-weight:600;color:#fff}}
#search{{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:10}}
#search input{{background:#222;color:#ddd;border:1px solid #444;border-radius:8px;padding:8px 16px;width:280px;font-size:14px;outline:none}}
#search input:focus{{border-color:#7F77DD}}
</style>
</head>
<body>
<div id="graph"></div>
<div class="tooltip" id="tip"></div>
<div id="controls">
  <button onclick="zoomIn()">＋ Zoom</button>
  <button onclick="zoomOut()">－ Zoom</button>
  <button onclick="resetView()">⟲ Reset</button>
  <button onclick="toggleLabels()">Aa Labels</button>
</div>
<div id="search"><input type="text" placeholder="Поиск сущности..." oninput="searchNode(this.value)"></div>
<div id="stats">
  <div><span class="stat-val">{G.number_of_nodes()}</span> узлов</div>
  <div><span class="stat-val">{G.number_of_edges()}</span> рёбер</div>
</div>
<div id="legend"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const data = {graph_data};
const TYPE_COLORS = {type_colors_json};
const DEFAULT_COLOR = "#888";

let showLabels = true;

const width = window.innerWidth;
const height = window.innerHeight;

const svg = d3.select("#graph").append("svg")
  .attr("width", width).attr("height", height);

const g = svg.append("g");

const zoom = d3.zoom().scaleExtent([0.1, 8]).on("zoom", e => g.attr("transform", e.transform));
svg.call(zoom);

const sim = d3.forceSimulation(data.nodes)
  .force("link", d3.forceLink(data.edges).id(d => d.id).distance(80).strength(d => Math.min(0.3, d.weight * 0.05)))
  .force("charge", d3.forceManyBody().strength(-120))
  .force("center", d3.forceCenter(width / 2, height / 2))
  .force("collision", d3.forceCollide().radius(d => nodeSize(d) + 2));

const link = g.append("g").selectAll("line").data(data.edges).join("line")
  .attr("stroke", "#333").attr("stroke-width", d => Math.min(4, 0.5 + d.weight * 0.5))
  .attr("stroke-opacity", d => Math.min(0.6, 0.1 + d.weight * 0.08));

const node = g.append("g").selectAll("circle").data(data.nodes).join("circle")
  .attr("r", d => nodeSize(d))
  .attr("fill", d => TYPE_COLORS[d.type] || DEFAULT_COLOR)
  .attr("stroke", "#222").attr("stroke-width", 1)
  .attr("cursor", "pointer")
  .call(d3.drag().on("start", dragStart).on("drag", dragging).on("end", dragEnd));

const label = g.append("g").selectAll("text").data(data.nodes).join("text")
  .text(d => d.freq >= 2 ? d.label : "")
  .attr("font-size", d => Math.max(9, Math.min(14, 8 + d.freq)))
  .attr("fill", "#ccc").attr("text-anchor", "middle").attr("dy", d => nodeSize(d) + 12)
  .attr("pointer-events", "none");

const tip = d3.select("#tip");
node.on("mouseover", (e, d) => {{
  tip.style("opacity", 1).html(
    "<b>" + d.label + "</b><br>" +
    "<span class='t-type'>" + d.type + "</span><br>" +
    "Частота: " + d.freq + "<br>" +
    "Кластер: " + d.comm + "<br>" +
    "Документы: " + d.docs
  );
}}).on("mousemove", e => {{
  tip.style("left", (e.pageX + 15) + "px").style("top", (e.pageY - 10) + "px");
}}).on("mouseout", () => tip.style("opacity", 0));

sim.on("tick", () => {{
  link.attr("x1", d => d.source.x).attr("y1", d => d.source.y)
    .attr("x2", d => d.target.x).attr("y2", d => d.target.y);
  node.attr("cx", d => d.x).attr("cy", d => d.y);
  label.attr("x", d => d.x).attr("y", d => d.y);
}});

function nodeSize(d) {{ return Math.max(4, Math.min(24, 3 + d.freq * 1.5)); }}
function dragStart(e, d) {{ if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }}
function dragging(e, d) {{ d.fx = e.x; d.fy = e.y; }}
function dragEnd(e, d) {{ if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }}
function zoomIn() {{ svg.transition().call(zoom.scaleBy, 1.5); }}
function zoomOut() {{ svg.transition().call(zoom.scaleBy, 0.67); }}
function resetView() {{ svg.transition().call(zoom.transform, d3.zoomIdentity.translate(width/2,height/2).scale(0.8).translate(-width/2,-height/2)); }}
function toggleLabels() {{ showLabels = !showLabels; label.attr("opacity", showLabels ? 1 : 0); }}
function searchNode(q) {{
  if (!q) {{ node.attr("opacity", 1); link.attr("opacity", 1); label.attr("opacity", showLabels?1:0); return; }}
  const ql = q.toLowerCase();
  node.attr("opacity", d => (d.label.toLowerCase().includes(ql) || d.id.includes(ql)) ? 1 : 0.1);
  label.attr("opacity", d => (d.label.toLowerCase().includes(ql) || d.id.includes(ql)) ? 1 : 0);
  const matched = new Set(data.nodes.filter(d => d.label.toLowerCase().includes(ql) || d.id.includes(ql)).map(d=>d.id));
  link.attr("opacity", d => (matched.has(d.source.id) || matched.has(d.target.id)) ? 0.6 : 0.02);
}}

// Legend
const types = [...new Set(data.nodes.map(n => n.type))].sort();
const leg = d3.select("#legend");
types.forEach(t => {{
  const row = leg.append("div").attr("class", "leg-item");
  row.append("div").attr("class", "leg-dot").style("background", TYPE_COLORS[t] || DEFAULT_COLOR);
  row.append("span").text(t + " (" + data.nodes.filter(n=>n.type===t).length + ")");
}});
</script>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"Визуализация сохранена: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 7: ЭКСПОРТ
# ══════════════════════════════════════════════════════════════

def export_graph(G, resolved_entities: list[dict], metrics: dict, output_dir: str):
    """Экспорт графа в различных форматах."""
    import networkx as nx

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # 1. GraphML (для Gephi, Neo4j import)
    graphml_path = output_path / "entity_graph.graphml"
    nx.write_graphml(G, str(graphml_path))
    log.info(f"GraphML: {graphml_path}")

    # 2. JSON (для web-визуализации)
    graph_json = {
        "nodes": [],
        "edges": [],
        "metrics": metrics,
    }

    for node, data in G.nodes(data=True):
        graph_json["nodes"].append({
            "id": node,
            "label": data.get("label", node),
            "type": data.get("entity_type", ""),
            "frequency": data.get("frequency", 1),
            "community": data.get("community", 0),
            "degree": G.degree(node),
            "source_docs": data.get("source_docs", ""),
        })

    for u, v, data in G.edges(data=True):
        graph_json["edges"].append({
            "source": u,
            "target": v,
            "weight": data.get("weight", 1),
        })

    json_path = output_path / "entity_graph.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(graph_json, f, ensure_ascii=False, indent=2)
    log.info(f"JSON: {json_path}")

    # 3. Resolved entities (полный список)
    ent_path = output_path / "resolved_entities.json"
    with open(ent_path, "w", encoding="utf-8") as f:
        json.dump(resolved_entities, f, ensure_ascii=False, indent=2)
    log.info(f"Entities: {ent_path}")

    # 4. Метрики
    metrics_path = output_path / "graph_metrics.json"
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)
    log.info(f"Metrics: {metrics_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 8: СТАТИСТИКА
# ══════════════════════════════════════════════════════════════

def print_stats(G, metrics: dict, resolved_entities: list[dict]):
    """Вывод статистики графа."""
    print(f"\n{'═' * 60}")
    print(f"  🕸  ГРАФ СУЩНОСТЕЙ")
    print(f"{'═' * 60}")
    print(f"  Узлов:          {metrics['nodes']}")
    print(f"  Рёбер:          {metrics['edges']}")
    print(f"  Плотность:      {metrics['density']}")
    print(f"  Ср. степень:    {metrics.get('avg_degree', 0)}")
    print(f"  Компонент:      {metrics.get('connected_components', 0)}")
    print(f"  Кластеров:      {metrics.get('communities', 0)}")
    print(f"  Макс. степень:  {metrics.get('max_degree_node', '?')} ({metrics.get('max_degree', 0)})")

    # Типы сущностей
    type_counts = Counter(e["entity_type"] for e in resolved_entities)
    print(f"{'─' * 60}")
    print("  Типы сущностей в графе:")
    for etype, count in type_counts.most_common():
        pct = count / len(resolved_entities) * 100
        bar = "█" * int(pct / 3)
        print(f"    {etype:15s} │ {count:4d} │ {pct:5.1f}% {bar}")

    # Top PageRank
    if metrics.get("top_pagerank"):
        print(f"{'─' * 60}")
        print("  Топ-10 по PageRank:")
        for item in metrics["top_pagerank"]:
            node = item["entity"]
            score = item["score"]
            data = G.nodes.get(node, {})
            etype = data.get("entity_type", "?")
            label = data.get("label", node)
            print(f"    {etype:10s} │ {label:30s} │ PR={score:.4f}")

    # Community sizes
    if metrics.get("community_sizes"):
        print(f"{'─' * 60}")
        sizes = metrics["community_sizes"]
        print(f"  Размеры кластеров: {sizes[:10]}")

    print(f"{'═' * 60}\n")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 9: ГЛАВНЫЙ ПАЙПЛАЙН
# ══════════════════════════════════════════════════════════════

def build_entity_graph(input_dir: str, output_dir: str):
    """Полный пайплайн построения графа."""

    # 1. Загрузка
    log.info("Шаг 1: Загрузка сущностей...")
    all_entities = load_all_entities(input_dir)

    # 2. Фильтрация
    log.info("Шаг 2: Фильтрация шума...")
    clean_entities = filter_entities(all_entities)

    # 3. Entity resolution
    log.info("Шаг 3: Entity resolution...")
    resolved, merge_map = resolve_entities(clean_entities)

    # 4. Построение графа
    log.info("Шаг 4: Построение графа...")
    G = build_graph(resolved)

    # 5. Метрики
    log.info("Шаг 5: Расчёт метрик...")
    metrics = compute_metrics(G)

    # 6. Статистика
    print_stats(G, metrics, resolved)

    # 7. Визуализация
    log.info("Шаг 6: Визуализация...")
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    visualize_html(G, str(output_path / "entity_graph.html"))

    # 8. Экспорт
    log.info("Шаг 7: Экспорт...")
    export_graph(G, resolved, metrics, output_dir)

    return G, resolved, metrics


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 10: ГЛАВНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Фаза 4: Построение графа сущностей")
    parser.add_argument("--input", "-i", required=True, help="Папка с *_entities.json")
    parser.add_argument("--output", "-o", default="./graph", help="Папка для результатов")
    args = parser.parse_args()

    G, resolved, metrics = build_entity_graph(args.input, args.output)

    print(f"🎉 Готово!")
    print(f"   Граф: {args.output}/entity_graph.html (открой в браузере!)")
    print(f"   GraphML: {args.output}/entity_graph.graphml (для Gephi)")
    print(f"   JSON: {args.output}/entity_graph.json")
