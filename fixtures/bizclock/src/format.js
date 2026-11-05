'use strict';

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

function pad(n) {
  return String(n).padStart(2, '0');
}

// "Fri 17:30", in the host's local time zone.
function formatCutoff(date) {
  return `${DAYS[date.getDay()]} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

module.exports = { formatCutoff };
