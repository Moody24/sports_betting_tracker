/* bet_builder.js — powers the 3-tab + screenshot bet builder at /bets/new */
(function () {
  'use strict';
  function showElement(el) {
    if (!el) return;
    el.classList.remove('d-none');
    el.style.display = '';
  }

  function hideElement(el) {
    if (!el) return;
    el.classList.add('d-none');
    el.style.display = 'none';
  }

  // Uses global MARKET_LABELS from display_config.js

  // ── Tab switching ─────────────────────────────────────────────────
  const tabs   = document.querySelectorAll('[data-bb-tab]');
  const panels = document.querySelectorAll('[data-bb-panel]');
  const TICKET_MODE_LABELS = { single: 'Game Line', prop: 'Player Prop', parlay: 'Parlay', screenshot: 'Screenshot' };

  var _activeTab = 'single';

  function showTab(name, pushHash) {
    const validTabs = ['single', 'prop', 'parlay', 'screenshot'];
    if (!validTabs.includes(name)) return;
    _activeTab = name;
    tabs.forEach(function (t) {
      var active = t.dataset.bbTab === name;
      t.classList.toggle('active', active);
      t.setAttribute('aria-selected', active ? 'true' : 'false');
      t.setAttribute('tabindex', active ? '0' : '-1');
    });
    panels.forEach(function (p) {
      p.hidden = p.dataset.bbPanel !== name;
    });
    document.querySelectorAll('[data-current-tab-input]').forEach(function (input) {
      input.value = name;
    });
    const modeLabel = document.getElementById('ticket-mode-label');
    if (modeLabel) modeLabel.textContent = TICKET_MODE_LABELS[name] || 'Ticket';
    if (pushHash) history.replaceState(null, '', '#' + name);
    if (name === 'parlay') _maybeLoadParlayProps();
    updateTicketSummary();
  }

  tabs.forEach(function (t, idx) {
    t.addEventListener('click', function () { showTab(t.dataset.bbTab, true); });
    t.addEventListener('keydown', function (e) {
      if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(e.key)) return;
      e.preventDefault();
      var next = idx;
      if (e.key === 'ArrowRight') next = (idx + 1) % tabs.length;
      if (e.key === 'ArrowLeft') next = (idx - 1 + tabs.length) % tabs.length;
      if (e.key === 'Home') next = 0;
      if (e.key === 'End') next = tabs.length - 1;
      tabs[next].focus();
      showTab(tabs[next].dataset.bbTab, true);
    });
  });

  const hash = location.hash.replace('#', '');
  const initialTab = ['single', 'prop', 'parlay', 'screenshot'].includes(SERVER_CURRENT_TAB)
    ? SERVER_CURRENT_TAB
    : (['single', 'prop', 'parlay', 'screenshot'].includes(hash) ? hash : 'single');
  showTab(initialTab, false);

  // ── Over / Under toggle buttons ───────────────────────────────────
  const propBetTypeSelect = document.getElementById('prop-bet-type');
  const sideOverBtn  = document.getElementById('side-over-btn');
  const sideUnderBtn = document.getElementById('side-under-btn');

  function syncSideBtns(side) {
    if (sideOverBtn)  sideOverBtn.classList.toggle('active', side === 'over');
    if (sideUnderBtn) sideUnderBtn.classList.toggle('active', side === 'under');
  }

  if (sideOverBtn) {
    sideOverBtn.addEventListener('click', function () {
      if (propBetTypeSelect) propBetTypeSelect.value = 'over';
      syncSideBtns('over');
      updateTicketSummary();
    });
  }

  if (sideUnderBtn) {
    sideUnderBtn.addEventListener('click', function () {
      if (propBetTypeSelect) propBetTypeSelect.value = 'under';
      syncSideBtns('under');
      updateTicketSummary();
    });
  }

  if (propBetTypeSelect) syncSideBtns(propBetTypeSelect.value || 'over');

  // ── Ticket summary ────────────────────────────────────────────────
  function makeTicketRow(label, val) {
    var row = document.createElement('div');
    row.className = 'bb-ticket-row';
    var lbl = document.createElement('span');
    lbl.className = 'bb-ticket-label';
    lbl.textContent = label;
    var v = document.createElement('span');
    v.className = 'bb-ticket-val';
    v.textContent = val;
    row.appendChild(lbl);
    row.appendChild(v);
    return row;
  }

  function updateTicketSummary() {
    var ticketBody = document.getElementById('ticket-body');
    if (!ticketBody) return;

    var rows = [];
    if (_activeTab === 'single') {
      var teamA  = (document.getElementById('single-team-a')  || {}).value || '';
      var teamB  = (document.getElementById('single-team-b')  || {}).value || '';
      var date   = (document.getElementById('single-match-date') || {}).value || '';
      var btype  = document.getElementById('single-bet-type');
      var stake  = (document.getElementById('bet_amount') || {}).value || '';
      var odds   = (document.getElementById('single-odds') || {}).value || '';
      if (teamA || teamB) rows.push({ label: 'Matchup', val: (teamA || '\u2014') + ' @ ' + (teamB || '\u2014') });
      if (date)  rows.push({ label: 'Date', val: date });
      if (btype && btype.options[btype.selectedIndex]) rows.push({ label: 'Type', val: btype.options[btype.selectedIndex].text });
      if (stake) rows.push({ label: 'Stake', val: '$' + parseFloat(stake).toFixed(2) });
      if (odds)  rows.push({ label: 'Odds', val: (parseInt(odds) > 0 ? '+' : '') + odds });
    } else if (_activeTab === 'prop') {
      var player = (document.getElementById('prop-player-name') || {}).value || '';
      var market = document.getElementById('prop-prop-type');
      var line   = (document.getElementById('prop-prop-line') || {}).value || '';
      var side   = (document.getElementById('prop-bet-type') || {}).value || '';
      var pstake = (document.getElementById('prop-stake') || {}).value || '';
      var podds  = (document.getElementById('prop-odds') || {}).value || '';
      if (player) rows.push({ label: 'Player', val: player });
      if (market && market.options[market.selectedIndex]) rows.push({ label: 'Market', val: market.options[market.selectedIndex].text });
      if (line)   rows.push({ label: 'Line', val: line });
      if (side)   rows.push({ label: 'Side', val: side.charAt(0).toUpperCase() + side.slice(1) });
      if (pstake) rows.push({ label: 'Stake', val: '$' + parseFloat(pstake).toFixed(2) });
      if (podds)  rows.push({ label: 'Odds', val: (parseInt(podds) > 0 ? '+' : '') + podds });
    } else if (_activeTab === 'parlay') {
      var legs = typeof parlayLegs !== 'undefined' ? parlayLegs.length : 0;
      var lstake = (document.getElementById('parlay-stake') || {}).value || '';
      rows.push({ label: 'Legs', val: String(legs) });
      if (lstake) rows.push({ label: 'Stake', val: '$' + parseFloat(lstake).toFixed(2) });
    }

    while (ticketBody.firstChild) ticketBody.removeChild(ticketBody.firstChild);

    if (!rows.length) {
      var emptyDiv = document.createElement('div');
      emptyDiv.className = 'bb-ticket-empty';
      var icon = document.createElement('i');
      icon.className = 'bi bi-plus-circle-dotted bb-ticket-empty-icon';
      var msg = document.createElement('p');
      msg.className = 'mt-2 mb-0 small';
      msg.textContent = 'Fill in the form to preview your ticket';
      emptyDiv.appendChild(icon);
      emptyDiv.appendChild(msg);
      ticketBody.appendChild(emptyDiv);
      return;
    }

    rows.forEach(function (r) { ticketBody.appendChild(makeTicketRow(r.label, r.val)); });
  }

  ['single-team-a', 'single-team-b', 'single-match-date', 'single-bet-type',
   'bet_amount', 'single-odds',
   'prop-player-name', 'prop-prop-type', 'prop-prop-line', 'prop-stake', 'prop-odds',
   'parlay-stake'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) { el.addEventListener('input', updateTicketSummary); el.addEventListener('change', updateTicketSummary); }
  });

  // ── Game picker (shared datalist) ─────────────────────────────────
  var upcomingGames = [];

  fetch(UPCOMING_GAMES_URL)
    .then(r => r.json())
    .then(games => {
      upcomingGames = games;
      const dl = document.getElementById('game-datalist');
      if (!dl) return;
      games.forEach(g => {
        const opt = document.createElement('option');
        opt.value = g.label;
        dl.appendChild(opt);
      });
    })
    .catch(() => {});

  function autofillFromPicker(inputEl, teamAEl, teamBEl, dateEl, gameIdEl, ouLineEl, onMatch) {
    if (!inputEl) return;
    inputEl.addEventListener('input', function () {
      const match = upcomingGames.find(g => g.label === this.value);
      if (!match) return;
      if (teamAEl) teamAEl.value = match.team_a;
      if (teamBEl) teamBEl.value = match.team_b;
      if (dateEl)  dateEl.value  = match.match_date;
      if (gameIdEl) gameIdEl.value = match.game_id || '';
      if (ouLineEl && match.over_under_line) {
        ouLineEl.value = match.over_under_line;
      }
      if (typeof onMatch === 'function') onMatch();
    });
  }

  const singleTeamA = document.getElementById('single-team-a');
  const singleTeamB = document.getElementById('single-team-b');
  const singlePickedTeam = document.getElementById('single-picked-team');

  function refreshSinglePickedWinnerOptions() {
    if (!singlePickedTeam) return;
    const teamA = singleTeamA ? singleTeamA.value.trim() : '';
    const teamB = singleTeamB ? singleTeamB.value.trim() : '';
    const prev = singlePickedTeam.value;

    singlePickedTeam.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select winner';
    singlePickedTeam.appendChild(placeholder);

    [teamA, teamB].filter(Boolean).forEach(function (team) {
      const opt = document.createElement('option');
      opt.value = team;
      opt.textContent = team;
      singlePickedTeam.appendChild(opt);
    });

    if ([teamA, teamB].includes(prev)) {
      singlePickedTeam.value = prev;
    } else {
      singlePickedTeam.value = '';
    }
  }

  autofillFromPicker(
    document.getElementById('single-game-picker'),
    singleTeamA,
    singleTeamB,
    document.getElementById('single-match-date'),
    document.getElementById('single-game-id'),
    document.getElementById('single-ou-line'),
    refreshSinglePickedWinnerOptions
  );

  autofillFromPicker(
    document.getElementById('prop-game-picker'),
    document.getElementById('prop-team-a'),
    document.getElementById('prop-team-b'),
    document.getElementById('prop-match-date'),
    document.getElementById('prop-game-id'),
    null
  );

  // ── Single tab: show/hide O/U line & Picked Team fields ──────────
  const singleBetType = document.getElementById('single-bet-type');
  const ouGroup       = document.getElementById('single-ou-group');
  const pickedGroup   = document.getElementById('single-picked-group');

  function updateSingleFields() {
    if (!singleBetType) return;
    const v = singleBetType.value;
    if (ouGroup)     ouGroup.classList.toggle('d-none', v !== 'over' && v !== 'under');
    if (pickedGroup) pickedGroup.classList.toggle('d-none', v !== 'moneyline');
  }
  if (singleBetType) {
    singleBetType.addEventListener('change', updateSingleFields);
    updateSingleFields();
  }
  if (singleTeamA) singleTeamA.addEventListener('input', refreshSinglePickedWinnerOptions);
  if (singleTeamB) singleTeamB.addEventListener('input', refreshSinglePickedWinnerOptions);
  refreshSinglePickedWinnerOptions();

  function wireUnitsAutoCalc(unitsInputId, stakeInputId) {
    if (USER_UNIT_SIZE === null || USER_UNIT_SIZE === undefined) return;
    const unitSize = parseFloat(USER_UNIT_SIZE);
    if (!unitSize || unitSize <= 0) return;

    const unitsEl = document.getElementById(unitsInputId);
    const stakeEl = document.getElementById(stakeInputId);
    if (!unitsEl || !stakeEl) return;

    let manualStakeOverride = false;

    stakeEl.addEventListener('input', function () {
      manualStakeOverride = true;
    });

    unitsEl.addEventListener('input', function () {
      if (manualStakeOverride) return;
      const units = parseFloat(unitsEl.value);
      if (!units || units <= 0) {
        stakeEl.value = '';
        return;
      }
      stakeEl.value = (units * unitSize).toFixed(2);
    });
  }

  wireUnitsAutoCalc('single-units', 'bet_amount');
  wireUnitsAutoCalc('prop-units', 'prop-stake');
  wireUnitsAutoCalc('parlay-units', 'parlay-stake');

  // ── Bonus multiplier previews ─────────────────────────────────────
  function calcProfit(stake, odds) {
    if (!stake || !odds) return null;
    if (odds > 0) return stake * odds / 100;
    if (odds < 0) return stake * 100 / Math.abs(odds);
    return 0;
  }

  function makeBonusPreview(multInputId, stakeInputId, oddsInputId, previewId) {
    const multEl    = document.getElementById(multInputId);
    const stakeEl   = document.getElementById(stakeInputId);
    const oddsEl    = oddsInputId ? document.getElementById(oddsInputId) : null;
    const previewEl = document.getElementById(previewId);
    if (!multEl || !previewEl) return;

    function update() {
      const mult  = parseFloat(multEl.value) || 1.0;
      const stake = parseFloat(stakeEl ? stakeEl.value : '0') || 0;
      const odds  = oddsEl ? (parseInt(oddsEl.value) || null) : null;

      if (mult <= 1.0 || !stake) {
        previewEl.textContent = '';
        return;
      }

      var base = calcProfit(stake, odds);
      if (base !== null) {
        var boosted = base * mult;
        previewEl.textContent =
          'Base profit: $' + base.toFixed(2) +
          ' → Boosted: $' + boosted.toFixed(2) +
          ' (\xd7' + mult.toFixed(2) + ')';
        previewEl.className = 'small text-warning';
      } else {
        previewEl.textContent = 'Bonus \xd7' + mult.toFixed(2) + ' will be applied.';
        previewEl.className = 'small text-warning';
      }
    }

    multEl.addEventListener('input', update);
    if (stakeEl) stakeEl.addEventListener('input', update);
    if (oddsEl)  oddsEl.addEventListener('input', update);
  }

  makeBonusPreview('single-bonus-mult', 'bet_amount', null, 'single-bonus-preview');
  makeBonusPreview('prop-bonus-mult',   'prop-stake', null, 'prop-bonus-preview');

  // ── Parlay builder (state-based) ──────────────────────────────────
  var parlayLegs = [];

  // Init from session storage on page load
  (function () {
    var storedLegs = typeof getParlayQueue === 'function' ? getParlayQueue() : [];
    storedLegs.forEach(function (stored) {
      var storedOdds = stored.american_odds;
      if (storedOdds === undefined || storedOdds === null || storedOdds === '') {
        storedOdds = stored.odds;
      }
      parlayLegs.push({
        player: stored.player_name || '',
        player_name: stored.player_name || '',
        prop_type: stored.prop_type || 'player_points',
        line: parseFloat(stored.prop_line || '0') || 0,
        prop_line: parseFloat(stored.prop_line || '0') || 0,
        side: stored.bet_type || 'over',
        bet_type: stored.bet_type || 'over',
        odds: storedOdds === undefined || storedOdds === null || storedOdds === '' ? null : (parseInt(storedOdds, 10) || null),
        bookmaker: '',
        team_a: stored.team_a || '',
        team_b: stored.team_b || '',
        match_date: stored.match_date || '',
        game_id: stored.game_id || '',
      });
    });
    if (parlayLegs.length) {
      renderSelectedLegs();
      updateTicketSummary();
    }
  })();

  function renderSelectedLegs() {
    var container = document.getElementById('parlay-selected-legs');
    if (!container) return;

    var legsCountEl = document.getElementById('parlay-legs-count');
    if (legsCountEl) {
      if (parlayLegs.length > 0) {
        legsCountEl.textContent = parlayLegs.length + ' leg' + (parlayLegs.length !== 1 ? 's' : '');
        legsCountEl.classList.remove('d-none');
      } else {
        legsCountEl.classList.add('d-none');
      }
    }

    if (!parlayLegs.length) {
      var emptyP = document.createElement('p');
      emptyP.className = 'small text-secondary mb-0';
      emptyP.textContent = 'No legs added yet — click O/U buttons above.';
      while (container.firstChild) container.removeChild(container.firstChild);
      container.appendChild(emptyP);
      return;
    }

    while (container.firstChild) container.removeChild(container.firstChild);

    parlayLegs.forEach(function (leg, idx) {
      var marketLabel = MARKET_LABELS[leg.prop_type] || (leg.prop_type || '').replace('player_', '').replace(/_/g, ' ');
      var sideLabel = leg.side === 'over' ? 'O' : 'U';
      var oddsStr = leg.odds ? ((leg.odds > 0 ? '+' : '') + leg.odds) : '';
      var bookLabel = leg.bookmaker ? ' · ' + leg.bookmaker.slice(0, 2).toUpperCase() : '';

      var chipEl = document.createElement('div');
      chipEl.className = 'parlay-selected-leg';

      var labelSpan = document.createElement('span');
      var playerStrong = document.createElement('strong');
      playerStrong.textContent = leg.player || leg.player_name;
      labelSpan.appendChild(playerStrong);

      var mktSpan = document.createElement('span');
      mktSpan.className = 'text-secondary';
      mktSpan.textContent = ' ' + marketLabel + ' ';
      labelSpan.appendChild(mktSpan);

      var detailSpan = document.createElement('span');
      var detailText = sideLabel + (leg.line || leg.prop_line || '');
      if (oddsStr) detailText += ' ' + oddsStr + bookLabel;
      detailSpan.textContent = detailText;
      labelSpan.appendChild(detailSpan);

      var removeBtn = document.createElement('button');
      removeBtn.type = 'button';
      removeBtn.className = 'parlay-leg-remove';
      removeBtn.textContent = '×';
      removeBtn.setAttribute('data-idx', String(idx));
      removeBtn.addEventListener('click', function () {
        var i = parseInt(removeBtn.getAttribute('data-idx'), 10);
        parlayLegs.splice(i, 1);
        _syncParlayQueueFromState();
        renderSelectedLegs();
        updateTicketSummary();
      });

      chipEl.appendChild(labelSpan);
      chipEl.appendChild(removeBtn);
      container.appendChild(chipEl);
    });
  }

  function _syncParlayQueueFromState() {
    if (typeof setParlayQueue !== 'function') return;
    var queue = parlayLegs.map(function (l) {
      return {
        team_a: l.team_a,
        team_b: l.team_b,
        match_date: l.match_date,
        bet_type: l.side,
        game_id: l.game_id,
        player_name: l.player || l.player_name,
        prop_type: l.prop_type,
        prop_line: l.line || l.prop_line,
        american_odds: l.odds,
      };
    });
    setParlayQueue(queue);
  }

  function addParlayLegFromCard(propData, side) {
    var odds = side === 'over' ? propData.over_odds : propData.under_odds;
    var bookmaker = side === 'over' ? (propData.best_over_book || '') : (propData.best_under_book || '');

    var leg = {
      player: propData.player,
      player_name: propData.player,
      prop_type: propData.market,
      line: propData.line,
      prop_line: propData.line,
      side: side,
      bet_type: side,
      odds: odds,
      bookmaker: bookmaker,
      team_a: propData.team_a,
      team_b: propData.team_b,
      match_date: propData.match_date,
      game_id: propData.game_id,
    };

    // Dedup: one side per (player, market)
    var existIdx = -1;
    parlayLegs.forEach(function (existing, i) {
      if ((existing.player || existing.player_name) === propData.player && existing.prop_type === propData.market) {
        existIdx = i;
      }
    });
    if (existIdx >= 0) {
      parlayLegs[existIdx] = leg;
    } else {
      parlayLegs.push(leg);
    }

    _syncParlayQueueFromState();
    renderSelectedLegs();
    updateTicketSummary();

    var selectedLegsEl = document.getElementById('parlay-selected-legs');
    if (selectedLegsEl) selectedLegsEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function maybePrefillParlayFromQuery() {
    var qp = new URLSearchParams(window.location.search || '');
    if (qp.get('add_to_parlay') !== '1') return;

    var playerName = qp.get('player_name') || '';
    if (!playerName) return;

    var leg = {
      player: playerName,
      player_name: playerName,
      prop_type: qp.get('prop_type') || 'player_points',
      line: parseFloat(qp.get('prop_line') || '0') || 0,
      prop_line: parseFloat(qp.get('prop_line') || '0') || 0,
      side: (qp.get('bet_type') || 'over').toLowerCase() === 'under' ? 'under' : 'over',
      bet_type: (qp.get('bet_type') || 'over').toLowerCase() === 'under' ? 'under' : 'over',
      odds: null,
      bookmaker: '',
      team_a: qp.get('team_a') || '',
      team_b: qp.get('team_b') || '',
      match_date: qp.get('match_date') || '',
      game_id: qp.get('game_id') || '',
    };

    parlayLegs.push(leg);
    _syncParlayQueueFromState();
    renderSelectedLegs();
    updateTicketSummary();
    showTab('parlay', true);

    var stakeEl = document.getElementById('parlay-stake');
    if (stakeEl) {
      stakeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      stakeEl.focus();
    }
  }

  maybePrefillParlayFromQuery();

  // ── Parlay submit ─────────────────────────────────────────────────
  var parlayForm = document.getElementById('parlay-form');

  if (parlayForm) {
    parlayForm.addEventListener('submit', function (e) {
      e.preventDefault();
      var stake = parseFloat(document.getElementById('parlay-stake').value);
      var outcome = document.getElementById('parlay-outcome').value;
      var bonusMult = parseFloat(document.getElementById('parlay-bonus-mult').value) || 1.0;
      var parlayUnitsEl = document.getElementById('parlay-units');
      var parlayUnits = parlayUnitsEl ? (parseFloat(parlayUnitsEl.value) || null) : null;

      if (!stake || stake <= 0) {
        showParlayFeedback('Enter a stake amount.', 'danger');
        return;
      }

      if (parlayLegs.length === 0) {
        showParlayFeedback('Add at least one leg from the prop browser above.', 'danger');
        return;
      }

      var legs = parlayLegs.map(function (leg) {
        return {
          team_a: leg.team_a,
          team_b: leg.team_b,
          match_date: leg.match_date,
          bet_type: leg.side,
          game_id: leg.game_id,
          player_name: leg.player || leg.player_name,
          prop_type: leg.prop_type,
          prop_line: leg.line || leg.prop_line,
          american_odds: leg.odds,
        };
      });

      var submitBtn = parlayForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;

      fetch(PARLAY_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
        body: JSON.stringify({ stake: stake, units: parlayUnits, outcome: outcome, legs: legs, bonus_multiplier: bonusMult }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.success) {
            if (typeof clearParlayQueue === 'function') clearParlayQueue();
            parlayLegs = [];
            window.location.href = data.redirect || '/bets';
          } else {
            showParlayFeedback(data.error || 'Something went wrong.', 'danger');
            submitBtn.disabled = false;
          }
        })
        .catch(function () {
          showParlayFeedback('Network error. Please try again.', 'danger');
          submitBtn.disabled = false;
        });
    });
  }

  function showParlayFeedback(msg, type) {
    var parlayFeedback = document.getElementById('parlay-feedback');
    if (!parlayFeedback) return;
    parlayFeedback.className = 'alert alert-' + type + ' py-2 small';
    parlayFeedback.textContent = msg;
    parlayFeedback.classList.remove('d-none');
  }

  // Keep backward-compat alias used by older code paths
  function showFeedback(msg, type) { showParlayFeedback(msg, type); }

  // ── Props Browser (Tab 2) ─────────────────────────────────────────
  var allPropsData = null;
  var allPropsLoaded = false;

  const loadPropsBtn    = document.getElementById('load-all-props-btn');
  const propsBrowser    = document.getElementById('props-browser');
  const propsSearchInp  = document.getElementById('props-search-input');

  function refreshOcrPickedWinnerOptions() {
    const pickedTeamEl = document.getElementById('ocr-picked-team');
    const teamAEl = document.getElementById('ocr-team-a');
    const teamBEl = document.getElementById('ocr-team-b');
    if (!pickedTeamEl || !teamAEl || !teamBEl) return;

    const teamA = (teamAEl.value || '').trim();
    const teamB = (teamBEl.value || '').trim();
    const prev = pickedTeamEl.value;

    pickedTeamEl.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select winner';
    pickedTeamEl.appendChild(placeholder);

    [teamA, teamB].filter(Boolean).forEach(function (team) {
      const option = document.createElement('option');
      option.value = team;
      option.textContent = team;
      pickedTeamEl.appendChild(option);
    });

    if ([teamA, teamB].includes(prev)) {
      pickedTeamEl.value = prev;
    }
  }

  function ensureAllPropsLoaded(onSuccess, onFail) {
    if (allPropsLoaded && Array.isArray(allPropsData)) {
      onSuccess(allPropsData);
      return;
    }
    fetch(ALL_PROPS_URL)
      .then(r => r.json())
      .then(data => {
        var normalized = Array.isArray(data)
          ? data
          : (Array.isArray(data && data.props) ? data.props : []);
        allPropsData = normalized;
        allPropsLoaded = true;
        onSuccess(normalized);
      })
      .catch(function () {
        if (typeof onFail === 'function') onFail();
      });
  }

  function filterProps(query) {
    if (!allPropsData) return [];
    const q = (query || '').toLowerCase().trim();
    if (!q) return allPropsData;
    return allPropsData.filter(p =>
      (p.player || '').toLowerCase().includes(q) ||
      (p.team_a || '').toLowerCase().includes(q) ||
      (p.team_b || '').toLowerCase().includes(q) ||
      (p.player_team || '').toLowerCase().includes(q) ||
      (MARKET_LABELS[p.market] || p.market || '').toLowerCase().includes(q)
    );
  }

  if (loadPropsBtn) {
    loadPropsBtn.addEventListener('click', function () {
      if (allPropsLoaded) {
        showElement(propsBrowser);
        showElement(propsSearchInp);
        renderPropsBrowser(allPropsData);
        return;
      }

      loadPropsBtn.disabled = true;
      loadPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
      ensureAllPropsLoaded(function (data) {
        renderPropsBrowser(data);
        showElement(propsBrowser);
        showElement(propsSearchInp);
        loadPropsBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Loaded ' + data.length + ' props';
      }, function () {
        loadPropsBtn.disabled = false;
        loadPropsBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Failed — retry';
        showFeedback('Could not load props. Check that ODDS_API_KEY is set.', 'warning');
      });
    });
  }

  if (propsSearchInp) {
    propsSearchInp.addEventListener('input', function () {
      if (!allPropsData) return;
      renderPropsBrowser(filterProps(this.value));
    });
  }

  // Parlay search + game filter — live filtering of the card grid
  var parlayPropsSearchInp = document.getElementById('parlay-props-search-input');
  var parlayGameFilter = document.getElementById('parlay-game-filter');
  var loadParlayPropsBtn = document.getElementById('load-parlay-props-btn');

  if (parlayPropsSearchInp) {
    parlayPropsSearchInp.addEventListener('input', function () {
      if (!allPropsData) return;
      renderParlayPropsBrowser(allPropsData);
    });
  }

  if (parlayGameFilter) {
    parlayGameFilter.addEventListener('change', function () {
      if (!allPropsData) return;
      renderParlayPropsBrowser(allPropsData);
    });
  }

  function setParlayGridStatus(msg, isError) {
    var grid = document.getElementById('parlay-prop-grid');
    if (!grid) return;
    while (grid.firstChild) grid.removeChild(grid.firstChild);
    var p = document.createElement('p');
    p.className = 'small text-center py-2 ' + (isError ? 'text-danger' : 'text-secondary');
    p.textContent = msg;
    grid.appendChild(p);
  }

  function _maybeLoadParlayProps() {
    if (allPropsLoaded && allPropsData) {
      renderParlayPropsBrowser(allPropsData);
      return;
    }
    setParlayGridStatus('Loading props...', false);
    ensureAllPropsLoaded(function (data) {
      renderParlayPropsBrowser(data);
      if (loadParlayPropsBtn) {
        loadParlayPropsBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Loaded';
      }
    }, function () {
      setParlayGridStatus('Failed to load props. You can retry or add a leg manually.', true);
      if (loadParlayPropsBtn) {
        loadParlayPropsBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Retry';
      }
    });
  }

  if (loadParlayPropsBtn) {
    loadParlayPropsBtn.addEventListener('click', function () {
      loadParlayPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
      _maybeLoadParlayProps();
    });
  }

  function applyPropBrowserSelection(data) {
    var side      = data.side;
    var propTypeEl= document.getElementById('prop-prop-type');
    var playerEl  = document.getElementById('prop-player-name');
    var lineEl    = document.getElementById('prop-prop-line');
    var betTypeEl = document.getElementById('prop-bet-type');
    var teamAEl   = document.getElementById('prop-team-a');
    var teamBEl   = document.getElementById('prop-team-b');
    var dateEl    = document.getElementById('prop-match-date');
    var gameIdEl  = document.getElementById('prop-game-id');

    if (playerEl)   playerEl.value   = data.player  || '';
    if (propTypeEl) propTypeEl.value = data.market  || '';
    if (lineEl)     lineEl.value     = data.line    || '';
    if (betTypeEl)  betTypeEl.value  = side;
    if (teamAEl)    teamAEl.value    = data.teamA   || '';
    if (teamBEl)    teamBEl.value    = data.teamB   || '';
    if (dateEl)     dateEl.value     = data.date    || '';
    if (gameIdEl)   gameIdEl.value   = data.gameId  || '';

    syncSideBtns(side);

    // Update selected-prop indicator
    var selCard   = document.getElementById('prop-selected-card');
    var selPlayer = document.getElementById('prop-sel-player');
    var selMarket = document.getElementById('prop-sel-market');
    var selSide   = document.getElementById('prop-sel-side');
    var selLine   = document.getElementById('prop-sel-line');
    if (selCard) {
      if (selPlayer) selPlayer.textContent = data.player  || '';
      if (selMarket) selMarket.textContent = MARKET_LABELS[data.market] || (data.market || '').replace('player_', '');
      if (selSide)   selSide.textContent   = side.charAt(0).toUpperCase() + side.slice(1);
      if (selLine)   selLine.textContent   = data.line || '';
      showElement(selCard);
    }

    var clearBtn = document.getElementById('prop-clear-selection');
    if (clearBtn) {
      clearBtn.onclick = function () {
        hideElement(selCard);
        if (playerEl)   playerEl.value   = '';
        if (propTypeEl) propTypeEl.value = 'player_points';
        if (lineEl)     lineEl.value     = '';
        if (betTypeEl)  betTypeEl.value  = 'over';
        syncSideBtns('over');
        updateTicketSummary();
      };
    }

    var stakeEl = document.getElementById('prop-stake');
    if (stakeEl) { stakeEl.scrollIntoView({ behavior: 'smooth', block: 'center' }); stakeEl.focus(); }

    updateTicketSummary();
  }

  function renderPropsBrowser(props) {
    if (!propsBrowser) return;

    while (propsBrowser.firstChild) propsBrowser.removeChild(propsBrowser.firstChild);

    if (!props || !props.length) {
      var msg = document.createElement('p');
      msg.className = 'small text-secondary text-center py-2';
      msg.textContent = 'No props match your search.';
      propsBrowser.appendChild(msg);
      return;
    }

    props.forEach(function (p) {
      var marketLabel = MARKET_LABELS[p.market] || p.market.replace('player_', '');
      var overOdds  = p.over_odds  > 0 ? '+' + p.over_odds  : String(p.over_odds  || '');
      var underOdds = p.under_odds > 0 ? '+' + p.under_odds : String(p.under_odds || '');

      var card = document.createElement('div');
      card.className = 'prop-card';

      var playerEl = document.createElement('div');
      playerEl.className = 'prop-card-player';
      playerEl.textContent = p.player || '';
      card.appendChild(playerEl);

      var mktEl = document.createElement('div');
      mktEl.className = 'prop-card-market';
      mktEl.textContent = marketLabel;
      card.appendChild(mktEl);

      var lineEl = document.createElement('div');
      lineEl.className = 'prop-card-line';
      lineEl.textContent = p.line || '';
      card.appendChild(lineEl);

      var btns = document.createElement('div');
      btns.className = 'prop-card-btns';

      var overBtn = document.createElement('button');
      overBtn.type = 'button';
      overBtn.className = 'btn btn-xs btn-outline-success prop-browse-btn';
      overBtn.dataset.side   = 'over';
      overBtn.dataset.player = p.player  || '';
      overBtn.dataset.market = p.market  || '';
      overBtn.dataset.line   = p.line    || '';
      overBtn.dataset.teamA  = p.team_a  || '';
      overBtn.dataset.teamB  = p.team_b  || '';
      overBtn.dataset.date   = p.match_date || '';
      overBtn.dataset.gameId = p.game_id || '';
      overBtn.textContent = 'O ' + overOdds;

      var underBtn = document.createElement('button');
      underBtn.type = 'button';
      underBtn.className = 'btn btn-xs btn-outline-danger prop-browse-btn';
      underBtn.dataset.side   = 'under';
      underBtn.dataset.player = p.player  || '';
      underBtn.dataset.market = p.market  || '';
      underBtn.dataset.line   = p.line    || '';
      underBtn.dataset.teamA  = p.team_a  || '';
      underBtn.dataset.teamB  = p.team_b  || '';
      underBtn.dataset.date   = p.match_date || '';
      underBtn.dataset.gameId = p.game_id || '';
      underBtn.textContent = 'U ' + underOdds;

      btns.appendChild(overBtn);
      btns.appendChild(underBtn);
      card.appendChild(btns);
      propsBrowser.appendChild(card);
    });

    propsBrowser.querySelectorAll('.prop-browse-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        applyPropBrowserSelection({
          side:   btn.dataset.side,
          player: btn.dataset.player,
          market: btn.dataset.market,
          line:   btn.dataset.line,
          teamA:  btn.dataset.teamA,
          teamB:  btn.dataset.teamB,
          date:   btn.dataset.date,
          gameId: btn.dataset.gameId,
        });
        btn.classList.add('active');
        setTimeout(function () { btn.classList.remove('active'); }, 800);
      });
    });
  }

  function renderParlayPropsBrowser(props) {
    var grid = document.getElementById('parlay-prop-grid');
    var gameFilter = document.getElementById('parlay-game-filter');
    if (!grid) return;

    // Apply search + game filter
    var searchVal = ((parlayPropsSearchInp || {}).value || '').toLowerCase().trim();
    var gameFilterVal = gameFilter ? gameFilter.value : '';

    var filtered = props || [];
    if (searchVal) {
      filtered = filtered.filter(function (p) {
        return (p.player || '').toLowerCase().includes(searchVal) ||
          (p.team_a || '').toLowerCase().includes(searchVal) ||
          (p.team_b || '').toLowerCase().includes(searchVal) ||
          (p.player_team || '').toLowerCase().includes(searchVal) ||
          (MARKET_LABELS[p.market] || p.market || '').toLowerCase().includes(searchVal);
      });
    }
    if (gameFilterVal) {
      filtered = filtered.filter(function (p) {
        return ((p.team_a || '') + ' @ ' + (p.team_b || '')) === gameFilterVal;
      });
    }

    // Populate game filter options (first render only)
    if (gameFilter && gameFilter.options.length <= 1 && props && props.length) {
      var seenMatchups = {};
      props.forEach(function (p) {
        var key = (p.team_a || '') + ' @ ' + (p.team_b || '');
        if (!seenMatchups[key]) {
          seenMatchups[key] = true;
          var opt = document.createElement('option');
          opt.value = key;
          opt.textContent = key;
          gameFilter.appendChild(opt);
        }
      });
    }

    while (grid.firstChild) grid.removeChild(grid.firstChild);

    if (!filtered.length) {
      var noMatch = document.createElement('p');
      noMatch.className = 'small text-secondary text-center py-2';
      noMatch.textContent = !props || !props.length
        ? 'No props available. Check that ODDS_API_KEY is set.'
        : 'No props match your search.';
      grid.appendChild(noMatch);
      return;
    }

    filtered.forEach(function (p) {
      grid.appendChild(_buildParlayPropCard(p));
    });
  }

  function _buildParlayPropCard(p) {
    var card = document.createElement('div');
    card.className = 'prop-card';

    var marketLabel = MARKET_LABELS[p.market] || (p.market || '').replace('player_', '').replace(/_/g, ' ');
    var fmtOdds = function (o) { return o ? ((o > 0 ? '+' : '') + o) : '--'; };

    // Player name
    var playerEl = document.createElement('div');
    playerEl.className = 'prop-card-player';
    playerEl.textContent = p.player || '';
    card.appendChild(playerEl);

    // Market label
    var mktEl = document.createElement('div');
    mktEl.className = 'prop-card-market';
    mktEl.textContent = marketLabel;
    card.appendChild(mktEl);

    // Line + movement badge
    var lineEl = document.createElement('div');
    lineEl.className = 'prop-card-line';
    var mv = p.movement;
    if (mv && mv.direction !== 'flat' && mv.line_delta) {
      var badge = document.createElement('span');
      badge.className = mv.direction === 'up' ? 'prop-movement-up me-1' : 'prop-movement-down me-1';
      badge.textContent = (mv.direction === 'up' ? '↑' : '↓') + ' ' + Math.abs(mv.line_delta).toFixed(1);
      lineEl.appendChild(badge);
    }
    var lineStrong = document.createElement('strong');
    lineStrong.textContent = String(p.line || '');
    lineEl.appendChild(lineStrong);
    card.appendChild(lineEl);

    // Per-book rows (FD / DK)
    var books = p.books || {};
    var fdBook = books.fanduel;
    var dkBook = books.draftkings;
    var bestOverBook = p.best_over_book || '';
    var bestUnderBook = p.best_under_book || '';

    function makeBookRow(label, bookData, bookKey) {
      var row = document.createElement('div');
      row.className = 'prop-book-row';
      var lbl = document.createElement('span');
      lbl.className = 'prop-book-label';
      lbl.textContent = label;
      row.appendChild(lbl);
      var overSpan = document.createElement('span');
      if (bestOverBook === bookKey) overSpan.className = 'prop-book-best';
      overSpan.textContent = 'O ' + fmtOdds(bookData.over_odds);
      row.appendChild(overSpan);
      var underSpan = document.createElement('span');
      if (bestUnderBook === bookKey) underSpan.className = 'prop-book-best';
      underSpan.textContent = 'U ' + fmtOdds(bookData.under_odds);
      row.appendChild(underSpan);
      return row;
    }

    if (fdBook) card.appendChild(makeBookRow('FD', fdBook, 'fanduel'));
    if (dkBook) card.appendChild(makeBookRow('DK', dkBook, 'draftkings'));

    // O/U action buttons
    var btns = document.createElement('div');
    btns.className = 'd-flex gap-1 mt-2';

    var overBtn = document.createElement('button');
    overBtn.type = 'button';
    overBtn.className = 'btn btn-xs btn-outline-success flex-fill';
    overBtn.textContent = 'O ' + fmtOdds(p.over_odds);
    overBtn.addEventListener('click', function () {
      addParlayLegFromCard(p, 'over');
      overBtn.classList.add('active');
      setTimeout(function () { overBtn.classList.remove('active'); }, 600);
    });

    var underBtn = document.createElement('button');
    underBtn.type = 'button';
    underBtn.className = 'btn btn-xs btn-outline-danger flex-fill';
    underBtn.textContent = 'U ' + fmtOdds(p.under_odds);
    underBtn.addEventListener('click', function () {
      addParlayLegFromCard(p, 'under');
      underBtn.classList.add('active');
      setTimeout(function () { underBtn.classList.remove('active'); }, 600);
    });

    btns.appendChild(overBtn);
    btns.appendChild(underBtn);
    card.appendChild(btns);

    return card;
  }

  var manualLegAddBtn = document.getElementById('manual-leg-add-btn');
  if (manualLegAddBtn) {
    manualLegAddBtn.addEventListener('click', function () {
      var player = (document.getElementById('manual-leg-player') || {}).value || '';
      var market = (document.getElementById('manual-leg-market') || {}).value || 'player_points';
      var line = parseFloat((document.getElementById('manual-leg-line') || {}).value || '');
      var side = (document.getElementById('manual-leg-side') || {}).value || 'over';
      var oddsRaw = (document.getElementById('manual-leg-odds') || {}).value || '';
      var odds = oddsRaw === '' ? null : parseInt(oddsRaw, 10);
      var teamA = ((document.getElementById('manual-leg-team-a') || {}).value || '').trim();
      var teamB = ((document.getElementById('manual-leg-team-b') || {}).value || '').trim();
      var matchDate = (document.getElementById('manual-leg-date') || {}).value || '';

      if (!player.trim()) {
        showParlayFeedback('Enter a player name for the manual leg.', 'danger');
        return;
      }
      if (!Number.isFinite(line)) {
        showParlayFeedback('Enter a valid line for the manual leg.', 'danger');
        return;
      }

      addParlayLegFromCard({
        player: player.trim(),
        market: market,
        line: line,
        over_odds: side === 'over' ? odds : null,
        under_odds: side === 'under' ? odds : null,
        best_over_book: '',
        best_under_book: '',
        team_a: teamA,
        team_b: teamB,
        match_date: matchDate,
        game_id: '',
      }, side);

      ['manual-leg-player', 'manual-leg-line', 'manual-leg-odds'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.value = '';
      });
      showParlayFeedback('Manual leg added.', 'success');
    });
  }

  // ── Screenshot OCR (Tab 4) ────────────────────────────────────────
  const ocrDropzone   = document.getElementById('ocr-dropzone');
  const ocrFileInput  = document.getElementById('ocr-file-input');
  const ocrPreview    = document.getElementById('ocr-preview');
  const ocrPreviewImg = document.getElementById('ocr-preview-img');
  const ocrStatus     = document.getElementById('ocr-status');
  const ocrSection    = document.getElementById('ocr-form-section');

  if (ocrDropzone) {
    ocrDropzone.addEventListener('click', () => ocrFileInput.click());

    ocrDropzone.addEventListener('dragover', e => {
      e.preventDefault();
      ocrDropzone.style.borderColor = 'var(--bs-info)';
    });
    ocrDropzone.addEventListener('dragleave', () => {
      ocrDropzone.style.borderColor = '';
    });
    ocrDropzone.addEventListener('drop', e => {
      e.preventDefault();
      ocrDropzone.style.borderColor = '';
      const file = e.dataTransfer.files[0];
      if (file) processOcrFile(file);
    });

    ocrFileInput.addEventListener('change', function () {
      if (this.files[0]) processOcrFile(this.files[0]);
    });
  }

  function showOcrStatus(msg, type) {
    if (!ocrStatus) return;
    ocrStatus.className = 'alert alert-' + type + ' py-2 small mb-3';
    ocrStatus.textContent = msg;
    ocrStatus.classList.remove('d-none');
  }

  function processOcrFile(file) {
    // Validate
    const allowed = ['image/png', 'image/jpeg', 'image/webp', 'image/bmp'];
    if (!allowed.includes(file.type)) {
      showOcrStatus('Unsupported file type. Please upload a PNG, JPG, or WEBP image.', 'warning');
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      showOcrStatus('File is too large (max 10 MB).', 'warning');
      return;
    }

    // Show preview
    const reader = new FileReader();
    reader.onload = e => {
      if (ocrPreviewImg) ocrPreviewImg.src = e.target.result;
      showElement(ocrPreview);
    };
    reader.readAsDataURL(file);

    showOcrStatus('Processing image with OCR…', 'info');
    hideElement(ocrSection);

    const fd = new FormData();
    fd.append('screenshot', file);

    fetch(OCR_URL, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF_TOKEN },
      body: fd,
    })
      .then(r => r.json())
      .then(data => {
        if (data.error) {
          showOcrStatus('OCR error: ' + data.error, 'danger');
          return;
        }

        // Autofill fields
        setVal('ocr-player',    data.player_name);
        setVal('ocr-odds',      data.american_odds);
        setVal('ocr-stake',     data.stake);
        setVal('ocr-team-a',    data.team_a);
        setVal('ocr-team-b',    data.team_b);
        setVal('ocr-game-id',   data.game_id);

        const ocrBetType = document.getElementById('ocr-bet-type');
        if (ocrBetType) ocrBetType.value = data.bet_type || 'over';

        const ocrPropType = document.getElementById('ocr-prop-type');
        if (ocrPropType) ocrPropType.value = data.prop_type || '';
        const propLineEl = document.getElementById('ocr-prop-line');
        const totalLineEl = document.getElementById('ocr-ou-line');
        if (propLineEl && totalLineEl) {
          propLineEl.value = '';
          totalLineEl.value = '';
          if (data.prop_type) {
            setVal('ocr-prop-line', data.prop_line);
          } else if (data.bet_type === 'over' || data.bet_type === 'under') {
            setVal('ocr-ou-line', data.prop_line);
          }
        }
        refreshOcrPickedWinnerOptions();
        setVal('ocr-picked-team', '');

        const rawPre = document.getElementById('ocr-raw-pre');
        if (rawPre) rawPre.textContent = data.raw_text || '';

        // Set today's date as default if missing
        const ocrDate = document.getElementById('ocr-match-date');
        if (ocrDate && !ocrDate.value) {
          const today = new Date();
          ocrDate.value = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, '0'), String(today.getDate()).padStart(2, '0')].join('-');
        }

        showElement(ocrSection);
        showOcrStatus(
          'OCR complete — review the fields below and adjust before saving.',
          'success'
        );
      })
      .catch(() => {
        showOcrStatus('Network error during OCR. Please try again.', 'danger');
      });
  }

  function setVal(id, val) {
    const el = document.getElementById(id);
    if (el && val !== null && val !== undefined) el.value = val;
  }

  const ocrTeamAEl = document.getElementById('ocr-team-a');
  const ocrTeamBEl = document.getElementById('ocr-team-b');
  if (ocrTeamAEl) ocrTeamAEl.addEventListener('input', refreshOcrPickedWinnerOptions);
  if (ocrTeamBEl) ocrTeamBEl.addEventListener('input', refreshOcrPickedWinnerOptions);
  refreshOcrPickedWinnerOptions();

  // ── Bankroll warning ──────────────────────────────────────────────
  function watchBankroll(stakeInputId, warnId) {
    if (!USER_BANKROLL) return;
    const stakeEl = document.getElementById(stakeInputId);
    const warnEl  = document.getElementById(warnId);
    if (!stakeEl || !warnEl) return;
    stakeEl.addEventListener('input', function () {
      const stake = parseFloat(stakeEl.value) || 0;
      if (stake > USER_BANKROLL) {
        warnEl.textContent = 'Warning: stake ($' + stake.toFixed(2) + ') exceeds remaining bankroll ($' + USER_BANKROLL.toFixed(2) + ')';
        warnEl.className = 'small mt-1 text-warning';
        showElement(warnEl);
      } else {
        hideElement(warnEl);
      }
    });
  }
  watchBankroll('bet_amount',   'single-bankroll-warn');
  watchBankroll('prop-stake',   'prop-bankroll-warn');
  watchBankroll('parlay-stake', 'parlay-bankroll-warn');

  // ── Live payout preview ───────────────────────────────────────────
  function makeLivePayoutPreview(stakeInputId, oddsInputId, previewId) {
    const stakeEl   = document.getElementById(stakeInputId);
    const oddsEl    = document.getElementById(oddsInputId);
    const previewEl = document.getElementById(previewId);
    if (!stakeEl || !oddsEl || !previewEl) return;
    function update() {
      const stake = parseFloat(stakeEl.value) || 0;
      const odds  = parseInt(oddsEl.value)   || 0;
      if (!stake || !odds) { previewEl.textContent = ''; return; }
      const profit = calcProfit(stake, odds);
      if (profit === null) { previewEl.textContent = ''; return; }
      const payout = stake + profit;
      previewEl.textContent = 'Win: +$' + profit.toFixed(2) + ' · Total payout: $' + payout.toFixed(2);
      previewEl.className = 'small text-info mt-1';
    }
    stakeEl.addEventListener('input', update);
    oddsEl.addEventListener('input', update);
  }
  makeLivePayoutPreview('bet_amount', 'single-odds', 'single-payout-preview');
  makeLivePayoutPreview('prop-stake', 'prop-odds',   'prop-payout-preview');


  var parlaySubmitBtn = document.getElementById('parlay-submit-btn');
  if (parlaySubmitBtn) {
    parlaySubmitBtn.addEventListener('click', function (e) {
      if (!Array.isArray(parlayLegs) || parlayLegs.length < 2) {
        e.preventDefault();
        alert('Select at least 2 legs before submitting the parlay.');
      }
    });
  }

  // Initial ticket render
  updateTicketSummary();

})();
