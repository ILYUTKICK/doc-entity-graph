import json
import tempfile
import unittest
from pathlib import Path

from src.phase1_parsing import load_mineru_output, save_for_next_phase


class Phase1StructureTest(unittest.TestCase):
    def test_load_mineru_output_preserves_structured_elements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sample.pdf"
            source.write_bytes(b"%PDF-1.4 sample")

            auto_dir = root / "parsed" / "sample" / "auto"
            image_dir = auto_dir / "images"
            image_dir.mkdir(parents=True)
            (image_dir / "fig1.png").write_bytes(b"image")

            items = [
                {
                    "type": "text",
                    "text": "**1. Results**",
                    "text_level": 1,
                    "page_idx": 0,
                    "bbox": [0, 0, 100, 20],
                },
                {
                    "type": "image",
                    "img_path": "images/fig1.png",
                    "image_caption": [],
                    "page_idx": 0,
                    "bbox": [10, 30, 200, 140],
                },
                {
                    "type": "text",
                    "text": "*Рис. 1. Динамика курса USD/RUB*",
                    "page_idx": 0,
                    "bbox": [10, 145, 200, 165],
                },
                {
                    "type": "table",
                    "table_caption": ["Таблица 1. Summary"],
                    "table_body": "<table><tr><td>Metric</td></tr></table>",
                    "page_idx": 0,
                    "bbox": [10, 180, 220, 260],
                },
            ]
            (auto_dir / "sample_content_list.json").write_text(
                json.dumps(items, ensure_ascii=False),
                encoding="utf-8",
            )
            (auto_dir / "sample.md").write_text("# Results", encoding="utf-8")

            doc = load_mineru_output(str(root / "parsed"), str(source))

            self.assertEqual(doc.blocks[0].block_type, "title")
            self.assertEqual(doc.metadata["image_count"], 1)
            self.assertEqual(doc.metadata["element_type_counts"]["figure"], 1)

            figure = next(e for e in doc.elements if e.element_type == "figure")
            caption = next(e for e in doc.elements if e.element_type == "caption")
            table = next(e for e in doc.elements if e.element_type == "table")

            self.assertTrue(figure.image_path.endswith("images/fig1.png"))
            self.assertEqual(caption.ref_label, "рис. 1")
            self.assertEqual(caption.metadata["linked_element_id"], figure.element_id)
            self.assertIn("USD/RUB", figure.caption)
            self.assertEqual(table.ref_label, "табл. 1")
            self.assertIn("<table>", table.table_html)

            saved = root / "sample_parsed.json"
            save_for_next_phase(doc, str(saved))
            saved_data = json.loads(saved.read_text(encoding="utf-8"))

            self.assertIn("elements", saved_data)
            self.assertEqual(len(saved_data["elements"]), 4)


if __name__ == "__main__":
    unittest.main()
