(function () {
  'use strict';

  function initScoreStream() {
    var root = document.getElementById('nba-score-stream');
    if (!root) return;

    var sourceUrl = new URL(root.dataset.eventsUrl, window.location.origin);
    var feedUrl = new URL(root.dataset.feedUrl, window.location.origin);
    if (sourceUrl.origin !== window.location.origin || feedUrl.origin !== window.location.origin) return;

    var stateEl = document.getElementById('score-stream-state');
    var connectionEl = document.getElementById('score-stream-connection');
    var fetchedEl = document.getElementById('score-stream-fetched');
    var delayEl = document.getElementById('score-stream-delay');
    var gamesEl = document.getElementById('score-stream-games');
    var emptyEl = document.getElementById('score-stream-empty');
    var signInEl = document.getElementById('score-stream-sign-in');
    var source = null;
    var reconnectTimer = null;
    var pollTimer = null;
    var watchdogTimer = null;
    var requestTimeout = null;
    var requestController = null;
    var attempt = 0;
    var revision = -1;
    var requestGeneration = 0;
    var etag = null;
    var pollInterval = 30000;
    var authenticated = true;
    var suspended = false;
    var hasSnapshot = false;
    var gameCount = 0;
    var feedDisabled = false;
    var snapshotDate = null;
    var requiredDate = null;

    function visible() {
      return !document.hidden && !suspended && authenticated;
    }

    function showConnection(message) {
      if (connectionEl.textContent !== message) connectionEl.textContent = message;
    }

    function textNode(tag, className, value) {
      var node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = value;
      return node;
    }

    function validRevision(value) {
      if (typeof value !== 'number' && typeof value !== 'string') return null;
      if (typeof value === 'string' && !/^\d+$/.test(value)) return null;
      var number = Number(value);
      return Number.isSafeInteger(number) && number >= 0 ? number : null;
    }

    function dateLabel(value, includeTime) {
      if (!value) return null;
      var date = new Date(value);
      if (!Number.isFinite(date.getTime())) return null;
      return new Intl.DateTimeFormat('en-US', {
        timeZone: 'America/New_York', month: 'short', day: 'numeric',
        hour: includeTime ? 'numeric' : undefined,
        minute: includeTime ? '2-digit' : undefined,
        timeZoneName: includeTime ? 'short' : undefined
      }).format(date);
    }

    function score(value) {
      return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—';
    }

    function renderGames(games) {
      var fragment = document.createDocumentFragment();
      var labels = {
        scheduled: 'Scheduled', in_progress: 'In progress', final: 'Final',
        postponed: 'Postponed', canceled: 'Canceled', delayed: 'Delayed',
        suspended: 'Suspended', abandoned: 'Abandoned', unknown: 'Status unavailable'
      };
      games.slice(0, 50).forEach(function (game) {
        if (!game || typeof game !== 'object') return;
        var card = textNode('article', 'score-stream-card', '');
        var status = labels[game.status] || labels.unknown;
        var head = textNode('div', 'score-stream-card-head', '');
        head.appendChild(textNode('span', 'score-stream-game-state', status));
        var context = [];
        if (Number.isInteger(game.period) && game.period > 0) context.push('Period ' + game.period);
        if (typeof game.clock === 'string' && game.clock.trim()) context.push(game.clock.trim());
        if (!context.length) {
          var start = dateLabel(game.start_time, true);
          if (start) context.push(start);
          else if (typeof game.match_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(game.match_date)) context.push(game.match_date + ' ET');
        }
        head.appendChild(textNode('span', 'score-stream-game-time', context.join(' · ') || 'Time unavailable'));
        card.appendChild(head);
        ['away', 'home'].forEach(function (side) {
          var team = game[side] || {};
          var row = textNode('div', 'score-stream-team', '');
          var name = typeof team.name === 'string' && team.name ? team.name : (side === 'home' ? 'Home team' : 'Away team');
          row.appendChild(textNode('span', 'score-stream-team-name', name));
          var number = textNode('strong', 'score-stream-score', score(team.score));
          number.setAttribute('aria-label', name + ' score ' + (team.score == null ? 'unavailable' : score(team.score)));
          row.appendChild(number);
          card.appendChild(row);
        });
        fragment.appendChild(card);
      });
      gamesEl.replaceChildren(fragment);
      gameCount = gamesEl.childElementCount;
    }

    function renderFreshness(payload) {
      var freshness = payload.freshness || payload;
      var state = freshness.state || payload.state;
      var label = { disabled: 'Not configured', current: 'Current', stale: 'Stale', unavailable: 'Unavailable' }[state] || 'Unavailable';
      root.dataset.feedState = state || 'unavailable';
      feedDisabled = state === 'disabled';
      if (state === 'unavailable' || feedDisabled) {
        renderGames([]);
        hasSnapshot = false;
      }
      if (feedDisabled) {
        closeSource();
        clearTimers();
        showConnection('Automatic score feed is not configured.');
      }
      stateEl.textContent = label;
      var timestamp = freshness.last_success_at || payload.last_success_at;
      var fetched = dateLabel(timestamp, true);
      fetchedEl.textContent = fetched ? 'Last successful fetch: ' + fetched : 'No successful fetch recorded';
      if (fetched) fetchedEl.setAttribute('datetime', timestamp);
      else fetchedEl.removeAttribute('datetime');
      delayEl.textContent = payload.provider_delay || freshness.provider_delay || 'Provider delay not guaranteed';
      emptyEl.hidden = gameCount > 0;
      if (state === 'disabled') {
        emptyEl.textContent = 'Free score feed not configured. Add a balldontlie API key to enable NBA scores and schedules.';
      } else if (state === 'current') {
        emptyEl.textContent = 'No games in the current feed. This is the latest successfully fetched schedule.';
      } else if (state === 'stale') {
        emptyEl.textContent = 'The score feed is stale. No games are available in the last saved snapshot.';
      } else {
        emptyEl.textContent = 'Score feed unavailable. Waiting for a successful provider fetch.';
      }
    }

    function applySnapshot(payload, reset) {
      if (payload && payload.ok === true && payload.data) payload = payload.data;
      if (!payload || typeof payload !== 'object' || !Array.isArray(payload.games)) throw new Error('Invalid score snapshot');
      var nextDate = typeof payload.match_date === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(payload.match_date) ? payload.match_date : null;
      if (requiredDate && nextDate && nextDate < requiredDate) return;
      var next = validRevision(payload.revision);
      if (next === null) throw new Error('Invalid score revision');
      if (!reset && next < revision) return;
      if (reset) {
        requestGeneration += 1;
        if (requestController) requestController.abort();
        etag = null;
      }
      if (next !== revision || reset || !hasSnapshot || nextDate !== snapshotDate) renderGames(payload.games);
      snapshotDate = nextDate;
      if (nextDate) requiredDate = nextDate;
      revision = next;
      hasSnapshot = true;
      root.dataset.revision = String(revision);
      var interval = Number(payload.poll_interval_seconds);
      if (Number.isFinite(interval)) pollInterval = Math.min(300000, Math.max(30000, interval * 1000));
      renderFreshness(payload);
    }

    function closeSource() {
      if (source) source.close();
      source = null;
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }

    function clearTimers() {
      clearTimeout(reconnectTimer);
      clearTimeout(pollTimer);
      clearTimeout(watchdogTimer);
      reconnectTimer = pollTimer = watchdogTimer = null;
    }

    function stopRequests() {
      requestGeneration += 1;
      if (requestController) requestController.abort();
      requestController = null;
      clearTimeout(requestTimeout);
      requestTimeout = null;
    }

    function requireSignIn() {
      authenticated = false;
      closeSource();
      clearTimers();
      stopRequests();
      showConnection('Session ended. Sign in to reconnect.');
      signInEl.hidden = false;
    }

    function schedulePoll(delay) {
      if (!visible() || feedDisabled || pollTimer !== null || requestController) return;
      pollTimer = setTimeout(function () {
        pollTimer = null;
        fetchSnapshot();
      }, delay);
    }

    function fetchSnapshot(initialCheck) {
      if (!visible() || requestController) return;
      var controller = new AbortController();
      requestController = controller;
      var generation = requestGeneration;
      var headers = { Accept: 'application/json' };
      if (etag) headers['If-None-Match'] = etag;
      requestTimeout = setTimeout(function () { controller.abort(); }, 10000);
      fetch(feedUrl.href, { credentials: 'same-origin', cache: 'no-store', headers: headers, signal: controller.signal })
        .then(function (response) {
          if (!visible() || generation !== requestGeneration) return null;
          if (response.status === 401 || response.status === 403 || (response.redirected && /\/auth\//.test(new URL(response.url).pathname))) {
            requireSignIn();
            return null;
          }
          if (response.status === 304) return null;
          if (!response.ok) throw new Error('Score feed request failed');
          etag = response.headers.get('ETag');
          return response.json();
        })
        .then(function (payload) {
          if (!payload || !visible() || generation !== requestGeneration) return;
          applySnapshot(payload, false);
          if (!feedDisabled && (!source || source.readyState !== 1)) showConnection('Updates via ' + Math.round(pollInterval / 1000) + '-second checks');
        })
        .catch(function (error) {
          if (!visible() || generation !== requestGeneration) return;
          showConnection(hasSnapshot ? 'Connection interrupted. Showing the last received snapshot.' : 'Unable to connect. Retrying automatically.');
        })
        .finally(function () {
          if (requestController !== controller) return;
          clearTimeout(requestTimeout);
          requestTimeout = null;
          requestController = null;
          if (initialCheck && visible() && !feedDisabled) connect();
          if (visible() && !feedDisabled && (!source || source.readyState !== 1)) schedulePoll(pollInterval);
        });
    }

    function scheduleReconnect() {
      if (!visible() || feedDisabled || reconnectTimer !== null || !window.EventSource) return;
      var ceiling = Math.min(30000, 1000 * Math.pow(2, Math.min(attempt, 5)));
      var delay = Math.min(30000, Math.max(1000, Math.round(ceiling * (0.8 + Math.random() * 0.4))));
      attempt += 1;
      reconnectTimer = setTimeout(function () {
        reconnectTimer = null;
        connect();
      }, delay);
    }

    function lostConnection(active) {
      if (source !== active) return;
      closeSource();
      if (!visible()) return;
      showConnection('Stream interrupted. Reconnecting; checking the saved feed meanwhile.');
      schedulePoll(0);
      scheduleReconnect();
    }

    function heardFromStream(active) {
      if (source !== active || !visible()) return false;
      attempt = 0;
      clearTimeout(pollTimer);
      pollTimer = null;
      clearTimeout(watchdogTimer);
      watchdogTimer = setTimeout(function () { lostConnection(active); }, 45000);
      showConnection('Stream connected');
      return true;
    }

    function connect() {
      if (!visible() || feedDisabled || source) return;
      if (!window.EventSource) {
        showConnection('Stream unavailable in this browser. Checking the saved feed.');
        schedulePoll(pollInterval);
        return;
      }
      showConnection('Connecting to score stream…');
      var url = new URL(sourceUrl.href);
      if (revision >= 0) url.searchParams.set('last_event_id', String(revision));
      var active;
      try {
        active = new EventSource(url.href);
      } catch (_) {
        schedulePoll(0);
        scheduleReconnect();
        return;
      }
      source = active;
      watchdogTimer = setTimeout(function () { lostConnection(active); }, 45000);
      ['snapshot', 'reset'].forEach(function (kind) {
        active.addEventListener(kind, function (event) {
          if (source !== active || !visible()) return;
          try {
            applySnapshot(JSON.parse(event.data), kind === 'reset');
            heardFromStream(active);
          } catch (_) {
            lostConnection(active);
          }
        });
      });
      active.addEventListener('heartbeat', function (event) {
        if (!heardFromStream(active)) return;
        try {
          var heartbeat = JSON.parse(event.data);
          if (heartbeat.freshness && validRevision(heartbeat.revision) === revision) {
            var currentDate = heartbeat.freshness.match_date;
            if (snapshotDate && typeof currentDate === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(currentDate) && currentDate > snapshotDate) {
              requiredDate = currentDate;
              renderGames([]);
              hasSnapshot = false;
              stopRequests();
              fetchSnapshot(false);
            }
            renderFreshness({ freshness: heartbeat.freshness });
          }
          if (heartbeat.stream && heartbeat.stream.state === 'unavailable') {
            showConnection('Stream connected; saved score data temporarily unavailable.');
          }
        } catch (_) {
          lostConnection(active);
        }
      });
      active.addEventListener('error', function () { lostConnection(active); });
    }

    function pause() {
      closeSource();
      clearTimers();
      stopRequests();
      if (authenticated) showConnection('Updates paused while this page is hidden.');
    }

    document.addEventListener('visibilitychange', function () {
      if (document.hidden) pause();
      else if (feedDisabled || !hasSnapshot) fetchSnapshot(true);
      else connect();
    });
    window.addEventListener('pagehide', function () {
      suspended = true;
      pause();
    });
    window.addEventListener('pageshow', function (event) {
      if (event.persisted) {
        suspended = false;
        if (feedDisabled || !hasSnapshot) fetchSnapshot(true);
        else connect();
      }
    });
    fetchSnapshot(true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initScoreStream);
  else initScoreStream();
})();
