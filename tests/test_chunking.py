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


if __name__ == "__main__":
    unittest.main()

