"""Фаза 3: извлечение именованных сущностей из чанков.

Вход:
    ``data/chunked/*_chunked.json`` из Фазы 2.

Выход:
    ``data/entities/*_entities.json`` с нормализованными сущностями и
    сохранённой привязкой к чанкам и документам.

Движки:
    ``spacy`` для базового NER, ``gliner`` для zero-shot доменных меток и
    ``llm`` для опционального API-режима.
"""

import json
import logging
import re
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import Counter, defaultdict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase3")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 1: СТРУКТУРЫ ДАННЫХ
# ══════════════════════════════════════════════════════════════

@dataclass
class Entity:
    """Одна извлечённая сущность."""
    text: str                        # Текст сущности как в документе
    normalized: str                  # Нормализованная форма
    entity_type: str                 # PERSON, ORG, CONCEPT, DATE, ...
    confidence: float                # Уверенность модели [0, 1]
    chunk_id: str                    # Из какого чанка
    source_doc: str                  # Из какого документа
    context: str = ""                # Предложение-контекст (для RE)
    start_char: int = 0              # Позиция в чанке
    end_char: int = 0
    page_start: int = 0              # Страница начала чанка
    page_end: int = 0                # Страница конца чанка
    section_title: str = ""          # Секция чанка
    section_hierarchy: list = field(default_factory=list)
    block_indices: list = field(default_factory=list)
    source_blocks: list = field(default_factory=list)
    source_element_ids: list = field(default_factory=list)
    related_element_ids: list = field(default_factory=list)
    source_elements: list = field(default_factory=list)
    related_elements: list = field(default_factory=list)


@dataclass
class DocumentEntities:
    """Все сущности одного документа."""
    source_path: str
    source_hash: str
    engine: str                      # spacy / gliner / llm
    total_entities: int
    unique_entities: int
    entities: list[Entity]
    entity_counts: dict = field(default_factory=dict)  # {type: count}
    stats: dict = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 2: ЗАГРУЗКА ДАННЫХ ИЗ ФАЗЫ 2
# ══════════════════════════════════════════════════════════════

