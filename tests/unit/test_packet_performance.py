import multiprocessing
import queue
import time
import unittest

from advisor_core.packet import sanitize_text


def _sanitize_in_child(result_queue):
    started = time.monotonic()
    text = "r" * 200_000
    sanitized = sanitize_text(text, max_chars=16_384)
    result_queue.put((time.monotonic() - started, sanitized))


class PacketRedactionPerformanceTests(unittest.TestCase):
    def test_long_near_match_is_linear_and_still_redacts_embedded_credentials(self):
        context = multiprocessing.get_context("fork")
        result_queue = context.Queue()
        process = context.Process(target=_sanitize_in_child, args=(result_queue,))
        process.start()
        process.join(4)
        try:
            self.assertFalse(process.is_alive(), "200k-character redaction exceeded 4 seconds")
            self.assertEqual(process.exitcode, 0)
            elapsed, excerpt = result_queue.get(timeout=1)
            self.assertLess(elapsed, 4.0)
            self.assertEqual(len(excerpt), 16_384)
            self.assertEqual(excerpt.count(" …[clipped]… "), 1)
            self.assertTrue(excerpt.startswith("r"))
            self.assertTrue(excerpt.endswith("r"))
            self.assertNotIn("[REDACTED]", excerpt)
        finally:
            if process.is_alive():
                process.terminate()
                process.join()
            result_queue.close()

    def test_embedded_identifier_credentials_remain_redacted(self):
        secret = "xsk-" + "a" * 24
        excerpt = sanitize_text("prefix_" + secret + "_suffix", max_chars=100)
        self.assertNotIn(secret, excerpt)
        self.assertIn("[REDACTED]", excerpt)

    def test_credential_crossing_excerpt_boundary_is_redacted_before_clipping(self):
        secret = "xsk-" + "a" * 24
        source = "r" * 40 + secret + "z" * 100
        excerpt = sanitize_text(source, max_chars=48)
        self.assertNotIn("xsk-", excerpt)
        self.assertNotIn("a" * 12, excerpt)
        self.assertIn("[REDACTED]", excerpt)
