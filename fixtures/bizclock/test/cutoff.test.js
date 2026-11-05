'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { nextCutoff, isBusinessDay } = require('..');

// Months are 0-based: 10 is November. 2026-11-04 is a Wednesday.
const local = (d, h, m) => new Date(2026, 10, d, h, m);

test('an order before the cutoff is paid the same day', () => {
  assert.deepEqual(nextCutoff(local(4, 10, 0)), local(4, 17, 30));
});

test('an order exactly at the cutoff is still same-day', () => {
  assert.deepEqual(nextCutoff(local(4, 17, 30)), local(4, 17, 30));
});

test('an order after the cutoff rolls to the next day', () => {
  assert.deepEqual(nextCutoff(local(4, 18, 5)), local(5, 17, 30));
});

test('Friday after the cutoff rolls over the weekend to Monday', () => {
  assert.deepEqual(nextCutoff(local(6, 19, 0)), local(9, 17, 30));
});

test('weekend orders go to Monday', () => {
  assert.deepEqual(nextCutoff(local(7, 9, 0)), local(9, 17, 30));
  assert.deepEqual(nextCutoff(local(8, 23, 59)), local(9, 17, 30));
});

test('custom cutoff time', () => {
  assert.deepEqual(nextCutoff(local(4, 11, 0), { cutoff: '12:00' }), local(4, 12, 0));
  assert.deepEqual(nextCutoff(local(4, 12, 1), { cutoff: '12:00' }), local(5, 12, 0));
});

test('rejects bad input', () => {
  assert.throws(() => nextCutoff(new Date('nope')), TypeError);
  assert.throws(() => nextCutoff(local(4, 10, 0), { cutoff: '5pm' }), TypeError);
  assert.throws(() => nextCutoff(local(4, 10, 0), { cutoff: '24:00' }), RangeError);
});

test('isBusinessDay', () => {
  assert.equal(isBusinessDay(local(6, 12, 0)), true);
  assert.equal(isBusinessDay(local(7, 12, 0)), false);
});
