import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(
  new URL("../src/pages/MarketCenter.tsx", import.meta.url),
  "utf8",
);

test("market source switches clear stale rows before loading", () => {
  assert.match(source, /hotRequest\.current \+= 1; setSnapshot\(null\);/);
  assert.match(source, /setLoading\(true\);\s*setSnapshot\(null\);\s*try \{/);
  assert.match(source, /loading && !snapshot[\s\S]*正在读取\{current\.label\}数据/);
});

test("only the latest hot-list request may update the selected source", () => {
  assert.match(source, /const request = \+\+hotRequest\.current;/);
  assert.match(source, /if \(request !== hotRequest\.current\) return;/);
  assert.match(source, /loadHot\(date, 0, source\)/);
});
