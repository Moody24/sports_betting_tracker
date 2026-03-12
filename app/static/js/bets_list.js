(function () {
  function formatNum(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return Number(value).toFixed(1).replace(/\.0$/, '');
  }

  function trendMessage(data) {
    const isOver = data.bet_type === 'over';
    const projected = Number(data.projected_final || 0);
    const line = Number(data.line || 0);
    const diff = projected - line;

    if (!line) return 'Tracking';

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
    const currentEl = card.querySelector('[data-live-current]');
    const statusEl = card.querySelector('[data-live-status]');
    const barEl = card.querySelector('[data-live-bar]');
    const projEl = card.querySelector('[data-live-proj]');
    const deltaEl = card.querySelector('[data-live-delta]');
    const trendEl = card.querySelector('[data-live-trend]');
    const periodEl = card.querySelector('[data-live-period]');
    const clockEl = card.querySelector('[data-live-clock]');
    const stateEl = card.querySelector('[data-live-state]');

    if (!data.ok) {
      if (trendEl) { trendEl.className = 'badge live-trend text-bg-secondary'; trendEl.textContent = data.error || 'Unavailable'; }
      return false;
    }

    // Reveal detail sections once real data arrives
    card.querySelectorAll('[data-live-details],[data-live-details-bar],[data-live-details-meta]').forEach(function (el) { el.removeAttribute('hidden'); });

    // Upgrade header to live style once we have data
    var headerLabel = card.querySelector('.text-secondary .bi-clock-history');
    if (headerLabel) {
      var parent = headerLabel.parentElement;
      parent.className = parent.className.replace('text-secondary', 'text-info');
      headerLabel.className = headerLabel.className.replace('bi-clock-history', 'bi-broadcast');
    }

    if (currentEl) currentEl.textContent = formatNum(data.current_stat);
    if (statusEl) statusEl.textContent = data.status_text || 'Live';
    if (projEl) projEl.textContent = formatNum(data.projected_final);
    if (periodEl) periodEl.textContent = `Period: ${data.period || '—'}`;
    if (clockEl) clockEl.textContent = `Clock: ${data.clock || '—'}`;
    if (stateEl) stateEl.textContent = `State: ${data.game_state || 'unknown'}`;
    if (deltaEl) {
      const delta = Number(data.delta_to_line || 0);
      deltaEl.textContent = `Δ line: ${delta >= 0 ? '+' : ''}${formatNum(delta)}`;
    }

    if (barEl) {
      const pct = Math.max(0, Math.min(100, Number(data.progress_pct || 0)));
      barEl.style.width = `${pct}%`;
      const progressEl = card.querySelector('[data-live-progress]');
      if (progressEl) progressEl.setAttribute('aria-valuenow', String(Math.round(pct)));
    }

    if (trendEl) {
      const trend = trendMessage(data);
      trendEl.className = `badge live-trend ${trend.cls}`;
      trendEl.textContent = trend.text;
    }

    card.dataset.gameState = data.game_state || 'unknown';
    return data.game_state !== 'final';
  }

  function pollCard(card) {
    const url = card.dataset.url;
    if (!url) return;
    fetch(url)
      .then((r) => r.json())
      .then((data) => {
        const shouldContinue = applyProgressCard(card, data);
        if (!shouldContinue) {
          card.dataset.pollingStopped = '1';
        }
      })
      .catch(() => {
        const statusEl = card.querySelector('[data-live-status]');
        if (statusEl) statusEl.textContent = 'Live update failed';
      });
  }

  function buildBatchDescriptors(cards) {
    return cards
      .filter((c) => c.dataset.pollingStopped !== '1')
      .map((c) => {
        const url = c.dataset.url || '';
        const match = url.match(/\/nba\/prop-progress\/([^?]+)/);
        const espnId = match ? match[1] : '';
        const params = new URLSearchParams(url.split('?')[1] || '');
        return {
          card_id: c.dataset.cardId || url,
          espn_id: espnId,
          player: params.get('player') || '',
          prop_type: params.get('prop_type') || '',
          line: parseFloat(params.get('line') || '0'),
          bet_type: params.get('bet_type') || '',
        };
      })
      .filter((d) => d.espn_id && d.player && d.prop_type);
  }

  function pollBatch(cards) {
    const descriptors = buildBatchDescriptors(cards);
    if (!descriptors.length) return;

    fetch('/nba/prop-progress/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(descriptors),
    })
      .then((r) => r.json())
      .then((results) => {
        cards.forEach((card) => {
          if (card.dataset.pollingStopped === '1') return;
          const key = card.dataset.cardId || card.dataset.url || '';
          const data = results[key];
          if (!data) return;
          const shouldContinue = applyProgressCard(card, data);
          if (!shouldContinue) {
            card.dataset.pollingStopped = '1';
          }
        });
      })
      .catch(() => {
        cards.forEach((card) => {
          const statusEl = card.querySelector('[data-live-status]');
          if (statusEl) statusEl.textContent = 'Live update failed';
        });
      });
  }

  function initLiveProgress() {
    const cards = Array.from(document.querySelectorAll('[data-live-prop-card]'));
    if (!cards.length) return;

    // Assign stable card IDs so the batch response can be matched back
    cards.forEach((card, i) => {
      if (!card.dataset.cardId) {
        card.dataset.cardId = card.dataset.url || String(i);
      }
    });

    pollBatch(cards);
    setInterval(() => pollBatch(cards), 30000);
  }

  document.querySelectorAll('.parlay-toggle-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const pid = this.dataset.parlayId;
      const legs = document.querySelector('[data-parlay-legs="' + pid + '"]');
      const open = this.getAttribute('aria-expanded') === 'true';
      if (legs) legs.hidden = open;
      const nextExpanded = open ? 'false' : 'true';
      this.setAttribute('aria-expanded', nextExpanded);
      this.setAttribute('aria-label', (open ? 'Expand' : 'Collapse') + ' parlay legs');
      const icon = this.querySelector('.toggle-icon');
      if (icon) icon.style.transform = open ? 'rotate(180deg)' : '';
    });
  });

  initLiveProgress();
})();
