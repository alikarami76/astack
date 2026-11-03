"""Rinzler Locust template, for when a Python load script is easier than k6 (for example, a flow with logic).

Check the target first:  python3 scripts/safety.py target <host> --purpose load [--approval "<owner's words>"]
Then (headless, with a CSV of percentiles):
  locust -f scripts/perf/locustfile.py --host http://localhost:3333 --headless -u 10 -r 2 -t 5m --csv <run>/perf/locust
Endpoints are GET-only. A session cookie, if needed, is exported by the owner as SESSION_COOKIE; Rinzler never reads it.
"""
import os

from locust import HttpUser, between, task

ENDPOINTS = [p for p in os.environ.get("ENDPOINTS", "/").split(",") if p]


class SellerPanelReader(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        cookie = os.environ.get("SESSION_COOKIE")
        if cookie:
            self.client.headers["Cookie"] = cookie

    @task
    def read_pages(self):
        for path in ENDPOINTS:
            with self.client.get(path, name=path, catch_response=True) as r:
                if not 200 <= r.status_code < 300:
                    r.failure(f"status {r.status_code}")
