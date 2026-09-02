from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CodeRabbitConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with (REPOSITORY_ROOT / ".coderabbit.yaml").open(encoding="utf-8") as stream:
            cls.configuration = yaml.safe_load(stream)

    def test_reviews_are_low_noise_and_merge_gating(self) -> None:
        reviews = self.configuration["reviews"]

        self.assertEqual("quiet", reviews["profile"])
        self.assertTrue(reviews["request_changes_workflow"])
        self.assertFalse(reviews["high_level_summary"])
        self.assertFalse(reviews["poem"])
        self.assertTrue(reviews["review_status"])
        self.assertFalse(reviews["review_details"])

    def test_automatic_and_incremental_reviews_are_disabled(self) -> None:
        automatic_review = self.configuration["reviews"]["auto_review"]

        self.assertFalse(automatic_review["enabled"])
        self.assertFalse(automatic_review["drafts"])
        self.assertFalse(automatic_review["auto_incremental_review"])
        self.assertEqual([], automatic_review["labels"])
        self.assertEqual("", automatic_review["description_keyword"])

    def test_actionable_threads_are_reserved_for_blockers(self) -> None:
        instructions = self.configuration["reviews"]["path_instructions"]

        self.assertEqual(1, len(instructions))
        self.assertEqual("**", instructions[0]["path"])
        text = instructions[0]["instructions"]
        self.assertIn("Critical or High", text)
        self.assertIn("Medium and Low observations advisory", text)

    def test_cache_external_knowledge_and_unsolicited_chat_are_disabled(self) -> None:
        self.assertTrue(self.configuration["reviews"]["disable_cache"])
        self.assertTrue(self.configuration["knowledge_base"]["opt_out"])
        self.assertFalse(
            self.configuration["knowledge_base"]["web_search"]["enabled"]
        )
        self.assertFalse(self.configuration["chat"]["auto_reply"])
        integrations = self.configuration["chat"]["integrations"]
        self.assertEqual("disabled", integrations["jira"]["usage"])
        self.assertEqual("disabled", integrations["linear"]["usage"])

    def test_code_generation_features_are_disabled(self) -> None:
        reviews = self.configuration["reviews"]
        finishing_touches = reviews["finishing_touches"]

        self.assertFalse(reviews["enable_prompt_for_ai_agents"])
        self.assertFalse(finishing_touches["docstrings"]["enabled"])
        self.assertFalse(finishing_touches["unit_tests"]["enabled"])
        self.assertFalse(finishing_touches["simplify"]["enabled"])
        self.assertFalse(finishing_touches["autofix"]["enabled"])
        self.assertFalse(finishing_touches["fix_ci"]["enabled"])
        self.assertFalse(finishing_touches["resolve_merge_conflict"]["enabled"])
        self.assertEqual([], finishing_touches["custom"])


if __name__ == "__main__":
    unittest.main()
