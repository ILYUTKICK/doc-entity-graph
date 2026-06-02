"""
═══════════════════════════════════════════════════════════════
  Полная чистка + GLiNER + пересборка графа
  Проект: Построение графа сущностей документов
═══════════════════════════════════════════════════════════════

Что делает:
  1. Чистит SpaCy-сущности от OCR/LaTeX шума
  2. Прогоняет GLiNER (zero-shot NER) для финансовых концепций
  3. Объединяет результаты SpaCy + GLiNER
  4. Пересобирает граф с жёсткой фильтрацией рёбер

Установка:
  pip install gliner networkx

Запуск:
  python phase_cleanup_rebuild.py \\
    --entities ./entities/ \\
    --chunked ./chunked/ \\
    --output ./graph_clean/
"""

import json
import logging
import re
from pathlib import Path
from collections import Counter, defaultdict
from itertools import combinations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("cleanup")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: ОЧИСТКА SPACY СУЩНОСТЕЙ
# ══════════════════════════════════════════════════════════════

# OCR-транслит: кириллица, прочитанная как латиница
# Паттерн: слово из 3+ букв, смешивающее латиницу с заглавными без смысла
OCR_TRANSLITERATION = re.compile(
    r"^[A-Za-z]*[AOHECTBNMKP][a-z]*[AOHECTBNMKP][a-z]*$"
)

# Расширенные паттерны шума
NOISE_PATTERNS = [
    # LaTeX
    r"\\[a-zA-Z]+",                     # \frac, \sigma, \dots, \mathsf
    r"\$[^$]+\$",                        # $формула$
    r"\{[^}]*\}",                        # {внутренность}
    r"\\begin|\\end",

    # OCR мусор — типичные ложные срабатывания
    r"^Add\s*Sk",                        # Add Skills (артефакт)
    r"山",                               # Японский иероглиф (OCR-ошибка)

    # Математика как текст
    r"^[\d\s\+\-\*\/\=\{\}\(\)\[\]\\.,;:<>^_|~]+$",
    r"^\d+\s*[\+\-\*/]",                # 2 + , 1 +
    r"sigma|varepsilon|alpha|beta|gamma|delta|lambda|mu\b",

    # Слишком короткие бессмысленные
    r"^[A-Za-z]{1,2}$",                 # "r", "Pn", "ng"
    r"^[А-Яа-яёЁ]{1,2}$",             # "и", "на"

    # Артефакты форматирования
    r"textstyle|mathsf|operatorname",
    r"^[\s\-–—]+$",
]
NOISE_RE = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]

# Типы, бесполезные для графа знаний
SKIP_TYPES = {"NUMBER", "PERCENT", "QUANTITY", "MONEY"}

# Стоп-сущности (частые ложные срабатывания SpaCy)
STOP_ENTITIES = {
    "add skills", "add sk", "add skобмениваются", "add skiкупленная",
    "adalisa", "adalиса", "ohn", "mehee", "kak", "ang", "takxe",
    "tako", "tohho", "aoxoahoctn", "noptpeng", "noptpeni",
    "paccmotpnm", "otcytctbnn", "heto", "netopryembimn",
    "bce", "bcex", "oha", "ann", "ana bcex", "teky山ag",
    "ueha aktnba", "teky山ag ueha aktnba",
    "koappnuneht", "koppenauna", "koappnunehtbi",
    "ctpaxobka", "bblnnahat", "bblnnat",
    "bennunhbi", "aktib", "aktibob",
    "cymmaph0", "ctoumoctb", "paktophoie",
    "hababaet", "mokho", "hado", "ecnn",
    "moaenh", "moaenn", "bbnhat",
    "\\dots", "\\mathsf", "\\varepsilon",
    "\\textstyle", "\\operatorname",
    "naba 10", "naba 12",
}


