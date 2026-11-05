'use strict';

// When does an order get paid out?
//
//   node examples/payout.js
//
// Prints one line, "cutoff=<Day HH:MM> payout=<Day>", in local time.
// The order below is placed on Friday at 17:10, before the 17:30 cutoff, so
// the payout should be Friday. Exits 0 if it is, 1 otherwise.

const { nextCutoff } = require('..');

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const pad = (n) => String(n).padStart(2, '0');

const order = new Date(2026, 10, 6, 17, 10); // Fri 6 Nov 2026, 17:10 local
const cutoff = nextCutoff(order);
const payout = DAYS[cutoff.getDay()];

console.log(`cutoff=${DAYS[cutoff.getDay()]} ${pad(cutoff.getHours())}:${pad(cutoff.getMinutes())} payout=${payout}`);
process.exit(payout === 'Fri' ? 0 : 1);
