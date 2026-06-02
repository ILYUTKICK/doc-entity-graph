"""
═══════════════════════════════════════════════════════════════
  Фаза 1: Парсинг документов с MinerU
  Проект: Построение графа сущностей документов
═══════════════════════════════════════════════════════════════

Этот скрипт покрывает:
  1. Установку и настройку MinerU
  2. Парсинг PDF/DOCX/PPTX/изображений
  3. Постобработку результатов
  4. Подготовку данных для следующей фазы (чанкинг + NER)

Требования:
  - Python 3.10-3.13
  - GPU (рекомендуется) или CPU
  - pip install mineru[all]
"""

# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 0: УСТАНОВКА
# ══════════════════════════════════════════════════════════════

# --- Вариант A: Базовая установка (Pipeline backend, работает на CPU) ---
# pip install --upgrade pip
# pip install uv
# uv pip install -U "mineru[all]"

# --- Вариант B: Только VLM backend (MinerU 2.5, нужна GPU) ---
# pip install "mineru-vl-utils[vllm]"

# --- Вариант C: Docker (самый простой для production) ---
# docker pull opendatalab/mineru:latest
# docker run -p 8000:8000 opendatalab/mineru:latest

# --- Настройка источника моделей (если HuggingFace недоступен) ---
# import os
# os.environ["MINERU_MODEL_SOURCE"] = "modelscope"