def clean_spacy_entities(entities_dir: str) -> list[dict]:
    """Загрузка и агрессивная чистка SpaCy сущностей."""
    entities_path = Path(entities_dir)
    all_clean = []
    total_raw = 0
    total_removed = 0

    for ef in sorted(entities_path.glob("*_entities.json")):
        with open(ef, "r", encoding="utf-8") as f:
            data = json.load(f)

        doc_name = Path(data["source"]).stem
        raw = data["entities"]
        total_raw += len(raw)

        clean = []
        for ent in raw:
            text = ent.get("text", "").strip()
            norm = ent.get("normalized", text.lower())
            etype = ent.get("entity_type", "")

            # Тип
            if etype in SKIP_TYPES:
                continue

            # Стоп-лист
            if norm in STOP_ENTITIES:
                continue

            # Слишком короткий / длинный
            if len(text) < 3 or len(text) > 100:
                continue

            # Шумовые паттерны
            if any(r.search(text) for r in NOISE_RE):
                continue

            # OCR-транслитерация: строка из латиницы, которая не является
            # нормальным английским словом (эвристика: >50% заглавных)
            upper_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
            if upper_ratio > 0.5 and len(text) > 4 and not _is_known_acronym(text):
                continue

            # Чисто латинские "слова" < 5 букв, не аббревиатуры
            if re.match(r"^[a-z]{3,4}$", norm) and norm not in {"capm", "apt", "wacc"}:
                continue

            ent["_source_doc"] = doc_name
            clean.append(ent)

        removed = len(raw) - len(clean)
        total_removed += removed
        all_clean.extend(clean)
        log.info(f"  {doc_name}: {len(raw)} → {len(clean)} (удалено {removed})")

    log.info(f"SpaCy чистка: {total_raw} → {len(all_clean)} (удалено {total_removed})")
    return all_clean


def _is_known_acronym(text: str) -> bool:
    """Известные аббревиатуры, которые не надо фильтровать."""
    known = {"CAPM", "APT", "WACC", "SML", "CML", "OLS", "ANOVA", "GDP", "USA", "EU"}
    return text.upper() in known


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: GLINER — ZERO-SHOT NER
# ══════════════════════════════════════════════════════════════

# Типы сущностей, заточенные под финансовые документы
FINANCE_LABELS = [
    "person",
    "organization",
    "financial model",
    "financial instrument",
    "economic theory",
    "formula name",
    "academic publication",
    "university",
    "stock exchange",
    "country",
]

GLINER_TYPE_MAP = {
    "person": "PERSON",
    "organization": "ORG",
    "financial model": "CONCEPT",
    "financial instrument": "INSTRUMENT",
    "economic theory": "CONCEPT",
    "formula name": "FORMULA",
    "academic publication": "WORK",
    "university": "ORG",
    "stock exchange": "ORG",
    "country": "LOCATION",
}


def run_gliner(chunked_dir: str, threshold: float = 0.4) -> list[dict]:
    """
    Прогон GLiNER на всех чанках.
    Zero-shot: не нужно дообучение, задаём типы текстом.
    """
    try:
        from gliner import GLiNER
    except ImportError as exc:
        log.error(f"GLiNER недоступен: {exc}")
        log.info("Продолжаем только с очищенными SpaCy сущностями...")
        return []

    log.info("Загрузка модели GLiNER (urchade/gliner_multi-v2.1)...")
    try:
        model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
    except Exception as exc:
        log.error(f"GLiNER модель недоступна: {exc}")
        log.info("Продолжаем только с очищенными SpaCy сущностями...")
        return []
    log.info(f"GLiNER загружен. Типы: {FINANCE_LABELS}")

    chunked_path = Path(chunked_dir)
    all_entities = []

    for cf in sorted(chunked_path.glob("*_chunked.json")):
        with open(cf, "r", encoding="utf-8") as f:
            data = json.load(f)

        doc_name = Path(data["source"]).stem
        chunks = data["chunks"]
        doc_entities = []

        for i, chunk in enumerate(chunks):
            text = chunk["text"]
            chunk_id = chunk["chunk_id"]

            if not text.strip() or len(text) < 20:
                continue

            # GLiNER ограничен по длине — разбиваем
            segments = _split_for_gliner(text, max_len=800)

            for seg in segments:
                try:
                    preds = model.predict_entities(seg, FINANCE_LABELS, threshold=threshold)
                except Exception as e:
                    log.warning(f"GLiNER ошибка: {e}")
                    continue

                for pred in preds:
                    etype = GLINER_TYPE_MAP.get(pred["label"], pred["label"].upper())
                    ent_text = pred["text"].strip()

                    # Базовая фильтрация
                    if len(ent_text) < 2 or len(ent_text) > 100:
                        continue

                    doc_entities.append({
                        "text": ent_text,
                        "normalized": ent_text.lower().strip(),
                        "entity_type": etype,
                        "confidence": round(pred["score"], 3),
                        "chunk_id": chunk_id,
                        "source_doc": doc_name,
                        "context": _get_context(text, ent_text),
                        "_source_doc": doc_name,
                        "_engine": "gliner",
                    })

            if (i + 1) % 10 == 0:
                log.info(f"  GLiNER [{doc_name}]: {i+1}/{len(chunks)} чанков")

        log.info(f"  GLiNER {doc_name}: {len(doc_entities)} сущностей")
        all_entities.extend(doc_entities)

    log.info(f"GLiNER всего: {len(all_entities)} сущностей")
    return all_entities


