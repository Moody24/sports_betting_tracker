(function () {
  'use strict';

  function postEvent(body) {
    try {
      var payload = JSON.stringify(body);
      if (navigator.sendBeacon) {
        var blob = new Blob([payload], { type: 'application/json' });
        navigator.sendBeacon('/telemetry/ux', blob);
        return;
      }
      fetch('/telemetry/ux', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: payload,
        keepalive: true,
      }).catch(function () {});
    } catch (_err) {
      // Never break page behavior if telemetry fails.
    }
  }

  window.trackUxEvent = function trackUxEvent(eventName, meta) {
    if (!eventName) return;
    postEvent({
      event: String(eventName),
      page: window.location.pathname,
      meta: meta || {},
      ts: Date.now(),
    });
  };
})();
