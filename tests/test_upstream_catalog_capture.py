"""Admission evidence collector tests; no network or Docker."""
import unittest
from scripts.upstream_catalog_capture import (assert_repeatable, canonical_bytes,
                                              capture_catalog, reject_secrets)


def tool(name):
    return {"name": name, "inputSchema": {"type": "object"}}


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, pages, **kwargs):
        seen = []
        async def fetch(cursor):
            seen.append(cursor)
            return pages[len(seen)-1]
        result = await capture_catalog(fetch, **kwargs)
        return result, seen

    async def test_complete_pages_and_stable_bytes(self):
        pages = [{"tools": [tool("b")], "nextCursor": "next"}, {"tools": [tool("a")]}]
        first, seen = await self.collect(pages)
        second, _ = await self.collect(pages)
        self.assertEqual(seen, [None, "next"])
        self.assertEqual([x["name"] for x in first["tools"]], ["a", "b"])
        self.assertEqual(first["count"], 2)
        assert_repeatable(first, second)
        second["tools"][0]["description"] = "changed"
        with self.assertRaisesRegex(ValueError, "changed"):
            assert_repeatable(first, second)

    async def test_empty_catalog(self):
        result, _ = await self.collect([{"tools": []}])
        self.assertEqual(result["count"], 0)

    async def test_missing_and_malformed(self):
        for page in [{}, {"tools": None}, {"tools": [{}]}, {"tools": [tool(1)]}]:
            with self.subTest(page=page), self.assertRaises(ValueError):
                await self.collect([page])

    async def test_duplicate_refused_across_pages(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            await self.collect([{"tools": [tool("a")], "nextCursor": "x"}, {"tools": [tool("a")]}])

    async def test_loop_and_invalid_cursors(self):
        for cursor in ["", 3, True]:
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                await self.collect([{"tools": [tool("a")], "nextCursor": cursor}])
        with self.assertRaisesRegex(ValueError, "looping"):
            await self.collect([{"tools": [tool("a")], "nextCursor": "x"},
                                {"tools": [tool("b")], "nextCursor": "x"}])

    async def test_empty_continuation_refused(self):
        with self.assertRaisesRegex(ValueError, "empty continuation"):
            await self.collect([{"tools": [tool("a")], "nextCursor": "x"}, {"tools": []}])

    async def test_all_bounds(self):
        cases = [({"max_pages": 1}, [{"tools": [tool("a")], "nextCursor": "x"}]),
                 ({"max_tools": 1}, [{"tools": [tool("a"), tool("b")]}]),
                 ({"max_bytes": 4}, [{"tools": [tool("a")]}]),
                 ({"max_pages": 0}, [])]
        for kwargs, pages in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await self.collect(pages, **kwargs)

    async def test_fetch_failure_not_partial_success(self):
        async def failed(_):
            raise TimeoutError("synthetic timeout")
        with self.assertRaises(TimeoutError):
            await capture_catalog(failed)

    def test_secret_rejected_before_write(self):
        with self.assertRaisesRegex(ValueError, "secret detected"):
            reject_secrets(canonical_bytes({"value": "disposable-canary"}), ["disposable-canary"])
        reject_secrets(canonical_bytes({"value": "safe"}), ["disposable-canary"])


if __name__ == "__main__":
    unittest.main()
