import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


NETWORKX_AVAILABLE = importlib.util.find_spec("networkx") is not None


@unittest.skipUnless(NETWORKX_AVAILABLE, "networkx is not installed")
class LinkingGraphTest(unittest.TestCase):
    def test_build_linking_graph_connects_entity_chunk_and_figure(self):
        from src.phase5_linking import (
            build_linking_graph,
            chunk_node_id,
            element_node_id,
            entity_node_id,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parsed_dir = root / "parsed"
            chunked_dir = root / "chunked"
            entities_dir = root / "entities"
            parsed_dir.mkdir()
            chunked_dir.mkdir()
            entities_dir.mkdir()

            parsed = {
                "source": "sample.pdf",
                "source_hash": "abcdef123456",
                "total_pages": 1,
                "elements": [
                    {
                        "element_id": "el_fig_1",
                        "element_type": "figure",
                        "text": "",
                        "page_number": 0,
                        "block_index": 1,
                        "ref_label": "рис. 1",
                        "caption": "Рис. 1. Forecast quality",
                        "image_path": "images/fig1.png",
                        "section_title": "Forecast",
                        "metadata": {"caption_element_id": "el_cap_1"},
                    },
                    {
                        "element_id": "el_cap_1",
                        "element_type": "caption",
                        "text": "Рис. 1. Forecast quality",
                        "page_number": 0,
                        "block_index": 2,
                        "ref_label": "рис. 1",
                        "section_title": "Forecast",
                        "metadata": {
                            "relation": "caption_of",
                            "linked_element_id": "el_fig_1",
                        },
                    },
                ],
            }
            chunked = {
                "source": "sample.pdf",
                "source_hash": "abcdef123456",
                "total_chunks": 1,
                "chunks": [
                    {
                        "chunk_id": "chunk-1",
                        "text": "ARIMA is discussed near Figure 1.",
                        "page_start": 0,
                        "page_end": 0,
                        "section_title": "Forecast",
                        "source_element_ids": ["el_cap_1"],
                        "related_element_ids": ["el_fig_1"],
                        "source_elements": [
                            {"element_id": "el_cap_1", "element_type": "caption"}
                        ],
                        "related_elements": [
                            {"element_id": "el_fig_1", "element_type": "figure", "ref_label": "рис. 1"}
                        ],
                    }
                ],
            }
            entity = {
                "text": "ARIMA",
                "normalized": "arima",
                "entity_type": "CONCEPT",
                "confidence": 0.9,
                "chunk_id": "chunk-1",
                "source_doc": "sample.pdf",
                "source_element_ids": ["el_cap_1"],
                "related_element_ids": ["el_fig_1"],
                "source_elements": [
                    {"element_id": "el_cap_1", "element_type": "caption"}
                ],
                "related_elements": [
                    {"element_id": "el_fig_1", "element_type": "figure", "ref_label": "рис. 1"}
                ],
            }
            entities = {
                "source": "sample.pdf",
                "source_hash": "abcdef123456",
                "engine": "fake",
                "total_entities": 1,
                "unique_entities": 1,
                "entities": [entity],
            }

            (parsed_dir / "sample_parsed.json").write_text(
                json.dumps(parsed, ensure_ascii=False),
                encoding="utf-8",
            )
            (chunked_dir / "sample_chunked.json").write_text(
                json.dumps(chunked, ensure_ascii=False),
                encoding="utf-8",
            )
            (entities_dir / "sample_entities.json").write_text(
                json.dumps(entities, ensure_ascii=False),
                encoding="utf-8",
            )

            graph, metrics = build_linking_graph(
                entities_dir=str(entities_dir),
                chunked_dir=str(chunked_dir),
                parsed_dir=str(parsed_dir),
            )

        ent_node = entity_node_id({**entity, "_doc_name": "sample"})
        chunk_node = chunk_node_id("chunk-1")
        fig_node = element_node_id("el_fig_1")
        cap_node = element_node_id("el_cap_1")

        self.assertIn(ent_node, graph)
        self.assertIn(chunk_node, graph)
        self.assertIn(fig_node, graph)
        self.assertIn(cap_node, graph)
        self.assertTrue(graph.has_edge(ent_node, chunk_node, key="MENTIONED_IN"))
        self.assertTrue(graph.has_edge(ent_node, fig_node, key="DISCUSSED_NEAR"))
        self.assertTrue(graph.has_edge(fig_node, cap_node, key="HAS_CAPTION"))
        self.assertEqual(metrics["entity_figure_links"], 1)
        self.assertEqual(metrics["caption_links"], 1)
        self.assertEqual(metrics["discussed_near_kept"], 2)
        self.assertEqual(metrics["discussed_near_pruned"], 0)

    def test_discussed_near_edges_are_limited_per_element(self):
        from src.phase5_linking import build_linking_graph, element_node_id

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parsed_dir = root / "parsed"
            chunked_dir = root / "chunked"
            entities_dir = root / "entities"
            parsed_dir.mkdir()
            chunked_dir.mkdir()
            entities_dir.mkdir()

            parsed = {
                "source": "sample.pdf",
                "elements": [
                    {
                        "element_id": "fig",
                        "element_type": "figure",
                        "page_number": 0,
                        "ref_label": "рис. 1",
                        "section_title": "Results",
                    }
                ],
            }
            chunks = []
            entities = []
            for idx, text in enumerate(["ARIMA", "VAR", "MAPE"], start=1):
                chunk_id = f"chunk-{idx}"
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "text": text,
                        "page_start": 0,
                        "page_end": 0,
                        "section_title": "Results",
                        "source_element_ids": [],
                        "related_element_ids": ["fig"],
                    }
                )
                entities.append(
                    {
                        "text": text,
                        "normalized": text.lower(),
                        "entity_type": "CONCEPT",
                        "confidence": 0.9,
                        "chunk_id": chunk_id,
                        "source_doc": "sample.pdf",
                        "page_start": 0,
                        "page_end": 0,
                        "section_title": "Results",
                        "source_element_ids": [],
                        "related_element_ids": ["fig"],
                    }
                )

            (parsed_dir / "sample_parsed.json").write_text(
                json.dumps(parsed, ensure_ascii=False),
                encoding="utf-8",
            )
            (chunked_dir / "sample_chunked.json").write_text(
                json.dumps({"source": "sample.pdf", "chunks": chunks}, ensure_ascii=False),
                encoding="utf-8",
            )
            (entities_dir / "sample_entities.json").write_text(
                json.dumps({"source": "sample.pdf", "entities": entities}, ensure_ascii=False),
                encoding="utf-8",
            )

            graph, metrics = build_linking_graph(
                entities_dir=str(entities_dir),
                chunked_dir=str(chunked_dir),
                parsed_dir=str(parsed_dir),
                max_entity_links_per_element=2,
            )

        fig_node = element_node_id("fig")
        discussed_edges = [
            edge
            for edge in graph.in_edges(fig_node, data=True, keys=True)
            if edge[3].get("relation") == "DISCUSSED_NEAR"
        ]
        self.assertEqual(len(discussed_edges), 2)
        self.assertEqual(metrics["discussed_near_candidates"], 3)
        self.assertEqual(metrics["discussed_near_kept"], 2)
        self.assertEqual(metrics["discussed_near_pruned"], 1)


if __name__ == "__main__":
    unittest.main()