import os
import sys
import json
import time
import re
import hashlib
import logging
import shlex
from pathlib import Path
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase1")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
CAPTION_RE = re.compile(
    r"^\s*(?P<kind>рисунок|рис\.?|figure|fig\.?|таблица|табл\.?|table)"
    r"\s*[\-–—№#]*\s*(?P<num>\d+(?:[.\-–]\d+)?|[ivxlcdm]+)?",
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: СТРУКТУРЫ ДАННЫХ
# ══════════════════════════════════════════════════════════════

@dataclass
class ParsedBlock:
    """Один структурный блок документа после парсинга MinerU."""
    block_type: str          # "text", "table", "formula", "image", "title", "list"
    content: str             # Markdown-текст блока
    page_number: int         # Номер страницы (0-indexed)
    bbox: list[float]        # [x0, y0, x1, y1] координаты на странице
    block_index: int         # Порядковый номер блока в reading order
    confidence: float = 1.0  # Уверенность распознавания


@dataclass
class DocumentElement:
    """Структурный элемент документа для связей между текстом, графиками и таблицами."""
    element_id: str
    element_type: str        # "text", "title", "caption", "figure", "table", "formula", "list"
    text: str = ""
    page_number: int = 0
    bbox: list[float] = field(default_factory=lambda: [0, 0, 0, 0])
    block_index: int = 0
    confidence: float = 1.0
    source_type: str = ""
    source_block_index: Optional[int] = None
    ref_label: str = ""      # Например: "рис. 1", "table 2"
    caption: str = ""
    image_path: str = ""
    table_html: str = ""
    formula: str = ""
    section_title: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """Результат парсинга одного документа."""
    source_path: str
    source_hash: str                        # MD5 для дедупликации
    total_pages: int
    blocks: list[ParsedBlock] = field(default_factory=list)
    elements: list[DocumentElement] = field(default_factory=list)
    full_markdown: str = ""
    metadata: dict = field(default_factory=dict)
    parse_time_sec: float = 0.0
    backend_used: str = "pipeline"


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: ПАРСИНГ ЧЕРЕЗ CLI (самый простой способ)
# ══════════════════════════════════════════════════════════════

def parse_via_cli(
    input_path: str,
    output_dir: str,
    backend: str = "auto",
) -> Path:
    """
    Запуск MinerU через CLI — подходит для быстрого старта.

    Backends:
      - "pipeline": DocLayout-YOLO + PP-OCRv5 (CPU/GPU, быстрый)
      - "vlm":      MinerU 2.5 VLM модель (GPU, точный)
      - "hybrid":   Pipeline layout + VLM для сложных блоков
      - "auto":     Автовыбор по доступному железу

    Выходные файлы:
      - *.md       — полный Markdown документа
      - *_content_list.json — структурированные блоки
      - images/    — извлечённые изображения
    """
    import subprocess

    mineru_cmd = os.environ.get("MINERU_BIN")
    cmd = shlex.split(mineru_cmd) if mineru_cmd else [
        sys.executable,
        "-m", "mineru.cli.client",
    ]
    cmd.extend([
        "-p", input_path,
        "-o", output_dir,
    ])
    if backend != "auto":
        cmd.extend(["-b", backend])

    log.info(f"Запуск MinerU CLI: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        log.error(f"MinerU CLI ошибка:\n{result.stderr}")
        raise RuntimeError(f"MinerU завершился с кодом {result.returncode}")

    output_path = Path(output_dir)
    log.info(f"Парсинг завершён. Результаты в: {output_path}")
    return output_path


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: ПАРСИНГ ЧЕРЕЗ API (для production и батчей)
# ══════════════════════════════════════════════════════════════

def parse_via_api(
    input_path: str,
    api_url: str = "http://127.0.0.1:8000",
    poll_interval: float = 2.0,
    timeout: int = 300,
) -> dict:
    """
    Парсинг через FastAPI-сервер MinerU.

    Предварительно запустить сервер:
      mineru-api --host 0.0.0.0 --port 8000

    Для VLM с предзагрузкой модели:
      mineru-api --host 0.0.0.0 --port 8000 --enable-vlm-preload true
    """
    import requests

    # Асинхронная отправка задачи
    log.info(f"Отправка файла на API: {input_path}")
    with open(input_path, "rb") as f:
        resp = requests.post(
            f"{api_url}/tasks",
            files={"files": (Path(input_path).name, f)},
            data={"return_md": "true"},
        )
    resp.raise_for_status()
    task_id = resp.json()["task_id"]
    log.info(f"Задача создана: {task_id}")

    # Поллинг статуса
    start = time.time()
    while time.time() - start < timeout:
        status_resp = requests.get(f"{api_url}/tasks/{task_id}")
        status_data = status_resp.json()

        if status_data.get("state") == "done":
            log.info(f"Задача завершена за {time.time() - start:.1f}с")
            result_resp = requests.get(f"{api_url}/tasks/{task_id}/result")
            return result_resp.json()

        if status_data.get("state") == "failed":
            raise RuntimeError(f"Задача провалилась: {status_data}")

        queued = status_data.get("queued_ahead", "?")
        log.info(f"Ожидание... (в очереди перед нами: {queued})")
        time.sleep(poll_interval)

    raise TimeoutError(f"Таймаут {timeout}с превышен для задачи {task_id}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: ПАРСИНГ ЧЕРЕЗ VLM НАПРЯМУЮ (MinerU 2.5)
# ══════════════════════════════════════════════════════════════

def parse_via_vlm_direct(image_path: str) -> str:
    """
    Прямой вызов MinerU 2.5 VLM для одной страницы.
    Использует двухстадийный coarse-to-fine парсинг.

    Требования:
      pip install "mineru-vl-utils[transformers]"
      GPU с минимум 4GB VRAM

    Для батч-обработки используйте vllm backend:
      pip install "mineru-vl-utils[vllm]"
    """
    from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
    from PIL import Image
    from mineru_vl_utils import MinerUClient

    # Загрузка модели (кэшируется после первого вызова)
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        "opendatalab/MinerU2.5-Pro-2604-1.2B",  # Последняя Pro версия
        dtype="auto",
        device_map="auto",
    )
    processor = AutoProcessor.from_pretrained(
        "opendatalab/MinerU2.5-Pro-2604-1.2B",
        use_fast=True,
    )
    client = MinerUClient(
        backend="transformers",
        model=model,
        processor=processor,
    )

    # two_step_extract: Stage I (layout) → Stage II (content)
    result = client.two_step_extract(Image.open(image_path))
    return result


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: ПОСТОБРАБОТКА РЕЗУЛЬТАТОВ
# ══════════════════════════════════════════════════════════════

def load_mineru_output(output_dir: str, source_path: str) -> ParsedDocument:
    """
    Загрузка и структурирование выхода MinerU.

    Реальная структура MinerU (v3.x):
      <output_dir>/
        auto/
          <name>.md              — полный Markdown
          <name>_middle.json     — блоки с метаданными (основной)
          <name>_content_list.json — альтернативное имя (старые версии)
          images/                — извлечённые изображения
    """
    output_path = Path(output_dir)
    source_hash = _file_md5(source_path)

    # ── Поиск директории с результатами ──
    # MinerU кладёт в: <output>/<docname>/auto/
    doc_dir = None

    # Стратегия 1: ищем auto/ подпапку рекурсивно
    auto_dirs = list(output_path.rglob("auto"))
    if auto_dirs:
        doc_dir = auto_dirs[0]

    # Стратегия 2: ищем любой .md файл
    if not doc_dir:
        md_candidates = list(output_path.rglob("*.md"))
        if md_candidates:
            doc_dir = md_candidates[0].parent

    # Стратегия 3: сама output папка
    if not doc_dir:
        doc_dir = output_path

    log.info(f"Найдена директория результатов: {doc_dir}")

    # ── Загрузка Markdown ──
    md_files = list(doc_dir.glob("*.md"))
    full_markdown = ""
    if md_files:
        full_markdown = md_files[0].read_text(encoding="utf-8")
        log.info(f"Markdown загружен: {len(full_markdown)} символов")

    # ── Загрузка структурированных блоков ──
    blocks = []
    elements = []
    json_files = (
        list(doc_dir.glob("*_content_list.json"))
        or list(doc_dir.glob("*_content_list_v2.json"))
        or list(doc_dir.glob("*_middle.json"))
        or [f for f in doc_dir.glob("*.json") if "model" not in f.name]
    )
    # Убираем _v2 и _model версии если есть основной _content_list
    json_files = [f for f in json_files if "_model" not in f.name]

    if json_files:
        with open(json_files[0], "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        flat_items = _flatten_mineru_json(raw_data)
        elements = _build_document_elements(flat_items, source_hash, doc_dir)

        for idx, item in enumerate(flat_items):
            text = _extract_block_text(item)
            if not text:
                continue

            block_type = _normalize_element_type(item, text)
            if block_type == "figure":
                continue

            block = ParsedBlock(
                block_type=block_type,
                content=text,
                page_number=_extract_page_number(item),
                bbox=_extract_bbox(item),
                block_index=idx,
                confidence=item.get("score", item.get("confidence", 1.0)),
            )
            blocks.append(block)

        log.info(f"Загружено блоков: {len(blocks)}")
        log.info(f"Структурных элементов: {len(elements)}")
    else:
        log.warning("JSON с блоками не найден")

    # ── Если блоков нет, но Markdown есть — парсим из Markdown ──
    if not blocks and full_markdown:
        blocks = _blocks_from_markdown(full_markdown)
        elements = _elements_from_blocks(blocks, source_hash)
        log.info(f"Блоки восстановлены из Markdown: {len(blocks)}")

    # ── Сборка документа ──
    page_numbers = {b.page_number for b in blocks} | {e.page_number for e in elements}
    total_pages = max(page_numbers) + 1 if page_numbers else 1

    img_dir = doc_dir / "images"
    img_count = len(list(img_dir.iterdir())) if img_dir.exists() else 0
    element_type_counts = Counter(e.element_type for e in elements)

    return ParsedDocument(
        source_path=source_path,
        source_hash=source_hash,
        total_pages=total_pages,
        blocks=blocks,
        elements=elements,
        full_markdown=full_markdown,
        metadata={
            "doc_name": Path(source_path).stem,
            "output_dir": str(doc_dir),
            "image_count": img_count,
            "json_source": str(json_files[0]) if json_files else None,
            "element_count": len(elements),
            "element_type_counts": dict(element_type_counts),
        },
    )


def _flatten_mineru_json(data) -> list[dict]:
    """
    MinerU JSON может быть:
      - list[dict] — плоский список блоков (_content_list.json)
      - list[list[dict]] — список страниц (_content_list_v2.json)
      - dict — метаданные (_middle.json), извлекаем блоки если есть
    """
    if not data:
        return []
    if isinstance(data, dict):
        # _middle.json — ищем списки внутри
        for key in ("pdf_info", "pages", "content"):
            if key in data and isinstance(data[key], list):
                return _flatten_mineru_json(data[key])
        return []
    if isinstance(data, list):
        if not data:
            return []
        if isinstance(data[0], list):
            flat = []
            for page_idx, page_blocks in enumerate(data):
                for block in page_blocks:
                    if isinstance(block, dict):
                        block.setdefault("page_idx", page_idx)
                        flat.append(block)
            return flat
        if isinstance(data[0], dict):
            return data
    return []


def _extract_block_text(item: dict) -> str:
    """
    Извлечение текста из блока MinerU.

    Форматы:
      - {"type": "text", "text": "..."} — простой текст
      - {"type": "table", "table_body": "<table>...</table>"} — таблица
      - {"type": "formula", "latex": "..."} — формула
      - {"type": "title", "content": {"title_content": [{"content": "..."}]}}
      - {"type": "paragraph", "content": {"paragraph_content": [{"content": "..."}]}}
    """
    # Прямые поля с содержимым
    for key in ("text", "table_body", "table_html", "latex", "formula", "equation"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    # Вложенная структура content
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        for key in ("title_content", "paragraph_content", "table_content", "formula_content"):
            parts = content.get(key, [])
            if parts:
                texts = []
                for part in parts:
                    if isinstance(part, dict):
                        texts.append(part.get("content", part.get("text", "")))
                    elif isinstance(part, str):
                        texts.append(part)
                return " ".join(t.strip() for t in texts if t).strip()

    return ""


def _build_document_elements(
    flat_items: list[dict],
    source_hash: str,
    doc_dir: Path,
) -> list[DocumentElement]:
    """Преобразование MinerU content_list в структурные элементы документа."""
    elements = []
    current_section = ""

    for idx, item in enumerate(flat_items):
        text = _extract_block_text(item)
        element_type = _normalize_element_type(item, text)

        if not text and element_type not in {"figure", "table", "formula"}:
            continue

        clean_text = _strip_markdown_wrappers(text)
        caption = _extract_caption_from_item(item)
        image_path = _extract_image_path(item, doc_dir) if element_type == "figure" else ""
        table_html = _extract_table_html(item) if element_type == "table" else ""
        formula = _extract_formula(item) if element_type == "formula" else ""

        if element_type == "title":
            current_section = clean_text

        metadata = {}
        if item.get("text_level"):
            metadata["text_level"] = item["text_level"]
        if caption and caption != clean_text:
            metadata["raw_caption"] = caption

        element = DocumentElement(
            element_id=f"{source_hash[:8]}_el_{len(elements):04d}",
            element_type=element_type,
            text=clean_text,
            page_number=_extract_page_number(item),
            bbox=_extract_bbox(item),
            block_index=idx,
            confidence=item.get("score", item.get("confidence", 1.0)),
            source_type=str(item.get("type", "")),
            source_block_index=idx,
            ref_label=_extract_ref_label(caption or clean_text),
            caption=caption,
            image_path=image_path,
            table_html=table_html,
            formula=formula,
            section_title=clean_text if element_type == "title" else current_section,
            metadata=metadata,
        )
        elements.append(element)

    _link_caption_neighbors(elements)
    return elements


def _elements_from_blocks(
    blocks: list[ParsedBlock],
    source_hash: str,
) -> list[DocumentElement]:
    """Фолбэк-элементы из текстовых блоков, если MinerU JSON недоступен."""
    elements = []
    current_section = ""

    for block in blocks:
        item = {"type": block.block_type}
        element_type = _normalize_element_type(item, block.content)
        clean_text = _strip_markdown_wrappers(block.content)

        if element_type == "title":
            current_section = clean_text

        elements.append(DocumentElement(
            element_id=f"{source_hash[:8]}_el_{len(elements):04d}",
            element_type=element_type,
            text=clean_text,
            page_number=block.page_number,
            bbox=block.bbox,
            block_index=block.block_index,
            confidence=block.confidence,
            source_type=block.block_type,
            source_block_index=block.block_index,
            ref_label=_extract_ref_label(clean_text),
            section_title=clean_text if element_type == "title" else current_section,
        ))

    _link_caption_neighbors(elements)
    return elements


def _normalize_element_type(item: dict, text: str = "") -> str:
    """Нормализация типов MinerU в типы, полезные для графа документа."""
    raw_type = str(item.get("type", item.get("block_type", "text"))).lower()

    if raw_type in {"image", "img", "figure"} or item.get("img_path") or item.get("image_path"):
        return "figure"
    if raw_type in {"table"} or item.get("table_body") or item.get("table_html"):
        return "table"
    if raw_type in {"formula", "equation", "inline_equation", "interline_equation"}:
        return "formula"
    if any(item.get(key) for key in ("latex", "formula", "equation")):
        return "formula"
    if raw_type in {"title", "heading", "header"} or item.get("text_level"):
        return "title"
    if raw_type in {"caption", "figure_caption", "table_caption"} or _is_caption_text(text):
        return "caption"
    if raw_type in {"list", "list_item"}:
        return "list"
    if raw_type in {"paragraph", "para"}:
        return "text"

    return raw_type or "text"


def _extract_page_number(item: dict) -> int:
    """Номер страницы из разных вариантов MinerU JSON."""
    for key in ("page_idx", "page_number", "page"):
        value = item.get(key)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _extract_bbox(item: dict) -> list[float]:
    """Координаты блока в формате [x0, y0, x1, y1]."""
    bbox = item.get("bbox") or item.get("box") or item.get("position")
    if isinstance(bbox, dict):
        return [
            bbox.get("x0", bbox.get("left", 0)),
            bbox.get("y0", bbox.get("top", 0)),
            bbox.get("x1", bbox.get("right", 0)),
            bbox.get("y1", bbox.get("bottom", 0)),
        ]
    if isinstance(bbox, list) and len(bbox) >= 4:
        return bbox[:4]
    return [0, 0, 0, 0]


def _strip_markdown_wrappers(text: str) -> str:
    """Убирает лёгкую Markdown-обёртку, не трогая внутренний текст."""
    stripped = (text or "").strip()
    stripped = re.sub(r"^\s{0,3}#{1,6}\s*", "", stripped)

    for marker in ("**", "__", "*", "_"):
        if stripped.startswith(marker) and stripped.endswith(marker) and len(stripped) >= len(marker) * 2:
            stripped = stripped[len(marker):-len(marker)].strip()
            break

    return stripped.strip()


def _is_caption_text(text: str) -> bool:
    """Эвристика: строка выглядит как подпись к рисунку или таблице."""
    return bool(CAPTION_RE.match(_strip_markdown_wrappers(text)))


def _extract_ref_label(text: str) -> str:
    """Нормализованная ссылка из подписи: 'рис. 1', 'table 2', 'табл. 3'."""
    match = CAPTION_RE.match(_strip_markdown_wrappers(text).lower())
    if not match:
        return ""

    kind = match.group("kind").rstrip(".")
    num = match.group("num") or ""

    if kind in {"рис", "рисунок"}:
        norm_kind = "рис."
    elif kind in {"fig", "figure"}:
        norm_kind = "figure"
    elif kind in {"табл", "таблица"}:
        norm_kind = "табл."
    else:
        norm_kind = "table"

    return f"{norm_kind} {num}".strip()


def _extract_caption_from_item(item: dict) -> str:
    """Подпись, если MinerU сохранил её прямо внутри image/table блока."""
    for key in ("caption", "image_caption", "table_caption"):
        value = item.get(key)
        caption = _value_to_text(value)
        if caption:
            return _strip_markdown_wrappers(caption)
    return ""


def _value_to_text(value) -> str:
    """Рекурсивно собирает текст из строк, списков и словарей."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_value_to_text(v) for v in value]
        return " ".join(p for p in parts if p).strip()
    if isinstance(value, dict):
        direct = _extract_block_text(value)
        if direct:
            return direct
        parts = [_value_to_text(v) for v in value.values()]
        return " ".join(p for p in parts if p).strip()
    return ""


def _extract_image_path(item: dict, doc_dir: Path) -> str:
    """Путь к извлечённому изображению, приведённый к пути относительно output dir."""
    raw_path = None
    for key in ("img_path", "image_path", "path", "src"):
        value = item.get(key)
        if _looks_like_image_path(value):
            raw_path = value
            break

    if not raw_path:
        raw_path = _find_image_path(item)

    if not raw_path:
        return ""

    image_path = Path(raw_path)
    if not image_path.is_absolute():
        image_path = doc_dir / image_path
    return str(image_path)


def _find_image_path(value) -> Optional[str]:
    """Поиск строки с расширением изображения внутри вложенной структуры."""
    if _looks_like_image_path(value):
        return value
    if isinstance(value, dict):
        for nested in value.values():
            found = _find_image_path(nested)
            if found:
                return found
    if isinstance(value, list):
        for nested in value:
            found = _find_image_path(nested)
            if found:
                return found
    return None


def _looks_like_image_path(value) -> bool:
    return isinstance(value, str) and Path(value).suffix.lower() in IMAGE_EXTENSIONS


def _extract_table_html(item: dict) -> str:
    for key in ("table_body", "table_html", "html"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_formula(item: dict) -> str:
    for key in ("latex", "formula", "equation", "text"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _link_caption_neighbors(elements: list[DocumentElement]) -> None:
    """Связывает подпись с ближайшим рисунком/таблицей на той же странице."""
    structured_types = {"figure", "table"}

    for caption in [e for e in elements if e.element_type == "caption"]:
        label = caption.ref_label
        if label.startswith(("рис.", "figure")):
            target_types = {"figure"}
        elif label.startswith(("табл.", "table")):
            target_types = {"table"}
        else:
            target_types = structured_types

        candidates = [
            e for e in elements
            if e.element_type in target_types
            and e.page_number == caption.page_number
            and abs(e.block_index - caption.block_index) <= 3
        ]
        if not candidates:
            continue

        target = min(
            candidates,
            key=lambda e: (abs(e.block_index - caption.block_index), e.block_index > caption.block_index),
        )
        caption.metadata["relation"] = "caption_of"
        caption.metadata["linked_element_id"] = target.element_id
        target.metadata.setdefault("caption_element_id", caption.element_id)

        if not target.caption:
            target.caption = caption.text
        if caption.ref_label and not target.ref_label:
            target.ref_label = caption.ref_label


def _blocks_from_markdown(md_text: str) -> list[ParsedBlock]:
    """Фолбэк: создание блоков из Markdown, если JSON недоступен."""
    blocks = []
    current_text = []
    block_idx = 0

    for line in md_text.split("\n"):
        stripped = line.strip()

        if stripped.startswith("#"):
            if current_text:
                blocks.append(ParsedBlock(
                    block_type="text",
                    content="\n".join(current_text),
                    page_number=0, bbox=[0, 0, 0, 0],
                    block_index=block_idx,
                ))
                block_idx += 1
                current_text = []
            blocks.append(ParsedBlock(
                block_type="title",
                content=stripped.lstrip("# ").strip(),
                page_number=0, bbox=[0, 0, 0, 0],
                block_index=block_idx,
            ))
            block_idx += 1
        elif stripped == "":
            if current_text:
                blocks.append(ParsedBlock(
                    block_type="text",
                    content="\n".join(current_text),
                    page_number=0, bbox=[0, 0, 0, 0],
                    block_index=block_idx,
                ))
                block_idx += 1
                current_text = []
        else:
            current_text.append(stripped)

    if current_text:
        blocks.append(ParsedBlock(
            block_type="text",
            content="\n".join(current_text),
            page_number=0, bbox=[0, 0, 0, 0],
            block_index=block_idx,
        ))

    return blocks


def _file_md5(filepath: str) -> str:
    """MD5 хэш файла для дедупликации документов."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 6: ФИЛЬТРАЦИЯ И ОЧИСТКА
# ══════════════════════════════════════════════════════════════

# Паттерны шума, которые MinerU может подхватить
NOISE_PATTERNS = [
    r"^\d+$",                          # голые номера страниц
    r"^page\s*\d+",                    # "Page 1", "page 2"
    r"^стр\.\s*\d+",                   # "Стр. 5"
    r"^\s*[-–—]\s*\d+\s*[-–—]\s*$",    # "- 5 -"
    r"confidential|draft|watermark",    # водяные знаки
]


def clean_blocks(doc: ParsedDocument) -> ParsedDocument:
    """
    Очистка блоков от шума.

    Убирает:
      - Пустые блоки
      - Номера страниц / колонтитулы
      - Водяные знаки
      - Дублирующиеся блоки (headers/footers на каждой странице)
    """
    import re

    noise_re = [re.compile(p, re.IGNORECASE) for p in NOISE_PATTERNS]
    seen_headers = {}
    cleaned = []

    for block in doc.blocks:
        text = block.content.strip()

        # Пропуск пустых
        if not text:
            continue

        # Пропуск шумовых паттернов
        if any(r.search(text) for r in noise_re):
            continue

        # Дедупликация повторяющихся headers/footers
        # (один и тот же текст на 3+ страницах = header/footer)
        short_text = text[:100]
        if short_text in seen_headers:
            seen_headers[short_text] += 1
            if seen_headers[short_text] >= 3:
                continue
        else:
            seen_headers[short_text] = 1

        cleaned.append(block)

    removed = len(doc.blocks) - len(cleaned)
    if removed:
        log.info(f"Очищено шумовых блоков: {removed}")

    doc.blocks = cleaned
    return doc


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 7: СТАТИСТИКА И ВАЛИДАЦИЯ
# ══════════════════════════════════════════════════════════════

def print_stats(doc: ParsedDocument):
    """Вывод статистики по распарсенному документу."""
    type_counts = Counter(b.block_type for b in doc.blocks)
    element_counts = Counter(e.element_type for e in doc.elements)
    total_chars = sum(len(b.content) for b in doc.blocks)

    print(f"\n{'═' * 50}")
    print(f"  📄 {Path(doc.source_path).name}")
    print(f"{'═' * 50}")
    print(f"  Страниц:  {doc.total_pages}")
    print(f"  Блоков:   {len(doc.blocks)}")
    print(f"  Элементов: {len(doc.elements)}")
    print(f"  Символов: {total_chars:,}")
    print(f"  Backend:  {doc.backend_used}")
    print(f"  Время:    {doc.parse_time_sec:.1f}с")
    print(f"  MD5:      {doc.source_hash[:12]}...")
    print(f"{'─' * 50}")
    print("  Типы блоков:")
    for btype, count in type_counts.most_common():
        pct = count / len(doc.blocks) * 100
        bar = "█" * int(pct / 5)
        print(f"    {btype:12s} │ {count:4d} │ {pct:5.1f}% {bar}")
    if element_counts:
        print(f"{'─' * 50}")
        print("  Структурные элементы:")
        for etype, count in element_counts.most_common():
            pct = count / len(doc.elements) * 100
            bar = "█" * int(pct / 5)
            print(f"    {etype:12s} │ {count:4d} │ {pct:5.1f}% {bar}")
    print(f"{'═' * 50}\n")


def validate_parsing(doc: ParsedDocument) -> list[str]:
    """
    Валидация качества парсинга.
    Возвращает список предупреждений.
    """
    warnings = []

    # Проверка покрытия страниц
    pages_with_content = {b.page_number for b in doc.blocks}
    missing_pages = set(range(doc.total_pages)) - pages_with_content
    if missing_pages:
        warnings.append(
            f"Нет контента на страницах: {sorted(missing_pages)}"
        )

    # Проверка пустых блоков
    empty_blocks = sum(1 for b in doc.blocks if len(b.content.strip()) < 5)
    if empty_blocks > len(doc.blocks) * 0.1:
        warnings.append(
            f"Много почти пустых блоков: {empty_blocks}/{len(doc.blocks)}"
        )

    # Проверка средней длины текстовых блоков
    text_blocks = [b for b in doc.blocks if b.block_type == "text"]
    if text_blocks:
        avg_len = sum(len(b.content) for b in text_blocks) / len(text_blocks)
        if avg_len < 20:
            warnings.append(
                f"Средний текстовый блок слишком короткий: {avg_len:.0f} символов"
            )

    # Проверка таблиц (должны содержать | или HTML-теги)
    table_blocks = [b for b in doc.blocks if b.block_type == "table"]
    for tb in table_blocks:
        if "|" not in tb.content and "<" not in tb.content:
            warnings.append(
                f"Таблица на стр. {tb.page_number} не содержит табличного формата"
            )

    # Проверка структурного слоя для документов с извлечёнными изображениями
    image_count = doc.metadata.get("image_count", 0)
    figure_count = sum(1 for e in doc.elements if e.element_type == "figure")
    if image_count and figure_count == 0:
        warnings.append(
            f"MinerU извлёк изображения ({image_count}), но figure-элементы не найдены"
        )

    for w in warnings:
        log.warning(w)

    return warnings


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 8: СОХРАНЕНИЕ ДЛЯ СЛЕДУЮЩЕЙ ФАЗЫ
# ══════════════════════════════════════════════════════════════

def save_for_next_phase(doc: ParsedDocument, output_path: str):
    """
    Сохранение результатов в формате, готовом для Фазы 2 (чанкинг).

    Формат: JSON с полной информацией о каждом блоке + провенанс.
    """
    output = {
        "source": doc.source_path,
        "source_hash": doc.source_hash,
        "total_pages": doc.total_pages,
        "backend": doc.backend_used,
        "parse_time_sec": doc.parse_time_sec,
        "metadata": doc.metadata,
        "blocks": [asdict(b) for b in doc.blocks],
        "elements": [asdict(e) for e in doc.elements],
        "full_markdown": doc.full_markdown,
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Результаты сохранены: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 9: БАТЧ-ОБРАБОТКА КОЛЛЕКЦИИ ДОКУМЕНТОВ
# ══════════════════════════════════════════════════════════════

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".jpeg"}


def batch_parse(
    input_dir: str,
    output_dir: str,
    backend: str = "auto",
    skip_existing: bool = True,
) -> list[ParsedDocument]:
    """
    Батч-парсинг всех документов в папке.

    Args:
        input_dir: Папка с документами
        output_dir: Куда складывать результаты
        backend: "pipeline" | "vlm" | "hybrid" | "auto"
        skip_existing: Пропускать уже обработанные файлы

    Returns:
        Список ParsedDocument
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Поиск файлов
    files = [
        f for f in input_path.rglob("*")
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    log.info(f"Найдено файлов для парсинга: {len(files)}")

    results = []
    for i, filepath in enumerate(files, 1):
        log.info(f"[{i}/{len(files)}] Обработка: {filepath.name}")

        # Проверка: уже обработан?
        result_json = output_path / f"{filepath.stem}_parsed.json"
        if skip_existing and result_json.exists():
            log.info(f"  ⏭  Пропущен (уже существует)")
            with open(result_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            doc = ParsedDocument(
                source_path=data["source"],
                source_hash=data["source_hash"],
                total_pages=data["total_pages"],
                blocks=[ParsedBlock(**b) for b in data.get("blocks", [])],
                elements=[DocumentElement(**e) for e in data.get("elements", [])],
                full_markdown=data.get("full_markdown", ""),
                metadata=data.get("metadata", {}),
                parse_time_sec=data.get("parse_time_sec", 0.0),
                backend_used=data.get("backend", "pipeline"),
            )
            results.append(doc)
            continue

        try:
            t0 = time.time()

            # Парсинг
            doc_output = output_path / filepath.stem
            parse_via_cli(str(filepath), str(doc_output), backend=backend)

            # Загрузка результатов
            doc = load_mineru_output(str(doc_output), str(filepath))
            doc.parse_time_sec = time.time() - t0
            doc.backend_used = backend

            # Очистка
            doc = clean_blocks(doc)

            # Валидация
            validate_parsing(doc)

            # Статистика
            print_stats(doc)

            # Сохранение
            save_for_next_phase(doc, str(result_json))

            results.append(doc)

        except Exception as e:
            import traceback
            log.error(f"  ✗ Ошибка при обработке {filepath.name}: {e}")
            traceback.print_exc()
            continue

    log.info(f"\nОбработано: {len(results)}/{len(files)} документов")
    return results


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 10: ГЛАВНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Примеры запуска:

    1. Один файл через CLI:
       python phase1_parsing.py --input paper.pdf --output ./parsed

    2. Папка с документами:
       python phase1_parsing.py --input ./documents/ --output ./parsed

    3. С VLM backend (точнее, но нужна GPU):
       python phase1_parsing.py --input ./documents/ --output ./parsed --backend vlm

    4. Через API (запустить mineru-api отдельно):
       python phase1_parsing.py --input paper.pdf --output ./parsed --use-api
    """
    import argparse

    parser = argparse.ArgumentParser(description="Фаза 1: Парсинг документов с MinerU")
    parser.add_argument("--input", "-i", required=True, help="Файл или папка с документами")
    parser.add_argument("--output", "-o", default="./parsed_output", help="Папка для результатов")
    parser.add_argument("--backend", "-b", default="auto", choices=["pipeline", "vlm", "hybrid", "auto"])
    parser.add_argument("--use-api", action="store_true", help="Использовать MinerU API сервер")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_dir():
        # Батч-обработка
        results = batch_parse(
            str(input_path),
            args.output,
            backend=args.backend,
        )
        print(f"\n🎉 Готово! Обработано {len(results)} документов")
        print(f"   Результаты в: {args.output}/")

    elif input_path.is_file():
        t0 = time.time()

        if args.use_api:
            # Через API
            result = parse_via_api(str(input_path), api_url=args.api_url)
            print(json.dumps(result, indent=2, ensure_ascii=False)[:2000])
        else:
            # Через CLI
            parse_via_cli(str(input_path), args.output, backend=args.backend)
            doc = load_mineru_output(args.output, str(input_path))
            doc.parse_time_sec = time.time() - t0
            doc.backend_used = args.backend
            doc = clean_blocks(doc)
            validate_parsing(doc)
            print_stats(doc)
            save_for_next_phase(doc, f"{args.output}/{input_path.stem}_parsed.json")
            print(f"\n🎉 Готово! Результат: {args.output}/{input_path.stem}_parsed.json")

    else:
        print(f"✗ Путь не найден: {args.input}")
