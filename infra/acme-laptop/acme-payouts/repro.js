'use strict';

// The customer's repro for the bizclock bug report, run against the bizclock
// that is installed here. Same contract as bizclock's examples/payout.js:
// prints "cutoff=<Day HH:MM> payout=<Day>", exits 0 only if payout is Friday.
const { payoutFor } = require('./src/schedule');

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const pad = (n) => String(n).padStart(2, '0');

// Order placed Friday 6 Nov 2026, local time, before the 17:30 cutoff: 17:10 by
// default (Meera); the image sets ACME_ORDER_AT for other customers (Kiran: 17:20).
const [hh, mm] = (process.env.ACME_ORDER_AT || '17:10').split(':').map(Number);
const { settlesAt } = payoutFor({ id: 'ord_7F3', placedAt: new Date(2026, 10, 6, hh, mm) });
const payout = DAYS[settlesAt.getDay()];

console.log(`cutoff=${DAYS[settlesAt.getDay()]} ${pad(settlesAt.getHours())}:${pad(settlesAt.getMinutes())} payout=${payout}`);
process.exit(payout === 'Fri' ? 0 : 1);
