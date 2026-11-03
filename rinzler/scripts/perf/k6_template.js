// Rinzler k6 template: API load test with percentile thresholds.
// Run through perf.py, which checks the target first:
//   python3 scripts/perf/perf.py k6 scripts/perf/k6_template.js --base-url http://localhost:3333 --scenario smoke --out <run>/perf
// Scenarios: smoke (1 user, 1 minute), load (expected traffic), stress (ramp past it), soak (long and steady), spike.
// Endpoints are GET-only by default. If an endpoint needs a session, the OWNER exports SESSION_COOKIE in his own
// terminal; Rinzler never reads, prints or stores it.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL || 'http://localhost:3333';
const SCENARIO = __ENV.SCENARIO || 'smoke';
// Edit per feature. Paths only; keep to read endpoints unless the owner approved writes on demo.
const ENDPOINTS = (__ENV.ENDPOINTS || '/').split(',');

const PROFILES = {
    smoke: { executor: 'constant-vus', vus: 1, duration: '1m' },
    load: { executor: 'ramping-vus', stages: [{ duration: '2m', target: 20 }, { duration: '5m', target: 20 }, { duration: '1m', target: 0 }] },
    stress: { executor: 'ramping-vus', stages: [{ duration: '2m', target: 20 }, { duration: '3m', target: 60 }, { duration: '3m', target: 120 }, { duration: '2m', target: 0 }] },
    soak: { executor: 'constant-vus', vus: 15, duration: '60m' },
    spike: { executor: 'ramping-vus', stages: [{ duration: '30s', target: 5 }, { duration: '10s', target: 100 }, { duration: '1m', target: 100 }, { duration: '10s', target: 5 }] },
};

export const options = {
    scenarios: { [SCENARIO]: PROFILES[SCENARIO] },
    thresholds: {
        http_req_failed: ['rate<0.01'],
        http_req_duration: ['p(95)<800', 'p(99)<1500'],
    },
    summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const params = __ENV.SESSION_COOKIE ? { headers: { Cookie: __ENV.SESSION_COOKIE } } : {};

export default function () {
    for (const path of ENDPOINTS) {
        const res = http.get(`${BASE}${path}`, params);
        check(res, { 'status is 2xx': (r) => r.status >= 200 && r.status < 300 });
    }
    sleep(1);
}
