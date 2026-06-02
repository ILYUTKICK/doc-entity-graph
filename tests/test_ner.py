import unittest
import json
import tempfile
from pathlib import Path

from src.phase3_ner import Entity, deduplicate_entities, extract_entities, normalize_entity


class FakeEngine:
    def extract(self, text: str, chunk_id: str, source_doc: str):
        if "ARIMA" not in text:
            return []
        return [
            Entity(
                text="ARIMA",
                normalized="arima",
                entity_type="CONCEPT",
                confidence=0.9,
                chunk_id=chunk_id,
                source_doc=source_doc,
                context=text,
            )
        ]


class NerPostprocessingTest(unittest.TestCase):
    def test_normalize_entity_strips_punctuation_and_case(self):
        self.assertEqual(normalize_entity("  CAPM, "), "capm")
        self.assertEqual(normalize_entity("Sharpe"), "sharpe")

    def test_deduplicate_entities_merges_chunks(self):
        entities = [
            Entity(
                text="CAPM",
                normalized="capm",
                entity_type="CONCEPT",
                confidence=0.8,
                chunk_id="chunk-1",
                source_doc="sample.pdf",
                page_start=0,
                page_end=0,
                block_indices=[1],
                source_element_ids=["el_text_1"],
                related_element_ids=["el_fig_1"],
                source_blocks=[{"block_index": 1, "text_preview": "CAPM text"}],
                related_elements=[{"element_id": "el_fig_1", "element_type": "figure"}],
            ),
            Entity(
                text="CAPM",
                normalized="capm",
                entity_type="CONCEPT",
                confidence=0.9,
                chunk_id="chunk-2",
                source_doc="sample.pdf",
                page_start=1,
                page_end=1,
                block_indices=[2],
                source_element_ids=["el_text_2"],
                related_element_ids=["el_fig_2"],
                source_blocks=[{"block_index": 2, "text_preview": "More CAPM text"}],
                related_elements=[{"element_id": "el_fig_2", "element_type": "figure"}],
            ),
        ]

        deduped = deduplicate_entities(entities)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].confidence, 0.9)
        self.assertEqual(set(deduped[0].chunk_id.split(",")), {"chunk-1", "chunk-2"})
        self.assertEqual(deduped[0].page_start, 0)
        self.assertEqual(deduped[0].page_end, 1)
        self.assertEqual(deduped[0].block_indices, [1, 2])
        self.assertEqual(deduped[0].source_element_ids, ["el_text_1", "el_text_2"])
        self.assertEqual(deduped[0].related_element_ids, ["el_fig_1", "el_fig_2"])
        self.assertEqual(len(deduped[0].source_blocks), 2)

    def test_extract_entities_attaches_chunk_provenance(self):
        chunked = {
            "source": "sample.pdf",
            "source_hash": "abcdef1234567890",
            "total_chunks": 1,
            "chunks": [
                {
                    "chunk_id": "chunk-1",
                    "text": "ARIMA is discussed near Figure 1.",
                    "page_start": 2,
                    "page_end": 2,
                    "section_title": "Forecast Results",
                    "section_hierarchy": ["Forecast Results"],
                    "block_indices": [10, 11],
                    "source_blocks": [
                        {"block_index": 10, "block_type": "caption"},
                        {"block_index": 11, "block_type": "text"},
                    ],
                    "source_element_ids": ["el_caption_1", "el_text_1"],
                    "related_element_ids": ["el_figure_1"],
                    "source_elements": [
                        {"element_id": "el_caption_1", "element_type": "caption"},
                        {"element_id": "el_text_1", "element_type": "text"},
                    ],
                    "related_elements": [
                        {"element_id": "el_figure_1", "element_type": "figure", "ref_label": "рис. 1"}
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample_chunked.json"
            path.write_text(json.dumps(chunked), encoding="utf-8")

            doc = extract_entities(str(path), engine_name="fake", engine_instance=FakeEngine())

        self.assertEqual(doc.unique_entities, 1)
        ent = doc.entities[0]
        self.assertEqual(ent.text, "ARIMA")
        self.assertEqual(ent.page_start, 2)
        self.assertEqual(ent.section_title, "Forecast Results")
        self.assertEqual(ent.block_indices, [10, 11])
        self.assertEqual(ent.source_element_ids, ["el_caption_1", "el_text_1"])
        self.assertEqual(ent.related_element_ids, ["el_figure_1"])
        self.assertEqual(ent.related_elements[0]["ref_label"], "рис. 1")
        self.assertEqual(doc.stats["unique_with_related_elements"], 1)


if __name__ == "__main__":
    unittest.main()
