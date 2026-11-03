import { defineConfig, devices } from '@playwright/test';

/*
 * Rinzler's Playwright config. It lives in Rinzler's own work copy at tests/rinzler/ (copied there by
 * `scripts/local/app.sh e2e`), never in ~/code.
 *
 * - No login: auth endpoints are stubbed by the repo's tests/fixtures.ts (imported by evidence.ts), so no
 *   credentials and no OTP are involved. When a real session is needed, the owner logs in by hand in a headed
 *   browser; Rinzler never types or stores credentials.
 * - serviceWorkers 'block' keeps the app's MSW worker out of the way so page.route stubs see every request.
 * - Evidence on failure: screenshot, video, trace, plus console and network logs attached by evidence.ts.
 * - retries 0: a test that passes only on retry is flaky and goes to the KB, not hidden. Use --repeat-each to
 *   measure flakiness.
 */
const OUT = process.env.RINZLER_E2E_OUT || '../rinzler-e2e-results';

export default defineConfig({
    testDir: './scenarios',
    timeout: 45_000,
    fullyParallel: true,
    retries: 0,
    workers: process.env.CI ? 1 : 2,
    outputDir: `${OUT}/artifacts`,
    reporter: [['line'], ['json', { outputFile: `${OUT}/results.json` }], ['html', { outputFolder: `${OUT}/html`, open: 'never' }]],
    use: {
        baseURL: process.env.RINZLER_BASE_URL || 'http://localhost:3333/',
        testIdAttribute: 'data-pw',
        locale: 'fa-IR',
        timezoneId: 'Asia/Tehran',
        serviceWorkers: 'block',
        trace: 'retain-on-failure',
        screenshot: 'only-on-failure',
        video: 'retain-on-failure',
    },
    expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled' } },
    projects: [
        { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
        { name: 'mobile', use: { ...devices['Pixel 7'] } },
    ],
});
