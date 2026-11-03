# Rinzler research: current QA best practice

Written 2026-10-24 for Rinzler, the QA agent for your product (a React web app tested with Playwright, a PHP backend, Persian/RTL UI).

How to read this: each section says what strong teams do, with numbered references, then gives a "Rinzler rule". Numbers in brackets point to the Sources list. URLs were checked with an HTTP request on 2026-10-24 (status 200). A few sources I wanted were dropped because they did not load (see "Gaps" at the end). Where a statement is my own practitioner judgement rather than a cited claim, it is marked (judgement).

---

## 1. Test pyramid, trophy, and risk-based testing

Google sorts tests by size (small = one process, medium = one machine, large = more) and suggests a rough 80/15/5 split of small/medium/large tests; the reason is speed and reliability, not tidiness [1]. Fowler and Vocke's "practical test pyramid" says the same: many fast low-level tests, few slow UI tests, and push each check to the lowest level that can catch it [2][3]. Kent C. Dodds argues for a "trophy": mostly integration tests, because they give the most confidence per unit of cost for UI apps ("Write tests. Not too many. Mostly integration.") [4]. The two shapes agree on the key point: UI end-to-end tests are the most expensive and the slowest, so use few of them, on the flows that matter.

Risk-based testing decides where the effort goes. ISTQB defines risk as likelihood of a problem combined with its impact [5]. For your product, impact is large when money or seller trust is involved (wrong price shown, wrong fee, a promotion that applies to the wrong items) and likelihood is higher in new or recently changed code.

**Rinzler rule:** score every feature or requirement on likelihood (1-3: code churn, complexity, past bugs) times impact (1-3: money, sellers affected, reversibility). Spend most cases on score 6-9, a thin smoke pass on 1-2. Put each case at the lowest level that can catch the bug (API or data check before a browser flow).

## 2. Test strategy and plans from requirements; traceability

Bach and Bolton's Heuristic Test Strategy Model gives a checklist of what to consider when building a strategy: project factors, product factors, and quality criteria [6]. Teams that take testing seriously keep every case linked to the requirement it checks, so a gap (requirement with no case) and a waste (case with no requirement) are both visible. The cheapest working version is an ID on each requirement and the same ID on each case and result (judgement; the model in [6] supplies the "what to cover" side, the ID chain is plain bookkeeping).

**Rinzler rule:** from a PRD, extract numbered requirements (R-001...), mark each as testable or ambiguous (ambiguous ones become questions to the PM, not guessed cases). Every case carries `req: R-0xx`, every result carries the case id. The final report shows a coverage table: requirement -> cases -> pass/fail/blocked/not run.

## 3. Exploratory testing and test design techniques

Hendrickson's charter template is "Explore (target) with (resources) to discover (information)", time-boxed in sessions of roughly 60-90 minutes, followed by notes on what was found and what was not covered [7]. Session-based test management (Jonathan and James Bach) adds a debrief and counts sessions rather than cases (named here from general knowledge; I did not fetch a primary page for it, so it is unverified).

Two heuristics give the agent ideas for what to look at. SFDIPOT (Structure, Function, Data, Interfaces, Platform, Operations, Time) lists product dimensions [6]. HICCUPPS (History, Image, Comparable products, Claims, User expectations, Product, Purpose, Statutes) lists oracles, meaning ways to decide that something is wrong without a written spec [8]. Bolton's note is that these are prompts, not checklists to complete.

For inputs: boundary value analysis and equivalence partitioning cut a huge input space to a few cases (judgement, standard technique, ISTQB syllabus [5]). Pairwise testing covers every pair of parameter values with far fewer combinations; NIST research found most real failures come from interactions of only one to a few parameters [9].

**Rinzler rule:** an exploratory run always starts from a written charter with a time box and a scope, and ends with a short debrief (found, not covered, new questions). Use SFDIPOT to pick areas, HICCUPPS to name the oracle behind every suspected bug. For price, discount, percentage and date fields, always test boundaries (0, 1, max, max+1, empty, decimals, Persian digits). When 3+ parameters combine (for example campaign type, product category, seller type), generate a pairwise set instead of the full grid.

## 4. Bug reports, severity vs priority, duplicates

Bettenburg et al. surveyed Eclipse, Mozilla and Apache developers: the most useful report content is steps to reproduce, observed and expected behaviour, stack traces and test cases; the most common problems are missing steps, wrong or vague expected behaviour, and duplicates [10]. ISTQB separates severity (how bad the effect is) from priority (how soon to fix it); the tester proposes severity, the product owner sets priority [5].

