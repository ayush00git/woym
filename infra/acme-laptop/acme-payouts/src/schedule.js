'use strict';

// Acme's payout scheduler: when does a merchant's order settle?
const { nextCutoff } = require('bizclock');

function payoutFor(order) {
  return { orderId: order.id, settlesAt: nextCutoff(order.placedAt) };
}

module.exports = { payoutFor };
