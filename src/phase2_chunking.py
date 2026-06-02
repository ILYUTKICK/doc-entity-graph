"""
═══════════════════════════════════════════════════════════════
  Фаза 2: Семантический чанкинг
  Проект: Построение графа сущностей документов
═══════════════════════════════════════════════════════════════

Вход:  *_parsed.json из Фазы 1
Выход: *_chunked.json — чанки, готовые для NER/RE (Фаза 3)

Стратегии чанкинга:
  1. По секциям документа (заголовки = границы)
  2. С перекрытием для сохранения контекста NER
  3. Адаптивный размер: мелкие блоки склеиваются, большие разбиваются

Запуск:
  python phase2_chunking.py -i ./parsed/ -o ./chunked/
  python phase2_chunking.py -i ./parsed/ -o ./chunked/ --max-tokens 512
"""

import json
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase2")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: СТРУКТУРЫ ДАННЫХ
# ══════════════════════════════════════════════════════════════

@dataclass
class Chunk:
    """Один чанк, готовый для NER/RE."""
    chunk_id: str                    # Уникальный ID: {doc_hash}_{chunk_index}
    text: str                        # Текст чанка
    token_count: int                 # Примерное количество токенов
    source_doc: str                  # Путь к исходному документу
    source_hash: str                 # MD5 документа
    page_start: int                  # Первая страница
    page_end: int                    # Последняя страница
    section_title: str = ""          # Заголовок текущей секции
    section_hierarchy: list = field(default_factory=list)  # [H1, H2, H3]
    block_indices: list = field(default_factory=list)      # Индексы блоков
    has_equations: bool = False      # Есть ли формулы
    has_tables: bool = False         # Есть ли таблицы
    overlap_prev: bool = False       # Перекрывается с предыдущим чанком
    overlap_next: bool = False       # Перекрывается со следующим чанком


@dataclass
class ChunkedDocument:
    """Результат чанкинга одного документа."""
    source_path: str
    source_hash: str
    total_chunks: int
    chunks: list[Chunk]
    config: dict                     # Параметры чанкинга
    stats: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: ЗАГРУЗКА ДАННЫХ ИЗ ФАЗЫ 1
# ══════════════════════════════════════════════════════════════