**Rinzler rule:** a bug report has: title with the symptom, environment and build, numbered steps, expected vs actual, evidence (screenshot, trace, request/response), the oracle used, requirement id, proposed severity with one sentence of reasoning. Rinzler proposes severity only; priority is left to the PM. Before filing, search existing Jira tickets and the KB for the same symptom or component, and link instead of filing a duplicate. Before filing, a separate verifier tries to refute the bug (reproduce twice, check data, check whether it is documented behaviour). Unverified claims are labelled as such.

## 5. Regression selection and flaky tests

Meta's predictive test selection trains a model on past changes and test outcomes and runs only the tests likely to fail for a given change; it caught over 99.9% of faulty changes while running about a third of the tests [11]. The general idea (select by what changed and by past failure history) works without machine learning: map changed files or features to the tests that cover them, and add tests that failed recently.

Google's numbers on flakiness: about 1.5% of all test runs are flaky, and almost 16% of tests show some flakiness; fixes only keep pace with new flakiness, and engineers who get used to false alarms start ignoring real failures [12]. Their later analysis groups causes (the test, the code under test, the infrastructure, the environment) and warns that rerunning is not proof of a product bug being absent [13]. Playwright's own advice: retries exist to survive rare infrastructure noise, and a test that passes only on retry is reported as "flaky" so it can be tracked, not hidden [14].

**Rinzler rule:** run (1) tests for the changed area, (2) tests that failed in the last N runs, (3) a small always-on smoke set. A failure is only called flaky if it fails and passes on the same build; the retry count is capped at 1-2, each flaky case is recorded in the KB with its first-seen date and suspected cause, and cases flaky more than twice are quarantined (excluded from pass/fail, still run and reported separately) with an owner. Never silently retry until green.

## 6. Contract and API testing

Pact's consumer-driven contract testing: the consumer (the front end) records the requests it makes and the responses it needs; the provider (the backend) must prove it still satisfies them; so a breaking API change is caught before release without a full end-to-end environment [15]. It fits when the front end and backend release independently, as a web app and its backend often do. At minimum, schema checks (response matches an OpenAPI or JSON schema, required fields, types, enum values) catch the most common break.

**Rinzler rule:** for each requirement touching an API, check three things before any browser test: status codes and error shape, response against schema (types, required fields, units such as Rial vs Toman), and business invariants (discounted price <= base price, percent in 0-100). Report contract drift as a finding even when the UI still works. (Full Pact adoption is a team decision, not something Rinzler imposes.)

## 7. E2E with Playwright, and visual regression

Playwright's best-practices page says: test what the user sees, use user-facing locators (`getByRole`, `getByLabel`, `getByText`, `getByTestId` as a fallback) rather than CSS or XPath tied to layout [16][17]; use web-first assertions such as `await expect(locator).toBeVisible()`, which retry until true, rather than asserting on a value read once [16][18]; keep tests independent (each starts from a fresh browser context, no test depends on another) [16]; and do not test third-party sites, mock them with `page.route` [16][19]. Configure traces `on-first-retry` so a failing test leaves a trace with DOM snapshots, network and console log [14][20].

For visual checks, `toHaveScreenshot()` compares to a stored baseline; options include `mask` (hide dynamic parts such as dates and prices from live data), `animations: 'disabled'`, and thresholds (`maxDiffPixels`, `maxDiffPixelRatio`, `threshold`) [21]. Baselines are only valid on the same OS and browser, so generate them in the same container used in CI [21].

**Rinzler rule:** tests use role/label locators and web-first assertions, never fixed sleeps (`waitForTimeout`). Each test sets up its own data through API or fixtures and cleans up. Third-party calls are routed to mocks; our own backend is called for real in staging only. Trace on first retry, attached to the bug report. Visual comparison is used only for stable, important screens, with dynamic regions masked and a stated threshold; a visual diff is a "needs human look" signal, not an automatic bug.

## 8. Accessibility and RTL/Persian checks

WCAG 2.2 Level AA is the usual target [22]. axe-core automates a subset of WCAG checks (contrast, missing labels, ARIA misuse) and Playwright documents running it in tests with `@axe-core/playwright`; the docs themselves say automated scans catch only some problems and manual checks are still needed [23][24]. For right-to-left, W3C i18n guidance says to set `dir="rtl"` and `lang="fa"` on the document and use the Unicode bidi algorithm's isolation (`<bdi>`, `dir="auto"`) for embedded text of the other direction, such as a Latin SKU or a number inside a Persian sentence [25][26].

