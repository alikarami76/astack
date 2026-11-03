## Summary

This sample shows what the report template does with ordinary Markdown. Replace every section with your own content. Headings become bookmarks and table-of-contents entries, tables repeat their header row when they break across pages, and code keeps its left-to-right layout.

## Findings

### What changed

Three things changed during the quarter:

1. The review cycle moved from weekly to daily.
2. Promotions now expire at midnight local time.
3. Sellers see the reason for every rejected price.

### Numbers

| Metric | Before | After |
|---|---:|---:|
| Median review time (hours) | 18.0 | 2.5 |
| Rejected prices per day | 1,240 | 610 |
| Seller appeals per week | 95 | 40 |

> The figures above only illustrate the template.

## Method

Prices were compared with the rule set in force on each day[^rules]. The query that produced the table is below.

```sql
SELECT day, COUNT(*) AS rejected
FROM price_review
WHERE status = 'rejected'
GROUP BY day;
```

[^rules]: The rule set is versioned, so each day is compared with the rules that applied on that day.

## Next steps

- Share the report with the category teams.
- Repeat the measurement next quarter with the same method.
