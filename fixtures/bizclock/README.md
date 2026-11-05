# bizclock

Business-day cutoffs and payout dates for Node. Zero dependencies.

```js
const { nextCutoff } = require('bizclock');

// Order placed Friday 17:10 local time, default cutoff 17:30
nextCutoff(new Date(2026, 10, 6, 17, 10)); // -> Fri 6 Nov 2026, 17:30 local
```

## API

### `nextCutoff(date, { cutoff = '17:30' } = {})`

Returns the cutoff (a `Date`) that an order placed at `date` falls under:

- on a business day, at or before the cutoff: that day's cutoff;
- after the cutoff, or on a weekend: the next business day's cutoff.

Payouts settle at the cutoff, so the payout day is the day of the returned date.

### `isBusinessDay(date)`

`true` Monday to Friday.

## Time zones

Cutoffs are wall-clock times in the host's local time zone (`TZ`), the same
time a merchant sees on their dashboard.

## Example

```sh
node examples/payout.js
# cutoff=Fri 17:30 payout=Fri
```

## License

MIT
