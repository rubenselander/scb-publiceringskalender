# Documentation harvest fixtures

Frozen normalized source records from documentation branch `1ab86f9` (captured 2026-10-05).
These are legacy records, not original response bytes. `inventory.json` records the
request URL, capture timestamp and hash of each committed fixture file.

SCB: full Arbetsmarknad fragment, independently inspected 1,080 anchor occurrences.
First product is Arbetsmiljöundersökningen, code AM0501; its first quality declaration
link is for 2024. SIRIS: only the first three rows of the Grundskola och sameskola,
Betyg årskurs 6, Vårtermin 2025 document response. First document ID 554117 has a
blank official-statistics flag; protocol-relative file URL is preserved in the fixture.

Failure, cap and ambiguous-match examples in the tests are synthetic regression cases.
They are not presented as live source evidence.