def _split_for_gliner(text: str, max_len: int = 800) -> list[str]:
    """Разбиение длинного текста на сегменты для GLiNER."""
    if len(text) <= max_len:
        return [text]
    segments = []
    overlap = 100
    for i in range(0, len(text), max_len - overlap):
        segments.append(text[i:i + max_len])
    return segments


def _get_context(text: str, entity: str, window: int = 100) -> str:
    """Контекст вокруг сущности."""
    pos = text.find(entity)
    if pos == -1:
        pos = text.lower().find(entity.lower())
    if pos == -1:
        return ""
    start = max(0, pos - window)
    end = min(len(text), pos + len(entity) + window)
    ctx = text[start:end].strip()
    return ("..." if start > 0 else "") + ctx + ("..." if end < len(text) else "")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: ОБЪЕДИНЕНИЕ + ENTITY RESOLUTION
# ══════════════════════════════════════════════════════════════

def merge_and_resolve(spacy_entities: list[dict], gliner_entities: list[dict]) -> list[dict]:
    """
    Объединение SpaCy + GLiNER сущностей с дедупликацией.

    Приоритет: GLiNER confidence > SpaCy (у SpaCy фиксированный 0.85)
    """
    all_entities = spacy_entities + gliner_entities
    log.info(f"Объединение: {len(spacy_entities)} SpaCy + {len(gliner_entities)} GLiNER = {len(all_entities)}")

    # Группировка по normalized тексту
    groups = defaultdict(list)
    for ent in all_entities:
        norm = ent.get("normalized", ent["text"].lower().strip())
        groups[norm].append(ent)

    # Fuzzy merge
    merge_map = {}
    keys = sorted(groups.keys(), key=lambda k: -len(groups[k]))

    for i, ka in enumerate(keys):
        if ka in merge_map:
            continue
        for kb in keys[i + 1:]:
            if kb in merge_map:
                continue
            if _should_merge(ka, kb):
                merge_map[kb] = ka
                groups[ka].extend(groups[kb])

    # Сборка resolved сущностей
    resolved = []
    for norm, group in groups.items():
        if norm in merge_map:
            continue

        text_counts = Counter(e["text"] for e in group)
        best_text = text_counts.most_common(1)[0][0]

        type_counts = Counter(e["entity_type"] for e in group)
        best_type = type_counts.most_common(1)[0][0]

        all_chunks = set()
        for e in group:
            for cid in str(e.get("chunk_id", "")).split(","):
                if cid.strip():
                    all_chunks.add(cid.strip())

        all_docs = set(e.get("_source_doc", "") for e in group if e.get("_source_doc"))
        engines = set(e.get("_engine", "spacy") for e in group)

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
            "context": best_context[:300],
            "aliases": sorted(set(e["text"] for e in group) - {best_text})[:5],
            "engines": sorted(engines),
        })

    resolved.sort(key=lambda e: -e["frequency"])
    log.info(f"Entity resolution: {len(all_entities)} → {len(resolved)} уникальных")
    return resolved