def load_chunked_document(json_path: str) -> dict:
    """Загрузка результатов Фазы 2."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    log.info(
        f"Загружен: {Path(data['source']).name} — "
        f"{data['total_chunks']} чанков"
    )
    return data


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 3: ДВИЖОК 1 — SpaCy (быстрый, CPU)
# ══════════════════════════════════════════════════════════════

class SpacyNER:
    """
    NER через SpaCy — быстрый baseline.

    Типы: PERSON, ORG, LOC, DATE, MONEY, PRODUCT, EVENT, WORK_OF_ART
    Поддержка русского (ru_core_news_lg) и английского (en_core_web_sm).
    """

    # Маппинг SpaCy типов → наши типы
    TYPE_MAP = {
        "PER": "PERSON",
        "PERSON": "PERSON",
        "ORG": "ORG",
        "LOC": "LOCATION",
        "GPE": "LOCATION",
        "DATE": "DATE",
        "MONEY": "MONEY",
        "PRODUCT": "PRODUCT",
        "EVENT": "EVENT",
        "WORK_OF_ART": "WORK",
        "FAC": "FACILITY",
        "NORP": "GROUP",
        "CARDINAL": "NUMBER",
        "ORDINAL": "NUMBER",
        "PERCENT": "PERCENT",
    }

    def __init__(self):
        import spacy

        self.nlp_ru = None
        self.nlp_en = None

        # Загрузка моделей
        try:
            self.nlp_ru = spacy.load("ru_core_news_lg")
            log.info("SpaCy: загружена ru_core_news_lg")
        except OSError:
            try:
                self.nlp_ru = spacy.load("ru_core_news_sm")
                log.info("SpaCy: загружена ru_core_news_sm")
            except OSError:
                log.warning("SpaCy: русская модель не найдена")

        try:
            self.nlp_en = spacy.load("en_core_web_sm")
            log.info("SpaCy: загружена en_core_web_sm")
        except OSError:
            log.warning("SpaCy: английская модель не найдена")

        if not self.nlp_ru and not self.nlp_en:
            raise RuntimeError(
                "Нет ни одной SpaCy модели. Установи:\n"
                "  python -m spacy download ru_core_news_lg\n"
                "  python -m spacy download en_core_web_sm"
            )

    def _detect_lang(self, text: str) -> str:
        """Определение языка по наличию кириллицы."""
        cyrillic = len(re.findall("[а-яА-ЯёЁ]", text))
        latin = len(re.findall("[a-zA-Z]", text))
        return "ru" if cyrillic > latin else "en"

    def extract(self, text: str, chunk_id: str, source_doc: str) -> list[Entity]:
        """Извлечение сущностей из текста."""
        lang = self._detect_lang(text)
        nlp = self.nlp_ru if lang == "ru" and self.nlp_ru else self.nlp_en
        if not nlp:
            nlp = self.nlp_ru or self.nlp_en

        doc = nlp(text)
        entities = []

        for ent in doc.ents:
            etype = self.TYPE_MAP.get(ent.label_, ent.label_)

            # Контекст: предложение, содержащее сущность
            context = ""
            for sent in doc.sents:
                if sent.start <= ent.start < sent.end:
                    context = sent.text.strip()
                    break

            entities.append(Entity(
                text=ent.text.strip(),
                normalized=normalize_entity(ent.text.strip()),
                entity_type=etype,
                confidence=0.85,  # SpaCy не даёт confidence, ставим дефолт
                chunk_id=chunk_id,
                source_doc=source_doc,
                context=context,
                start_char=ent.start_char,
                end_char=ent.end_char,
            ))

        return entities


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 4: ДВИЖОК 2 — GLiNER (zero-shot)
# ══════════════════════════════════════════════════════════════

class GLiNEREngine:
    """
    Zero-shot NER через GLiNER.
    Позволяет задавать произвольные типы сущностей без дообучения.
    """

    # Типы, релевантные для финансовых документов
    DEFAULT_LABELS = [
        "person",
        "organization",
        "financial model",
        "financial instrument",
        "economic concept",
        "formula",
        "date",
        "location",
        "publication",
        "university",
    ]

    # Маппинг GLiNER labels → наши типы
    TYPE_MAP = {
        "person": "PERSON",
        "organization": "ORG",
        "financial model": "CONCEPT",
        "financial instrument": "CONCEPT",
        "economic concept": "CONCEPT",
        "formula": "FORMULA",
        "date": "DATE",
        "location": "LOCATION",
        "publication": "WORK",
        "university": "ORG",
    }

    def __init__(self, model_name: str = "urchade/gliner_multi-v2.1", labels: list = None):
        from gliner import GLiNER

        log.info(f"GLiNER: загрузка модели {model_name}...")
        self.model = GLiNER.from_pretrained(model_name)
        self.labels = labels or self.DEFAULT_LABELS
        log.info(f"GLiNER: типы сущностей: {self.labels}")

    def extract(self, text: str, chunk_id: str, source_doc: str) -> list[Entity]:
        """Извлечение сущностей через zero-shot."""
        # GLiNER ограничен по длине — разбиваем длинный текст
        max_len = 1000
        segments = [text[i:i + max_len] for i in range(0, len(text), max_len - 100)]

        entities = []
        for segment in segments:
            try:
                predictions = self.model.predict_entities(
                    segment,
                    self.labels,
                    threshold=0.3,
                )
            except Exception as e:
                log.warning(f"GLiNER ошибка на сегменте: {e}")
                continue

            for pred in predictions:
                etype = self.TYPE_MAP.get(pred["label"], pred["label"].upper())

                entities.append(Entity(
                    text=pred["text"].strip(),
                    normalized=normalize_entity(pred["text"].strip()),
                    entity_type=etype,
                    confidence=round(pred["score"], 3),
                    chunk_id=chunk_id,
                    source_doc=source_doc,
                    context=_extract_context(text, pred["text"]),
                    start_char=pred.get("start", 0),
                    end_char=pred.get("end", 0),
                ))

        return entities


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 5: ДВИЖОК 3 — LLM (самый гибкий)
# ══════════════════════════════════════════════════════════════

class LLMEngine:
    """
    NER через LLM API (Claude / OpenAI / локальная модель).

    Самый точный, но самый медленный и дорогой.
    Подходит для domain-specific типов.
    """

    SYSTEM_PROMPT = """Ты — система извлечения именованных сущностей (NER) из финансовых и экономических документов.

