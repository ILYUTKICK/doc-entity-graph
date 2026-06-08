#!/usr/bin/env bash
# Установка окружения для проекта.
#
# Environment variables:
#   ENV_NAME=doc-graph        имя conda-окружения
#   PYTHON_VERSION=3.11       версия Python для conda
#   SKIP_SPACY_MODELS=1       не скачивать SpaCy-модели
#   SPACY_MODELS="..."        список SpaCy-моделей для скачивания

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$PROJECT_ROOT"

ENV_NAME="${ENV_NAME:-doc-graph}"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
SPACY_MODELS="${SPACY_MODELS:-ru_core_news_lg en_core_web_sm}"

echo "═══════════════════════════════════════════"
echo "  Установка окружения: doc-entity-graph"
echo "═══════════════════════════════════════════"

echo "Создаём директории..."
mkdir -p data/raw data/parsed data/chunked data/entities data/graph
mkdir -p outputs/figures notebooks configs docs tests

if command -v conda >/dev/null 2>&1; then
    if ! conda env list | grep -q "^${ENV_NAME}[[:space:]]"; then
        echo "Создаём conda-окружение ${ENV_NAME} (Python ${PYTHON_VERSION})..."
        conda create -n "$ENV_NAME" "python=${PYTHON_VERSION}" -y
    fi

    echo "Активируем conda-окружение..."
    eval "$(conda shell.bash hook)"
    conda activate "$ENV_NAME"
else
    echo "Conda не найдена, создаём локальное .venv..."
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

echo "Устанавливаем зависимости..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ "${SKIP_SPACY_MODELS:-0}" != "1" ]; then
    echo "Скачиваем модели SpaCy..."
    for model in $SPACY_MODELS; do
        python -m spacy download "$model"
    done
else
    echo "Пропускаем скачивание SpaCy-моделей (SKIP_SPACY_MODELS=1)."
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Проверка установки"
echo "═══════════════════════════════════════════"
python -c "import spacy; print('  SpaCy:    OK (' + spacy.__version__ + ')')"
python -c "from gliner import GLiNER; print('  GLiNER:   OK')"
python -c "import networkx; print('  NetworkX: OK (' + networkx.__version__ + ')')"
python -B -m unittest discover -s tests

echo ""
echo " Всё готово!"
echo "   1. Положите документы в data/raw/"
echo "   2. Запустите: bash scripts/run_pipeline.sh"
