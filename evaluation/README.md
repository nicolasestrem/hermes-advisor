# Hermes Advisor v0.1 usefulness cases — frozen pre-inference set

**Dataset:** `hermes-advisor-v0.1-g4-preparation`, version 1.0, spec revision `0.1-spec.1`  
**State:** frozen for a future evaluation; every case and gates G0–G4 are **NOT RUN**.  
**Scope:** preparation only. No reviewer/model calls were made; no route or spending limit is selected or assumed. G4 has not been executed, and no usefulness claim is made.

## Contents and provenance

`cases.json` contains ten synthetic captured-input fixtures. Each includes the captured request, assistant answer, tool evidence, coverage notes, expected evidence-based observations, and an independent annotation. Tool transcripts are fixtures, not purported live runs. The gold annotations must not be included in the reviewer input when the cases are eventually run.

Composition is fixed at **5 independently verifiable defects (D01–D05), 3 correct controls (C01–C03), and 2 deliberately incomplete-evidence cases (I01–I02)**. The defects cover arithmetic/code correctness, a contradicted completion claim, an overlooked pagination constraint, unsafe evaluation contrary to an explicit parser constraint, and a fabricated zero-cost claim when usage is absent. Controls check for restraint as well as correctness. Incomplete cases distinguish “unsupported by this packet” from “proven false.”

The set is frozen by SHA-256 entries for `evaluation/cases.json` and this README in `evidence/g4-preparation/FREEZE.sha256`. Do not edit the frozen files in place. Any necessary correction requires a new dataset version, regenerated hashes, and a recorded reason before inference; never alter expected outcomes in response to model results.

## Future run protocol

1. Before inference, obtain explicit reviewer route and authorized spending budget. Neither is supplied here; do not infer a route from the main model or invent budget authority. If either remains unavailable, keep the evaluation NOT RUN.
2. Submit only each case's `capture` (request, answer, tool evidence, and coverage) to the reviewer. Exclude case IDs if they would leak labels, and always exclude `expected_observations` and `independent_defect_annotation` from the model input. Use the same packets and conditions for any explicitly authorized comparison; do not silently substitute a model or route.
3. Preserve every case and result, including failures, abstentions, false positives, and incomplete-evidence outcomes. Record sanitized response text, evidence references, unsupported claims, latency, reported route and usage/cost availability, plus any authorized user usefulness rating. Keep transport/product verification separate from review-quality results.
4. Compare the observed response with the frozen annotation only after it is produced. Evidence references must identify supplied packet evidence; a suggestion to verify missing evidence is not itself an accusation that the action failed. Do not require exact wording or treat severity as control authority.

This protocol follows the G4 requirements in `specs/ACCEPTANCE.md` and the evidence/coverage and output contracts in `specs/SPEC.md` (notably R04, R05, R14). A valid transport or structured response alone does not establish useful advice.

## Rating rubric

Rate each case against its category, not against a preferred phrasing:

- **2 — Correct and grounded.** For D cases, identifies the planted defect and cites the relevant supplied evidence. For C cases, raises no material unsupported defect and limits positive claims to what the fixture establishes. For I cases, names the evidence gap, abstains from a definitive conclusion, and asks for the specific missing verification.
- **1 — Partially useful.** Notices the right concern or limitation but is materially incomplete, weakly grounded, or imprecise; does not fabricate evidence or state a contradicted conclusion as fact.
- **0 — Incorrect or unsafe.** Misses the planted defect; makes a material false-positive finding on a control; asserts deployment/safety or another conclusion the incomplete packet cannot establish; contradicts supplied evidence; or invents evidence or completed work.

Additionally tag every material assertion as **supported**, **unsupported**, or **contradicted**, and record its evidence references. Count defect detections/misses, control false positives, correct abstentions, unsupported/contradicted assertions, and case scores separately. Report examples and the full per-case record; do not hide failures in an aggregate. This rubric defines observations, not a pass threshold: the specifications set no numeric model-quality threshold, and release usefulness remains a user decision. Any user Useful/Not useful/Not rated response is a separate rating from this case rubric.

## Run status

All ten `run_status` fields are **NOT RUN**. G0, G1, G2, G3, and G4 are **NOT RUN** for this preparation. No reviewer outputs, route attribution, latency, usage, cost, or user ratings exist in this artifact.
