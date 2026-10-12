# QA schema and attack patterns

## Contents

1. Output schema
2. Causal reconstruction
3. Follow-up attack matrix
4. Spoken-answer rules
5. Evidence and metric rules
6. Pack update rules

## 1. Output schema

Use this structure for every reconstructed achievement:

```markdown
### Q{n}. {A skeptical question about one result}

**確認済み事実**：{Only facts supported by authoritative files.}

**再構成仮説**：{A plausible mechanism, explicitly phrased as a hypothesis.}

**回答（60秒以内）**：{Conclusion → problem → decision/action → value → limitation.}

**深掘り質問と回答**

- **{Root-cause question}**
  {Short answer.}
- **{Personal-contribution question}**
  {Short answer.}
- **{Measurement/attribution question}**
  {Short answer or `{{要確認：...}}`.}
```

Optional additions:

- `**面接官の質疑**` for one aggressive challenge.
- `**NG**` for an especially tempting overclaim.
- `**Septeniでの転用**` or an equivalent company connection when the transfer is not obvious.

Do not mix verified facts and reconstruction in one unlabeled paragraph.

## 2. Causal reconstruction

Reverse-engineer a result in this order:

1. What business or user outcome was failing?
2. What observable process could produce that failure?
3. What alternative explanations existed?
4. What did the candidate decide personally?
5. What did the team or external partner execute?
6. What artifact or operating rule changed?
7. What result was recorded?
8. What is still not proven?

Prefer process artifacts over abstract claims:

- decision criteria
- before/after workflow
- acceptance conditions
- prototype
- prioritization rule
- configuration model
- review or approval step
- release feedback loop
- failure and recovery rule

When the exact historical process is absent, write a reconstruction hypothesis and ask the candidate to confirm the missing decision, episode, or artifact.

## 3. Follow-up attack matrix

Choose at least three attacks per answer. Use more for high-risk metrics.

| Attack | Interviewer's intent | Answer direction |
|---|---|---|
| Why this problem? | Test problem selection | Explain user/business harm and rejected alternatives |
| Root cause? | Detect solution-first thinking | Separate symptom, cause hypothesis, and evidence |
| Your contribution? | Detect team-result inflation | State candidate decision/artifact; credit team execution |
| How measured? | Test numerical credibility | Define numerator, denominator, baseline, period, sample |
| Causality? | Detect attribution errors | Name concurrent factors and avoid sole-cause claims |
| Trade-off? | Test prioritization | State what was delayed, simplified, or deliberately excluded |
| Failure? | Test learning honesty | Give observed miss, correction, and changed rule |
| Counterexample? | Test overgeneralization | State where the method would not work |
| Repeatability? | Test seniority | Explain which mechanism transfers and which domain knowledge does not |
| Current state? | Test stale portfolio claims | Separate past delivery, current runtime, and business use |

## 4. Spoken-answer rules

- Start with the conclusion.
- Use 3–5 short sentences.
- Explain what changed without implementation jargon.
- Prefer `何をしたか → なぜしたか → 価値` over technology names.
- Use one representative example rather than a catalogue.
- Keep the answer roughly 180–320 Japanese characters when practical.
- Do not repeat `結論として` mechanically in every follow-up.
- In Japanese interviews use `御社`, not `貴社`.

Translate technical work into business language:

| Technical description | Nontechnical description |
|---|---|
| state machine / retry | 止まった仕事を見つけ、回復できるものだけやり直す |
| schema / template | 担当者ごとの判断を、同じ項目で確認できる形にする |
| fallback | 一つの手段が使えない時に、安全な代替へ切り替える |
| model evaluation | AIの答えをそのまま使わず、合格条件で点検する |
| configuration | 業種ごとに別製品を作らず、違いを設定で扱う |

## 5. Evidence and metric rules

Use this priority:

1. Actual project code, state, logs, and dated outputs for current-state claims.
2. Candidate profile and résumé data for career claims.
3. Company facts for employer claims.
4. Existing QA only as a draft to challenge, never as an independent source.

For every multiplier, percentage, reduction, user count, rollout count, category count, or release count, verify:

- exact definition
- numerator and denominator
- before/after baseline
- measurement period
- sample or population
- candidate contribution
- concurrent factors

If these are absent, keep the recorded result under `確認済み事実`, add a `{{要確認}}`, and weaken the oral wording. Never invent a denominator to make the result sound complete.

## 6. Pack update rules

- Append previous answers instead of deleting them unless the user asks for replacement.
- Continue question numbering without duplicates.
- Keep one canonical QA file, normally `03_interview_qa.md`.
- Update `00_README.md` if its question count or content summary changes.
- Update `06_numbers_card.md` with source, definition, and interview caveat.
- Do not regenerate slides or audio unless requested.
- Use `interview/generated/` or the pack under `output/` for generated artifacts; do not create loose root files.
