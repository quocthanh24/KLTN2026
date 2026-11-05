const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), ts = require('typescript');
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, filename);
const { calendarDate, recentRange, durationLabel } = require('../src/features/learning-analytics/contracts.ts');
test('analytics date uses UTC+7 across UTC midnight and year boundary', () => {
  assert.equal(calendarDate(new Date('2025-12-31T17:00:00Z')), '2026-01-01');
  assert.deepEqual(recentRange(7,new Date('2025-12-31T17:00:00Z')), {start:'2025-12-26',end:'2026-01-01'});
});
test('inclusive 30-day calendar range respects leap day', () => {
  assert.deepEqual(recentRange(30,new Date('2024-03-01T04:00:00Z')), {start:'2024-02-01',end:'2024-03-01'});
});
test('duration formatting preserves nonzero evidence and floors only aggregate display', () => {
  assert.equal(durationLabel(0),'0 giờ 0 phút');
  assert.equal(durationLabel(59_999_999),'Dưới 1 phút');
  assert.equal(durationLabel(60_000_000),'0 giờ 1 phút');
  assert.equal(durationLabel(3_659_999_999),'1 giờ 0 phút');
});
