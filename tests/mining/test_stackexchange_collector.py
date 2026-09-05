"""验证 StackExchange 采集器的多站点轮换逻辑（不发真实网络请求）。"""
from __future__ import annotations

import unittest
from unittest import mock

from knowpipe.mining.collectors import stackexchange


class TestStackExchangeMultiSiteCycling(unittest.TestCase):
    def _make_page(self, site: str, page: int, items_per_page: int, max_page: int):
        if page > max_page:
            return {"items": [], "has_more": False}
        items = [
            {
                "question_id": f"{site}-{page}-{i}",
                "title": f"{site} q {page}-{i}",
                "body": "<p>body text</p>",
                "tags": ["tag"],
                "creation_date": 0,
                "link": "https://example.com",
                "content_license": "CC BY-SA",
            }
            for i in range(items_per_page)
        ]
        return {"items": items, "has_more": page < max_page}

    def test_switches_to_next_site_when_current_site_exhausted(self):
        # site_a 只有 2 页（每页 3 条，共 6 条），不足 target，必须换到 site_b 补足。
        def fake_fetch_page(site, page):
            if site == "site_a":
                return self._make_page(site, page, items_per_page=3, max_page=2)
            return self._make_page(site, page, items_per_page=3, max_page=2)

        with mock.patch.object(stackexchange, "_fetch_page", side_effect=fake_fetch_page):
            records = list(stackexchange.collect(10, sites=("site_a", "site_b")))

        self.assertEqual(len(records), 10)
        sites_used = {r["source_site"] for r in records}
        self.assertEqual(sites_used, {"site_a", "site_b"})
        # site_a 耗尽后才轮换到 site_b：前 6 条应全部来自 site_a。
        self.assertTrue(all(r["source_site"] == "site_a" for r in records[:6]))
        self.assertTrue(all(r["source_site"] == "site_b" for r in records[6:]))

    def test_stops_before_next_site_when_target_reached_on_first_site(self):
        def fake_fetch_page(site, page):
            return self._make_page(site, page, items_per_page=100, max_page=25)

        with mock.patch.object(stackexchange, "_fetch_page", side_effect=fake_fetch_page):
            records = list(stackexchange.collect(30, sites=("site_a", "site_b")))

        self.assertEqual(len(records), 30)
        self.assertTrue(all(r["source_site"] == "site_a" for r in records))

    def test_respects_page_cap_per_site_without_key(self):
        # site_a 提供的数据量足够多页，但每站最多只能翻到 MAX_PAGE_WITHOUT_KEY 页；
        # 超过上限时应自动换到下一站，而不是继续请求超限页码。
        fetch_calls = []

        def fake_fetch_page(site, page):
            fetch_calls.append((site, page))
            return self._make_page(site, page, items_per_page=100, max_page=999)

        with mock.patch.object(stackexchange, "_fetch_page", side_effect=fake_fetch_page):
            target = stackexchange.MAX_PAGE_WITHOUT_KEY * 100 + 50
            records = list(stackexchange.collect(target, sites=("site_a", "site_b")))

        self.assertEqual(len(records), target)
        site_a_pages = [p for s, p in fetch_calls if s == "site_a"]
        self.assertTrue(all(p <= stackexchange.MAX_PAGE_WITHOUT_KEY for p in site_a_pages))
        self.assertTrue(any(s == "site_b" for s, _ in fetch_calls))


if __name__ == "__main__":
    unittest.main()
