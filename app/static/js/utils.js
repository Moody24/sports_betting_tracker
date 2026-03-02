/**
 * Shared utilities — loaded before betslip.js and bet_builder.js.
 */

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

var PARLAY_QUEUE_STORAGE_KEY = 'sbt_parlay_queue_v1';

function normalizeLegValue(value) {
  return String(value ?? '').trim();
}

function normalizePropLine(value) {
  var numeric = Number(value);
  return Number.isFinite(numeric) ? String(numeric) : normalizeLegValue(value);
}

function legSignature(leg) {
  if (!leg) return '';
  return [
    normalizeLegValue(leg.game_id),
    normalizeLegValue(leg.team_a),
    normalizeLegValue(leg.team_b),
    normalizeLegValue(leg.match_date),
    normalizeLegValue(leg.player_name),
    normalizeLegValue(leg.prop_type),
    normalizePropLine(leg.prop_line),
    normalizeLegValue(leg.bet_type).toLowerCase(),
  ].join('|');
}

function getParlayQueue() {
  try {
    var raw = sessionStorage.getItem(PARLAY_QUEUE_STORAGE_KEY);
    if (!raw) return [];
    var parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_) {
    return [];
  }
}

function setParlayQueue(queue) {
  var safeQueue = Array.isArray(queue) ? queue : [];
  try {
    sessionStorage.setItem(PARLAY_QUEUE_STORAGE_KEY, JSON.stringify(safeQueue));
  } catch (_) {}
}

function addParlayLeg(leg) {
  if (!leg || typeof leg !== 'object') return getParlayQueue();
  var queue = getParlayQueue();
  var signature = legSignature(leg);
  if (!signature) return queue;
  var exists = queue.some(function (queuedLeg) {
    return legSignature(queuedLeg) === signature;
  });
  if (!exists) {
    queue.push(leg);
    setParlayQueue(queue);
  }
  return queue;
}

function removeParlayLeg(signature) {
  var queue = getParlayQueue().filter(function (leg) {
    return legSignature(leg) !== signature;
  });
  setParlayQueue(queue);
  return queue;
}

function clearParlayQueue() {
  try {
    sessionStorage.removeItem(PARLAY_QUEUE_STORAGE_KEY);
  } catch (_) {}
}
