/* unified_bet_builder.js — single-flow game -> selections -> shared slip */
(function () {
  'use strict';

  var root = document.getElementById('ub-root');
  if (!root) return;

  var gamePicker = document.getElementById('ub-game-picker');
  var gameMeta = document.getElementById('ub-game-meta');
  var refreshBtn = document.getElementById('ub-refresh-btn');
  var lastUpdatedEl = document.getElementById('ub-last-updated');
  var homeMlBtn = document.getElementById('ub-home-ml-btn');
  var awayMlBtn = document.getElementById('ub-away-ml-btn');
  var overBtn = document.getElementById('ub-over-btn');
  var underBtn = document.getElementById('ub-under-btn');
  var filterAllBtn = document.getElementById('ub-filter-all');
  var filterPtsBtn = document.getElementById('ub-filter-pts');
  var filterRebBtn = document.getElementById('ub-filter-reb');
  var filterAstBtn = document.getElementById('ub-filter-ast');
  var filter3pmBtn = document.getElementById('ub-filter-3pm');
  var propsStatus = document.getElementById('ub-props-status');
  var propsList = document.getElementById('ub-props-list');
  var slipList = document.getElementById('ub-slip-list');
  var modeBadge = document.getElementById('ub-mode-badge');
  var stakeEl = document.getElementById('ub-stake');
  var unitsEl = document.getElementById('ub-units');
  var submitBtn = document.getElementById('ub-submit-btn');
  var clearSlipBtn = document.getElementById('ub-clear-slip-btn');
  var payoutPreviewEl = document.getElementById('ub-payout-preview');
  var feedback = document.getElementById('ub-feedback');

  var games = [];
  var selectedGame = null;
  var allProps = null;
  var propsPromise = null;
  var propFilter = 'all';
  var slip = [];

  function fmtOdds(v) {
    if (v === null || v === undefined || v === '') return '--';
    var n = parseInt(v, 10);
    if (!n) return '--';
    return n > 0 ? ('+' + n) : String(n);
  }

  function setFeedback(msg, type) {
    if (!feedback) return;
    feedback.className = 'alert alert-' + type + ' py-2 small';
    feedback.textContent = msg;
    feedback.classList.remove('d-none');
  }

  function clearFeedback() {
    if (!feedback) return;
    feedback.classList.add('d-none');
  }

  function setLastUpdated(ts) {
    if (!lastUpdatedEl) return;
    if (!ts) {
      lastUpdatedEl.textContent = 'Not refreshed yet.';
      return;
    }
    try {
      lastUpdatedEl.textContent = 'Updated ' + new Date(ts).toLocaleTimeString();
    } catch (_e) {
      lastUpdatedEl.textContent = 'Updated';
    }
  }

  function gameKey(g) {
    return [g.team_a || '', g.team_b || '', g.match_date || '', g.game_id || ''].join('|');
  }

  function legKey(leg) {
    if (leg.player_name) {
      return ['prop', leg.player_name, leg.prop_type, leg.bet_type, leg.game_id || '', leg.prop_line].join('|');
    }
    if (leg.bet_type === 'moneyline') {
      return ['ml', leg.picked_team, leg.game_id || '', leg.match_date || ''].join('|');
    }
    return ['total', leg.bet_type, leg.game_id || '', leg.over_under_line].join('|');
  }

  function setBtnState(btn, enabled, label) {
    if (!btn) return;
    btn.disabled = !enabled;
    if (label) btn.textContent = label;
  }

  function asAmerican(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = parseInt(v, 10);
    if (!n) return null;
    return n;
  }

  function americanToDecimal(american) {
    if (!american) return null;
    if (american > 0) return 1 + (american / 100);
    return 1 + (100 / Math.abs(american));
  }

  function updatePayoutPreview() {
    if (!payoutPreviewEl) return;
    var stake = parseFloat((stakeEl || {}).value || '');
    if (!stake || stake <= 0) {
      payoutPreviewEl.textContent = 'Projected payout appears here once stake and odds are set.';
      return;
    }
    if (!slip.length) {
      payoutPreviewEl.textContent = 'Add at least one pick to see projected payout.';
      return;
    }

    var decimals = [];
    slip.forEach(function (leg) {
      var d = americanToDecimal(asAmerican(leg.american_odds));
      if (d) decimals.push(d);
    });

    if (slip.length === 1) {
      if (!decimals.length) {
        payoutPreviewEl.textContent = 'Set odds for your pick to calculate payout.';
        return;
      }
      var dec = decimals[0];
      var payout = stake * dec;
      var profit = payout - stake;
      payoutPreviewEl.textContent = 'Projected: +$' + profit.toFixed(2) + ' (total $' + payout.toFixed(2) + ')';
      return;
    }

    if (decimals.length !== slip.length) {
      payoutPreviewEl.textContent = 'Set odds for all legs to calculate parlay payout.';
      return;
    }

    var combined = 1;
    decimals.forEach(function (d) { combined *= d; });
    var parlayPayout = stake * combined;
    var parlayProfit = parlayPayout - stake;
    payoutPreviewEl.textContent = 'Parlay projected: +$' + parlayProfit.toFixed(2) + ' (total $' + parlayPayout.toFixed(2) + ')';
  }

  function updateModeBadge() {
    if (!modeBadge) return;
    if (!slip.length) {
      modeBadge.className = 'badge text-bg-secondary';
      modeBadge.textContent = 'No picks';
      return;
    }
    if (slip.length === 1) {
      modeBadge.className = 'badge text-bg-info';
      modeBadge.textContent = 'Single';
      return;
    }
    modeBadge.className = 'badge text-bg-success';
    modeBadge.textContent = 'Parlay · ' + slip.length + ' legs';
  }

  function setFilter(next) {
    propFilter = next;
    [
      [filterAllBtn, 'all'],
      [filterPtsBtn, 'player_points'],
      [filterRebBtn, 'player_rebounds'],
      [filterAstBtn, 'player_assists'],
      [filter3pmBtn, 'player_threes'],
    ].forEach(function (pair) {
      if (!pair[0]) return;
      pair[0].classList.toggle('active', pair[1] === propFilter);
    });
    renderProps();
  }

  function renderMarketButtons() {
    if (!selectedGame) {
      setBtnState(homeMlBtn, false, 'Home ML');
      setBtnState(awayMlBtn, false, 'Away ML');
      setBtnState(overBtn, false, 'Over');
      setBtnState(underBtn, false, 'Under');
      if (gameMeta) gameMeta.textContent = 'Pick a game to unlock moneyline, total, and props.';
      return;
    }

    var homeOdds = selectedGame.moneyline_home;
    var awayOdds = selectedGame.moneyline_away;
    setBtnState(homeMlBtn, true, (selectedGame.team_b || 'Home') + ' ML ' + fmtOdds(homeOdds));
    setBtnState(awayMlBtn, true, (selectedGame.team_a || 'Away') + ' ML ' + fmtOdds(awayOdds));

    var line = selectedGame.over_under_line;
    var canTotal = line !== null && line !== undefined && line !== '';
    setBtnState(overBtn, canTotal, canTotal ? ('Over ' + line) : 'Over (line unavailable)');
    setBtnState(underBtn, canTotal, canTotal ? ('Under ' + line) : 'Under (line unavailable)');

    if (gameMeta) {
      gameMeta.textContent = (selectedGame.team_a || 'Away') + ' @ ' + (selectedGame.team_b || 'Home') +
        ' • ' + (selectedGame.match_date || '') +
        (canTotal ? (' • Total ' + line) : ' • Total line unavailable');
    }
  }

  function addLeg(leg) {
    var key = legKey(leg);
    var replaced = false;
    slip = slip.map(function (existing) {
      if (legKey(existing) === key) {
        replaced = true;
        return leg;
      }
      return existing;
    });
    if (!replaced) slip.push(leg);
    renderSlip();
    clearFeedback();
  }

  function removeLeg(idx) {
    slip.splice(idx, 1);
    renderSlip();
  }

  function clearSlip() {
    slip = [];
    renderSlip();
    clearFeedback();
  }

  function renderSlip() {
    if (!slipList) return;
    while (slipList.firstChild) slipList.removeChild(slipList.firstChild);

    if (!slip.length) {
      var empty = document.createElement('div');
      empty.className = 'bb-legs-empty';
      var p = document.createElement('p');
      p.className = 'mb-0 small mt-2 bb-legs-empty-text';
      p.textContent = 'No selections yet.';
      empty.appendChild(p);
      slipList.appendChild(empty);
      updateModeBadge();
      updatePayoutPreview();
      return;
    }

    slip.forEach(function (leg, idx) {
      var row = document.createElement('div');
      row.className = 'parlay-leg-item';

      var info = document.createElement('div');
      info.className = 'parlay-leg-info';

      var title = document.createElement('div');
      title.className = 'parlay-leg-player';
      var detail = document.createElement('div');
      detail.className = 'parlay-leg-detail';

      if (leg.player_name) {
        var mkt = (window.MARKET_LABELS && window.MARKET_LABELS[leg.prop_type]) || (leg.prop_type || '').replace('player_', '').replace(/_/g, ' ');
        title.textContent = leg.player_name;
        detail.textContent = mkt + ' • ' + leg.bet_type.toUpperCase() + ' ' + leg.prop_line;
      } else if (leg.bet_type === 'moneyline') {
        title.textContent = leg.picked_team + ' ML';
        detail.textContent = (leg.team_a || '') + ' @ ' + (leg.team_b || '');
      } else {
        title.textContent = (leg.bet_type || '').toUpperCase() + ' ' + leg.over_under_line;
        detail.textContent = (leg.team_a || '') + ' @ ' + (leg.team_b || '');
      }

      info.appendChild(title);
      info.appendChild(detail);

      var oddsWrap = document.createElement('div');
      var oddsInput = document.createElement('input');
      oddsInput.type = 'number';
      oddsInput.className = 'form-control form-control-sm';
      oddsInput.style.width = '86px';
      oddsInput.placeholder = 'Odds';
      oddsInput.value = leg.american_odds === null || leg.american_odds === undefined ? '' : String(leg.american_odds);
      oddsInput.addEventListener('input', function () {
        var v = oddsInput.value;
        slip[idx].american_odds = v === '' ? null : parseInt(v, 10);
        updatePayoutPreview();
      });
      oddsWrap.appendChild(oddsInput);

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'parlay-leg-remove';
      remove.textContent = '×';
      remove.addEventListener('click', function () { removeLeg(idx); });

      row.appendChild(info);
      row.appendChild(oddsWrap);
      row.appendChild(remove);
      slipList.appendChild(row);
    });

    updateModeBadge();
    updatePayoutPreview();
  }

  function isSameGameProp(p) {
    if (!selectedGame) return false;
    if ((p.game_id || '') && (selectedGame.game_id || '')) {
      return String(p.game_id) === String(selectedGame.game_id);
    }
    var candidate = {
      team_a: p.team_a || '',
      team_b: p.team_b || '',
      match_date: p.match_date || '',
      game_id: p.game_id || '',
    };
    return gameKey(candidate) === gameKey(selectedGame);
  }

  function propsForSelectedGame() {
    if (!Array.isArray(allProps) || !selectedGame) return [];
    return allProps.filter(isSameGameProp);
  }

  function applyPropFilter(rows) {
    if (propFilter === 'all') return rows;
    return rows.filter(function (p) { return (p.market || '') === propFilter; });
  }

  function renderProps() {
    if (!propsList) return;
    while (propsList.firstChild) propsList.removeChild(propsList.firstChild);

    if (!selectedGame) {
      propsStatus.textContent = 'Pick a game first.';
      return;
    }
    if (!Array.isArray(allProps)) {
      propsStatus.textContent = 'Loading props for selected game...';
      return;
    }

    var rows = applyPropFilter(propsForSelectedGame());
    propsStatus.textContent = rows.length
      ? ('Showing ' + rows.length + ' ' + (propFilter === 'all' ? 'props' : propFilter.replace('player_', '')) + ' picks for this game.')
      : 'No props available for this filter.';

    rows.forEach(function (p) {
      var card = document.createElement('div');
      card.className = 'prop-card';

      var player = document.createElement('div');
      player.className = 'prop-card-player';
      player.textContent = p.player || '';

      var market = document.createElement('div');
      market.className = 'prop-card-market';
      market.textContent = (window.MARKET_LABELS && window.MARKET_LABELS[p.market]) || (p.market || '').replace('player_', '').replace(/_/g, ' ');

      var line = document.createElement('div');
      line.className = 'prop-card-line';
      line.textContent = String(p.line || '');

      var btns = document.createElement('div');
      btns.className = 'prop-card-btns';

      var ob = document.createElement('button');
      ob.type = 'button';
      ob.className = 'btn btn-xs btn-outline-success';
      ob.textContent = 'O ' + fmtOdds(p.over_odds);
      ob.addEventListener('click', function () {
        addLeg({
          team_a: p.team_a,
          team_b: p.team_b,
          match_date: p.match_date,
          game_id: p.game_id,
          bet_type: 'over',
          player_name: p.player,
          prop_type: p.market,
          prop_line: p.line,
          over_under_line: null,
          picked_team: null,
          american_odds: asAmerican(p.over_odds),
        });
      });

      var ub = document.createElement('button');
      ub.type = 'button';
      ub.className = 'btn btn-xs btn-outline-danger';
      ub.textContent = 'U ' + fmtOdds(p.under_odds);
      ub.addEventListener('click', function () {
        addLeg({
          team_a: p.team_a,
          team_b: p.team_b,
          match_date: p.match_date,
          game_id: p.game_id,
          bet_type: 'under',
          player_name: p.player,
          prop_type: p.market,
          prop_line: p.line,
          over_under_line: null,
          picked_team: null,
          american_odds: asAmerican(p.under_odds),
        });
      });

      btns.appendChild(ob);
      btns.appendChild(ub);
      card.appendChild(player);
      card.appendChild(market);
      card.appendChild(line);
      card.appendChild(btns);
      propsList.appendChild(card);
    });
  }

  function addMoneyline(isHome) {
    if (!selectedGame) return;
    var pickedTeam = isHome ? selectedGame.team_b : selectedGame.team_a;
    var odds = isHome ? selectedGame.moneyline_home : selectedGame.moneyline_away;
    addLeg({
      team_a: selectedGame.team_a,
      team_b: selectedGame.team_b,
      match_date: selectedGame.match_date,
      game_id: selectedGame.game_id,
      bet_type: 'moneyline',
      player_name: null,
      prop_type: null,
      prop_line: null,
      over_under_line: null,
      picked_team: pickedTeam,
      american_odds: asAmerican(odds),
    });
  }

  function addTotal(side) {
    if (!selectedGame || selectedGame.over_under_line === null || selectedGame.over_under_line === undefined || selectedGame.over_under_line === '') {
      return;
    }
    addLeg({
      team_a: selectedGame.team_a,
      team_b: selectedGame.team_b,
      match_date: selectedGame.match_date,
      game_id: selectedGame.game_id,
      bet_type: side,
      player_name: null,
      prop_type: null,
      prop_line: null,
      over_under_line: selectedGame.over_under_line,
      picked_team: null,
      american_odds: -110,
    });
  }

  function findGameForLeg(leg) {
    if (!Array.isArray(games) || !games.length) return null;
    var byId = null;
    if (leg.game_id) {
      byId = games.find(function (g) { return String(g.game_id || '') === String(leg.game_id || ''); });
      if (byId) return byId;
    }
    return games.find(function (g) {
      return (g.team_a || '') === (leg.team_a || '') &&
        (g.team_b || '') === (leg.team_b || '') &&
        (g.match_date || '') === (leg.match_date || '');
    }) || null;
  }

  function refreshSlipOdds() {
    if (!slip.length) return;
    slip = slip.map(function (leg) {
      var next = Object.assign({}, leg);
      if (leg.player_name && Array.isArray(allProps)) {
        var match = allProps.find(function (p) {
          return (p.player || '') === (leg.player_name || '') &&
            (p.market || '') === (leg.prop_type || '') &&
            String(p.game_id || '') === String(leg.game_id || '');
        });
        if (match) {
          next.american_odds = leg.bet_type === 'over' ? asAmerican(match.over_odds) : asAmerican(match.under_odds);
          next.prop_line = match.line;
        }
      } else if (leg.bet_type === 'moneyline') {
        var gm = findGameForLeg(leg);
        if (gm) {
          next.american_odds = (leg.picked_team === gm.team_b)
            ? asAmerican(gm.moneyline_home)
            : asAmerican(gm.moneyline_away);
        }
      } else if (leg.bet_type === 'over' || leg.bet_type === 'under') {
        var tg = findGameForLeg(leg);
        if (tg && tg.over_under_line !== null && tg.over_under_line !== undefined && tg.over_under_line !== '') {
          next.over_under_line = tg.over_under_line;
        }
      }
      return next;
    });
    renderSlip();
  }

  function submitSlip() {
    clearFeedback();

    if (!slip.length) {
      setFeedback('Add at least one selection.', 'danger');
      return;
    }

    var stake = parseFloat((stakeEl || {}).value || '');
    if (!stake || stake <= 0) {
      setFeedback('Enter a valid stake amount.', 'danger');
      return;
    }

    var units = parseFloat((unitsEl || {}).value || '');
    if (!units || units <= 0) units = null;

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Submitting...';

    fetch(PLACE_BETS_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
      body: JSON.stringify({
        stake: stake,
        units: units,
        is_parlay: slip.length > 1,
        legs: slip,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data && data.success) {
          window.location.href = '/bets';
          return;
        }
        setFeedback((data && data.error) ? data.error : 'Unable to submit slip.', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Submit Slip';
      })
      .catch(function () {
        setFeedback('Network error while submitting slip.', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Submit Slip';
      });
  }

  function selectGameByLabel(label) {
    selectedGame = null;
    games.forEach(function (g) {
      if (g.label === label) selectedGame = g;
    });
    renderMarketButtons();
    renderProps();
    ensurePropsLoaded().then(function () {
      renderProps();
      refreshSlipOdds();
    });
  }

  function loadGames() {
    return fetch(UPCOMING_GAMES_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        games = Array.isArray(data) ? data : [];
        return games;
      })
      .catch(function () {
        games = [];
        return games;
      });
  }

  function ensurePropsLoaded(forceRefresh) {
    if (!forceRefresh && Array.isArray(allProps)) return Promise.resolve(allProps);
    if (!forceRefresh && propsPromise) return propsPromise;

    propsStatus.textContent = 'Loading props...';
    propsPromise = fetch(ALL_PROPS_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        allProps = Array.isArray(data) ? data : [];
        propsPromise = null;
        return allProps;
      })
      .catch(function () {
        allProps = [];
        propsStatus.textContent = 'Failed to load props.';
        propsPromise = null;
        return allProps;
      });

    return propsPromise;
  }

  function hydrateSelectionAfterRefresh() {
    if (!selectedGame) return;
    var prior = selectedGame;
    if (prior.game_id) {
      var byId = games.find(function (g) { return String(g.game_id || '') === String(prior.game_id || ''); });
      if (byId) selectedGame = byId;
    }
    if (!selectedGame || !selectedGame.label) {
      selectedGame = games.find(function (g) { return g.label === prior.label; }) || selectedGame;
    }
    if (selectedGame && gamePicker) gamePicker.value = selectedGame.label || gamePicker.value;
  }

  function refreshData(manual) {
    if (refreshBtn) {
      refreshBtn.disabled = true;
      refreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Refreshing...';
    }

    return Promise.all([loadGames(), ensurePropsLoaded(true)])
      .then(function () {
        hydrateSelectionAfterRefresh();
        renderMarketButtons();
        renderProps();
        refreshSlipOdds();
        setLastUpdated(Date.now());
        if (manual) setFeedback('Odds refreshed.', 'success');
      })
      .catch(function () {
        if (manual) setFeedback('Failed to refresh odds.', 'warning');
      })
      .finally(function () {
        if (refreshBtn) {
          refreshBtn.disabled = false;
          refreshBtn.innerHTML = '<i class="bi bi-arrow-clockwise me-1"></i>Refresh Odds';
        }
      });
  }

  if (gamePicker) {
    gamePicker.addEventListener('input', function () {
      selectGameByLabel(gamePicker.value || '');
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function () {
      refreshData(true);
    });
  }

  if (homeMlBtn) homeMlBtn.addEventListener('click', function () { addMoneyline(true); });
  if (awayMlBtn) awayMlBtn.addEventListener('click', function () { addMoneyline(false); });
  if (overBtn) overBtn.addEventListener('click', function () { addTotal('over'); });
  if (underBtn) underBtn.addEventListener('click', function () { addTotal('under'); });

  if (filterAllBtn) filterAllBtn.addEventListener('click', function () { setFilter('all'); });
  if (filterPtsBtn) filterPtsBtn.addEventListener('click', function () { setFilter('player_points'); });
  if (filterRebBtn) filterRebBtn.addEventListener('click', function () { setFilter('player_rebounds'); });
  if (filterAstBtn) filterAstBtn.addEventListener('click', function () { setFilter('player_assists'); });
  if (filter3pmBtn) filter3pmBtn.addEventListener('click', function () { setFilter('player_threes'); });

  if (stakeEl) stakeEl.addEventListener('input', updatePayoutPreview);
  if (submitBtn) submitBtn.addEventListener('click', submitSlip);
  if (clearSlipBtn) clearSlipBtn.addEventListener('click', clearSlip);

  renderMarketButtons();
  renderSlip();
  setLastUpdated(null);

  refreshData(false).then(function () {
    if (games.length) {
      selectedGame = games[0];
      if (gamePicker && !gamePicker.value) gamePicker.value = selectedGame.label || '';
      renderMarketButtons();
      renderProps();
      refreshSlipOdds();
      return;
    }
    propsStatus.textContent = 'No games available right now.';
  });

  // Keep odds current while user is on the page.
  setInterval(function () {
    refreshData(false);
  }, 60000);
})();
