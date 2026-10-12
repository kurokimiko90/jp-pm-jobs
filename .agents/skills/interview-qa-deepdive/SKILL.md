---
name: interview-qa-deepdive
description: Upgrade an existing interview pack with evidence-grounded achievement reconstruction, concise nontechnical answers, layered follow-up questions, adversarial counterquestions, and metric verification. Use when asked to deepen interview QA, challenge an answer, infer how résumé results were achieved, turn technical project work into business language, append prior answers to a QA pack, or audit whether interview claims and numbers are defensible.
---

# Interview QA Deep Dive

Strengthen an existing interview pack without inventing experience. Convert each important claim into a short answer that survives questions about causality, ownership, measurement, trade-offs, failure, and repeatability.

## Required input

Identify the target by `job_id` or pack path. Prefer:

- `output/prep/{job_id}_{company}/03_interview_qa.md`
- `data/candidate_profile.yaml`
- `resume/jp/data.yaml`
- the job row and `raw_jd` in `data/jobs.sqlite`
- `interview/companies/{slug}_facts.md`
- actual code, state, logs, and output from any named proof project

Treat candidate profile and résumé files as authoritative only for recorded career facts. When the user names a current project such as miko-ws, inspect that project's current files and distinguish present state from résumé-time claims.

Do not modify `data/candidate_profile.yaml`, `data/cognitive_profile.yaml`, `data/tech_footprint.yaml`, or `resume/jp/data.yaml`.

## Workflow

1. Read the target QA, JD, company facts, candidate profile, and career data.
2. Read [references/qa-schema.md](references/qa-schema.md) completely before drafting.
3. Build a private evidence ledger with four columns:
   `claim`, `source`, `status`, `missing definition`.
   Use only these statuses:
   - `confirmed`
   - `current-project-observation`
   - `reconstruction-hypothesis`
   - `unconfirmed`
4. Select 5–8 claims with the highest interview risk:
   quantified results, team outcomes, strategy changes, AI quality, adoption, failures, or unfamiliar-domain transfer.
5. For each claim, reconstruct the causal chain:
   `business problem → root-cause thinking → decision → concrete action → result → value → limitation`.
6. Write the oral answer first, then attack it from at least three angles:
   - why/root cause
   - personal contribution versus team contribution
   - metric definition, denominator, baseline, period, or attribution
   Add trade-off, failure, counterexample, or repeatability when relevant.
7. Append or update a clearly named section in the canonical QA. Preserve earlier answers. Update the pack README question count and `06_numbers_card.md` when numbers or caveats change.
8. Run the deterministic audit:

```bash
python3 .agents/skills/interview-qa-deepdive/scripts/audit_qa.py \
  --qa output/prep/{job_id}_{company}/03_interview_qa.md \
  --evidence data/candidate_profile.yaml \
  --evidence resume/jp/data.yaml
```

Add actual proof-project files with another `--evidence` for claims derived from current code or state. Never add the QA itself as evidence. Fix all errors; report warnings and unresolved verification items to the user.

## Grounding rules

- Separate `確認済み事実` from `再構成仮説` visibly.
- Do not let a plausible reconstruction silently become first-person history.
- Mark missing metric definitions as `{{要確認：...}}`.
- If a multiplier, percentage, or reduction lacks a denominator or comparison period, say `資料上では` or downgrade the oral claim to `大幅改善`.
- Say what the candidate decided and did; do not claim sole ownership of a team result.
- Distinguish generated, selected, approved, used, retained, and business impact.
- Distinguish registered users from MAU, supported categories from actual customers, and cumulative rollout from active usage.
- When two sources disagree, state the conflict and use a nonnumeric phrase until resolved.
- Do not claim advertising-domain expertise from an adjacent AI or SaaS project. Transfer the problem-solving method, then state what must be relearned.

## External LLM and PII

Prefer local Codex reasoning for this workflow. If any repository script sends material to an external LLM, pass candidate data only through:

```python
from tools.deid import build_deid_profile
```

Use the whitelist returned by `build_deid_profile()`. Never send raw `candidate_profile.yaml`, résumé identity/contact fields, address, birth year, or current annual compensation. Run `tools.pii_gate.scrub_for_external()` on other text before transmission.

## Completion gate

Finish only when:

- each reconstructed result has a causal explanation and concrete action;
- each answer starts with the outcome and fits roughly 30–60 seconds;
- a nontechnical interviewer can understand what changed and why it mattered;
- every high-risk answer has at least three follow-ups with answers;
- unsupported numbers are removed or visibly marked for confirmation;
- source conflicts and current project limitations are explicit;
- the audit script has no errors.
