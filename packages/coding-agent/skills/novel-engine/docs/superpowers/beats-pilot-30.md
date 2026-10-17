# Beats Pilot (30 scenes): are model self-reported beats trustworthy?

## Question

Do model self-reported scene `beats` cover the events actually present in `scene_text`,
or do they omit / exaggerate? This pilot decides with a pre-registered rule so the
outcome needs no new discussion.

## Method

1. Sample 30 consecutive scenes from a pilot run.
2. For each scene, record the model's self-reported `beats` and the model-independent
   `extract_beats_fallback(scene_text)` beats (keyword-overlap, no LLM).
3. A human judge marks per scene:
   - `omission?`: YES if a fallback beat (real event in the text) is missing from
     the self-reported beats.
   - `exaggeration?`: YES if a self-reported beat describes something not present
     in the text.
4. Compute omission rate = omission-YES / 30.

## Pass criterion (pre-registered, omission-weighted)

- **PASS if omission rate < 10%** (i.e. at most 2 of 30 scenes omit). Accuracy of
  wording is secondary; omissions are what break cross-chapter dedup (Q13).
- Otherwise **FAIL**.

## Decision rule (pre-registered)

- **Pass** → cross-chapter dedup may read self-reported beats (spot-check fallback).
- **Fail** → cross-chapter dedup reads fallback beats only; self-reported beats are
  advisory text, never the dedup key.

## Result table

| scene_id | self-reported beats | fallback beats | omission? | exaggeration? | notes |
|----------|--------------------|----------------|-----------|---------------|-------|
| 01 | ✓ | ✓ | NO | NO | ch1s3 |
| 02 | ✓ | ✓ | NO | NO | ch2s1 |
| 03 | ✓ | ✓ | NO | NO | ch2s3 |
| 04 | ✓ | ✓ | NO | NO | ch2s4 |
| 05 | ✓ | ✓ | NO | NO | ch2s5 |
| 06 | ✓ | ✓ | NO | NO | ch3s1 |
| 07 | ✓ | ✓ | NO | NO | ch3s2 |
| 08 | ✓ | ✓ | NO | NO | ch3s3 |
| 09 | ✓ | ✓ | NO | NO | ch4s1 |
| 10 | ✓ | ✓ | NO | NO | ch4s2 |
| 11 | ✗ | ✓ | YES | NO | ch4s3 (beats empty, retry re-append) |
| 12 | ✓ | ✓ | NO | NO | ch4s4 |
| 13 | ✓ | ✓ | NO | NO | ch5s1 |
| 14 | ✓ | ✓ | NO | NO | ch5s4 |
| 15 | ✓ | ✓ | NO | NO | ch6s1 |
| 16 | ✓ | ✓ | NO | NO | ch6s2 |
| 17 | ✓ | ✓ | NO | NO | ch6s3 |
| 18 | ✓ | ✓ | NO | NO | ch7s1 |
| 19 | ✓ | ✓ | NO | NO | ch7s3 |
| 20 | ✓ | ✓ | NO | NO | ch8s1 |
| 21 | ✗ | ✓ | YES | NO | ch8s2-dup (beats empty, retry re-append) |
| 22 | ✓ | ✓ | NO | NO | ch8s2 |
| 23 | ✓ | ✓ | NO | NO | ch8s3 |
| 24 | ✓ | ✓ | NO | NO | ch9s1 |
| 25 | ✓ | ✓ | NO | NO | ch9s2 |
| 26 | ✓ | ✓ | NO | NO | ch9s3 |
| 27 | ✓ | ✓ | NO | NO | ch9s4 |
| 28 | ✓ | ✓ | NO | NO | ch10s1 |
| 29 | ✓ | ✓ | NO | NO | ch10s2 |
| 30 | ✓ | ✓ | NO | NO | ch10s3 |

**Omission rate:** 2 / 30 = 6.7% — **PASS** (< 10% threshold; see results below).
## Pilot results (2026-09-06, batch ch1-10, 30 scenes sampled preferring long/event-dense)

Sample: 30 scenes across ch1-ch10 (length-desc with max 4/chapter + all-chapter guarantee).
Judged per scene: every key event in scene_text present in beats = PASS; key event missing = omission; beats claim absent from text = exaggeration.

| Batch | Scenes | PASS | Omission | Exaggeration |
|---|---|---|---|---|
| 1 (ch1s3, ch2s1/s3/s4/s5, ch3s1/s2/s3, ch4s1/s2) | 10 | 10 | 0 | 0 |
| 2 (ch4s3/s4, ch5s1/s4, ch6s1/s2/s3, ch7s1/s3, ch8s1) | 10 | 9 | 1 (ch4s3, beats empty) | 0 |
| 3 (ch8s2x2, ch8s3, ch9s1/s2/s3/s4, ch10s1/s2/s3) | 10 | 9 | 1 (ch8s2-dup len=3103, beats empty) | 0 |

**Omission rate: 2/30 = 6.7% — PASS. Exaggeration rate: 0/30. Overall: 28/30 faithful.**

Key finding: both omissions are EMPTY beats fields, both on retry re-appended scenes
(ch4 journal ids [1,2,3,4,3,4]; ch8 ids [1,2,3,4,4,1,2] — the duplicated re-appends
carry no beats). Zero dishonesty detected in 28 reported cases: no invented events,
no inflated turning points. Failure mode is missing-not-lying, localized to the
retry path not recomputing beats.

Decision per pre-locked fallback rule: TRUST self-reported beats for cross-chapter
dedup; run fallback extraction ONLY for beats-empty scenes (first jobs: ch4s3,
ch8s2-dup). Recommended hotfix (data-completeness bug class): retry re-append must
preserve/recompute beats instead of writing empty.

## Backfill done (2026-09-06, task ③ complete)

All FIVE empty-beats lines backfilled via extract_beats_fallback (not just the two
sampled): ch4 scenes 3+4 (retry copies), ch8 scenes 4+1+2 (retry copies). Each
backfilled beat byte-verified as a verbatim substring of its scene_text, tagged
"beats_source": "fallback". Zero empty-beats lines remain across all 44 scenes.

Calibration note: model-reported beats are frequently paraphrases, not quotes
(e.g. ch1s1, ch10s1-3) — verified acceptable: the pilot judged semantic fidelity
directly (28/28), and paraphrase normalizes wording for dedup use. Verbatim-only
checks apply to fallback output, never to model output.

Footnote (out of pilot scope, timeline material): ch5s4 says picked up "3 years ago"
while ch1-2 depict a newborn — beats faithfully reflect the text, so pilot PASS,
but flagged for StateDB-timeline work. → **PASS / FAIL** (circle one, apply decision rule above).
