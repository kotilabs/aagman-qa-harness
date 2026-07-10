import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from aagman_qa.runners import research


class TestResearchBatch(unittest.TestCase):
    def test_batch_submits_prompts_then_checks_results(self):
        browser = MagicMock()
        browser.current_url.return_value = "https://app.aagman.ai/screener/ws-test"
        artifact_dir = Path("/tmp/test-artifacts")

        tests = [
            {"id": "rs-01", "prompt": "Find Nifty 50 stocks above 200 SMA"},
            {"id": "rs-02", "prompt": "Top 5 volume gainers today"},
        ]

        with patch.object(research, "_navigate_to_research"), \
             patch.object(research, "_start_new_screener_chat"), \
             patch.object(research, "_activate_chat_tab"), \
             patch.object(research, "_goto_workspace"), \
             patch.object(research, "_activate_results_tab"), \
             patch.object(research, "submit_aagman_prompt") as submit, \
             patch.object(research, "_check_batch_result", return_value="result"), \
             patch.object(research, "assert_no_error_texts"), \
             patch.object(research, "_capture_screenshot"), \
             patch.object(research, "capture_failure_screenshot"), \
             patch.object(research, "_scroll_to_bottom"), \
             patch.object(research.time, "sleep"):
            results = research.run_batch(
                browser,
                "https://app.aagman.ai",
                tests,
                artifact_dir,
                batch_size=2,
                settle_delay=0,
            )

        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r.status, "PASS")
        self.assertEqual(submit.call_count, 2)

    def test_batch_chunks_large_lists(self):
        browser = MagicMock()
        browser.current_url.return_value = "https://app.aagman.ai/screener/ws-test"
        tests = [{"id": f"rs-{i:02d}", "prompt": f"prompt {i}"} for i in range(12)]

        with patch.object(research, "_navigate_to_research"), \
             patch.object(research, "_start_new_screener_chat"), \
             patch.object(research, "_activate_chat_tab"), \
             patch.object(research, "_goto_workspace"), \
             patch.object(research, "_activate_results_tab"), \
             patch.object(research, "submit_aagman_prompt") as submit, \
             patch.object(research, "_check_batch_result", return_value="result"), \
             patch.object(research, "assert_no_error_texts"), \
             patch.object(research, "_capture_screenshot"), \
             patch.object(research, "capture_failure_screenshot"), \
             patch.object(research, "_scroll_to_bottom"), \
             patch.object(research.time, "sleep"):
            results = research.run_batch(
                browser,
                "https://app.aagman.ai",
                tests,
                Path("/tmp"),
                batch_size=10,
                settle_delay=0,
            )

        self.assertEqual(len(results), 12)
        self.assertEqual(submit.call_count, 12)


if __name__ == "__main__":
    unittest.main()
