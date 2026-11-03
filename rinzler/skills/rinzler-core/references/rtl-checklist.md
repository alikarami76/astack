# Persian and right-to-left checklist (every touched page)

1. The page runs right-to-left (`dir="rtl"`); icons that show direction (back, next, progress) are mirrored, others are not.
2. Numbers the seller reads (prices, counts, percents, dates) use Persian digits (۰-۹); numbers the seller types are accepted in Persian and Latin digits and give the same result.
3. Thousands separators in prices are consistent; currency is named (ریال or تومان) and the unit matches the API's unit.
4. Persian text uses Persian ye and kaf (ی، ک), not Arabic (ي، ك); search and filters match both.
5. Zero-width non-joiner (نیم‌فاصله) is kept in labels (for example «تنوع‌ها»), and does not break search.
6. Mixed Persian and Latin text (product codes such as SKU-123, English brand names) keeps the right order; percent signs sit on the correct side.
7. Dates are Jalali where the seller reads them, with the Asia/Tehran time zone; deadlines ("48 hours before") are computed in that zone.
8. Long Persian labels do not overflow on mobile width (Pixel 7 project) and on 1280 px desktop.
9. Accessibility, WCAG 2.2 AA: run axe (`checkA11y` in `$HOME/agent-stack/rinzler/playwright/evidence.ts`); inputs have labels, errors are announced and tied to their field, focus order follows the visual right-to-left order, contrast passes.
10. Loading, empty, error, locked (module off) and success states each show their own Persian message.
