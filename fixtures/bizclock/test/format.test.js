'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { formatCutoff } = require('..');

test('formatCutoff pads hours and minutes', () => {
  assert.equal(formatCutoff(new Date(2026, 10, 6, 9, 5)), 'Fri 09:05');
  assert.equal(formatCutoff(new Date(2026, 10, 9, 17, 30)), 'Mon 17:30');
});
