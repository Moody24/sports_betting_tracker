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

    if (!data.ok) {
      if (statusEl) statusEl.textContent = data.error || 'Unavailable';
      return false;
    }

    if (currentEl) currentEl.textContent = formatNum(data.current_stat);
    if (statusEl) statusEl.textContent = data.status_text || 'Live';
    if (projEl) projEl.textContent = formatNum(data.projected_final);
    if (deltaEl) {
      const delta = Number(data.delta_to_line || 0);
      deltaEl.textContent = `Δ line: ${delta >= 0 ? '+' : ''}${formatNum(delta)}`;
    }

    if (barEl) {
      const pct = Math.max(0, Math.min(100, Number(data.progress_pct || 0)));
      barEl.style.width = `${pct}%`;
      barEl.setAttribute('aria-valuenow', String(Math.round(pct)));
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

  function initLiveProgress() {
    const cards = Array.from(document.querySelectorAll('[data-live-prop-card]'));
    if (!cards.length) return;

    cards.forEach((card) => pollCard(card));

    setInterval(() => {
      cards.forEach((card) => {
        if (card.dataset.pollingStopped === '1') return;
        pollCard(card);
      });
    }, 30000);
  }

  document.querySelectorAll('.parlay-toggle-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      const pid = this.dataset.parlayId;
      const legs = document.querySelector('[data-parlay-legs="' + pid + '"]');
      const open = this.getAttribute('aria-expanded') === 'true';
      if (legs) legs.style.display = open ? 'none' : '';
      this.setAttribute('aria-expanded', open ? 'false' : 'true');
      const icon = this.querySelector('.toggle-icon');
      if (icon) icon.style.transform = open ? 'rotate(180deg)' : '';
    });
  });

  document.querySelectorAll('.delete-bet-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!confirm('Delete this bet?')) e.preventDefault();
    });
  });

  initLiveProgress();
})();