Для каждого текста извлеки ВСЕ сущности следующих типов:
- PERSON: имена людей (авторы, экономисты, учёные)
- ORG: организации (компании, биржи, университеты, банки)
- CONCEPT: финансовые/экономические концепции и модели (CAPM, APT, хеджирование, арбитраж)
- INSTRUMENT: финансовые инструменты (акции, облигации, фьючерсы, форварды, опционы)
- FORMULA: математические формулы и уравнения
- DATE: даты, периоды
- WORK: публикации, книги, статьи
- LOCATION: страны, города, рынки

Ответь ТОЛЬКО в JSON формате, без пояснений:
[
  {"text": "...", "type": "...", "confidence": 0.95},
  ...
]

Если сущностей нет, верни пустой список: []"""

    def __init__(self, api_key: str = None, model: str = "claude-sonnet-4-20250514", base_url: str = None):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url or "https://api.anthropic.com"
        log.info(f"LLM Engine: {model}")

    def extract(self, text: str, chunk_id: str, source_doc: str) -> list[Entity]:
        """Извлечение сущностей через LLM."""
        import requests

        # Формируем запрос
        if "anthropic" in self.base_url:
            return self._call_anthropic(text, chunk_id, source_doc)
        else:
            return self._call_openai(text, chunk_id, source_doc)

    def _call_anthropic(self, text: str, chunk_id: str, source_doc: str) -> list[Entity]:
        import requests

        resp = requests.post(
            f"{self.base_url}/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": self.model,
                "max_tokens": 2000,
                "system": self.SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": text[:4000]}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["content"][0]["text"]
        return self._parse_llm_response(content, chunk_id, source_doc, text)

    def _call_openai(self, text: str, chunk_id: str, source_doc: str) -> list[Entity]:
        import requests

        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": text[:4000]},
                ],
                "temperature": 0,
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return self._parse_llm_response(content, chunk_id, source_doc, text)

    def _parse_llm_response(
        self, response: str, chunk_id: str, source_doc: str, original_text: str
    ) -> list[Entity]:
        """Парсинг JSON ответа от LLM."""
        # Извлекаем JSON из ответа
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r"```json?\s*", "", response)
            response = response.rstrip("`").strip()

        try:
            items = json.loads(response)
        except json.JSONDecodeError:
            # Пробуем найти JSON в тексте
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                try:
                    items = json.loads(match.group())
                except json.JSONDecodeError:
                    log.warning(f"Не удалось распарсить LLM ответ для чанка {chunk_id}")
                    return []
            else:
                return []

        entities = []
        for item in items:
            if not isinstance(item, dict) or "text" not in item:
                continue

            entities.append(Entity(
                text=item["text"].strip(),
                normalized=normalize_entity(item["text"].strip()),
                entity_type=item.get("type", "UNKNOWN"),
                confidence=float(item.get("confidence", 0.9)),
                chunk_id=chunk_id,
                source_doc=source_doc,
                context=_extract_context(original_text, item["text"]),
            ))

        return entities


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 6: НОРМАЛИЗАЦИЯ СУЩНОСТЕЙ
# ══════════════════════════════════════════════════════════════

def normalize_entity(text: str) -> str:
    """
    Нормализация текста сущности.

    - Убираем лишние пробелы, переносы строк
    - Приводим к нижнему регистру для сравнения
    - Убираем знаки препинания по краям
    """
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(".,;:!?()[]{}\"'«»""")
    return text.lower()


def _extract_context(full_text: str, entity_text: str, window: int = 150) -> str:
    """Извлечение контекста вокруг сущности."""
    pos = full_text.find(entity_text)
    if pos == -1:
        pos = full_text.lower().find(entity_text.lower())
    if pos == -1:
        return ""

    start = max(0, pos - window)
    end = min(len(full_text), pos + len(entity_text) + window)

    context = full_text[start:end].strip()
    if start > 0:
        context = "..." + context
    if end < len(full_text):
        context = context + "..."

    return context


