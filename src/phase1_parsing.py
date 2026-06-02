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
import hashlib
import logging
import shlex
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase1")


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
class ParsedDocument:
    """Результат парсинга одного документа."""
    source_path: str
    source_hash: str                        # MD5 для дедупликации
    total_pages: int
    blocks: list[ParsedBlock] = field(default_factory=list)
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

        for idx, item in enumerate(flat_items):
            text = _extract_block_text(item)
            if not text:
                continue

            block = ParsedBlock(
                block_type=item.get("type", "text"),
                content=text,
                page_number=item.get("page_idx", item.get("page_number", 0)),
                bbox=item.get("bbox", [0, 0, 0, 0]),
                block_index=idx,
                confidence=item.get("score", item.get("confidence", 1.0)),
            )
            blocks.append(block)

        log.info(f"Загружено блоков: {len(blocks)}")
    else:
        log.warning("JSON с блоками не найден")

    # ── Если блоков нет, но Markdown есть — парсим из Markdown ──
    if not blocks and full_markdown:
        blocks = _blocks_from_markdown(full_markdown)
        log.info(f"Блоки восстановлены из Markdown: {len(blocks)}")

    # ── Сборка документа ──
    source_hash = _file_md5(source_path)
    page_numbers = {b.page_number for b in blocks}
    total_pages = max(page_numbers) + 1 if page_numbers else 1

    img_dir = doc_dir / "images"
    img_count = len(list(img_dir.iterdir())) if img_dir.exists() else 0

    return ParsedDocument(
        source_path=source_path,
        source_hash=source_hash,
        total_pages=total_pages,
        blocks=blocks,
        full_markdown=full_markdown,
        metadata={
            "doc_name": Path(source_path).stem,
            "output_dir": str(doc_dir),
            "image_count": img_count,
            "json_source": str(json_files[0]) if json_files else None,
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
      - {"type": "title", "content": {"title_content": [{"content": "..."}]}}
      - {"type": "paragraph", "content": {"paragraph_content": [{"content": "..."}]}}
    """
    # Прямое поле text
    if "text" in item and isinstance(item["text"], str):
        return item["text"].strip()

    # Вложенная структура content
    content = item.get("content")
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, dict):
        for key in ("title_content", "paragraph_content", "table_content"):
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
    from collections import Counter

    type_counts = Counter(b.block_type for b in doc.blocks)
    total_chars = sum(len(b.content) for b in doc.blocks)

    print(f"\n{'═' * 50}")
    print(f"  📄 {Path(doc.source_path).name}")
    print(f"{'═' * 50}")
    print(f"  Страниц:  {doc.total_pages}")
    print(f"  Блоков:   {len(doc.blocks)}")
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
                full_markdown=data.get("full_markdown", ""),
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
