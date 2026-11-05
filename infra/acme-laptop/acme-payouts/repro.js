'use strict';

// Meera's repro for the bizclock bug report, run against the bizclock that
// is installed here. Same contract as bizclock's examples/payout.js:
// prints "cutoff=<Day HH:MM> payout=<Day>", exits 0 only if payout is Friday.
const { payoutFor } = require('./src/schedule');

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const pad = (n) => String(n).padStart(2, '0');

// Order placed Friday 6 Nov 2026 at 17:10 local time, before the 17:30 cutoff.
const { settlesAt } = payoutFor({ id: 'ord_7F3', placedAt: new Date(2026, 10, 6, 17, 10) });
const payout = DAYS[settlesAt.getDay()];

console.log(`cutoff=${DAYS[settlesAt.getDay()]} ${pad(settlesAt.getHours())}:${pad(settlesAt.getMinutes())} payout=${payout}`);
process.exit(payout === 'Fri' ? 0 : 1);
