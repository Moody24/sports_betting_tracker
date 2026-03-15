/* unified_bet_builder.js — single-flow game -> selections -> shared slip */
(function () {
  'use strict';

  var root = document.getElementById('ub-root');
  if (!root) return;

  var gamePicker = document.getElementById('ub-game-picker');
  var gameMeta = document.getElementById('ub-game-meta');
  var homeMlBtn = document.getElementById('ub-home-ml-btn');
  var awayMlBtn = document.getElementById('ub-away-ml-btn');
  var overBtn = document.getElementById('ub-over-btn');
  var underBtn = document.getElementById('ub-under-btn');
  var loadPropsBtn = document.getElementById('ub-load-props-btn');
  var propsSearch = document.getElementById('ub-props-search');
  var propsStatus = document.getElementById('ub-props-status');
  var propsList = document.getElementById('ub-props-list');
  var slipList = document.getElementById('ub-slip-list');
  var stakeEl = document.getElementById('ub-stake');
  var unitsEl = document.getElementById('ub-units');
  var submitBtn = document.getElementById('ub-submit-btn');
  var feedback = document.getElementById('ub-feedback');

  var games = [];
  var selectedGame = null;
  var allProps = null;
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
  }

  function propsForSelectedGame() {
    if (!Array.isArray(allProps) || !selectedGame) return [];
    var gk = gameKey(selectedGame);
    return allProps.filter(function (p) {
      var candidate = {
        team_a: p.team_a || '',
        team_b: p.team_b || '',
        match_date: p.match_date || '',
        game_id: p.game_id || '',
      };
      return gameKey(candidate) === gk;
    });
  }

  function renderProps() {
    if (!propsList) return;
    while (propsList.firstChild) propsList.removeChild(propsList.firstChild);

    if (!selectedGame) {
      propsStatus.textContent = 'Pick a game first.';
      return;
    }
    if (!Array.isArray(allProps)) {
      propsStatus.textContent = 'Props not loaded yet.';
      return;
    }

    var search = ((propsSearch || {}).value || '').toLowerCase().trim();
    var rows = propsForSelectedGame();
    if (search) {
      rows = rows.filter(function (p) {
        var mkt = (window.MARKET_LABELS && window.MARKET_LABELS[p.market]) || p.market || '';
        return (p.player || '').toLowerCase().indexOf(search) >= 0 ||
          mkt.toLowerCase().indexOf(search) >= 0;
      });
    }

    propsStatus.textContent = rows.length ? ('Showing ' + rows.length + ' props for selected game.') : 'No props available for selected game.';

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
          american_odds: p.over_odds || null,
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
          american_odds: p.under_odds || null,
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
      american_odds: odds || null,
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
  }

  function loadGames() {
    return fetch(UPCOMING_GAMES_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        games = Array.isArray(data) ? data : [];
      })
      .catch(function () {
        games = [];
      });
  }

  function loadProps() {
    propsStatus.textContent = 'Loading props...';
    loadPropsBtn.disabled = true;
    loadPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';

    fetch(ALL_PROPS_URL)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        allProps = Array.isArray(data) ? data : [];
        propsSearch.classList.remove('d-none');
        renderProps();
        loadPropsBtn.disabled = false;
        loadPropsBtn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Reload Props';
      })
      .catch(function () {
        allProps = [];
        propsStatus.textContent = 'Failed to load props.';
        loadPropsBtn.disabled = false;
        loadPropsBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Retry';
      });
  }

  if (gamePicker) {
    gamePicker.addEventListener('input', function () {
      selectGameByLabel(gamePicker.value || '');
    });
  }
  if (homeMlBtn) homeMlBtn.addEventListener('click', function () { addMoneyline(true); });
  if (awayMlBtn) awayMlBtn.addEventListener('click', function () { addMoneyline(false); });
  if (overBtn) overBtn.addEventListener('click', function () { addTotal('over'); });
  if (underBtn) underBtn.addEventListener('click', function () { addTotal('under'); });
  if (loadPropsBtn) loadPropsBtn.addEventListener('click', loadProps);
  if (propsSearch) {
    propsSearch.addEventListener('input', function () {
      renderProps();
    });
  }
  if (submitBtn) submitBtn.addEventListener('click', submitSlip);

  renderMarketButtons();
  renderSlip();

  loadGames().then(function () {
    if (games.length) {
      // Keep user context by preselecting first available game.
      selectedGame = games[0];
      if (gamePicker && !gamePicker.value) gamePicker.value = selectedGame.label || '';
      renderMarketButtons();
    }
  });
})();