Persian-specific checks (judgement, from general Unicode knowledge, not a cited source): digits can be Persian (U+06F0-06F9), Arabic-Indic (U+0660-0669) or ASCII, and the product should be consistent and search/inputs should accept all three; Persian letters ye and kaf have Arabic look-alike code points (U+064A, U+0643) that break search and matching; half-space (ZWNJ, U+200C) must survive saving; thousands separators and negative signs and percent signs can visually flip in RTL; icons with direction (arrows, back, progress bars) should mirror; Jalali dates need correct month names and leap years.

**Rinzler rule:** run axe on every page the change touches and report only AA violations; add a manual checklist for keyboard order, focus visibility and error messages. For RTL, always check: layout mirrored, mixed Persian/Latin/number text reads correctly, digit consistency, input accepting all digit forms and normalising before saving, truncation and wrapping of long Persian text, and Jalali dates. Screenshot every RTL finding.

## 9. Data validation, reconciliation, synthetic data, oracles

Data tests in dbt and Great Expectations are plain assertions on tables: not null, unique, accepted values, relationships between tables, row-count and sum comparisons [27][28]. Reconciliation means computing the same figure by two independent routes (for example the price the UI shows vs the price recomputed from the source tables and the documented formula) and flagging any difference beyond a tolerance (judgement, standard practice). Property-based testing (Hypothesis) states a rule that must always hold, then generates many random inputs to try to break it; it also shrinks failures to a minimal example [29]. Good properties for pricing: final price never negative, discount never exceeds base price, applying a rule twice gives the same result, rounding is the same everywhere.

The oracle problem (how do we know the right answer?) is handled by combining oracles: written requirement, the formula in the KB, a second independent computation, previous release behaviour, comparable screens [8]. Bolton and Bach also separate "checking" (a machine comparing to an expected value) from "testing" (a person or agent judging) [30].

**Rinzler rule:** each case names its oracle. Prefer an independent recomputation from the KB formula over "it looks right". Data checks run read-only (SELECT only) on staging or a replica, never writing. Synthetic or generated data is created in staging with a recognisable prefix so it can be found and removed. If no oracle exists, the result is "needs a human decision", never a pass.

## 10. Performance

k6 defines the standard test types: smoke (does it work at all), average-load, stress (beyond normal), soak (long, finds leaks), spike (sudden surge), and breakpoint [31]. Thresholds turn a metric into a pass/fail rule, for example `http_req_duration: ['p(95)<500']` and `http_req_failed: ['rate<0.01']` [32]. Locust does the same with Python user classes [33]. The Google SRE book says to use percentiles, not averages, for latency targets because the average hides the slow tail users actually feel; SLOs are written as "p99 under X for Y% of the time" [34][35].

For the browser, Core Web Vitals "good" thresholds, measured at the 75th percentile of real page loads, are LCP at most 2.5 s, INP at most 200 ms, CLS at most 0.1 [36]. Lighthouse CI runs Lighthouse on each change and can fail the build on budgets [37]. Amazon's deployment practice runs tests and load against pre-production and then ramps changes slowly with monitoring, rather than load-testing the live system blind [38].

**Rinzler rule:** a perf run states its type, target, and threshold before it runs. First run records a baseline (p50/p95/p99 and error rate, several repeats); later runs fail only if p95 is worse than baseline by more than an agreed margin (start at 20%) on repeated runs, because one run is noise. Report percentiles, never only averages. Run only against staging or demo; any load against production requires explicit written approval from the user for that run, with limits and a stop condition. Lighthouse runs report LCP/INP/CLS against the thresholds above.

## 11. Evaluating LLM and AI features

What strong teams do, in order:

