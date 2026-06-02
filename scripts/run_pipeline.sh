#!/usr/bin/env bash
# Полный пайплайн: от PDF до графа сущностей.
#
# Usage:
#   bash scripts/run_pipeline.sh [input_dir] [backend] [max_tokens] [min_edge_weight]
#
# Environment variables:
#   PYTHON_BIN=python
#   NER_ENGINE=spacy
#   CLEAN_RUN=1

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

# Параметры (можно менять)
INPUT_DIR="${1:-data/raw}"
BACKEND="${2:-pipeline}"
MAX_TOKENS="${3:-512}"
MIN_EDGE_WEIGHT="${4:-1}"
PYTHON_BIN="${PYTHON_BIN:-python}"
NER_ENGINE="${NER_ENGINE:-spacy}"
CLEAN_RUN="${CLEAN_RUN:-1}"

mkdir -p data/raw data/parsed data/chunked data/entities data/graph outputs/figures

clean_generated_dir() {
    local dir="$1"
    find "$dir" -mindepth 1 ! -name ".gitkeep" -exec rm -rf {} +
}

count_files() {
    local dir="$1"
    local pattern="$2"
    find "$dir" -maxdepth 1 -type f -name "$pattern" | wc -l | tr -d ' '
}

require_files() {
    local dir="$1"
    local pattern="$2"
    local phase_name="$3"
    local count
    count="$(count_files "$dir" "$pattern")"
    if [ "$count" -eq 0 ]; then
        echo "✗ ${phase_name}: не найдено ${pattern} в ${dir}/"
        echo "  Останавливаю пайплайн, чтобы не использовать старые артефакты."
        exit 1
    fi
    echo "  OK: ${count} файлов ${pattern} в ${dir}/"
}

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ПОЛНЫЙ ПАЙПЛАЙН: PDF → ГРАФ СУЩНОСТЕЙ"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Вход:      ${INPUT_DIR}/"
echo "  Backend:   ${BACKEND}"
echo "  Max tokens: ${MAX_TOKENS}"
echo "  NER:       ${NER_ENGINE}"
echo "  Min edge:  ${MIN_EDGE_WEIGHT}"
echo "  Clean run: ${CLEAN_RUN}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Проверка входных данных
if [ ! -d "$INPUT_DIR" ]; then
    echo "✗ Папка ${INPUT_DIR}/ не существует."
    echo "  Создайте её или запустите: bash scripts/setup_env.sh"
    exit 1
fi

FILE_COUNT=$(find "$INPUT_DIR" -type f \( -name "*.pdf" -o -name "*.docx" -o -name "*.pptx" \) | wc -l | tr -d ' ')
if [ "$FILE_COUNT" -eq 0 ]; then
    echo "✗ В ${INPUT_DIR}/ нет поддерживаемых документов."
    echo "  Положите PDF/DOCX/PPTX файлы и перезапустите."
    exit 1
fi

echo "Найдено документов: ${FILE_COUNT}"
echo ""

if [ "$CLEAN_RUN" = "1" ]; then
    echo "Очищаем старые промежуточные артефакты..."
    clean_generated_dir "data/parsed"
    clean_generated_dir "data/chunked"
    clean_generated_dir "data/entities"
    clean_generated_dir "data/graph"
    clean_generated_dir "outputs"
    mkdir -p outputs/figures
    touch data/parsed/.gitkeep data/chunked/.gitkeep data/entities/.gitkeep data/graph/.gitkeep
    touch outputs/.gitkeep outputs/figures/.gitkeep
    echo ""
fi

# Фаза 1: Парсинг
echo "═══ Фаза 1: Парсинг документов (MinerU) ═══"
"$PYTHON_BIN" src/phase1_parsing.py \
    -i "$INPUT_DIR" \
    -o data/parsed/ \
    -b "$BACKEND"
require_files "data/parsed" "*_parsed.json" "Фаза 1"
echo ""

# Фаза 2: Чанкинг
echo "═══ Фаза 2: Семантический чанкинг ═══"
"$PYTHON_BIN" src/phase2_chunking.py \
    -i data/parsed/ \
    -o data/chunked/ \
    --max-tokens "$MAX_TOKENS"
require_files "data/chunked" "*_chunked.json" "Фаза 2"
echo ""

# Фаза 3: NER (SpaCy)
echo "═══ Фаза 3: Извлечение сущностей (${NER_ENGINE}) ═══"
"$PYTHON_BIN" src/phase3_ner.py \
    -i data/chunked/ \
    -o data/entities/ \
    --engine "$NER_ENGINE"
require_files "data/entities" "*_entities.json" "Фаза 3"
echo ""

# Фаза 4: Чистка + GLiNER + граф
echo "═══ Фаза 4: Очистка + GLiNER + граф ═══"
"$PYTHON_BIN" src/phase_cleanup_rebuild.py \
    -e data/entities/ \
    -c data/chunked/ \
    -o outputs/ \
    --min-edge-weight "$MIN_EDGE_WEIGHT"
require_files "outputs" "entity_graph_clean.html" "Фаза 4"
echo ""

# Фаза 5: Linking-граф документа
echo "═══ Фаза 5: Linking-граф документа ═══"
"$PYTHON_BIN" src/phase5_linking.py \
    -e data/entities/ \
    -c data/chunked/ \
    -p data/parsed/ \
    -o outputs/
require_files "outputs" "document_links.html" "Фаза 5"
echo ""

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  🎉 ГОТОВО!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Граф сущностей:       outputs/entity_graph_clean.html"
echo "  Linking-граф:         outputs/document_links.html"
echo "  Entity GraphML:       outputs/entity_graph_clean.graphml"
echo "  Linking GraphML:      outputs/document_links.graphml"
echo "  Entity JSON:          outputs/entity_graph_clean.json"
echo "  Linking JSON:         outputs/document_links.json"
echo ""
echo "  Открыть entity graph:  open outputs/entity_graph_clean.html"
echo "  Открыть linking graph: open outputs/document_links.html"
