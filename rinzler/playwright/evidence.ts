import type { Page, TestInfo } from '@playwright/test';

import { expect, test as base } from '../fixtures';

/*
 * Evidence fixture: every test gets the repo's auth stubs (tests/fixtures.ts) plus a log of console errors, page
 * errors and failed or 4xx/5xx network calls. On failure they are attached to the report next to Playwright's own
 * screenshot, video and trace, so a bug draft can cite them. Values that look like secrets are masked.
 */
type Evidence = { console: string[]; network: string[] };

const SECRET = /(authorization|cookie|token|password|otp)(["']?\s*[:=]\s*)[^\s"',;&]+/gi;
const mask = (s: string) => s.replace(SECRET, '$1$2<redacted>').replace(/eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}/g, '<jwt>');

export const test = base.extend<{ evidence: Evidence }>({
    evidence: [
        async ({ page }, use, testInfo: TestInfo) => {
            const ev: Evidence = { console: [], network: [] };
            page.on('console', (m) => {
                if (m.type() === 'error' || m.type() === 'warning') ev.console.push(`${m.type()}: ${mask(m.text())}`);
            });
            page.on('pageerror', (e) => ev.console.push(`pageerror: ${mask(e.message)}`));
            page.on('requestfailed', (r) => ev.network.push(`FAILED ${r.method()} ${r.url()} ${r.failure()?.errorText}`));
            page.on('response', (r) => {
                if (r.status() >= 400) ev.network.push(`${r.status()} ${r.request().method()} ${r.url()}`);
            });
            // eslint-disable-next-line react-hooks/rules-of-hooks
            await use(ev);
            if (testInfo.status !== testInfo.expectedStatus) {
                await testInfo.attach('console.txt', { body: ev.console.join('\n') || '(none)', contentType: 'text/plain' });
                await testInfo.attach('network.txt', { body: ev.network.join('\n') || '(none)', contentType: 'text/plain' });
            }
        },
        { auto: true },
    ],
});

/** Stub one API path with a JSON body shaped like your company's {status, data} envelope. */
export async function stubApi(page: Page, pattern: string, data: unknown, status = 200) {
    await page.route(pattern, (route) =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ status: 'ok', data }) }),
    );
}

/** Standing right-to-left and Persian checks for any page (docs/research.md section 8). */
export async function checkRtl(page: Page) {
    const dir = await page.evaluate(() => document.documentElement.getAttribute('dir') || getComputedStyle(document.body).direction);
    expect(dir, 'page direction').toBe('rtl');
    const bad = await page.evaluate(() => {
        const text = document.body.innerText;
        return { arabicYe: /[يك]/.test(text) };
    });
    expect(bad.arabicYe, 'Arabic ye or kaf in visible text').toBe(false);
}

/** axe-core WCAG 2.2 AA scan when @axe-core/playwright is installed in the work copy (npm i --no-save). */
export async function checkA11y(page: Page, testInfo: TestInfo) {
    let AxeBuilder: any;
    try {
        AxeBuilder = (await import('@axe-core/playwright')).default;
    } catch {
        testInfo.annotations.push({ type: 'a11y', description: 'skipped: @axe-core/playwright not installed' });
        return;
    }
    const res = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    await testInfo.attach('axe.json', { body: JSON.stringify(res.violations, null, 1), contentType: 'application/json' });
    expect(res.violations.filter((v: any) => ['critical', 'serious'].includes(v.impact)), 'serious a11y violations').toEqual([]);
}

export { expect };