def load_parsed_document(json_path: str) -> dict:
    """Загрузка результатов Фазы 1."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.info(
        f"Загружен: {Path(data['source']).name} — "
        f"{len(data['blocks'])} блоков, {data['total_pages']} стр."
    )
    return data


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: УТИЛИТЫ
# ══════════════════════════════════════════════════════════════

def estimate_tokens(text: str) -> int:
    """
    Быстрая оценка количества токенов.
    Для русского текста ~1.5 токена на слово (кириллица дороже).
    Для английского ~1.3 токена на слово.
    """
    words = len(text.split())
    has_cyrillic = bool(re.search("[а-яА-ЯёЁ]", text))
    multiplier = 1.5 if has_cyrillic else 1.3
    return max(1, int(words * multiplier))


def is_title_block(block: dict) -> bool:
    """Проверка: является ли блок заголовком."""
    btype = block.get("block_type", block.get("type", ""))
    text = block.get("content", block.get("text", ""))

    if btype == "title":
        return True

    # Markdown-заголовки в тексте
    if text.strip().startswith("#"):
        return True

    # Короткая строка с заглавными буквами (типичный заголовок слайда)
    if len(text.strip()) < 100 and text.strip().isupper():
        return True

    return False


def get_title_level(block: dict) -> int:
    """Уровень заголовка: 1 = H1, 2 = H2, ..., 0 = не заголовок."""
    text = block.get("content", block.get("text", "")).strip()

    # Markdown-стиль: ## Заголовок
    if text.startswith("#"):
        level = len(text) - len(text.lstrip("#"))
        return min(level, 4)

    # MinerU text_level
    text_level = block.get("text_level", 0)
    if text_level:
        return text_level

    # Если title по типу — считаем H2 по умолчанию
    if is_title_block(block):
        return 2

    return 0


def clean_title(text: str) -> str:
    """Очистка текста заголовка от Markdown-разметки."""
    return text.strip().lstrip("#").strip()


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: СТРАТЕГИЯ 1 — СЕКЦИОННЫЙ ЧАНКИНГ
# ══════════════════════════════════════════════════════════════

def chunk_by_sections(
    blocks: list[dict],
    source_doc: str,
    source_hash: str,
    max_tokens: int = 512,
    min_tokens: int = 50,
    overlap_sentences: int = 2,
) -> list[Chunk]:
    """
    Чанкинг по секциям документа.

    Логика:
      1. Заголовки = границы секций
      2. Блоки между заголовками объединяются в один чанк
      3. Если чанк > max_tokens — разбивается по предложениям
      4. Если чанк < min_tokens — склеивается со следующим
      5. Между чанками добавляется перекрытие (overlap)

    Args:
        blocks: список блоков из Фазы 1
        max_tokens: максимум токенов в чанке (512 для BERT, 1024 для LLM)
        min_tokens: минимальный размер чанка
        overlap_sentences: сколько предложений перекрытия
    """
    # ── Шаг 1: Группировка блоков по секциям ──
    sections = _group_into_sections(blocks)
    log.info(f"Найдено секций: {len(sections)}")

    # ── Шаг 2: Секции → чанки с учётом размера ──
    raw_chunks = []
    for section in sections:
        section_chunks = _section_to_chunks(
            section, max_tokens=max_tokens, min_tokens=min_tokens
        )
        raw_chunks.extend(section_chunks)

    # ── Шаг 3: Добавление перекрытия ──
    if overlap_sentences > 0:
        raw_chunks = _add_overlap(raw_chunks, overlap_sentences)

    # ── Шаг 4: Сборка финальных Chunk объектов ──
    chunks = []
    for idx, rc in enumerate(raw_chunks):
        chunk = Chunk(
            chunk_id=f"{source_hash[:8]}_{idx:04d}",
            text=rc["text"],
            token_count=estimate_tokens(rc["text"]),
            source_doc=source_doc,
            source_hash=source_hash,
            page_start=rc.get("page_start", 0),
            page_end=rc.get("page_end", 0),
            section_title=rc.get("section_title", ""),
            section_hierarchy=rc.get("hierarchy", []),
            block_indices=rc.get("block_indices", []),
            has_equations=rc.get("has_equations", False),
            has_tables=rc.get("has_tables", False),
            overlap_prev=rc.get("overlap_prev", False),
            overlap_next=rc.get("overlap_next", False),
        )
        chunks.append(chunk)

    return chunks


def _group_into_sections(blocks: list[dict]) -> list[dict]:
    """
    Группировка блоков по секциям.
    Каждая секция = заголовок + все блоки до следующего заголовка.
    """
    sections = []
    current_section = {
        "title": "",
        "hierarchy": [],
        "blocks": [],
        "pages": set(),
        "has_equations": False,
        "has_tables": False,
        "block_indices": [],
    }
    # Стек заголовков для иерархии [H1, H2, H3]
    hierarchy_stack = []

    for block in blocks:
        btype = block.get("block_type", block.get("type", "text"))
        text = block.get("content", block.get("text", "")).strip()
        page = block.get("page_number", block.get("page_idx", 0))
        b_idx = block.get("block_index", 0)

        if not text:
            continue

        level = get_title_level(block)

        if level > 0:
            # ─── Новый заголовок → сохраняем текущую секцию ───
            if current_section["blocks"]:
                sections.append(current_section)

            # Обновляем иерархию
            title_text = clean_title(text)
            # Обрезаем стек до текущего уровня
            hierarchy_stack = [
                h for h in hierarchy_stack if h[0] < level
            ]
            hierarchy_stack.append((level, title_text))

            current_section = {
                "title": title_text,
                "hierarchy": [h[1] for h in hierarchy_stack],
                "blocks": [],
                "pages": {page},
                "has_equations": False,
                "has_tables": False,
                "block_indices": [],
            }
        else:
            # ─── Обычный блок → добавляем в текущую секцию ───
            current_section["blocks"].append(text)
            current_section["pages"].add(page)
            current_section["block_indices"].append(b_idx)

            if btype == "equation":
                current_section["has_equations"] = True
            if btype == "table":
                current_section["has_tables"] = True

    # Последняя секция
    if current_section["blocks"]:
        sections.append(current_section)

    return sections


def _section_to_chunks(
    section: dict,
    max_tokens: int,
    min_tokens: int,
) -> list[dict]:
    """
    Преобразование секции в один или несколько чанков.
    Если секция слишком большая — разбиваем по предложениям.
    """
    full_text = "\n".join(section["blocks"])
    tokens = estimate_tokens(full_text)
    pages = sorted(section["pages"]) if section["pages"] else [0]

    base = {
        "section_title": section["title"],
        "hierarchy": section["hierarchy"],
        "has_equations": section["has_equations"],
        "has_tables": section["has_tables"],
        "block_indices": section["block_indices"],
    }

    # Секция помещается в один чанк
    if tokens <= max_tokens:
        # Добавляем заголовок секции в текст для контекста NER
        text = full_text
        if section["title"]:
            text = f"{section['title']}\n\n{full_text}"

        return [{
            **base,
            "text": text.strip(),
            "page_start": pages[0],
            "page_end": pages[-1],
        }]

    # Секция слишком большая — разбиваем по предложениям
    sentences = _split_sentences(full_text)
    chunks = []
    current_sents = []
    current_tokens = 0

    for sent in sentences:
        sent_tokens = estimate_tokens(sent)

        if current_tokens + sent_tokens > max_tokens and current_sents:
            # Сохраняем текущий чанк
            text = " ".join(current_sents)
            if section["title"]:
                text = f"{section['title']}\n\n{text}"

            chunks.append({
                **base,
                "text": text.strip(),
                "page_start": pages[0],
                "page_end": pages[-1],
            })
            current_sents = []
            current_tokens = 0

        current_sents.append(sent)
        current_tokens += sent_tokens

    # Остаток
    if current_sents:
        text = " ".join(current_sents)
        if section["title"]:
            text = f"{section['title']}\n\n{text}"

        # Если остаток слишком маленький — склеиваем с последним
        if current_tokens < min_tokens and chunks:
            chunks[-1]["text"] += "\n" + text.strip()
        else:
            chunks.append({
                **base,
                "text": text.strip(),
                "page_start": pages[0],
                "page_end": pages[-1],
            })

    return chunks


def _split_sentences(text: str) -> list[str]:
    """
    Разбиение текста на предложения.
    Поддержка русского и английского.
    """
    # Разбиваем по точке/!/? с учётом сокращений
    pattern = r"(?<=[.!?])\s+(?=[A-ZА-ЯЁ\d\"«])"
    sentences = re.split(pattern, text)

    # Фильтруем пустые
    return [s.strip() for s in sentences if s.strip()]


def _add_overlap(chunks: list[dict], n_sentences: int) -> list[dict]:
    """
    Добавление перекрытия между соседними чанками.
    Последние n предложений текущего чанка добавляются в начало следующего.
    """
    if len(chunks) <= 1:
        return chunks

    for i in range(len(chunks) - 1):
        # Берём последние n предложений текущего чанка
        sentences = _split_sentences(chunks[i]["text"])
        overlap_text = " ".join(sentences[-n_sentences:])

        if overlap_text:
            chunks[i]["overlap_next"] = True
            chunks[i + 1]["overlap_prev"] = True
            chunks[i + 1]["text"] = overlap_text + "\n" + chunks[i + 1]["text"]

    return chunks


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: СТРАТЕГИЯ 2 — СКОЛЬЗЯЩЕЕ ОКНО
# ══════════════════════════════════════════════════════════════

def chunk_sliding_window(
    blocks: list[dict],
    source_doc: str,
    source_hash: str,
    window_tokens: int = 512,
    step_tokens: int = 384,
) -> list[Chunk]:
    """
    Чанкинг скользящим окном — для документов без чёткой структуры.

    Overlap = window_tokens - step_tokens.
    При window=512, step=384 → overlap=128 токенов (~25%).
    """
    # Склеиваем весь текст
    full_text = "\n".join(
        block.get("content", block.get("text", ""))
        for block in blocks
        if block.get("content", block.get("text", "")).strip()
    )

    sentences = _split_sentences(full_text)
    chunks = []
    current_sents = []
    current_tokens = 0
    chunk_idx = 0

    i = 0
    while i < len(sentences):
        sent = sentences[i]
        sent_tokens = estimate_tokens(sent)

        current_sents.append(sent)
        current_tokens += sent_tokens

        if current_tokens >= window_tokens:
            # Создаём чанк
            text = " ".join(current_sents)
            chunks.append(Chunk(
                chunk_id=f"{source_hash[:8]}_sw{chunk_idx:04d}",
                text=text.strip(),
                token_count=estimate_tokens(text),
                source_doc=source_doc,
                source_hash=source_hash,
                page_start=0,
                page_end=0,
                overlap_prev=chunk_idx > 0,
                overlap_next=True,
            ))
            chunk_idx += 1

            # Откатываемся на step_tokens
            rollback_tokens = 0
            while current_sents and rollback_tokens < (window_tokens - step_tokens):
                removed = current_sents.pop(0)
                rollback_tokens += estimate_tokens(removed)
            current_tokens -= rollback_tokens

        i += 1

    # Остаток
    if current_sents:
        text = " ".join(current_sents)
        chunks.append(Chunk(
            chunk_id=f"{source_hash[:8]}_sw{chunk_idx:04d}",
            text=text.strip(),
            token_count=estimate_tokens(text),
            source_doc=source_doc,
            source_hash=source_hash,
            page_start=0,
            page_end=0,
            overlap_prev=chunk_idx > 0,
            overlap_next=False,
        ))

    return chunks


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 6: ОСНОВНОЙ ПАЙПЛАЙН
# ══════════════════════════════════════════════════════════════

def chunk_document(
    parsed_json_path: str,
    strategy: str = "sections",
    max_tokens: int = 512,
    min_tokens: int = 50,
    overlap_sentences: int = 2,
) -> ChunkedDocument:
    """
    Чанкинг одного документа.

    Args:
        parsed_json_path: путь к *_parsed.json из Фазы 1
        strategy: "sections" или "sliding"
        max_tokens: максимум токенов в чанке
        min_tokens: минимальный размер чанка
        overlap_sentences: перекрытие (количество предложений)
    """
    # Загрузка данных из Фазы 1
    data = load_parsed_document(parsed_json_path)
    blocks = data["blocks"]
    source_doc = data["source"]
    source_hash = data["source_hash"]

    config = {
        "strategy": strategy,
        "max_tokens": max_tokens,
        "min_tokens": min_tokens,
        "overlap_sentences": overlap_sentences,
    }

    # Выбор стратегии
    if strategy == "sections":
        chunks = chunk_by_sections(
            blocks, source_doc, source_hash,
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            overlap_sentences=overlap_sentences,
        )
    elif strategy == "sliding":
        chunks = chunk_sliding_window(
            blocks, source_doc, source_hash,
            window_tokens=max_tokens,
            step_tokens=int(max_tokens * 0.75),
        )
    else:
        raise ValueError(f"Неизвестная стратегия: {strategy}")

    # Фильтрация пустых чанков
    chunks = [c for c in chunks if c.text.strip() and c.token_count >= 10]

    # Статистика
    token_counts = [c.token_count for c in chunks]
    stats = {
        "total_chunks": len(chunks),
        "total_tokens": sum(token_counts),
        "avg_tokens": sum(token_counts) / len(token_counts) if token_counts else 0,
        "min_tokens": min(token_counts) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
        "chunks_with_equations": sum(1 for c in chunks if c.has_equations),
        "chunks_with_tables": sum(1 for c in chunks if c.has_tables),
        "chunks_with_overlap": sum(1 for c in chunks if c.overlap_prev or c.overlap_next),
    }

    return ChunkedDocument(
        source_path=source_doc,
        source_hash=source_hash,
        total_chunks=len(chunks),
        chunks=chunks,
        config=config,
        stats=stats,
    )


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 7: СТАТИСТИКА И ВАЛИДАЦИЯ
# ══════════════════════════════════════════════════════════════

def print_stats(doc: ChunkedDocument):
    """Вывод статистики по чанкам."""
    s = doc.stats
    print(f"\n{'═' * 55}")
    print(f"  📄 {Path(doc.source_path).name}")
    print(f"{'═' * 55}")
    print(f"  Стратегия:      {doc.config['strategy']}")
    print(f"  Max токенов:    {doc.config['max_tokens']}")
    print(f"  Чанков:         {s['total_chunks']}")
    print(f"  Всего токенов:  {s['total_tokens']:,}")
    print(f"  Среднее:        {s['avg_tokens']:.0f} токенов/чанк")
    print(f"  Мин/Макс:       {s['min_tokens']} / {s['max_tokens']}")
    print(f"  С формулами:    {s['chunks_with_equations']}")
    print(f"  С перекрытием:  {s['chunks_with_overlap']}")
    print(f"{'─' * 55}")

    # Распределение размеров
    buckets = {"< 100": 0, "100-300": 0, "300-500": 0, "500+": 0}
    for c in doc.chunks:
        if c.token_count < 100:
            buckets["< 100"] += 1
        elif c.token_count < 300:
            buckets["100-300"] += 1
        elif c.token_count < 500:
            buckets["300-500"] += 1
        else:
            buckets["500+"] += 1

    print("  Распределение размеров:")
    for bucket, count in buckets.items():
        pct = count / len(doc.chunks) * 100 if doc.chunks else 0
        bar = "█" * int(pct / 5)
        print(f"    {bucket:>8s} │ {count:3d} │ {pct:5.1f}% {bar}")

    # Примеры секций
    sections = set()
    for c in doc.chunks:
        if c.section_title:
            sections.add(c.section_title)
    if sections:
        print(f"{'─' * 55}")
        print(f"  Секции ({len(sections)}):")
        for title in list(sections)[:10]:
            print(f"    • {title[:60]}")
        if len(sections) > 10:
            print(f"    ... и ещё {len(sections) - 10}")

    print(f"{'═' * 55}\n")


def preview_chunks(doc: ChunkedDocument, n: int = 3):
    """Показать первые n чанков для проверки."""
    print(f"\n{'─' * 55}")
    print(f"  Превью чанков ({n} из {len(doc.chunks)}):")
    print(f"{'─' * 55}")
    for i, chunk in enumerate(doc.chunks[:n]):
        section = f" [{chunk.section_title}]" if chunk.section_title else ""
        overlap = " (overlap)" if chunk.overlap_prev else ""
        print(f"\n  ┌─ Чанк {chunk.chunk_id}{section}{overlap}")
        print(f"  │  Стр. {chunk.page_start}-{chunk.page_end} │ ~{chunk.token_count} токенов")
        print(f"  │")
        # Первые 200 символов текста
        preview = chunk.text[:200].replace("\n", "\n  │  ")
        print(f"  │  {preview}")
        if len(chunk.text) > 200:
            print(f"  │  ...")
        print(f"  └─")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 8: СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════

def save_chunked(doc: ChunkedDocument, output_path: str):
    """Сохранение результатов чанкинга для Фазы 3 (NER/RE)."""
    output = {
        "source": doc.source_path,
        "source_hash": doc.source_hash,
        "config": doc.config,
        "stats": doc.stats,
        "total_chunks": doc.total_chunks,
        "chunks": [asdict(c) for c in doc.chunks],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Результаты сохранены: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 9: БАТЧ-ОБРАБОТКА
# ══════════════════════════════════════════════════════════════

def batch_chunk(
    input_dir: str,
    output_dir: str,
    strategy: str = "sections",
    max_tokens: int = 512,
    overlap_sentences: int = 2,
) -> list[ChunkedDocument]:
    """Батч-чанкинг всех *_parsed.json в папке."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Поиск файлов Фазы 1
    parsed_files = sorted(input_path.glob("*_parsed.json"))
    if not parsed_files:
        log.error(f"Не найдено *_parsed.json в {input_dir}")
        return []

    log.info(f"Найдено документов для чанкинга: {len(parsed_files)}")

    results = []
    total_chunks = 0

    for i, pf in enumerate(parsed_files, 1):
        log.info(f"[{i}/{len(parsed_files)}] {pf.stem.replace('_parsed', '')}")

        try:
            doc = chunk_document(
                str(pf),
                strategy=strategy,
                max_tokens=max_tokens,
                overlap_sentences=overlap_sentences,
            )
            print_stats(doc)
            preview_chunks(doc, n=2)

            # Сохранение
            out_name = pf.stem.replace("_parsed", "_chunked") + ".json"
            save_chunked(doc, str(output_path / out_name))

            results.append(doc)
            total_chunks += doc.total_chunks

        except Exception as e:
            import traceback
            log.error(f"  ✗ Ошибка: {e}")
            traceback.print_exc()
            continue

    log.info(f"\nИтого: {total_chunks} чанков из {len(results)} документов")
    return results


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 10: ГЛАВНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Фаза 2: Семантический чанкинг")
    parser.add_argument("--input", "-i", required=True, help="Папка с *_parsed.json")
    parser.add_argument("--output", "-o", default="./chunked", help="Папка для результатов")
    parser.add_argument(
        "--strategy", "-s",
        default="sections",
        choices=["sections", "sliding"],
        help="Стратегия: sections (по заголовкам) или sliding (окно)",
    )
    parser.add_argument("--max-tokens", type=int, default=512, help="Макс. токенов в чанке")
    parser.add_argument("--overlap", type=int, default=2, help="Перекрытие (предложений)")
    args = parser.parse_args()

    results = batch_chunk(
        args.input,
        args.output,
        strategy=args.strategy,
        max_tokens=args.max_tokens,
        overlap_sentences=args.overlap,
    )

    if results:
        total = sum(r.total_chunks for r in results)
        print(f"\n🎉 Готово! {total} чанков в {args.output}/")
        print(f"   Следующий шаг: python phase3_ner.py -i {args.output}/")
