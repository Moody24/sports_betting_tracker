/* bet_builder.js — powers the 3-tab + screenshot bet builder at /bets/new */
(function () {
  'use strict';

  var MARKET_LABELS = {
    player_points: 'Points',
    player_rebounds: 'Rebounds',
    player_assists: 'Assists',
    player_threes: '3-Pointers',
    player_blocks: 'Blocks',
    player_steals: 'Steals',
    player_points_rebounds_assists: 'Points + Rebounds + Assists',
    player_points_rebounds: 'PTS+REB',
    player_points_assists: 'PTS+AST',
    player_rebounds_assists: 'REB+AST',
  };

  // ── Tab switching ─────────────────────────────────────────────────
  const tabs   = document.querySelectorAll('[data-bb-tab]');
  const panels = document.querySelectorAll('[data-bb-panel]');
  const TICKET_MODE_LABELS = { single: 'Game Line', prop: 'Player Prop', parlay: 'Parlay', screenshot: 'Screenshot' };

  var _activeTab = 'single';

  function showTab(name, pushHash) {
    const validTabs = ['single', 'prop', 'parlay', 'screenshot'];
    if (!validTabs.includes(name)) return;
    _activeTab = name;
    tabs.forEach(t => t.classList.toggle('active', t.dataset.bbTab === name));
    panels.forEach(p => p.classList.toggle('d-none', p.dataset.bbPanel !== name));
    const modeLabel = document.getElementById('ticket-mode-label');
    if (modeLabel) modeLabel.textContent = TICKET_MODE_LABELS[name] || 'Ticket';
    if (pushHash) {
      history.replaceState(null, '', '#' + name);
    }
    updateTicketSummary();
  }

  tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.bbTab, true)));

  const hash = location.hash.replace('#', '') || 'single';
  showTab(['single', 'prop', 'parlay', 'screenshot'].includes(hash) ? hash : 'single', false);

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
      var side   = propBetTypeSelect ? propBetTypeSelect.value : '';
      var pstake = (document.getElementById('prop-stake') || {}).value || '';
      var podds  = (document.getElementById('prop-odds') || {}).value || '';
      if (player) rows.push({ label: 'Player', val: player });
      if (market && market.options[market.selectedIndex]) rows.push({ label: 'Market', val: market.options[market.selectedIndex].text });
      if (line)   rows.push({ label: 'Line', val: line });
      if (side)   rows.push({ label: 'Side', val: side.charAt(0).toUpperCase() + side.slice(1) });
      if (pstake) rows.push({ label: 'Stake', val: '$' + parseFloat(pstake).toFixed(2) });
      if (podds)  rows.push({ label: 'Odds', val: (parseInt(podds) > 0 ? '+' : '') + podds });
    } else if (_activeTab === 'parlay') {
      var legs = legsContainer ? legsContainer.querySelectorAll('.parlay-leg').length : 0;
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

  // ── Parlay builder ────────────────────────────────────────────────
  let legCount = 0;

  function makeLegHTML(idx) {
    return `
    <div class="parlay-leg card-soft p-3 mb-2" data-leg-idx="${idx}">
      <div class="d-flex justify-content-between align-items-center mb-2">
        <span class="small fw-semibold text-secondary">Leg ${idx + 1}</span>
        <button type="button" class="btn btn-sm btn-outline-danger border-0 remove-leg-btn">
          <i class="bi bi-x-lg"></i>
        </button>
      </div>
      <div class="row g-2">
        <div class="col-12">
          <input type="text" class="form-control form-control-sm leg-game-picker"
            placeholder="Search game or type teams…" list="game-datalist" autocomplete="off">
        </div>
        <div class="col-6">
          <input type="text" class="form-control form-control-sm leg-team-a" placeholder="Team A / Away" required>
        </div>
        <div class="col-6">
          <input type="text" class="form-control form-control-sm leg-team-b" placeholder="Team B / Home" required>
        </div>
        <div class="col-6">
          <input type="date" class="form-control form-control-sm leg-date" required>
        </div>
        <div class="col-6">
          <select class="form-select form-select-sm leg-bet-type">
            <option value="moneyline">Moneyline</option>
            <option value="over">Over</option>
            <option value="under">Under</option>
            <option value="over" data-prop="1">Prop Over</option>
            <option value="under" data-prop="1">Prop Under</option>
          </select>
        </div>
        <div class="col-6 leg-ou-group">
          <input type="number" class="form-control form-control-sm leg-ou-line"
            placeholder="O/U Line e.g. 218.5" step="0.5">
        </div>
        <div class="col-6 leg-ml-group d-none">
          <input type="text" class="form-control form-control-sm leg-picked-team"
            placeholder="Picked winner">
        </div>
        <div class="col-12 leg-prop-group d-none">
          <div class="row g-2">
            <div class="col-6">
              <input type="text" class="form-control form-control-sm leg-player"
                placeholder="Player name">
            </div>
            <div class="col-6">
              <select class="form-select form-select-sm leg-prop-type">
                <option value="player_points">Points</option>
                <option value="player_rebounds">Rebounds</option>
                <option value="player_assists">Assists</option>
                <option value="player_points_rebounds_assists">Points + Rebounds + Assists</option>
                <option value="player_threes">3-Pointers Made</option>
                <option value="player_blocks">Blocks</option>
                <option value="player_steals">Steals</option>
              </select>
            </div>
            <div class="col-6">
              <input type="number" class="form-control form-control-sm leg-prop-line"
                placeholder="Prop line e.g. 25.5" step="0.5">
            </div>
          </div>
        </div>
        <input type="hidden" class="leg-game-id" value="">
      </div>
    </div>`;
  }

  function bindLegEvents(legEl) {
    const picker = legEl.querySelector('.leg-game-picker');
    const teamA  = legEl.querySelector('.leg-team-a');
    const teamB  = legEl.querySelector('.leg-team-b');
    const date   = legEl.querySelector('.leg-date');
    const gameId = legEl.querySelector('.leg-game-id');
    const ouLine = legEl.querySelector('.leg-ou-line');

    if (picker) {
      picker.addEventListener('input', function () {
        const match = upcomingGames.find(g => g.label === this.value);
        if (!match) return;
        teamA.value  = match.team_a;
        teamB.value  = match.team_b;
        date.value   = match.match_date;
        gameId.value = match.game_id || '';
        if (match.over_under_line) ouLine.value = match.over_under_line;
      });
    }

    const betType  = legEl.querySelector('.leg-bet-type');
    const ouGroup  = legEl.querySelector('.leg-ou-group');
    const mlGroup  = legEl.querySelector('.leg-ml-group');
    const propGroup= legEl.querySelector('.leg-prop-group');

    function updateLegFields() {
      const sel = betType.options[betType.selectedIndex];
      const isProp = sel.dataset.prop === '1';
      const v = betType.value;
      ouGroup.classList.toggle('d-none', isProp || v === 'moneyline');
      mlGroup.classList.toggle('d-none', v !== 'moneyline');
      propGroup.classList.toggle('d-none', !isProp);
    }
    betType.addEventListener('change', updateLegFields);
    updateLegFields();

    legEl.querySelector('.remove-leg-btn').addEventListener('click', function () {
      legEl.remove();
      renumberLegs();
      syncQueueFromRenderedLegs();
    });
  }

  function renumberLegs() {
    document.querySelectorAll('.parlay-leg').forEach(function (el, i) {
      var label = el.querySelector('.small.fw-semibold');
      if (label) label.textContent = 'Leg ' + (i + 1);
    });
    updateTicketSummary();
  }

  function syncQueueFromRenderedLegs() {
    if (!legsContainer || typeof setParlayQueue !== 'function') return;
    const queuedLegs = [];
    legsContainer.querySelectorAll('.parlay-leg').forEach(function (legEl) {
      const sel = legEl.querySelector('.leg-bet-type');
      if (!sel) return;
      const isProp = sel.options[sel.selectedIndex] && sel.options[sel.selectedIndex].dataset.prop === '1';
      if (!isProp) return;
      const playerName = legEl.querySelector('.leg-player').value.trim();
      const propType = legEl.querySelector('.leg-prop-type').value;
      const propLine = legEl.querySelector('.leg-prop-line').value;
      if (!playerName || !propType || !propLine) return;
      queuedLegs.push({
        team_a: legEl.querySelector('.leg-team-a').value.trim(),
        team_b: legEl.querySelector('.leg-team-b').value.trim(),
        match_date: legEl.querySelector('.leg-date').value,
        bet_type: sel.value,
        game_id: legEl.querySelector('.leg-game-id').value || '',
        player_name: playerName,
        prop_type: propType,
        prop_line: propLine,
      });
    });
    setParlayQueue(queuedLegs);
  }

  const legsContainer = document.getElementById('parlay-legs');
  const addLegBtn = document.getElementById('add-leg-btn');
  const clearParlayDraftBtn = document.getElementById('clear-parlay-draft-btn');

  function createParlayLeg(prefill) {
    if (!legsContainer) return null;
    legsContainer.insertAdjacentHTML('beforeend', makeLegHTML(legCount++));
    const newLeg = legsContainer.lastElementChild;
    bindLegEvents(newLeg);
    if (prefill) fillParlayLegFromPrefill(newLeg, prefill);
    return newLeg;
  }

  function clearRenderedParlayLegs() {
    if (!legsContainer) return;
    legsContainer.innerHTML = '';
    legCount = 0;
  }

  function prefillParlayFromQueue(options) {
    const shouldSwitchTab = !options || options.switchTab !== false;
    if (!legsContainer || typeof getParlayQueue !== 'function') return false;
    const queue = getParlayQueue();
    if (!queue.length) return false;

    clearRenderedParlayLegs();
    queue.forEach(function (queuedLeg) {
      createParlayLeg({
        teamA: queuedLeg.team_a || '',
        teamB: queuedLeg.team_b || '',
        matchDate: queuedLeg.match_date || '',
        playerName: queuedLeg.player_name || '',
        propType: queuedLeg.prop_type || 'player_points',
        propLine: queuedLeg.prop_line || '',
        betType: (queuedLeg.bet_type || 'over').toLowerCase() === 'under' ? 'under' : 'over',
        gameId: queuedLeg.game_id || '',
      });
    });

    if (shouldSwitchTab) showTab('parlay', true);
    return true;
  }

  if (addLegBtn && legsContainer) {
    addLegBtn.addEventListener('click', function () {
      createParlayLeg();
    });
  }

  if (clearParlayDraftBtn) {
    clearParlayDraftBtn.addEventListener('click', function () {
      if (typeof clearParlayQueue === 'function') clearParlayQueue();
      clearRenderedParlayLegs();
      createParlayLeg();
      showFeedback('Parlay draft cleared.', 'info');
    });
  }

  maybePrefillParlayFromQuery();
  const shouldAutoSwitchParlayTab = window.location.hash === '#parlay';
  if (!prefillParlayFromQueue({ switchTab: shouldAutoSwitchParlayTab })) {
    createParlayLeg();
  }

  // ── Parlay submit ─────────────────────────────────────────────────
  const parlayForm     = document.getElementById('parlay-form');
  const parlayFeedback = document.getElementById('parlay-feedback');

  if (parlayForm) {
    parlayForm.addEventListener('submit', function (e) {
      e.preventDefault();
      const stake   = parseFloat(document.getElementById('parlay-stake').value);
      const outcome = document.getElementById('parlay-outcome').value;
      const bonusMult = parseFloat(document.getElementById('parlay-bonus-mult').value) || 1.0;
      const parlayUnitsEl = document.getElementById('parlay-units');
      const parlayUnits = parlayUnitsEl ? (parseFloat(parlayUnitsEl.value) || null) : null;

      if (!stake || stake <= 0) {
        showFeedback('Enter a stake amount.', 'danger');
        return;
      }

      const legEls = legsContainer.querySelectorAll('.parlay-leg');
      if (legEls.length === 0) {
        showFeedback('Add at least one leg.', 'danger');
        return;
      }

      const legs = [];
      let valid = true;
      legEls.forEach(function (legEl) {
        const teamA   = legEl.querySelector('.leg-team-a').value.trim();
        const teamB   = legEl.querySelector('.leg-team-b').value.trim();
        const date    = legEl.querySelector('.leg-date').value;
        const sel     = legEl.querySelector('.leg-bet-type');
        const betType = sel.value;
        const isProp  = sel.options[sel.selectedIndex].dataset.prop === '1';

        if (!teamA || !teamB || !date) { valid = false; return; }

        const leg = {
          team_a: teamA,
          team_b: teamB,
          match_date: date,
          bet_type: betType,
          game_id: legEl.querySelector('.leg-game-id').value || '',
        };

        if (isProp) {
          leg.player_name = legEl.querySelector('.leg-player').value.trim();
          leg.prop_type   = legEl.querySelector('.leg-prop-type').value;
          leg.prop_line   = parseFloat(legEl.querySelector('.leg-prop-line').value) || null;
          leg.over_under_line = null;
        } else if (betType === 'moneyline') {
          leg.picked_team = legEl.querySelector('.leg-picked-team').value.trim();
          leg.over_under_line = null;
        } else {
          leg.over_under_line = parseFloat(legEl.querySelector('.leg-ou-line').value) || null;
        }

        legs.push(leg);
      });

      if (!valid) {
        showFeedback('Fill in team names and date for every leg.', 'danger');
        return;
      }

      const submitBtn = parlayForm.querySelector('button[type="submit"]');
      submitBtn.disabled = true;

      fetch(PARLAY_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
        body: JSON.stringify({ stake, units: parlayUnits, outcome, legs, bonus_multiplier: bonusMult }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
            if (typeof clearParlayQueue === 'function') clearParlayQueue();
            window.location.href = data.redirect || '/bets';
          } else {
            showFeedback(data.error || 'Something went wrong.', 'danger');
            submitBtn.disabled = false;
          }
        })
        .catch(() => {
          showFeedback('Network error. Please try again.', 'danger');
          submitBtn.disabled = false;
        });
    });
  }

  function showFeedback(msg, type) {
    if (!parlayFeedback) return;
    parlayFeedback.className = `alert alert-${type} py-2 small`;
    parlayFeedback.textContent = msg;
    parlayFeedback.classList.remove('d-none');
  }

  // ── Props Browser (Tab 2) ─────────────────────────────────────────
  var allPropsData = null;
  var allPropsLoaded = false;

  const loadPropsBtn    = document.getElementById('load-all-props-btn');
  const propsBrowser    = document.getElementById('props-browser');
  const propsSearchInp  = document.getElementById('props-search-input');
  const loadParlayPropsBtn = document.getElementById('load-parlay-props-btn');
  const parlayPropsBrowser = document.getElementById('parlay-props-browser');
  const parlayPropsSearchInp = document.getElementById('parlay-props-search-input');

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
        allPropsData = data;
        allPropsLoaded = true;
        onSuccess(data);
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
        propsBrowser.style.display = '';
        propsSearchInp.style.display = '';
        renderPropsBrowser(allPropsData);
        return;
      }

      loadPropsBtn.disabled = true;
      loadPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
      ensureAllPropsLoaded(function (data) {
        renderPropsBrowser(data);
        propsBrowser.style.display = '';
        propsSearchInp.style.display = '';
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

  if (loadParlayPropsBtn) {
    loadParlayPropsBtn.addEventListener('click', function () {
      if (allPropsLoaded) {
        parlayPropsBrowser.style.display = '';
        parlayPropsSearchInp.style.display = '';
        renderParlayPropsBrowser(allPropsData);
        return;
      }
      loadParlayPropsBtn.disabled = true;
      loadParlayPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';
      ensureAllPropsLoaded(function (data) {
        renderParlayPropsBrowser(data);
        parlayPropsBrowser.style.display = '';
        parlayPropsSearchInp.style.display = '';
        loadParlayPropsBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Loaded ' + data.length + ' props';
      }, function () {
        loadParlayPropsBtn.disabled = false;
        loadParlayPropsBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Failed — retry';
        showFeedback('Could not load props. Check that ODDS_API_KEY is set.', 'warning');
      });
    });
  }

  if (parlayPropsSearchInp) {
    parlayPropsSearchInp.addEventListener('input', function () {
      if (!allPropsData) return;
      renderParlayPropsBrowser(filterProps(this.value));
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
      selCard.style.display = '';
    }

    var clearBtn = document.getElementById('prop-clear-selection');
    if (clearBtn) {
      clearBtn.onclick = function () {
        if (selCard) selCard.style.display = 'none';
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

  function addParlayPropLegFromButton(btn) {
    if (!legsContainer) return;

    const leg = {
      team_a: btn.dataset.teamA || '',
      team_b: btn.dataset.teamB || '',
      match_date: btn.dataset.date || '',
      bet_type: (btn.dataset.side || 'over').toLowerCase() === 'under' ? 'under' : 'over',
      game_id: btn.dataset.gameId || '',
      player_name: btn.dataset.player || '',
      prop_type: btn.dataset.market || 'player_points',
      prop_line: btn.dataset.line || '',
    };

    if (typeof addParlayLeg === 'function') addParlayLeg(leg);
    prefillParlayFromQueue({ switchTab: true });

    const newLeg = legsContainer.lastElementChild;
    if (newLeg) {
      newLeg.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const playerEl = newLeg.querySelector('.leg-player');
      if (playerEl) playerEl.focus();
    }
  }

  function fillParlayLegFromPrefill(legEl, prefill) {
    if (!legEl || !prefill) return;

    const teamAEl = legEl.querySelector('.leg-team-a');
    const teamBEl = legEl.querySelector('.leg-team-b');
    const dateEl = legEl.querySelector('.leg-date');
    const gameIdEl = legEl.querySelector('.leg-game-id');
    const betTypeEl = legEl.querySelector('.leg-bet-type');
    const playerEl = legEl.querySelector('.leg-player');
    const propTypeEl = legEl.querySelector('.leg-prop-type');
    const propLineEl = legEl.querySelector('.leg-prop-line');

    if (teamAEl) teamAEl.value = prefill.teamA || '';
    if (teamBEl) teamBEl.value = prefill.teamB || '';
    if (dateEl) dateEl.value = prefill.matchDate || '';
    if (gameIdEl) gameIdEl.value = prefill.gameId || '';
    if (playerEl) playerEl.value = prefill.playerName || '';
    if (propTypeEl) propTypeEl.value = prefill.propType || 'player_points';
    if (propLineEl) propLineEl.value = prefill.propLine || '';

    if (betTypeEl) {
      for (let i = 0; i < betTypeEl.options.length; i += 1) {
        const opt = betTypeEl.options[i];
        if (opt.value === prefill.betType && opt.dataset.prop === '1') {
          betTypeEl.selectedIndex = i;
          break;
        }
      }
      betTypeEl.dispatchEvent(new Event('change'));
    }
  }

  function maybePrefillParlayFromQuery() {
    const qp = new URLSearchParams(window.location.search || '');
    if (qp.get('add_to_parlay') !== '1' || !legsContainer) return;

    const leg = {
      team_a: qp.get('team_a') || '',
      team_b: qp.get('team_b') || '',
      match_date: qp.get('match_date') || '',
      player_name: qp.get('player_name') || '',
      prop_type: qp.get('prop_type') || 'player_points',
      prop_line: qp.get('prop_line') || '',
      bet_type: (qp.get('bet_type') || 'over').toLowerCase() === 'under' ? 'under' : 'over',
      game_id: qp.get('game_id') || '',
    };

    if (typeof addParlayLeg === 'function') addParlayLeg(leg);
    prefillParlayFromQueue({ switchTab: true });

    const stakeEl = document.getElementById('parlay-stake');
    if (stakeEl) {
      stakeEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
      stakeEl.focus();
    }
  }

  function renderParlayPropsBrowser(props) {
    if (!parlayPropsBrowser) return;
    if (!props || !props.length) {
      parlayPropsBrowser.innerHTML = '<p class="small text-secondary text-center py-2">No props match your search.</p>';
      return;
    }

    const matchups = {};
    props.forEach(function (p) {
      const matchupKey = (p.team_a || '') + ' @ ' + (p.team_b || '');
      if (!matchups[matchupKey]) matchups[matchupKey] = {};
      const teamName = p.player_team || 'Unknown Team';
      if (!matchups[matchupKey][teamName]) matchups[matchupKey][teamName] = {};
      if (!matchups[matchupKey][teamName][p.player]) matchups[matchupKey][teamName][p.player] = [];
      matchups[matchupKey][teamName][p.player].push(p);
    });

    let html = '<div class="small">';
    Object.keys(matchups).sort().forEach(function (matchup) {
      const teams = matchups[matchup];
      let matchupCount = 0;
      Object.keys(teams).forEach(function (t) {
        Object.keys(teams[t]).forEach(function (player) {
          matchupCount += teams[t][player].length;
        });
      });

      html += '<details class="mb-2">';
      html += '<summary class="fw-semibold">' + escapeHtml(matchup) + ' <span class="text-secondary">(' + matchupCount + ' props)</span></summary>';

      Object.keys(teams).sort().forEach(function (teamName) {
        html += '<details class="ms-3 mt-2">';
        html += '<summary class="text-info">' + escapeHtml(teamName) + '</summary>';

        Object.keys(teams[teamName]).sort().forEach(function (playerName) {
          html += '<details class="ms-3 mt-1">';
          html += '<summary>' + escapeHtml(playerName) + '</summary>';
          html += '<div class="ms-3 mt-1">';

          teams[teamName][playerName].forEach(function (p) {
            const marketLabel = MARKET_LABELS[p.market] || p.market.replace('player_', '');
            const overOdds = p.over_odds > 0 ? '+' + p.over_odds : p.over_odds;
            const underOdds = p.under_odds > 0 ? '+' + p.under_odds : p.under_odds;
            html += '<div class="d-flex align-items-center justify-content-between gap-2 border-top border-secondary-subtle py-1">';
            html += '<div><span class="text-secondary">' + escapeHtml(marketLabel) + '</span> · <span>' + escapeHtml(p.line) + '</span></div>';
            html += '<div>';
            html += '<button type="button" class="btn btn-xs btn-outline-success me-1 parlay-prop-add-btn"'
              + ' data-side="over" data-player="' + escapeHtml(p.player) + '" data-market="' + escapeHtml(p.market) + '"'
              + ' data-line="' + escapeHtml(p.line) + '" data-team-a="' + escapeHtml(p.team_a) + '"'
              + ' data-team-b="' + escapeHtml(p.team_b) + '" data-date="' + escapeHtml(p.match_date) + '"'
              + ' data-game-id="' + escapeHtml(p.game_id) + '">Add Over (' + escapeHtml(overOdds) + ')</button>';
            html += '<button type="button" class="btn btn-xs btn-outline-danger parlay-prop-add-btn"'
              + ' data-side="under" data-player="' + escapeHtml(p.player) + '" data-market="' + escapeHtml(p.market) + '"'
              + ' data-line="' + escapeHtml(p.line) + '" data-team-a="' + escapeHtml(p.team_a) + '"'
              + ' data-team-b="' + escapeHtml(p.team_b) + '" data-date="' + escapeHtml(p.match_date) + '"'
              + ' data-game-id="' + escapeHtml(p.game_id) + '">Add Under (' + escapeHtml(underOdds) + ')</button>';
            html += '</div></div>';
          });

          html += '</div></details>';
        });

        html += '</details>';
      });

      html += '</details>';
    });
    html += '</div>';
    parlayPropsBrowser.innerHTML = html;

    parlayPropsBrowser.querySelectorAll('.parlay-prop-add-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        addParlayPropLegFromButton(btn);
      });
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
      if (ocrPreview)    ocrPreview.style.display = '';
    };
    reader.readAsDataURL(file);

    showOcrStatus('Processing image with OCR…', 'info');
    if (ocrSection) ocrSection.style.display = 'none';

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
          ocrDate.value = new Date().toISOString().slice(0, 10);
        }

        if (ocrSection) ocrSection.style.display = '';
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
        warnEl.style.display = '';
      } else {
        warnEl.style.display = 'none';
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

  // Initial ticket render
  updateTicketSummary();

})();