- Define success first. Anthropic's guidance: criteria must be specific, measurable, achievable and relevant, for example an F1 target or a "wrong answers under 0.1%" target, not "good quality" [39][40].
- Build a golden set. Many small, realistic cases, including edge and adversarial ones, beat a few perfect ones; automate grading wherever possible; start with 20-50 cases drawn from real failures [40][41][42] (the "20-50 cases" figure is a rule of thumb in [41], not a law).
- Prefer code-graded checks (exact match, regex, schema, number within tolerance, tool called with right arguments). Use model-graded checks only for what code cannot judge (tone, groundedness, helpfulness) [39][40][42].
- Judge with a rubric, ideally binary pass/fail per criterion, and calibrate the judge against human labels. Hamel Husain's method: have a domain expert label a set, measure the judge's true-positive and true-negative rates against those labels, iterate the prompt until it agrees, and keep a held-out set [43][44]. Cohen's kappa is a sound agreement statistic because it corrects for chance agreement (judgement; standard statistic).
- Know judge biases. Zheng et al. found that strong LLM judges agree with humans over 80% of the time, but show position bias (favour the first answer), verbosity bias (favour longer answers) and self-enhancement bias (favour their own model's output) [45]. Counter with swapped order, length-neutral rubrics, and a different model as judge than the one being tested.
- Measure repeatability. Pass@k (from HumanEval) is the chance that at least one of k samples is correct, so it measures capability [46]. Pass^k (from tau-bench) is the chance that all k samples are correct, so it measures reliability, which is what a user-facing feature needs [47].
- Error analysis beats dashboards: read real failed traces, group failures, and turn each group into a test [43][48].
- Safety and robustness: test prompt injection (OWASP LLM01), sensitive-data leakage, and excessive agency; add paraphrase variants of each input and check the answer does not change; add questions with no answer in the source and check the system refuses or says it does not know [49][50].
- Put the regression eval in CI, and track latency and cost per case alongside quality, because a better answer that is 5x slower or dearer is a trade-off to decide [39][40].

**Rinzler rule:** eval cases are stored as data with `id`, `req`, `tags` (capability, risk, paraphrase-group, adversarial), `input`, `expected` or `rubric`, and `grader` (code or model). No LLM judge is trusted until it has scored a human-labelled set with reported TPR/TNR (target 90%+ each, my suggestion) and kappa; the report prints those numbers next to any model-graded score. Report pass@k and pass^k with k stated, never a bare "accuracy". Keep a paraphrase group and an adversarial/refusal group for every feature. Groundedness is checked by requiring the cited source or number to be present in the retrieved data (code check first, judge second). Record p50/p95 latency and cost per case.

## 12. Shift-left and quality gates in CI

Microsoft's shift-left write-up: move quality checks earlier, make unit tests fast and reliable enough to block merges, and treat flaky or slow tests as defects in the pipeline itself [51]. Amazon's practice shows the later half of the same idea: automated gates and staged rollout rather than a human sign-off at the end [38]. A gate is useful only if it is fast, trusted and has a clear owner; a slow or flaky gate gets bypassed [12][51].

**Rinzler rule:** Rinzler contributes at three points: at PRD review (list ambiguous or untestable requirements before any code), at merge request (changed-area regression plus contract and schema checks, under about 10 minutes), and pre-release (full risk-ranked pass, exploratory charters, perf baseline compare). Each gate has a pass rule written in advance. Rinzler recommends blocking or not; the humans decide.

## 13. Quality metrics

DORA's four key measures are deployment frequency, lead time for changes, change failure rate and time to recover from a failed deployment [52]. Of these, change failure rate (the share of releases that need a fix, rollback or hotfix) is the one QA moves most. Also useful: escaped defects (bugs found in production that testing should have caught, ideally with a note on why), mean time to detect and to resolve, and defect density per area (only to compare similar areas over time) (judgement, widely used measures; DORA is the only cited source here).

Vanity or misleading: number of test cases, raw pass rate (a 100% pass rate on weak tests means nothing), automation percentage, bugs filed per tester, code coverage as a target (it shows what was not tested, not that what ran was well tested).

**Rinzler rule:** track and report five things per release: escaped defects (with root-cause tag), change failure rate, time to detect, requirement coverage (section 2), and flaky-test count. Never report test count or pass rate alone; always pair pass rate with risk coverage (what share of high-risk requirements were exercised).

---

## What this means for Rinzler's design

- **Risk-based plans with traceability.** Every plan starts from numbered requirements, each scored likelihood x impact; every case and result carries the requirement id; the report ends with a coverage table including "not run" and "blocked".
- **Ask before guessing.** Ambiguous requirements become questions to the PM and are listed in the plan; no invented expected behaviour.
- **Lowest level first.** Order of checks: data and schema, then API contract, then browser E2E, then visual and accessibility. A cheaper level that finds the bug stops the climb.
- **Time-boxed charters for exploring.** Each charter has target, resources, information sought, a time box and a debrief; SFDIPOT picks areas, HICCUPPS names the oracle, pairwise and boundary tables generate inputs.
- **A verifier that tries to refute every bug before filing.** A separate subagent reproduces, rechecks data, looks for "documented behaviour" and duplicates in Jira and the KB; only sustained bugs are filed, with proposed severity and evidence; priority is left to the PM.
- **Eval format with requirement ids and tags.** Eval cases are data files (`id, req, tags, input, expected/rubric, grader`), runnable in CI, with pass@k and pass^k, paraphrase and adversarial groups.
- **LLM judge only with a calibration set.** Judge prompts are versioned, scored against human labels (TPR/TNR, kappa), use a different model than the system under test, and swap answer order.
- **Perf baselines with percentile thresholds.** Baseline first, then p95/p99 and error-rate thresholds with repeats; staging only; production load tests need explicit user approval each time.
- **Flaky quarantine in the KB.** Flaky cases are recorded with first-seen date, cause and owner, quarantined after repeated flakes, reported separately; retries capped; never silent.
- **Persian/RTL as a standing checklist.** Digits, ye/kaf normalisation, ZWNJ, bidi, mirrored icons, Jalali dates, plus axe at WCAG 2.2 AA on every touched page.
- **Read-only and safe by default.** SELECT-only data checks, staging or demo only, test data prefixed and cleaned, nothing filed, pushed or load-run without the user's approval.
- **Lean subagent set.** Suggested: planner (PRD -> requirements -> plan), executor (runs cases, collects evidence), verifier (refutes bugs), and judge-calibrator (only for AI features). Everything else is a skill or a script, not another agent.

---

## Gaps and caveats

- Netflix chaos-engineering and Sapienz pages could not be confirmed (one returned 403 to my fetcher, one 404 on the first URL tried), so they are not cited. Meta's predictive test selection is cited instead.
- The Microsoft Research bug-report page returned 403; the Bettenburg paper is cited from the author's PDF instead.
- Session-based test management and Persian Unicode details are from general knowledge, marked as such.
- I did not re-read every cited page in full; figures stated with numbers (Google 1.5% and 16%, Meta 99.9% and one-third, Web Vitals thresholds, Zheng et al. 80%) come from my knowledge of those sources, and only the Google flakiness numbers were re-checked in the page text on 2026-10-24.
- The 80/15/5 test-size split, 20% perf regression margin, and 90% judge TPR/TNR target are guidelines or my suggestions, to be tuned with the team.

## Sources

1. "Testing Overview" (ch. 11), Software Engineering at Google, Google/O'Reilly, 2020. https://abseil.io/resources/swe-book/html/ch11.html
2. "The Practical Test Pyramid", Ham Vocke on martinfowler.com, 2018. https://martinfowler.com/articles/practical-test-pyramid.html
3. "TestPyramid", Martin Fowler, 2012. https://martinfowler.com/bliki/TestPyramid.html
4. "The Testing Trophy and Testing Classifications", Kent C. Dodds, 2021. https://kentcdodds.com/blog/the-testing-trophy-and-testing-classifications
5. ISTQB Glossary (risk, severity, priority) and Foundation Level syllabus, ISTQB, current. https://glossary.istqb.org/ and https://www.istqb.org/certifications/certified-tester-foundation-level
6. "Heuristic Test Strategy Model", James Bach, Satisfice, v6.x. https://www.satisfice.com/download/heuristic-test-strategy-model
7. "Explore It!", Elisabeth Hendrickson, Pragmatic Bookshelf, 2013. https://pragprog.com/titles/ehxta/explore-it/
8. "Few HICCUPPS", Michael Bolton, DevelopSense, 2012. https://developsense.com/blog/2012/07/few-hiccupps
9. "Automated Combinatorial Testing for Software", NIST CSRC, ongoing. https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software
10. "What Makes a Good Bug Report?", Bettenburg, Just, Schroter, Weiss, Premraj, Zimmermann, FSE 2008. https://thomas-zimmermann.com/publications/files/bettenburg-fse-2008.pdf
11. "Predictive Test Selection", Machalica et al., arXiv / Meta, 2018. https://arxiv.org/abs/1810.05286 (blog: https://engineering.fb.com/2018/11/21/developer-tools/predictive-test-selection/)
12. "Flaky Tests at Google and How We Mitigate Them", John Micco, Google Testing Blog, 2016. https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html
13. "Test Flakiness: One of the Main Challenges of Automated Testing", Google Testing Blog, 2020. https://testing.googleblog.com/2020/12/test-flakiness-one-of-main-challenges.html
14. "Retries", Playwright docs, Microsoft, current. https://playwright.dev/docs/test-retries
15. "Pact documentation", Pact Foundation, current. https://docs.pact.io/
16. "Best Practices", Playwright docs, Microsoft, current. https://playwright.dev/docs/best-practices
17. "Locators", Playwright docs, Microsoft, current. https://playwright.dev/docs/locators
18. "Assertions", Playwright docs, Microsoft, current. https://playwright.dev/docs/test-assertions
19. "Mock APIs", Playwright docs, Microsoft, current. https://playwright.dev/docs/mock
20. "Trace Viewer", Playwright docs, Microsoft, current. https://playwright.dev/docs/trace-viewer
21. "Visual comparisons", Playwright docs, Microsoft, current. https://playwright.dev/docs/test-snapshots
22. "Web Content Accessibility Guidelines (WCAG) 2.2", W3C, 2023. https://www.w3.org/TR/WCAG22/
23. "Accessibility testing", Playwright docs, Microsoft, current. https://playwright.dev/docs/accessibility-testing
24. axe-core, Deque Systems, current. https://github.com/dequelabs/axe-core
25. "Declaring base direction", W3C Internationalization, current. https://www.w3.org/International/questions/qa-html-dir
26. "Inline markup and bidirectional text in HTML", W3C Internationalization, current. https://www.w3.org/International/articles/inline-bidi-markup/
27. "Data tests", dbt docs, dbt Labs, current. https://docs.getdbt.com/docs/build/data-tests
28. Great Expectations documentation, GX, current. https://docs.greatexpectations.io/docs/home/
29. Hypothesis documentation, current. https://hypothesis.readthedocs.io/en/latest/
30. "Testing vs. Checking", Michael Bolton, DevelopSense, 2009. https://www.developsense.com/blog/2009/08/testing-vs-checking
31. "Test types", k6 docs, Grafana Labs, current. https://grafana.com/docs/k6/latest/testing-guides/test-types/
32. "Thresholds", k6 docs, Grafana Labs, current. https://grafana.com/docs/k6/latest/using-k6/thresholds/
33. Locust documentation, current. https://docs.locust.io/en/stable/
34. "Service Level Objectives", Site Reliability Engineering, Google, 2016. https://sre.google/sre-book/service-level-objectives/
35. "Monitoring Distributed Systems", Site Reliability Engineering, Google, 2016. https://sre.google/sre-book/monitoring-distributed-systems/
36. "Web Vitals", web.dev, Google, current. https://web.dev/articles/vitals
37. Lighthouse CI, GoogleChrome on GitHub, current. https://github.com/GoogleChrome/lighthouse-ci
38. "Automating safe, hands-off deployments", Amazon Builders' Library, AWS, 2020. https://aws.amazon.com/builders-library/automating-safe-hands-off-deployments/
39. "Define your success criteria", Anthropic docs, current. https://docs.anthropic.com/en/docs/build-with-claude/define-success
40. "Create strong empirical evaluations", Anthropic docs, current. https://docs.anthropic.com/en/docs/test-and-evaluate/develop-tests
41. "Demystifying evals for AI agents", Anthropic Engineering, 2025. https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
42. "Working with evals", OpenAI API docs, OpenAI, current. https://platform.openai.com/docs/guides/evals
43. "Your AI Product Needs Evals", Hamel Husain, 2024. https://hamel.dev/blog/posts/evals/
44. "Creating a LLM-as-a-Judge That Drives Business Results", Hamel Husain, 2024. https://hamel.dev/blog/posts/llm-judge/
45. "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena", Zheng et al., NeurIPS 2023. https://arxiv.org/abs/2306.05685
46. "Evaluating Large Language Models Trained on Code" (HumanEval, pass@k), Chen et al., OpenAI, 2021. https://arxiv.org/abs/2107.03374
47. "tau-bench: A Benchmark for Tool-Agent-User Interaction" (pass^k), Yao et al., Sierra, 2024. https://arxiv.org/abs/2406.12045
48. "A Field Guide to Rapidly Improving AI Products", Hamel Husain, 2025. https://hamel.dev/blog/posts/field-guide/
49. "OWASP Top 10 for LLM Applications", OWASP GenAI Security Project, 2025. https://genai.owasp.org/llm-top-10/
50. "AI Risk Management Framework", NIST, 2023. https://www.nist.gov/itl/ai-risk-management-framework
51. "Shift left to make testing fast and reliable", DevOps at Microsoft, Microsoft Learn, current. https://learn.microsoft.com/en-us/azure/devops/learn/devops-at-microsoft/shift-left-make-testing-fast-reliable
52. "DORA's software delivery metrics: the four keys", DORA, Google Cloud, current. https://dora.dev/guides/dora-metrics-four-keys/
