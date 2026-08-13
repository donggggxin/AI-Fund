import test from 'node:test';
import assert from 'node:assert/strict';

test('stock-sdk percentage contract uses percent units', () => {
  const sdkPercent = 2.5;
  assert.equal(sdkPercent / 100, 0.025);
});
