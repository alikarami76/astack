# Severity and priority

**Severity** is how bad the effect is. Rinzler proposes it with a one-sentence reason. **Priority** is how soon to fix it; Rinzler proposes it, the owner or the PM decides (ISTQB separates the two; see docs/research.md section 4).

| Severity | Meaning | your product examples |
|---|---|---|
| S1 Critical | Wrong money or data at scale, security or privacy exposure, or a core flow unusable with no workaround | A discount applied to the wrong items; a customer charged twice for one order; another user's data shown |
| S2 Major | A core flow blocked or wrong for a real group of sellers, with a workaround | All card payments fail while bank transfer still works; a whole category shows the wrong fee on the preview |
| S3 Minor | Wrong or misleading in an edge case or secondary flow; the task can still be done | A boundary value rejected in one input mode but accepted in another; a percent shown one point off |
| S4 Trivial | Cosmetic, wording, layout; no effect on decisions | Misaligned badge; Latin digits in a static label |

Raise one level when: money is charged or paid wrongly; the error is silent (no message, the seller cannot tell); it hits a promotion peak (flash sale, Black Friday). Lower one level when: a documented, easy workaround exists and the seller sees an error rather than a wrong result.

| Priority | Meaning |
|---|---|
| P1 | Fix now (hotfix) |
| P2 | Fix in the current sprint |
| P3 | Plan in the backlog |
| P4 | Fix when convenient |

A good bug title says what is wrong, where, and under which condition, in one line: "Flash discount form discount step shows 44% for an exact 45% price, so a valid bid at a 45% minimum is rejected", not "discount bug".
