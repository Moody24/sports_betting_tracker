(function () {
  function formatNum(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return Number(value).toFixed(1).replace(/\.0$/, '');
  }

  function trendMessage(data) {
    var isOver = data.bet_type === 'over';
    var projected = Number(data.projected_final || 0);
    var line = Number(data.line || 0);
    var diff = projected - line;

    if (!line) return { text: 'Tracking', cls: 'text-bg-secondary' };

    if (isOver) {
      if (diff >= 2.5) return { text: 'On pace to clear', cls: 'text-bg-success' };
      if (diff >= 0.5) return { text: 'Close to line', cls: 'text-bg-info' };
      if (diff > -1.5) return { text: 'Borderline pace', cls: 'text-bg-warning text-dark' };
      return { text: 'Pace against over', cls: 'text-bg-danger' };
    }

    if (diff <= -2.5) return { text: 'Comfortably below line', cls: 'text-bg-success' };
    if (diff <= -0.5) return { text: 'Pace under line', cls: 'text-bg-info' };
    if (diff < 1.5) return { text: 'Close to line', cls: 'text-bg-warning text-dark' };
    return { text: 'Pace against under', cls: 'text-bg-danger' };
  }

  function applyProgressCard(card, data) {
    var currentEl = card.querySelector('[data-live-current]');
    var statusEl = card.querySelector('[data-live-status]');
    var barEl = card.querySelector('[data-live-bar]');
    var projEl = card.querySelector('[data-live-proj]');
    var deltaEl = card.querySelector('[data-live-delta]');
    var trendEl = card.querySelector('[data-live-trend]');
    var periodEl = card.querySelector('[data-live-period]');
    var clockEl = card.querySelector('[data-live-clock]');
    var stateEl = card.querySelector('[data-live-state]');

    if (!data.ok) {
      if (trendEl) { trendEl.className = 'badge live-trend text-bg-secondary'; trendEl.textContent = data.error || 'Unavailable'; }
      return false;
    }

    card.querySelectorAll('[data-live-details],[data-live-details-bar],[data-live-details-meta]').forEach(function (el) { el.removeAttribute('hidden'); });

    if (currentEl) currentEl.textContent = formatNum(data.current_stat);
    if (statusEl) statusEl.textContent = data.status_text || 'Live';
    if (projEl) projEl.textContent = formatNum(data.projected_final);
    if (periodEl) periodEl.textContent = 'Period: ' + (data.period || '—');
    if (clockEl) clockEl.textContent = 'Clock: ' + (data.clock || '—');
    if (stateEl) stateEl.textContent = 'State: ' + (data.game_state || 'unknown');
    if (deltaEl) {
      var delta = Number(data.delta_to_line || 0);
      deltaEl.textContent = 'Δ line: ' + (delta >= 0 ? '+' : '') + formatNum(delta);
    }

    if (barEl) {
      var pct = Math.max(0, Math.min(100, Number(data.progress_pct || 0)));
      barEl.style.width = pct + '%';
      var progressEl = card.querySelector('[data-live-progress]');
      if (progressEl) progressEl.setAttribute('aria-valuenow', String(Math.round(pct)));
    }

    if (trendEl) {
      var trend = trendMessage(data);
      trendEl.className = 'badge live-trend ' + trend.cls;
      trendEl.textContent = trend.text;
    }

    card.dataset.gameState = data.game_state || 'unknown';
    return data.game_state !== 'final';
  }

  function buildBatchDescriptors(cards) {
    return cards
      .filter(function (c) { return c.dataset.pollingStopped !== '1'; })
      .map(function (c) {
        var url = c.dataset.url || '';
        var match = url.match(/\/nba\/prop-progress\/([^?]+)/);
        var espnId = match ? match[1] : '';
        var params = new URLSearchParams(url.split('?')[1] || '');
        return {
          card_id: c.dataset.cardId || url,
          espn_id: espnId,
          player: params.get('player') || '',
          prop_type: params.get('prop_type') || '',
          line: parseFloat(params.get('line') || '0'),
          bet_type: params.get('bet_type') || '',
        };
      })
      .filter(function (d) { return d.espn_id && d.player && d.prop_type; });
  }

  function pollBatch(cards) {
    var descriptors = buildBatchDescriptors(cards);
    if (!descriptors.length) return;

    fetch('/nba/prop-progress/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(descriptors),
    })
      .then(function (r) { return r.json(); })
      .then(function (results) {
        cards.forEach(function (card) {
          if (card.dataset.pollingStopped === '1') return;
          var key = card.dataset.cardId || card.dataset.url || '';
          var data = results[key];
          if (!data) return;
          var shouldContinue = applyProgressCard(card, data);
          if (!shouldContinue) card.dataset.pollingStopped = '1';
        });
      })
      .catch(function () {
        cards.forEach(function (card) {
          var statusEl = card.querySelector('[data-live-status]');
          if (statusEl) statusEl.textContent = 'Live update failed';
        });
      });
  }

  function initLiveProgress() {
    var cards = Array.from(document.querySelectorAll('[data-live-prop-card]'));
    if (!cards.length) return;
    cards.forEach(function (card, i) {
      if (!card.dataset.cardId) card.dataset.cardId = card.dataset.url || String(i);
    });
    pollBatch(cards);
    setInterval(function () { pollBatch(cards); }, 30000);
  }

  function initFilterState() {
    var form = document.getElementById('bets-filter-form');
    if (!form || typeof createFilterStateManager !== 'function') return;

    var manager = createFilterStateManager({
      storage: 'local',
      storageKey: 'sbt_bets_list_filters_v1',
      keys: ['status', 'type', 'start_date', 'end_date', 'q'],
    });

    var activeWrap = document.getElementById('bets-active-filters');
    var saveBtn = document.getElementById('bets-save-view');
    var resetBtn = document.getElementById('bets-reset-view');

    var labels = {
      status: 'Status',
      type: 'Type',
      start_date: 'From',
      end_date: 'To',
      q: 'Search',
    };

    function getStateFromForm() {
      var data = new FormData(form);
      var state = {};
      manager.keys.forEach(function (k) {
        state[k] = String(data.get(k) || '').trim();
      });
      return state;
    }

    function applyStateToForm(state) {
      manager.keys.forEach(function (k) {
        var el = form.elements[k];
        if (el) el.value = state[k] || '';
      });
    }

    function buildQuery(state) {
      var params = new URLSearchParams();
      manager.keys.forEach(function (k) {
        var val = String(state[k] || '').trim();
        if (val) params.set(k, val);
      });
      return params;
    }

    function formatChipValue(key, value) {
      if (!value) return '';
      var field = form.elements[key];
      if (field && field.tagName === 'SELECT') {
        var opt = field.options[field.selectedIndex];
        if (opt && opt.textContent) return opt.textContent.trim();
      }
      return value;
    }

    function renderChips(state) {
      if (!activeWrap) return;
      activeWrap.replaceChildren();
      var activeKeys = manager.keys.filter(function (k) { return state[k]; });
      if (!activeKeys.length) {
        var empty = document.createElement('span');
        empty.className = 'filter-chip-empty';
        empty.textContent = 'No active filters';
        activeWrap.appendChild(empty);
        return;
      }

      activeKeys.forEach(function (key) {
        var chip = document.createElement('span');
        chip.className = 'filter-chip';

        var label = document.createElement('span');
        label.className = 'filter-chip-label';
        label.textContent = labels[key] + ': ' + formatChipValue(key, state[key]);
        chip.appendChild(label);

        var removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'filter-chip-remove';
        removeBtn.dataset.key = key;
        removeBtn.setAttribute('aria-label', 'Remove ' + labels[key] + ' filter');
        removeBtn.title = 'Remove ' + labels[key] + ' filter';
        removeBtn.innerHTML = '<span aria-hidden="true">×</span>';
        chip.appendChild(removeBtn);

        activeWrap.appendChild(chip);
      });
    }

    var urlState = manager.getUrlState();
    if (Object.keys(urlState).length > 0) {
      manager.saveState(urlState);
      applyStateToForm(urlState);
      renderChips(getStateFromForm());
    } else {
      var saved = manager.readSavedState();
      if (Object.keys(saved).length > 0) {
        var params = buildQuery(saved);
        window.location.assign(window.location.pathname + '?' + params.toString());
        return;
      }
      renderChips(getStateFromForm());
    }

    if (saveBtn) {
      saveBtn.addEventListener('click', function () {
        var state = getStateFromForm();
        manager.saveState(state);
        renderChips(state);
      });
    }

    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        manager.clearState();
        window.location.assign(window.location.pathname);
      });
    }

    if (activeWrap) {
      activeWrap.addEventListener('click', function (e) {
        var btn = e.target.closest('.filter-chip-remove');
        if (!btn) return;
        var key = btn.dataset.key;
        if (!key || !form.elements[key]) return;
        form.elements[key].value = '';
        var state = getStateFromForm();
        manager.saveState(state);
        var params = buildQuery(state);
        var next = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
        window.location.assign(next);
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.parlay-toggle-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var pid = this.dataset.parlayId;
        var legs = document.querySelector('[data-parlay-legs="' + pid + '"]');
        var open = this.getAttribute('aria-expanded') === 'true';
        if (legs) legs.hidden = open;
        var nextExpanded = open ? 'false' : 'true';
        this.setAttribute('aria-expanded', nextExpanded);
        this.setAttribute('aria-label', (open ? 'Expand' : 'Collapse') + ' parlay legs');
        var icon = this.querySelector('.toggle-icon');
        if (icon) icon.style.transform = open ? 'rotate(180deg)' : '';
      });
    });

    initFilterState();
    initLiveProgress();
  });
})();
