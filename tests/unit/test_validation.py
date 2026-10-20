import unittest

from advisor_core.config import ConfigError, validate_settings
from advisor_core.response import ResponseValidationError, validate_response


class ConfigurationValidationTests(unittest.TestCase):
    def test_applies_defaults_and_requires_explicit_complete_route_for_save(self):
        settings = {
            "route": {"provider": "nous", "model": "z-ai/glm-5.3-flash"},
            "packet_bytes": 1024,
            "output_tokens": 128,
            "max_findings": 1,
            "deadline_seconds": 1,
            "session_allowance": 1,
        }
        self.assertEqual(validate_settings(settings), settings)
        with self.assertRaises(ConfigError):
            validate_settings({"packet_bytes": 1024})

    def test_rejects_boolean_fraction_unknown_and_malformed_route_without_coercion(self):
        base = {"route": {"provider": "p", "model": "m"}}
        for key, value in (
            ("packet_bytes", True),
            ("output_tokens", 128.5),
            ("made_up", 1),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(ConfigError):
                    validate_settings({**base, key: value})
        with self.assertRaises(ConfigError):
            validate_settings({"route": {"provider": "p", "model": "m", "url": "x"}})

    def test_enforces_exact_integer_boundaries_and_route_callback(self):
        base = {"route": {"provider": "p", "model": "m"}}
        for key, low, high in (
            ("packet_bytes", 1024, 32768),
            ("output_tokens", 128, 2048),
            ("max_findings", 1, 4),
            ("deadline_seconds", 1, 120),
            ("session_allowance", 1, 20),
        ):
            self.assertEqual(validate_settings({**base, key: low})[key], low)
            self.assertEqual(validate_settings({**base, key: high})[key], high)
            for out in (low - 1, high + 1):
                with self.assertRaises(ConfigError, msg=f"{key}={out}"):
                    validate_settings({**base, key: out})
        with self.assertRaises(ConfigError):
            validate_settings(base, route_validator=lambda route: False)


class ResponseValidationTests(unittest.TestCase):
    def test_accepts_valid_finding_outcome_with_exact_refs(self):
        result = {
            "outcome": "findings",
            "findings": [{
                "severity": "concern",
                "explanation": "The claim is not supported.",
                "evidence_refs": ["answer"],
                "next_step": "Verify the claim against the supplied result.",
            }],
        }
        self.assertEqual(validate_response(result, {"answer", "request"}, max_findings=1), result)

    def test_rejects_whole_response_for_bad_outcome_shape_or_evidence(self):
        good_finding = {
            "severity": "nit", "explanation": "Check this.",
            "evidence_refs": ["answer"], "next_step": "Verify it.",
        }
        invalid = (
            {"outcome": "findings", "findings": []},
            {"outcome": "no_findings", "findings": [good_finding]},
            {"outcome": "findings", "findings": [{**good_finding, "evidence_refs": ["missing"]}]},
            {"outcome": "findings", "findings": [{**good_finding, "extra": "no"}]},
            {"outcome": "findings", "findings": [{**good_finding, "severity": "fatal"}]},
            {"outcome": "findings", "findings": [{**good_finding, "evidence_refs": ["answer", "answer"]}]},
        )
        for result in invalid:
            with self.subTest(result=result):
                with self.assertRaises(ResponseValidationError):
                    validate_response(result, {"answer"}, max_findings=4)

    def test_enforces_applied_finding_limit_and_text_codepoint_bounds(self):
        finding = {
            "severity": "nit", "explanation": "x",
            "evidence_refs": ["answer"], "next_step": "y",
        }
        with self.assertRaises(ResponseValidationError):
            validate_response({"outcome": "findings", "findings": [finding, finding]}, {"answer"}, max_findings=1)
        with self.assertRaises(ResponseValidationError):
            validate_response({"outcome": "findings", "findings": [{**finding, "explanation": "x" * 2001}]}, {"answer"}, max_findings=4)
        self.assertEqual(validate_response({"outcome": "no_findings", "findings": []}, {"answer"}), {"outcome": "no_findings", "findings": []})
        self.assertEqual(validate_response({"outcome": "insufficient_evidence", "findings": []}, {"answer"})["outcome"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
