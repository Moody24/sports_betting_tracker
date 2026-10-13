(function () {
  function formatNum(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
    return Number(value).toFixed(1).replace(/\.0$/, '');
  }

  // One verdict, two renderings. The tag and the pace fill both derive from
  // `tone`, so they cannot disagree about whether a bet is going well — a
  // green bar under a losing trend is a lie the eye believes before it reads
  // the number, and that bug has already shipped here once.
  const TONE = {
    good: { tag: 'tag-win', pace: 'pace-good' },
    neutral: { tag: 'tag-push', pace: 'pace-neutral' },
    warn: { tag: 'tag-live', pace: 'pace-warn' },
    bad: { tag: 'tag-loss', pace: 'pace-bad' },
  };

  function trendMessage(data) {
    const isOver = data.bet_type === 'over';
    const projected = Number(data.projected_final || 0);
    const line = Number(data.line || 0);
    const diff = projected - line;

    if (!line) return { text: 'Tracking', tone: 'neutral' };

    if (isOver) {
      if (diff >= 2.5) return { text: 'On pace to clear', tone: 'good' };
      if (diff >= 0.5) return { text: 'Close to line', tone: 'neutral' };
      if (diff > -1.5) return { text: 'Borderline pace', tone: 'warn' };
      return { text: 'Pace against over', tone: 'bad' };
    }

    if (diff <= -2.5) return { text: 'Comfortably below line', tone: 'good' };
    if (diff <= -0.5) return { text: 'Pace under line', tone: 'neutral' };
    if (diff < 1.5) return { text: 'Close to line', tone: 'warn' };
    return { text: 'Pace against under', tone: 'bad' };
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
      const retryable = data.retryable === true || data.status === 'game_not_started';
      if (trendEl) {
        // The tag is a closed vocabulary of short labels, so it gets a fixed
        // one. The server's reason can be a whole sentence ("Player not found
        // in boxscore for ...") and putting that inside a nowrap pill pushed
        // the document sideways at 320px. It goes to its own wrapping line.
        trendEl.className = `tag ${TONE.neutral.tag}`;
        trendEl.textContent = retryable ? 'Retrying' : 'Unavailable';
      }
      if (retryable && statusEl) statusEl.textContent = 'Live update delayed; retrying';
      const errEl = card.querySelector('[data-live-error]');
      if (errEl && data.error) {
        errEl.textContent = data.error;
        errEl.removeAttribute('hidden');
      }
      return retryable;
    }

    const errEl = card.querySelector('[data-live-error]');
    if (errEl) errEl.setAttribute('hidden', '');
    // Reveal detail sections once real data arrives
    card.querySelectorAll('[data-live-details],[data-live-details-bar],[data-live-details-meta]').forEach(function (el) { el.removeAttribute('hidden'); });

    // NOTE: a block here used to "upgrade the header to live style" by finding
    // a Bootstrap clock-history icon and swapping it for a broadcast one. No
    // Bootstrap Icons stylesheet or font is loaded, and the icon macro emits
    // <svg class="icon">, so no such class has ever been in the DOM — the
    // selector matched nothing and the block never ran. Removed rather than
    // half-fixed: the intent (mark a row as gone-live) is real and belongs to
    // the Position Log row component, which owns that state.

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

    // The pace verdict is computed once and drives both the badge and the
    // bar. Previously only the badge used it, so "on pace" and "pace under
    // line" rendered as the same full-contrast bar — the loudest element on
    // the row saying nothing.
    const trend = trendMessage(data);
    const tone = TONE[trend.tone] || TONE.neutral;

    if (barEl) {
      // The axis carries three facts, so it cannot simply be "percent of the
      // line": that would pin the line at the right edge, where it says
      // nothing. Instead the axis spans far enough to hold both where the
      // player is and where they project to finish; the fill is the current
      // stat, and .pace-mark sits at the line. The gap between mark and fill
      // is then readable as the margin, in either direction.
      const pct = Math.max(0, Number(data.progress_pct || 0));
      const line = Number(data.line || 0);
      const projectedPct = line ? (Number(data.projected_final || 0) / line) * 100 : 0;
      const scale = Math.max(pct, projectedPct, 100) * 1.05;

      barEl.style.setProperty('--v', `${(pct / scale) * 100}%`);
      barEl.className = `pace-fill ${tone.pace}`;

      const progressEl = card.querySelector('[data-live-progress]');
      if (progressEl) {
        progressEl.style.setProperty('--x', `${(100 / scale) * 100}%`);
        progressEl.setAttribute('aria-valuenow', String(Math.round(pct)));
      }
    }

    if (trendEl) {
      trendEl.className = `tag ${tone.tag}`;
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
      .sort((a, b) => Number(b.dataset.progressDeferred === '1') - Number(a.dataset.progressDeferred === '1'))
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

  function progressBatches(descriptors) {
    // Keep aligned with app/utils/request_validation.py.
    const maxItems = 100;
    const maxGames = 15;
    const batches = [];
    let batch = [];
    let games = new Set();
    descriptors.forEach((descriptor) => {
      if (batch.length >= maxItems || (!games.has(descriptor.espn_id) && games.size >= maxGames)) {
        batches.push(batch);
        batch = [];
        games = new Set();
      }
      batch.push(descriptor);
      games.add(descriptor.espn_id);
    });
    if (batch.length) batches.push(batch);
    return batches;
  }

  async function pollBatch(cards) {
    const descriptors = buildBatchDescriptors(cards);
    if (!descriptors.length) return true;
    const results = Object.create(null);
    let failed = false;

    try {
      // Finish one request before starting the next, including retries.
      for (const batch of progressBatches(descriptors)) {
        const response = await fetch('/nba/prop-progress/batch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
          body: JSON.stringify(batch),
        });
        if (!response.ok) throw new Error('Live update request failed');
        const payload = await response.json();
        if (!payload || Array.isArray(payload) || typeof payload !== 'object' || payload.ok === false) {
          throw new Error('Invalid live update response');
        }
        Object.assign(results, payload);
        if (Object.values(payload).some((data) => data.retryable === true || data.status === 'game_not_started')) {
          failed = true;
          break;
        }
      }
    } catch (_) {
      failed = true;
    }

    // Apply one logical refresh; successful earlier chunks remain useful if
    // a later request failed, while unresolved cards show a retryable error.
    cards.forEach((card) => {
      if (card.dataset.pollingStopped === '1') return;
      const data = results[card.dataset.cardId];
      if (data) {
        card.dataset.progressDeferred = data.status === 'progress_deferred' ? '1' : '0';
        if (!applyProgressCard(card, data)) card.dataset.pollingStopped = '1';
      } else if (failed) {
        card.dataset.progressDeferred = '1';
        const statusEl = card.querySelector('[data-live-status]');
        if (statusEl) statusEl.textContent = 'Live update failed';
      }
    });
    return !failed;
  }

  function initLiveProgress() {
    const cards = Array.from(document.querySelectorAll('[data-live-prop-card]'));
    if (!cards.length) return;

    // Assign stable card IDs so the batch response can be matched back
    const cardIds = new Set();
    cards.forEach((card, i) => {
      let cardId = card.dataset.cardId || `live-card-${i}`;
      while (cardIds.has(cardId)) cardId += `-${i}`;
      card.dataset.cardId = cardId;
      cardIds.add(cardId);
    });

    let failures = 0;
    async function refresh() {
      const succeeded = await pollBatch(cards);
      failures = succeeded ? 0 : Math.min(failures + 1, 4);
      const hasActiveCards = cards.some((card) => card.dataset.pollingStopped !== '1');
      if (hasActiveCards) setTimeout(refresh, Math.min(30000 * (2 ** failures), 300000));
    }
    refresh();
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
    });
  });

  initLiveProgress();
})();