def _should_merge(a: str, b: str) -> bool:
    """Проверка: нужно ли объединить."""
    if a == b:
        return True
    if len(a) > 3 and len(b) > 3 and (a in b or b in a):
        return True
    if len(a) > 5 and len(b) > 5:
        dist = _levenshtein(a, b)
        if dist <= 2 and dist / max(len(a), len(b)) < 0.2:
            return True
    return False


def _levenshtein(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: ПОСТРОЕНИЕ ЧИСТОГО ГРАФА
# ══════════════════════════════════════════════════════════════

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


def build_clean_graph(resolved: list[dict], min_edge_weight: int = 2):
    """
    Построение графа с жёсткой фильтрацией.

    Ключевое отличие: min_edge_weight=2 убирает случайные co-occurrence.
    """
    import networkx as nx

    G = nx.Graph()

    for ent in resolved:
        G.add_node(
            ent["normalized"],
            label=ent["text"],
            entity_type=ent["entity_type"],
            frequency=ent["frequency"],
            source_docs=",".join(ent["source_docs"]),
            aliases=",".join(ent["aliases"][:5]),
            context=ent["context"][:200],
            engines=",".join(ent.get("engines", [])),
        )

    # Co-occurrence
    chunk_to_ents = defaultdict(list)
    for ent in resolved:
        for cid in ent["chunk_ids"]:
            chunk_to_ents[cid].append(ent["normalized"])

    edge_weights = Counter()
    for cid, ent_list in chunk_to_ents.items():
        unique = sorted(set(ent_list))
        for a, b in combinations(unique, 2):
            if a != b and G.has_node(a) and G.has_node(b):
                edge_weights[(min(a, b), max(a, b))] += 1

    # Только значимые рёбра
    for (a, b), w in edge_weights.items():
        if w >= min_edge_weight:
            G.add_edge(a, b, weight=w, co_occurrences=w)

    if resolved and edge_weights and G.number_of_edges() == 0:
        log.warning(
            "Все рёбра отфильтрованы min_edge_weight=%s. "
            "Для одного документа или одного чанка используй --min-edge-weight 1.",
            min_edge_weight,
        )

    # Удаляем изолированные узлы (без рёбер)
    isolates = list(nx.isolates(G))
    G.remove_nodes_from(isolates)
    if isolates:
        log.info(f"Удалено изолированных узлов: {len(isolates)}")

    log.info(f"Чистый граф: {G.number_of_nodes()} узлов, {G.number_of_edges()} рёбер")
    return G


def compute_metrics(G) -> dict:
    """Метрики графа."""
    import networkx as nx

    m = {"nodes": G.number_of_nodes(), "edges": G.number_of_edges()}
    if not m["nodes"]:
        return m

    m["density"] = round(nx.density(G), 4)
    degrees = dict(G.degree())
    m["avg_degree"] = round(sum(degrees.values()) / len(degrees), 2)
    m["max_degree_node"] = max(degrees, key=degrees.get)
    m["max_degree"] = degrees[m["max_degree_node"]]

    try:
        pr = nx.pagerank(G, weight="weight")
        m["top_pagerank"] = [
            {"entity": n, "label": G.nodes[n].get("label", n),
             "type": G.nodes[n].get("entity_type", "?"), "score": round(s, 4)}
            for n, s in sorted(pr.items(), key=lambda x: -x[1])[:15]
        ]
    except Exception:
        m["top_pagerank"] = []

    comps = list(nx.connected_components(G))
    m["connected_components"] = len(comps)
    m["largest_component"] = len(max(comps, key=len)) if comps else 0

    try:
        communities = nx.community.louvain_communities(G, weight="weight")
        m["communities"] = len(communities)
        m["community_sizes"] = sorted([len(c) for c in communities], reverse=True)
        for ci, members in enumerate(communities):
            for node in members:
                G.nodes[node]["community"] = ci
    except Exception:
        m["communities"] = 0

    return m


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: HTML ВИЗУАЛИЗАЦИЯ (D3.js)
# ══════════════════════════════════════════════════════════════

def generate_html(G, output_path: str, title: str = "Граф сущностей (очищенный)"):
    """Генерация интерактивной HTML визуализации."""

    nodes = []
    for node, data in G.nodes(data=True):
        nodes.append({
            "id": node,
            "label": data.get("label", node),
            "type": data.get("entity_type", "UNKNOWN"),
            "freq": data.get("frequency", 1),
            "comm": data.get("community", 0),
            "docs": data.get("source_docs", ""),
            "engines": data.get("engines", ""),
        })

    edges = []
    for u, v, data in G.edges(data=True):
        edges.append({"source": u, "target": v, "weight": data.get("weight", 1)})

    graph_data = json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False)
    colors_json = json.dumps(TYPE_COLORS)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#111;color:#ddd;font-family:system-ui,-apple-system,sans-serif;overflow:hidden}}
