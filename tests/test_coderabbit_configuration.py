from pathlib import Path
import unittest

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class CodeRabbitConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        with (REPOSITORY_ROOT / ".coderabbit.yaml").open(encoding="utf-8") as stream:
            cls.configuration = yaml.safe_load(stream)

    def test_reviews_are_low_noise_and_advisory(self) -> None:
        reviews = self.configuration["reviews"]

        self.assertEqual("chill", reviews["profile"])
        self.assertFalse(reviews["request_changes_workflow"])
        self.assertFalse(reviews["high_level_summary"])
        self.assertFalse(reviews["poem"])
        self.assertTrue(reviews["review_status"])
        self.assertFalse(reviews["review_details"])

    def test_automatic_review_runs_once_only_after_a_pull_request_is_ready(self) -> None:
        automatic_review = self.configuration["reviews"]["auto_review"]

        self.assertTrue(automatic_review["enabled"])
        self.assertFalse(automatic_review["drafts"])
        self.assertFalse(automatic_review["auto_incremental_review"])
        self.assertEqual(1, automatic_review["auto_pause_after_reviewed_commits"])

    def test_cache_external_knowledge_and_unsolicited_chat_are_disabled(self) -> None:
        self.assertTrue(self.configuration["reviews"]["disable_cache"])
        self.assertTrue(self.configuration["knowledge_base"]["opt_out"])
        self.assertFalse(
            self.configuration["knowledge_base"]["web_search"]["enabled"]
        )
        self.assertFalse(self.configuration["chat"]["auto_reply"])

    def test_code_generation_features_are_disabled(self) -> None:
        reviews = self.configuration["reviews"]
        finishing_touches = reviews["finishing_touches"]

        self.assertFalse(reviews["enable_prompt_for_ai_agents"])
        self.assertFalse(finishing_touches["docstrings"]["enabled"])
        self.assertFalse(finishing_touches["unit_tests"]["enabled"])
        self.assertFalse(finishing_touches["simplify"]["enabled"])
        self.assertEqual([], finishing_touches["custom"])


if __name__ == "__main__":
    unittest.main()
