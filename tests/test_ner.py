import unittest

from src.phase3_ner import Entity, deduplicate_entities, normalize_entity


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
            ),
            Entity(
                text="CAPM",
                normalized="capm",
                entity_type="CONCEPT",
                confidence=0.9,
                chunk_id="chunk-2",
                source_doc="sample.pdf",
            ),
        ]

        deduped = deduplicate_entities(entities)

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].confidence, 0.9)
        self.assertEqual(set(deduped[0].chunk_id.split(",")), {"chunk-1", "chunk-2"})


if __name__ == "__main__":
    unittest.main()

