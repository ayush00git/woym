'use strict';

// bizclock: business-day cutoffs and payout dates.
//
// All times are wall-clock times in the host's local time zone, which is what
// a merchant dashboard shows. An order placed on a business day before the
// cutoff (default 17:30) is paid out that day; anything later rolls to the
// next business day's cutoff.

const HOUR_MS = 60 * 60 * 1000;
const MINUTE_MS = 60 * 1000;
const DAY_MS = 24 * HOUR_MS;

const DEFAULT_CUTOFF = '17:30';

function parseCutoff(value) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value));
  if (!match) {
    throw new TypeError(`bizclock: cutoff must look like "HH:MM", got ${JSON.stringify(value)}`);
  }
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) {
    throw new RangeError(`bizclock: cutoff out of range: ${value}`);
  }
  return { hours, minutes };
}

// Offset of the host zone from UTC at `date`, in hours.
function localOffsetHours(date) {
  return Math.round(-date.getTimezoneOffset() / 60);
}

function isBusinessDay(date) {
  const day = date.getDay();
  return day !== 0 && day !== 6;
}

/**
 * The cutoff an order placed at `date` falls under: the same day's cutoff if
 * the order is on a business day at or before it, else the next business
 * day's cutoff. Returns a Date.
 */
function nextCutoff(date, options = {}) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
    throw new TypeError('bizclock: nextCutoff() needs a valid Date');
  }
  const cutoff = parseCutoff(options.cutoff || DEFAULT_CUTOFF);

  // Shift into "local wall-clock" time so the UTC getters below read local
  // fields, then shift the result back.
  const offsetMs = localOffsetHours(date) * HOUR_MS;
  const local = date.getTime() + offsetMs;
  const wall = new Date(local);

  let candidate =
    Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth(), wall.getUTCDate()) +
    cutoff.hours * HOUR_MS +
    cutoff.minutes * MINUTE_MS;

  while (candidate < local || isWeekendUTC(candidate)) {
    candidate += DAY_MS;
  }
  return new Date(candidate - offsetMs);
}

function isWeekendUTC(ms) {
  const day = new Date(ms).getUTCDay();
  return day === 0 || day === 6;
}

module.exports = { nextCutoff, isBusinessDay, DEFAULT_CUTOFF };
module.exports.formatCutoff = require('./format').formatCutoff;
