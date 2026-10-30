"""Lab rendering checks against the vendored demo contract."""

import unittest

from lab import (
    analyze_structure,
    build_result_payload,
    calibrate_demo,
    contract_field_report,
    response_demo,
    temperature_scale,
)
from lite import analyze
from vendor.serve_contract import SERVICE_VERSION, classify_confidence


class LabTest(unittest.TestCase):
    def test_demo_input_splits_like_the_contract(self) -> None:
        document = response_demo()["data"]["documents"][0]
        structure = analyze_structure(document["inputText"])
        self.assertEqual(structure["n_sent"], 3)
        self.assertEqual(
            [(item["start_sentence_index"], item["num_sentences"]) for item in structure["paragraphs"]],
            [(0, 2), (2, 1)],
        )

    def test_temperature_softens_without_changing_argmax(self) -> None:
        baseline = [0.18, 0.64, 0.18]
        restored = temperature_scale(baseline, 1.0)
        for left, right in zip(baseline, restored):
            self.assertAlmostEqual(left, right, places=6)
        report = calibrate_demo(1.1842)
        self.assertTrue(report["argmax_unchanged"])
        self.assertLess(max(report["calibrated"]), max(report["baseline"]))
        self.assertEqual(report["band"], classify_confidence(report["confidence"]))
        self.assertAlmostEqual(report["fitted_temperature"], 1.1842)

    def test_result_json_matches_the_example_contract(self) -> None:
        example = response_demo()
        document = example["data"]["documents"][0]
        payload = build_result_payload(document["inputText"], 1.1842)
        self.assertEqual(set(payload), set(example))
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["version"], "V2-hier-1")
        produced = payload["data"]["documents"][0]
        self.assertEqual(set(produced), set(document))
        self.assertEqual(len(produced["sentences"]), 3)
        self.assertEqual(
            [(item["startSentenceIndex"], item["numSentences"]) for item in produced["paragraphs"]],
            [(0, 2), (2, 1)],
        )
        self.assertEqual(set(produced["sentences"][0]), set(document["sentences"][0]))
        self.assertEqual(produced["sentences"][0]["sentence"], document["sentences"][0]["sentence"])
        self.assertEqual(produced["inputText"], document["inputText"])

    def test_contract_fields_and_version(self) -> None:
        payload = response_demo()
        self.assertEqual(payload["data"]["version"], SERVICE_VERSION)
        self.assertEqual(payload["data"]["version"], "V2-hier-1")
        self.assertTrue(all(item["present"] for item in contract_field_report()))

    def test_lite_returns_a_heuristic_not_a_verdict_constant(self) -> None:
        text = (
            "In today's rapidly evolving world, it is important to note that this "
            "robust solution serves as a testament to our commitment to innovation. "
            "Moreover, we leverage cutting-edge technology to deliver a seamless "
            "experience to our users. Furthermore, our team has built a "
            "comprehensive approach that plays a crucial role in improving "
            "efficiency. It is worth noting that this groundbreaking platform "
            "fosters collaboration and drives growth across the organization."
        )
        result = analyze(text)
        self.assertIn(result["band"], {
            "Weak AI-generated signal",
            "Uncertain / mixed signal",
            "Strong AI-generated signal",
        })
        self.assertEqual(set(result["signals"]), {
            "burstiness",
            "formulaic_phrases",
            "repetition",
            "vocabulary_uniformity",
        })


if __name__ == "__main__":
    unittest.main()
