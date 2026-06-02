import json
import tempfile
import unittest
from pathlib import Path

from src.phase2_chunking import chunk_document, estimate_tokens


class ChunkingTest(unittest.TestCase):
    def test_estimate_tokens_is_positive(self):
        self.assertGreater(estimate_tokens("Capital Asset Pricing Model"), 0)
        self.assertGreater(estimate_tokens("Модель оценки капитальных активов"), 0)

    def test_chunk_document_sections_keeps_section_context(self):
        parsed = {
            "source": "sample.pdf",
            "source_hash": "abcdef1234567890",
            "total_pages": 1,
            "blocks": [
                {
                    "block_type": "title",
                    "content": "Capital Asset Pricing Model",
                    "page_number": 0,
                    "bbox": [0, 0, 1, 1],
                    "block_index": 0,
                },
                {
                    "block_type": "text",
                    "content": "Sharpe introduced CAPM for explaining expected return.",
                    "page_number": 0,
                    "bbox": [0, 0, 1, 1],
                    "block_index": 1,
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample_parsed.json"
            path.write_text(json.dumps(parsed), encoding="utf-8")

            doc = chunk_document(str(path), max_tokens=128, overlap_sentences=0)

        self.assertEqual(doc.total_chunks, 1)
        self.assertEqual(doc.chunks[0].section_title, "Capital Asset Pricing Model")
        self.assertIn("Sharpe introduced CAPM", doc.chunks[0].text)

    def test_chunk_document_keeps_phase1_element_sources(self):
        parsed = {
            "source": "sample.pdf",
            "source_hash": "abcdef1234567890",
            "total_pages": 1,
            "blocks": [
                {
                    "block_type": "title",
                    "content": "Forecast Results",
                    "page_number": 0,
                    "bbox": [0, 0, 1, 1],
                    "block_index": 0,
                },
                {
                    "block_type": "caption",
                    "content": "Рис. 1. Forecast quality for ARIMA",
                    "page_number": 0,
                    "bbox": [0, 1, 1, 2],
                    "block_index": 2,
                },
                {
                    "block_type": "text",
                    "content": (
                        "The chunk discusses ARIMA forecast quality, MAPE, and the "
                        "graph shown above with enough detail for source tracking."
                    ),
                    "page_number": 0,
                    "bbox": [0, 2, 1, 3],
                    "block_index": 3,
                },
            ],
            "elements": [
                {
                    "element_id": "doc_el_0000",
                    "element_type": "title",
                    "text": "Forecast Results",
                    "page_number": 0,
                    "bbox": [0, 0, 1, 1],
                    "block_index": 0,
                    "source_block_index": 0,
                    "section_title": "Forecast Results",
                    "metadata": {},
                },
                {
                    "element_id": "doc_el_0001",
                    "element_type": "figure",
                    "text": "",
                    "page_number": 0,
                    "bbox": [0, 1, 1, 2],
                    "block_index": 1,
                    "source_block_index": 1,
                    "ref_label": "рис. 1",
                    "caption": "Рис. 1. Forecast quality for ARIMA",
                    "image_path": "images/fig1.png",
                    "section_title": "Forecast Results",
                    "metadata": {"caption_element_id": "doc_el_0002"},
                },
                {
                    "element_id": "doc_el_0002",
                    "element_type": "caption",
                    "text": "Рис. 1. Forecast quality for ARIMA",
                    "page_number": 0,
                    "bbox": [0, 1, 1, 2],
                    "block_index": 2,
                    "source_block_index": 2,
                    "ref_label": "рис. 1",
                    "section_title": "Forecast Results",
                    "metadata": {
                        "relation": "caption_of",
                        "linked_element_id": "doc_el_0001",
                    },
                },
                {
                    "element_id": "doc_el_0003",
                    "element_type": "text",
                    "text": "The chunk discusses ARIMA forecast quality.",
                    "page_number": 0,
                    "bbox": [0, 2, 1, 3],
                    "block_index": 3,
                    "source_block_index": 3,
                    "section_title": "Forecast Results",
                    "metadata": {},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample_parsed.json"
            path.write_text(json.dumps(parsed), encoding="utf-8")

            doc = chunk_document(str(path), max_tokens=128, overlap_sentences=0)

        chunk = doc.chunks[0]
        self.assertIn(0, chunk.block_indices)
        self.assertIn(2, chunk.block_indices)
        self.assertIn("doc_el_0002", chunk.source_element_ids)
        self.assertIn("doc_el_0003", chunk.source_element_ids)
        self.assertIn("doc_el_0001", chunk.related_element_ids)
        self.assertTrue(any(e["element_type"] == "figure" for e in chunk.related_elements))
        self.assertGreater(doc.stats["chunks_with_source_elements"], 0)
        self.assertGreater(doc.stats["chunks_with_related_elements"], 0)


if __name__ == "__main__":
    unittest.main()
