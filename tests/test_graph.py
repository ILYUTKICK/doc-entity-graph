import importlib.util
import unittest


NETWORKX_AVAILABLE = importlib.util.find_spec("networkx") is not None


@unittest.skipUnless(NETWORKX_AVAILABLE, "networkx is not installed")
class GraphTest(unittest.TestCase):
    def test_build_graph_adds_weighted_cooccurrence_edge(self):
        from src.phase4_graph import build_graph, compute_metrics

        resolved = [
            {
                "text": "Sharpe",
                "normalized": "sharpe",
                "entity_type": "PERSON",
                "frequency": 2,
                "chunk_ids": ["chunk-1", "chunk-2"],
                "source_docs": ["sample"],
                "context": "",
                "aliases": [],
            },
            {
                "text": "CAPM",
                "normalized": "capm",
                "entity_type": "CONCEPT",
                "frequency": 2,
                "chunk_ids": ["chunk-1", "chunk-2"],
                "source_docs": ["sample"],
                "context": "",
                "aliases": [],
            },
            {
                "text": "NYSE",
                "normalized": "nyse",
                "entity_type": "ORG",
                "frequency": 1,
                "chunk_ids": ["chunk-3"],
                "source_docs": ["sample"],
                "context": "",
                "aliases": [],
            },
        ]

        graph = build_graph(resolved)
        metrics = compute_metrics(graph)

        self.assertEqual(graph["capm"]["sharpe"]["weight"], 2)
        self.assertEqual(metrics["nodes"], 3)
        self.assertEqual(metrics["edges"], 1)


if __name__ == "__main__":
    unittest.main()