#graph{{width:100vw;height:100vh}}
.tooltip{{position:absolute;background:#222;border:1px solid #444;border-radius:8px;padding:10px 14px;font-size:13px;pointer-events:none;opacity:0;transition:opacity .15s;max-width:320px;line-height:1.6;z-index:100}}
.tooltip b{{color:#fff;font-size:14px}}
.tooltip .tag{{display:inline-block;font-size:11px;padding:1px 6px;border-radius:6px;margin-left:4px}}
#controls{{position:fixed;top:16px;left:16px;display:flex;flex-direction:column;gap:6px;z-index:10}}
#controls button{{background:#222;color:#ccc;border:1px solid #444;border-radius:6px;padding:6px 14px;cursor:pointer;font-size:13px;transition:background .15s}}
#controls button:hover{{background:#333}}
#legend{{position:fixed;bottom:16px;left:16px;background:rgba(26,26,26,0.95);border:1px solid #333;border-radius:10px;padding:12px 16px;z-index:10;max-height:300px;overflow-y:auto}}
.leg-item{{display:flex;align-items:center;gap:8px;margin:4px 0;font-size:12px;color:#aaa;cursor:pointer}}
.leg-item:hover{{color:#fff}}
.leg-dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
#stats{{position:fixed;top:16px;right:16px;background:rgba(26,26,26,0.95);border:1px solid #333;border-radius:10px;padding:14px 18px;font-size:13px;z-index:10}}
.stat-row{{display:flex;justify-content:space-between;gap:16px;margin:3px 0}}
.stat-val{{font-weight:600;color:#fff}}
#search{{position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:10}}
#search input{{background:#222;color:#ddd;border:1px solid #444;border-radius:8px;padding:8px 16px;width:300px;font-size:14px;outline:none}}
#search input:focus{{border-color:#7F77DD}}
#filter{{position:fixed;bottom:16px;right:16px;background:rgba(26,26,26,0.95);border:1px solid #333;border-radius:10px;padding:12px 16px;z-index:10;font-size:13px}}
#filter label{{color:#999;display:block;margin-bottom:6px}}
#filter input[type=range]{{width:160px}}
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
  <button onclick="togglePhysics()">⏯ Physics</button>
</div>
<div id="search"><input type="text" placeholder="Поиск сущности..." oninput="searchNode(this.value)"></div>
<div id="stats"></div>
<div id="legend"></div>
<div id="filter">
  <label>Мин. вес ребра: <span id="wv">1</span></label>
  <input type="range" min="1" max="10" value="1" step="1" oninput="filterEdges(+this.value)">
  <label style="margin-top:8px">Мин. частота: <span id="fv">1</span></label>
  <input type="range" min="1" max="10" value="1" step="1" oninput="filterNodes(+this.value)">
</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<script>
const data = {graph_data};
const TC = {colors_json};
const DC = "#666";
let showLabels = true, physicsOn = true;
const W = window.innerWidth, H = window.innerHeight;

const svg = d3.select("#graph").append("svg").attr("width",W).attr("height",H);
const g = svg.append("g");
const zoom = d3.zoom().scaleExtent([0.1,10]).on("zoom",e=>g.attr("transform",e.transform));
svg.call(zoom);

const sim = d3.forceSimulation(data.nodes)
  .force("link",d3.forceLink(data.edges).id(d=>d.id).distance(100).strength(d=>Math.min(0.4,d.weight*0.06)))
  .force("charge",d3.forceManyBody().strength(-150))
  .force("center",d3.forceCenter(W/2,H/2))
  .force("collision",d3.forceCollide().radius(d=>ns(d)+4));

const linkG = g.append("g");
const nodeG = g.append("g");
const labelG = g.append("g");
let link, node, label;

function ns(d){{return Math.max(5,Math.min(28,3+d.freq*2))}}

function render(minW,minF){{
  const fn = data.nodes.filter(d=>d.freq>=minF);
  const fids = new Set(fn.map(d=>d.id));
  const fe = data.edges.filter(d=>{{
    const si = typeof d.source==='object'?d.source.id:d.source;
    const ti = typeof d.target==='object'?d.target.id:d.target;
    return d.weight>=minW && fids.has(si) && fids.has(ti);
  }});

  link = linkG.selectAll("line").data(fe,d=>d.source.id+"-"+d.target.id);
  link.exit().remove();
  link = link.enter().append("line").merge(link)
    .attr("stroke","#333").attr("stroke-width",d=>Math.min(5,.5+d.weight*.6))
    .attr("stroke-opacity",d=>Math.min(0.7,.1+d.weight*.08));

  node = nodeG.selectAll("circle").data(fn,d=>d.id);
  node.exit().remove();
  node = node.enter().append("circle").merge(node)
    .attr("r",d=>ns(d)).attr("fill",d=>TC[d.type]||DC)
    .attr("stroke","#222").attr("stroke-width",1).attr("cursor","pointer")
    .call(d3.drag().on("start",ds).on("drag",dr).on("end",de));

  label = labelG.selectAll("text").data(fn,d=>d.id);
  label.exit().remove();
  label = label.enter().append("text").merge(label)
    .text(d=>d.freq>=2?d.label:"")
    .attr("font-size",d=>Math.max(10,Math.min(15,8+d.freq)))
    .attr("fill","#bbb").attr("text-anchor","middle").attr("dy",d=>ns(d)+14)
    .attr("pointer-events","none").attr("opacity",showLabels?1:0);

  const tip = d3.select("#tip");
  node.on("mouseover",(e,d)=>{{
    tip.style("opacity",1).html(
      "<b>"+d.label+"</b>"
      +"<span class='tag' style='background:"+(TC[d.type]||DC)+";color:#fff'>"+d.type+"</span><br>"
      +"Частота: "+d.freq+"<br>"
      +"Кластер: "+d.comm+"<br>"
      +"Документы: "+d.docs+"<br>"
      +"Движки: "+(d.engines||"spacy")
    );
    d3.select(e.target).attr("stroke","#fff").attr("stroke-width",2);
  }}).on("mousemove",e=>{{
    tip.style("left",(e.pageX+15)+"px").style("top",(e.pageY-10)+"px");
  }}).on("mouseout",(e)=>{{
    tip.style("opacity",0);
    d3.select(e.target).attr("stroke","#222").attr("stroke-width",1);
  }});

  sim.nodes(fn);
  sim.force("link").links(fe);
  sim.alpha(0.5).restart();
}}

render(1,1);

sim.on("tick",()=>{{
  if(link) link.attr("x1",d=>d.source.x).attr("y1",d=>d.source.y).attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);
  if(node) node.attr("cx",d=>d.x).attr("cy",d=>d.y);
  if(label) label.attr("x",d=>d.x).attr("y",d=>d.y);
}});

function ds(e,d){{if(!e.active)sim.alphaTarget(.3).restart();d.fx=d.x;d.fy=d.y}}
function dr(e,d){{d.fx=e.x;d.fy=e.y}}
function de(e,d){{if(!e.active)sim.alphaTarget(0);d.fx=null;d.fy=null}}
function zoomIn(){{svg.transition().call(zoom.scaleBy,1.5)}}
function zoomOut(){{svg.transition().call(zoom.scaleBy,.67)}}
function resetView(){{svg.transition().call(zoom.transform,d3.zoomIdentity.translate(W/2,H/2).scale(.8).translate(-W/2,-H/2))}}
function toggleLabels(){{showLabels=!showLabels;if(label)label.attr("opacity",showLabels?1:0)}}
function togglePhysics(){{physicsOn=!physicsOn;physicsOn?sim.alpha(.3).restart():sim.stop()}}
function searchNode(q){{
  if(!q){{if(node)node.attr("opacity",1);if(link)link.attr("opacity",.3);if(label)label.attr("opacity",showLabels?1:0);return}}
  const ql=q.toLowerCase();
  if(node)node.attr("opacity",d=>(d.label.toLowerCase().includes(ql)||d.id.includes(ql))?1:.08);
  if(label)label.attr("opacity",d=>(d.label.toLowerCase().includes(ql)||d.id.includes(ql))?1:0);
  const m=new Set(data.nodes.filter(d=>d.label.toLowerCase().includes(ql)||d.id.includes(ql)).map(d=>d.id));
  if(link)link.attr("opacity",d=>{{
    const si=typeof d.source==='object'?d.source.id:d.source;
    const ti=typeof d.target==='object'?d.target.id:d.target;
    return(m.has(si)||m.has(ti))?.5:.02;
  }});
}}
function filterEdges(v){{document.getElementById("wv").textContent=v;render(v,+document.querySelector('#filter input:nth-of-type(2)').value)}}
function filterNodes(v){{document.getElementById("fv").textContent=v;render(+document.querySelector('#filter input:nth-of-type(1)').value,v)}}

// Stats
const st=d3.select("#stats");
st.html("<div class='stat-row'><span>Узлов</span><span class='stat-val'>"+data.nodes.length+"</span></div>"
  +"<div class='stat-row'><span>Рёбер</span><span class='stat-val'>"+data.edges.length+"</span></div>"
  +"<div class='stat-row'><span>Плотность</span><span class='stat-val'>"+(data.edges.length/(data.nodes.length*(data.nodes.length-1)/2||1)).toFixed(4)+"</span></div>");

// Legend
const types=[...new Set(data.nodes.map(n=>n.type))].sort();
const leg=d3.select("#legend");
types.forEach(t=>{{
  const c=data.nodes.filter(n=>n.type===t).length;
  const row=leg.append("div").attr("class","leg-item");
  row.append("div").attr("class","leg-dot").style("background",TC[t]||DC);
  row.append("span").text(t+" ("+c+")");
}});
</script>
</body>
</html>"""

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    log.info(f"HTML визуализация: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 6: ЭКСПОРТ
# ══════════════════════════════════════════════════════════════

def export_all(G, resolved: list[dict], metrics: dict, output_dir: str):
    """Экспорт всех артефактов."""
    import networkx as nx

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # GraphML
    nx.write_graphml(G, str(out / "entity_graph_clean.graphml"))

    # JSON
    gj = {"nodes": [], "edges": [], "metrics": metrics}
    for n, d in G.nodes(data=True):
        gj["nodes"].append({
            "id": n, "label": d.get("label", n), "type": d.get("entity_type", ""),
            "frequency": d.get("frequency", 1), "community": d.get("community", 0),
            "degree": G.degree(n), "source_docs": d.get("source_docs", ""),
        })
    for u, v, d in G.edges(data=True):
        gj["edges"].append({"source": u, "target": v, "weight": d.get("weight", 1)})

    with open(out / "entity_graph_clean.json", "w", encoding="utf-8") as f:
        json.dump(gj, f, ensure_ascii=False, indent=2)

    with open(out / "resolved_entities_clean.json", "w", encoding="utf-8") as f:
        json.dump(resolved, f, ensure_ascii=False, indent=2)

    with open(out / "graph_metrics_clean.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    log.info(f"Экспорт завершён в {output_dir}/")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 7: СТАТИСТИКА
# ══════════════════════════════════════════════════════════════

def print_results(G, metrics: dict, resolved: list[dict]):
    """Финальная статистика."""
    print(f"\n{'═' * 60}")
    print(f"  🕸  ОЧИЩЕННЫЙ ГРАФ СУЩНОСТЕЙ")
    print(f"{'═' * 60}")
    print(f"  Узлов:           {metrics['nodes']}")
    print(f"  Рёбер:           {metrics['edges']}")
    print(f"  Плотность:       {metrics.get('density', 0)}")
    print(f"  Ср. степень:     {metrics.get('avg_degree', 0)}")
    print(f"  Компонент:       {metrics.get('connected_components', 0)}")
    print(f"  Кластеров:       {metrics.get('communities', 0)}")

    # Типы
    tc = Counter(e["entity_type"] for e in resolved if e["normalized"] in [n for n in G.nodes()])
    print(f"{'─' * 60}")
    print("  Типы сущностей:")
    for t, c in tc.most_common():
        pct = c / max(sum(tc.values()), 1) * 100
        bar = "█" * int(pct / 3)
        print(f"    {t:15s} │ {c:4d} │ {pct:5.1f}% {bar}")

    # Top PageRank
    if metrics.get("top_pagerank"):
        print(f"{'─' * 60}")
        print("  Топ-15 по PageRank:")
        for item in metrics["top_pagerank"]:
            print(f"    {item['type']:12s} │ {item['label']:30s} │ PR={item['score']:.4f}")

    # Engines
    eng_counts = Counter()
    for e in resolved:
        for eng in e.get("engines", ["spacy"]):
            eng_counts[eng] += 1
    print(f"{'─' * 60}")
    print(f"  Источники: {dict(eng_counts)}")

    print(f"{'═' * 60}\n")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 8: ГЛАВНЫЙ ПАЙПЛАЙН
# ══════════════════════════════════════════════════════════════

def main(entities_dir: str, chunked_dir: str, output_dir: str, min_edge_weight: int = 2):
    """Полный пайплайн: чистка + GLiNER + граф."""

    print(f"\n{'━' * 60}")
    print(f"  ПОЛНАЯ ПЕРЕСБОРКА ГРАФА")
    print(f"{'━' * 60}\n")

    # 1. Чистка SpaCy
    log.info("═══ Шаг 1: Очистка SpaCy сущностей ═══")
    spacy_clean = clean_spacy_entities(entities_dir)

    # 2. GLiNER
    log.info("\n═══ Шаг 2: GLiNER zero-shot NER ═══")
    gliner_entities = run_gliner(chunked_dir)

    # 3. Объединение
    log.info("\n═══ Шаг 3: Объединение + Entity Resolution ═══")
    resolved = merge_and_resolve(spacy_clean, gliner_entities)

    # 4. Граф
    log.info("\n═══ Шаг 4: Построение чистого графа ═══")
    G = build_clean_graph(resolved, min_edge_weight=min_edge_weight)

    # 5. Метрики
    log.info("\n═══ Шаг 5: Метрики ═══")
    metrics = compute_metrics(G)

    # 6. Результаты
    print_results(G, metrics, resolved)

    # 7. Визуализация
    log.info("═══ Шаг 6: HTML визуализация ═══")
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    generate_html(G, str(out / "entity_graph_clean.html"))

    # 8. Экспорт
    log.info("═══ Шаг 7: Экспорт ═══")
    export_all(G, resolved, metrics, output_dir)

    return G, resolved, metrics


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Полная чистка + GLiNER + пересборка графа")
    parser.add_argument("--entities", "-e", required=True, help="Папка с *_entities.json (SpaCy)")
    parser.add_argument("--chunked", "-c", required=True, help="Папка с *_chunked.json")
    parser.add_argument("--output", "-o", default="./graph_clean", help="Папка для результатов")
    parser.add_argument("--min-edge-weight", type=int, default=2, help="Мин. вес ребра (default: 2)")
    args = parser.parse_args()

    G, resolved, metrics = main(
        args.entities, args.chunked, args.output,
        min_edge_weight=args.min_edge_weight,
    )

    print(f"🎉 Готово!")
    print(f"   Граф: {args.output}/entity_graph_clean.html")
    print(f"   GraphML: {args.output}/entity_graph_clean.graphml")
    print(f"   Открой: open {args.output}/entity_graph_clean.html")