def attach_chunk_provenance(entities: list[Entity], chunk: dict) -> list[Entity]:
    """Наследует provenance чанка для каждой извлечённой сущности."""
    for ent in entities:
        ent.page_start = chunk.get("page_start", 0)
        ent.page_end = chunk.get("page_end", ent.page_start)
        ent.section_title = chunk.get("section_title", "")
        ent.section_hierarchy = list(chunk.get("section_hierarchy", []))
        ent.block_indices = list(chunk.get("block_indices", []))
        ent.source_blocks = list(chunk.get("source_blocks", []))
        ent.source_element_ids = list(chunk.get("source_element_ids", []))
        ent.related_element_ids = list(chunk.get("related_element_ids", []))
        ent.source_elements = list(chunk.get("source_elements", []))
        ent.related_elements = list(chunk.get("related_elements", []))

    return entities


def _unique_keep_order(values: list) -> list:
    """Дедупликация списка без потери порядка."""
    seen = set()
    result = []

    for value in values:
        if value is None:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)

    return result


def _merge_scalar_lists(groups: list[Entity], attr: str) -> list:
    """Объединение list[str/int] полей у сущностей."""
    values = []
    for ent in groups:
        value = getattr(ent, attr, [])
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    return _unique_keep_order(values)


def _merge_summary_lists(groups: list[Entity], attr: str) -> list[dict]:
    """Объединение списков словарей с кратким provenance-описанием."""
    result = []
    seen = set()

    for ent in groups:
        for item in getattr(ent, attr, []):
            if not isinstance(item, dict):
                continue

            key = (
                item.get("element_id"),
                item.get("block_index"),
                item.get("element_type"),
                item.get("block_type"),
                item.get("text_preview"),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(item)

    return result


def _split_chunk_ids(chunk_id: str) -> list[str]:
    return [cid.strip() for cid in chunk_id.split(",") if cid.strip()]


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 7: ПОСТОБРАБОТКА И ДЕДУПЛИКАЦИЯ
# ══════════════════════════════════════════════════════════════

def deduplicate_entities(entities: list[Entity]) -> list[Entity]:
    """
    Дедупликация сущностей внутри документа.

    Логика:
      - Группируем по normalized text + type
      - Оставляем с наивысшим confidence
      - Сохраняем все chunk_id для провенанса
    """
    groups = defaultdict(list)
    for ent in entities:
        key = (ent.normalized, ent.entity_type)
        groups[key].append(ent)

    deduped = []
    for (norm, etype), group in groups.items():
        # Берём сущность с макс. confidence
        best = max(group, key=lambda e: e.confidence)

        # Собираем все chunk_id и provenance
        all_chunks = _unique_keep_order([
            cid
            for ent in group
            for cid in _split_chunk_ids(ent.chunk_id)
        ])
        page_starts = [ent.page_start for ent in group]
        page_ends = [ent.page_end for ent in group]

        deduped.append(Entity(
            text=best.text,
            normalized=norm,
            entity_type=etype,
            confidence=best.confidence,
            chunk_id=",".join(all_chunks),  # Все чанки через запятую
            source_doc=best.source_doc,
            context=best.context,
            start_char=best.start_char,
            end_char=best.end_char,
            page_start=min(page_starts) if page_starts else best.page_start,
            page_end=max(page_ends) if page_ends else best.page_end,
            section_title=best.section_title,
            section_hierarchy=best.section_hierarchy,
            block_indices=_merge_scalar_lists(group, "block_indices"),
            source_blocks=_merge_summary_lists(group, "source_blocks"),
            source_element_ids=_merge_scalar_lists(group, "source_element_ids"),
            related_element_ids=_merge_scalar_lists(group, "related_element_ids"),
            source_elements=_merge_summary_lists(group, "source_elements"),
            related_elements=_merge_summary_lists(group, "related_elements"),
        ))

    return sorted(deduped, key=lambda e: (-e.confidence, e.entity_type, e.normalized))


def filter_noise_entities(entities: list[Entity], min_length: int = 2) -> list[Entity]:
    """
    Фильтрация шумовых сущностей.

    Убирает:
      - Слишком короткие (1 символ)
      - Чисто числовые
      - Стоп-слова
    """
    STOP_ENTITIES = {
        "р", "г", "ed", "vol", "no", "pp", "chapter", "глава", "стр",
        "рис", "табл", "см", "т.е", "и т.д", "etc", "e.g", "i.e",
        "the", "a", "an", "и", "в", "на", "по", "из", "к", "с",
    }

    filtered = []
    for ent in entities:
        text = ent.text.strip()
        norm = ent.normalized

        # Слишком короткий
        if len(text) < min_length:
            continue

        # Чисто числовой (кроме дат и годов)
        if re.match(r"^[\d.,\s]+$", text) and ent.entity_type not in ("DATE", "NUMBER", "MONEY"):
            continue

        # Стоп-слова
        if norm in STOP_ENTITIES:
            continue

        # Слишком длинный (скорее всего ошибка NER)
        if len(text) > 200:
            continue

        filtered.append(ent)

    removed = len(entities) - len(filtered)
    if removed:
        log.info(f"  Отфильтровано шумовых сущностей: {removed}")

    return filtered


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 8: ОСНОВНОЙ ПАЙПЛАЙН
# ══════════════════════════════════════════════════════════════

def extract_entities(
    chunked_json_path: str,
    engine_name: str = "spacy",
    engine_instance=None,
    api_key: str = None,
) -> DocumentEntities:
    """
    Извлечение сущностей из всех чанков документа.

    Args:
        chunked_json_path: путь к *_chunked.json
        engine_name: "spacy", "gliner", "llm"
        engine_instance: готовый движок (чтобы не создавать заново)
        api_key: API ключ (для LLM)
    """
    data = load_chunked_document(chunked_json_path)
    chunks = data["chunks"]
    source_doc = data["source"]
    source_hash = data["source_hash"]

    # Создаём движок
    if engine_instance:
        engine = engine_instance
    elif engine_name == "spacy":
        engine = SpacyNER()
    elif engine_name == "gliner":
        engine = GLiNEREngine()
    elif engine_name == "llm":
        if not api_key:
            raise ValueError("Для LLM движка нужен --api-key")
        engine = LLMEngine(api_key=api_key)
    else:
        raise ValueError(f"Неизвестный движок: {engine_name}")

    # Извлечение по чанкам
    all_entities = []
    for i, chunk in enumerate(chunks):
        chunk_id = chunk["chunk_id"]
        text = chunk["text"]

        if not text.strip():
            continue

        try:
            chunk_entities = engine.extract(text, chunk_id, source_doc)
            attach_chunk_provenance(chunk_entities, chunk)
            all_entities.extend(chunk_entities)
        except Exception as e:
            log.warning(f"  Ошибка NER в чанке {chunk_id}: {e}")
            continue

        if (i + 1) % 10 == 0:
            log.info(f"  Обработано чанков: {i + 1}/{len(chunks)}")

    log.info(f"  Извлечено сущностей (raw): {len(all_entities)}")

    # Постобработка
    all_entities = filter_noise_entities(all_entities)
    unique_entities = deduplicate_entities(all_entities)

    log.info(f"  Уникальных сущностей: {len(unique_entities)}")

    # Статистика по типам
    type_counts = Counter(e.entity_type for e in unique_entities)

    return DocumentEntities(
        source_path=source_doc,
        source_hash=source_hash,
        engine=engine_name,
        total_entities=len(all_entities),
        unique_entities=len(unique_entities),
        entities=unique_entities,
        entity_counts=dict(type_counts.most_common()),
        stats={
            "raw_count": len(all_entities),
            "unique_count": len(unique_entities),
            "chunks_processed": len(chunks),
            "avg_per_chunk": len(all_entities) / max(len(chunks), 1),
            "raw_with_source_elements": sum(1 for e in all_entities if e.source_element_ids),
            "raw_with_related_elements": sum(1 for e in all_entities if e.related_element_ids),
            "unique_with_source_elements": sum(1 for e in unique_entities if e.source_element_ids),
            "unique_with_related_elements": sum(1 for e in unique_entities if e.related_element_ids),
        },
    )


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 9: СТАТИСТИКА
# ══════════════════════════════════════════════════════════════

def print_stats(doc: DocumentEntities):
    """Вывод статистики по сущностям."""
    print(f"\n{'═' * 55}")
    print(f"  {Path(doc.source_path).name}")
    print(f"{'═' * 55}")
    print(f"  Движок:         {doc.engine}")
    print(f"  Raw сущностей:  {doc.total_entities}")
    print(f"  Уникальных:     {doc.unique_entities}")
    print(f"  С source elem:  {doc.stats.get('unique_with_source_elements', 0)}")
    print(f"  С related elem: {doc.stats.get('unique_with_related_elements', 0)}")
    print(f"{'─' * 55}")
    print("  Типы сущностей:")
    for etype, count in sorted(doc.entity_counts.items(), key=lambda x: -x[1]):
        pct = count / doc.unique_entities * 100 if doc.unique_entities else 0
        bar = "█" * int(pct / 5)
        print(f"    {etype:15s} │ {count:4d} │ {pct:5.1f}% {bar}")

    # Топ-10 сущностей
    print(f"{'─' * 55}")
    print("  Топ-10 сущностей:")
    for ent in doc.entities[:10]:
        conf = f"{ent.confidence:.0%}"
        chunks = len(ent.chunk_id.split(","))
        freq = f"({chunks} чанк{'ов' if chunks > 1 else ''})"
        print(f"    {ent.entity_type:10s} │ {ent.text:30s} │ {conf} {freq}")

    print(f"{'═' * 55}\n")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 10: СОХРАНЕНИЕ
# ══════════════════════════════════════════════════════════════

def save_entities(doc: DocumentEntities, output_path: str):
    """Сохранение сущностей для следующих фаз."""
    output = {
        "source": doc.source_path,
        "source_hash": doc.source_hash,
        "engine": doc.engine,
        "total_entities": doc.total_entities,
        "unique_entities": doc.unique_entities,
        "entity_counts": doc.entity_counts,
        "stats": doc.stats,
        "entities": [asdict(e) for e in doc.entities],
    }

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info(f"Результаты сохранены: {output_path}")


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 11: БАТЧ-ОБРАБОТКА
# ══════════════════════════════════════════════════════════════

def batch_extract(
    input_dir: str,
    output_dir: str,
    engine_name: str = "spacy",
    api_key: str = None,
) -> list[DocumentEntities]:
    """Батч-NER всех *_chunked.json."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    chunked_files = sorted(input_path.glob("*_chunked.json"))
    if not chunked_files:
        log.error(f"Не найдено *_chunked.json в {input_dir}")
        return []

    log.info(f"Найдено документов: {len(chunked_files)}")

    # Создаём движок один раз для всех документов
    engine = None
    if engine_name == "spacy":
        engine = SpacyNER()
    elif engine_name == "gliner":
        engine = GLiNEREngine()
    elif engine_name == "llm":
        engine = LLMEngine(api_key=api_key)

    results = []
    total_entities = 0

    for i, cf in enumerate(chunked_files, 1):
        log.info(f"[{i}/{len(chunked_files)}] {cf.stem.replace('_chunked', '')}")

        try:
            doc = extract_entities(
                str(cf),
                engine_name=engine_name,
                engine_instance=engine,
                api_key=api_key,
            )
            print_stats(doc)

            out_name = cf.stem.replace("_chunked", "_entities") + ".json"
            save_entities(doc, str(output_path / out_name))

            results.append(doc)
            total_entities += doc.unique_entities

        except Exception as e:
            import traceback
            log.error(f"  ✗ Ошибка: {e}")
            traceback.print_exc()
            continue

    log.info(f"\nИтого: {total_entities} уникальных сущностей из {len(results)} документов")
    return results


# ══════════════════════════════════════════════════════════════
# ЧАСТЬ 12: ГЛАВНЫЙ ЗАПУСК
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Фаза 3: Извлечение сущностей (NER)")
    parser.add_argument("--input", "-i", required=True, help="Папка с *_chunked.json")
    parser.add_argument("--output", "-o", default="./entities", help="Папка для результатов")
    parser.add_argument(
        "--engine", "-e",
        default="spacy",
        choices=["spacy", "gliner", "llm"],
        help="NER движок: spacy (быстрый), gliner (zero-shot), llm (точный)",
    )
    parser.add_argument("--api-key", help="API ключ для LLM движка")
    parser.add_argument(
        "--llm-model",
        default="claude-sonnet-4-20250514",
        help="Модель LLM (по умолчанию Claude Sonnet)",
    )
    args = parser.parse_args()

    results = batch_extract(
        args.input,
        args.output,
        engine_name=args.engine,
        api_key=args.api_key,
    )

    if results:
        total = sum(r.unique_entities for r in results)
        print(f"\n Готово! {total} уникальных сущностей в {args.output}/")
        print(f"   Следующий шаг: построение графа")
