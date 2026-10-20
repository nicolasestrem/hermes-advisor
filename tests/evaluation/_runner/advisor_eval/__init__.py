"""Frozen ten-case G4 usefulness evaluator: preparation, bounded run, scoring, report.

Nothing in this package performs inference on import or from its stand-alone CLI.
The only inference path is ``runner.run_cases`` fed a reviewer callable; the
parent-run Hermes plugin (``plugin/advisor-eval``) supplies the real product
reviewer ``advisor_host.Runtime.review`` over the host-owned ``ctx.llm`` facade.
"""

SCHEMA = "advisor-eval/1"
