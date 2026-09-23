# Artificial contract v1 fixture

This directory contains **artificial development data**, not results of the
production analytics pipeline. Identifiers, scores, roles, explanations and
cluster hypotheses are hand-authored solely to exercise the HTTP contract.
`rules.json` contains no approved analytics formulas.

The observed edges are `0007 → 7 → 0012`; `isolated` has no edges. `0007` and `7`
are distinct exact string identifiers. `0007` is a seed with incomplete inbound
observation and zero observed incoming amount; `0012` is outbound-censored.
The intermediate and isolated nodes include explicit unknown (`null`) quality
values. Empty observations do not establish a final recipient.

The run is `fixture-contract-v1`. Priorities are deliberately supplied as
`7, 0012, 0007, isolated` for deterministic pagination tests. Four nodes suffice
for the contract fixture; the real-run minimum top of 20 applies when at least
20 nodes exist. Every mandatory artifact is present and listed by logical name
in `manifest.json`. The extra `ai_response.json` is a sample local fallback.

Run the development server only with explicit fixture mode as documented in
the repository README. Do not use these files as production results.
