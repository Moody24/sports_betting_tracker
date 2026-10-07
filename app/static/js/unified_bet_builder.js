/* unified_bet_builder.js — single-flow game -> selections -> shared slip. */
(function () {
  'use strict';

  var root = document.getElementById('ub-root');
  if (!root) return;
  var COPY = window.UX_COPY || {};

  var gamePicker = document.getElementById('ub-game-picker');
  var gameMeta = document.getElementById('ub-game-meta');
  var refreshBtn = document.getElementById('ub-refresh-btn');
  var lastUpdatedEl = document.getElementById('ub-last-updated');
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
  var manualForm = document.getElementById('ub-manual-form');
  var playerSearch = document.getElementById('ub-player-search');
  var reviewDialog = document.getElementById('ub-quote-review');
  var reviewItems = [];
  var saving = false;
  var pendingSubmission = null;
  var lockedControls = [];
  var refreshing = null;
  var propsError = false;
  var gamesError = false;
  var quoteMaxAge = 10 * 60 * 1000;

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

  function asAmerican(v) {
    if (v === null || v === undefined || v === '') return null;
    var n = Number(v);
    if (!Number.isInteger(n) || Math.abs(n) < 100 || Math.abs(n) > 2147483647) return null;
    return n;
  }

  function americanToDecimal(american) {
    if (!american) return null;
    if (american > 0) return 1 + (american / 100);
    return 1 + (100 / Math.abs(american));
  }

  function setFeedback(msg, type) {
    if (!feedback) return;
    feedback.className = 'notice' + (type === 'danger' ? ' text-danger' : '');
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
      lastUpdatedEl.textContent = 'View refreshed ' + new Date(ts).toLocaleTimeString([], { timeZone: 'America/New_York' }) + ' ET';
    } catch (_e) {
      lastUpdatedEl.textContent = 'View refreshed';
    }
  }

  function gameKey(g) {
    return [g.team_a || '', g.team_b || '', g.match_date || '', g.game_id || ''].join('|');
  }

  function gameSelectValue(g) {
    if (g && g.selection_key) return 'provider:' + String(g.selection_key);
    if (g && g.game_id) return 'id:' + String(g.game_id);
    return 'key:' + gameKey(g || {});
  }

  function gameOptionLabel(g) {
    var teamA = g.team_a || 'Away';
    var teamB = g.team_b || 'Home';
    var dateTxt = g.match_date || 'Date TBD';
    var state = g.status === 'in_progress' ? 'In progress' : 'Scheduled';
    if (g.market_state === 'unavailable') state += ' · scores only';
    return teamA + ' @ ' + teamB + ' • ' + dateTxt + ' • ' + state;
  }

  function populateGamePicker() {
    if (!gamePicker) return;

    var selectedValue = selectedGame ? gameSelectValue(selectedGame) : '';
    while (gamePicker.firstChild) gamePicker.removeChild(gamePicker.firstChild);

    var defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = games.length ? 'Select a game...' : 'No games available';
    gamePicker.appendChild(defaultOpt);

    if (!games.length) {
      gamePicker.disabled = true;
      gamePicker.value = '';
      return;
    }
    gamePicker.disabled = false;

    var grouped = {};
    var dateOrder = [];
    games.forEach(function (g) {
      var dateKey = g.match_date || 'Other';
      if (!grouped[dateKey]) {
        grouped[dateKey] = [];
        dateOrder.push(dateKey);
      }
      grouped[dateKey].push(g);
    });

    dateOrder.sort();
    dateOrder.forEach(function (dateKey) {
      var optgroup = document.createElement('optgroup');
      optgroup.label = dateKey;
      grouped[dateKey].forEach(function (g) {
        var opt = document.createElement('option');
        opt.value = gameSelectValue(g);
        opt.textContent = gameOptionLabel(g);
        if (selectedValue && opt.value === selectedValue) opt.selected = true;
        optgroup.appendChild(opt);
      });
      gamePicker.appendChild(optgroup);
    });

    if (!selectedValue) gamePicker.value = '';
  }

  function marketIdentityKey(leg) {
    if (leg.player_name) {
      return ['prop', leg.player_name, leg.prop_type || '', leg.selection_key || leg.game_id || '', leg.match_date || ''].join('|');
    }
    if (leg.bet_type === 'moneyline') {
      return ['ml', leg.selection_key || leg.game_id || '', leg.match_date || '', leg.team_a || '', leg.team_b || ''].join('|');
    }
    return ['total', leg.selection_key || leg.game_id || '', leg.match_date || '', leg.team_a || '', leg.team_b || ''].join('|');
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
      pair[0].classList.toggle('is-active', pair[1] === propFilter);
      pair[0].setAttribute('aria-pressed', String(pair[1] === propFilter));
    });
    renderProps();
  }

  function updateModeBadge() {
    if (!modeBadge) return;
    if (!slip.length) {
      modeBadge.className = 'tag tag-pending';
      modeBadge.textContent = 'No picks';
      return;
    }
    if (slip.length === 1) {
      modeBadge.className = 'tag tag-live';
      modeBadge.textContent = 'Single';
      return;
    }
    modeBadge.className = 'tag tag-win';
    modeBadge.textContent = 'Parlay · ' + slip.length + ' legs';
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

  function renderGameMeta() {
    if (!gameMeta) return;
    if (!selectedGame) {
      gameMeta.textContent = 'Choose a game to see its available player markets.';
      return;
    }
    gameMeta.textContent = (selectedGame.team_a || 'Away') + ' @ ' + (selectedGame.team_b || 'Home') +
      ' · ' + (selectedGame.match_date || '') + ' ET · ' +
      (selectedGame.status === 'in_progress' ? 'In progress' : 'Scheduled');
  }

  function addLeg(leg) {
    if (saving) return false;
    var identity = marketIdentityKey(leg);
    var idx = -1;
    slip.forEach(function (existing, i) {
      if (marketIdentityKey(existing) === identity) idx = i;
    });

    if (idx >= 0) {
      setFeedback('This player or game market is already in your slip. Remove it before choosing a different side.', 'info');
      return false;
    } else {
      slip.push(leg);
      clearFeedback();
    }

    renderSlip();
    return true;
  }

  function removeLeg(idx) {
    if (saving) return;
    slip.splice(idx, 1);
    renderSlip();
  }

  function clearSlip() {
    if (saving) return;
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
      p.textContent = COPY.noSelectionsYet || 'No selections yet.';
      empty.appendChild(p);
      slipList.appendChild(empty);
      updateModeBadge();
      updateRrSection();
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
      var evidence = document.createElement('div');
      evidence.className = 'parlay-leg-detail parlay-leg-quote';
      evidence.textContent = leg.manual_entry ? 'Manual record · entered price retained' : quoteLabel(leg);
      info.appendChild(evidence);

      var controls = document.createElement('div');
      controls.className = 'builder-leg-controls';

      var sideSelect = document.createElement('select');
      sideSelect.className = 'form-select form-select-sm';
      sideSelect.setAttribute('aria-label', 'Selection side');
      ['over', 'under'].forEach(function (side) {
        var opt = document.createElement('option');
        opt.value = side;
        opt.textContent = side === 'over' ? 'Over' : 'Under';
        if (leg.bet_type === side) opt.selected = true;
        sideSelect.appendChild(opt);
      });

      var lineInput = document.createElement('input');
      lineInput.type = 'number';
      lineInput.setAttribute('aria-label', 'Selection line');
      lineInput.step = '0.5';
      lineInput.className = 'form-control form-control-sm';

      if (leg.player_name) {
        lineInput.value = leg.prop_line === null || leg.prop_line === undefined ? '' : String(leg.prop_line);
        sideSelect.addEventListener('change', function () {
          leg.bet_type = sideSelect.value;
          leg.recorded_odds_edited = true;
          refreshSlipOdds();
        });
        lineInput.addEventListener('input', function () {
          var v = parseFloat(lineInput.value);
          leg.prop_line = Number.isFinite(v) ? v : leg.prop_line;
          leg.recorded_odds_edited = true;
          detail.textContent = mkt + ' • ' + leg.bet_type.toUpperCase() + ' ' + leg.prop_line;
        });
        controls.appendChild(sideSelect);
        controls.appendChild(lineInput);
      } else if (leg.bet_type === 'moneyline') {
        var teamSelect = document.createElement('select');
        teamSelect.className = 'form-select form-select-sm';
        teamSelect.setAttribute('aria-label', 'Selected team');
        [leg.team_a, leg.team_b].forEach(function (tm) {
          if (!tm) return;
          var optTm = document.createElement('option');
          optTm.value = tm;
          optTm.textContent = tm;
          if (leg.picked_team === tm) optTm.selected = true;
          teamSelect.appendChild(optTm);
        });
        teamSelect.addEventListener('change', function () {
          leg.picked_team = teamSelect.value;
          refreshSlipOdds();
        });
        controls.appendChild(teamSelect);
      } else {
        lineInput.value = leg.over_under_line === null || leg.over_under_line === undefined ? '' : String(leg.over_under_line);
        sideSelect.addEventListener('change', function () {
          leg.bet_type = sideSelect.value;
          renderSlip();
        });
        lineInput.addEventListener('input', function () {
          var vv = parseFloat(lineInput.value);
          leg.over_under_line = Number.isFinite(vv) ? vv : leg.over_under_line;
          renderSlip();
        });
        controls.appendChild(sideSelect);
        controls.appendChild(lineInput);
      }

      var oddsInput = document.createElement('input');
      oddsInput.type = 'number';
      oddsInput.inputMode = 'numeric';
      oddsInput.setAttribute('aria-label', 'Recorded American odds');
      oddsInput.className = 'form-control form-control-sm';
      oddsInput.placeholder = 'Odds';
      oddsInput.value = leg.american_odds === null || leg.american_odds === undefined ? '' : String(leg.american_odds);
      oddsInput.addEventListener('input', function () {
        var vOdds = oddsInput.value;
        leg.american_odds = vOdds === '' ? null : Number(vOdds);
        leg.recorded_odds_edited = true;
        updatePayoutPreview();
      });
      controls.appendChild(oddsInput);

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'parlay-leg-remove';
      remove.textContent = '×';
      remove.setAttribute('aria-label', 'Remove selection ' + (idx + 1));
      remove.addEventListener('click', function () { removeLeg(idx); });

      row.appendChild(info);
      row.appendChild(controls);
      row.appendChild(remove);
      slipList.appendChild(row);
    });

    updateModeBadge();
    updateRrSection();
    updatePayoutPreview();
  }

  function isSameGameProp(p) {
    if (!selectedGame) return false;
    if (p.selection_key && selectedGame.selection_key) {
      return String(p.selection_key) === String(selectedGame.selection_key);
    }
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

  function propRelevanceScore(p) {
    var score = 0;
    var over = asAmerican(p.over_odds);
    var under = asAmerican(p.under_odds);
    if (over !== null) score += 40;
    if (under !== null) score += 40;
    if (p.line !== null && p.line !== undefined && p.line !== '') score += 30;
    var bestOver = americanToDecimal(over) || 0;
    var bestUnder = americanToDecimal(under) || 0;
    score += Math.max(bestOver, bestUnder) * 5;
    var movement = (p.movement || {}).line_delta;
    if (movement !== null && movement !== undefined) score += Math.min(Math.abs(movement), 3);
    return score;
  }

  function propsForSelectedGame() {
    if (!Array.isArray(allProps) || !selectedGame) return [];
    return allProps
      .filter(isSameGameProp)
      .filter(function (p) {
        var hasLine = typeof p.line === 'number' && Number.isFinite(p.line) && p.line >= 0;
        var hasAnyOdds = (asAmerican(p.over_odds) !== null && quoteEvidence(p, 'over').quote_token) ||
          (asAmerican(p.under_odds) !== null && quoteEvidence(p, 'under').quote_token);
        return hasLine && hasAnyOdds && freshQuote(p);
      })
      .sort(function (a, b) { return propRelevanceScore(b) - propRelevanceScore(a); });
  }

  function applyPropFilter(rows) {
    var search = playerSearch ? playerSearch.value.trim().toLowerCase() : '';
    return rows.filter(function (p) {
      return (propFilter === 'all' || p.market === propFilter) && (!search || String(p.player || '').toLowerCase().includes(search));
    });
  }

  function renderProps() {
    if (!propsList) return;
    while (propsList.firstChild) propsList.removeChild(propsList.firstChild);

    if (!selectedGame) {
      propsStatus.textContent = gamesError ? 'Unable to load games. Retry with Refresh markets, or use manual entry below.' : games.length ? 'Choose a scheduled or live game to see available player props.' : 'No verified games available. Retry with Refresh markets, or use manual entry below.';
      return;
    }
    if (selectedGame.market_state === 'unavailable' || String(selectedGame.selection_key || '').startsWith('balldontlie:')) {
      propsStatus.textContent = 'Schedule and scores only. The free Games feed does not include player props or odds. You can record a price manually below.';
      return;
    }
    if (propsError) {
      propsStatus.textContent = 'Unable to load player props. Retry with Refresh markets. Your slip has been kept.';
      return;
    }
    if (!Array.isArray(allProps)) {
      propsStatus.textContent = COPY.loadingSelectedGameProps || 'Loading player props for selected game...';
      return;
    }

    var allGameRows = propsForSelectedGame();
    var rows = applyPropFilter(allGameRows);
    var filteredOut = Math.max(0, allGameRows.length - rows.length);

    if (!rows.length) {
      propsStatus.textContent = allGameRows.length
        ? 'No props match this player or market filter. Clear the search or choose All.'
        : 'No verified current props available for this game. Refresh markets to retry, or record a price manually below.';
      return;
    }

    propsStatus.textContent = 'Showing ' + rows.length + ' current props' +
      (filteredOut ? (' (' + filteredOut + ' hidden by filter).') : '.') +
      ' Select Over or Under to add the quoted line and price.';

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
      line.textContent = String(p.line);

      var btns = document.createElement('div');
      btns.className = 'prop-card-btns';

      var ob = document.createElement('button');
      ob.type = 'button';
      ob.className = 'btn btn-xs btn-outline-success';
      ob.textContent = 'O ' + fmtOdds(p.over_odds);
      ob.disabled = asAmerican(p.over_odds) === null || !quoteEvidence(p, 'over').quote_token;
      ob.addEventListener('click', function () {
        addLeg(Object.assign({
          team_a: p.team_a,
          team_b: p.team_b,
          match_date: p.match_date,
          game_id: p.game_id,
          selection_key: p.selection_key,
          bet_type: 'over',
          player_name: p.player,
          prop_type: p.market,
          prop_line: p.line,
          over_under_line: null,
          picked_team: null,
          american_odds: asAmerican(p.over_odds),
        }, quoteEvidence(p, 'over')));
      });

      var ub = document.createElement('button');
      ub.type = 'button';
      ub.className = 'btn btn-xs btn-outline-danger';
      ub.textContent = 'U ' + fmtOdds(p.under_odds);
      ub.disabled = asAmerican(p.under_odds) === null || !quoteEvidence(p, 'under').quote_token;
      ub.addEventListener('click', function () {
        addLeg(Object.assign({
          team_a: p.team_a,
          team_b: p.team_b,
          match_date: p.match_date,
          game_id: p.game_id,
          selection_key: p.selection_key,
          bet_type: 'under',
          player_name: p.player,
          prop_type: p.market,
          prop_line: p.line,
          over_under_line: null,
          picked_team: null,
          american_odds: asAmerican(p.under_odds),
        }, quoteEvidence(p, 'under')));
      });

      btns.appendChild(ob);
      btns.appendChild(ub);
      card.appendChild(player);
      card.appendChild(market);
      if (p.quote_observed_at) {
        var observed = new Date(p.quote_observed_at);
        if (Number.isFinite(observed.getTime())) {
          var quoteTime = document.createElement('div');
          quoteTime.className = 'prop-card-quote';
          quoteTime.textContent = quoteLabel(p);
          card.appendChild(quoteTime);
        }
      }
      card.appendChild(line);
      card.appendChild(btns);
      propsList.appendChild(card);
    });
  }

  function findGameForLeg(leg) {
    if (!Array.isArray(games) || !games.length) return null;
    if (leg.selection_key) {
      return games.find(function (g) { return g.selection_key === leg.selection_key; }) || null;
    }
    if (leg.game_id) {
      var byId = games.find(function (g) { return String(g.game_id || '') === String(leg.game_id || ''); });
      if (byId) return byId;
    }
    return games.find(function (g) {
      return (g.team_a || '') === (leg.team_a || '') &&
        (g.team_b || '') === (leg.team_b || '') &&
        (g.match_date || '') === (leg.match_date || '');
    }) || null;
  }

  function freshQuote(prop) {
    if (!prop || prop.quote_source !== 'odds_api') return false;
    if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(prop.quote_observed_at || '')) return false;
    var observed = Date.parse(prop.quote_observed_at || '');
    var age = Date.now() - observed;
    return Number.isFinite(observed) && age >= -5000 && age <= quoteMaxAge;
  }

  function quoteEvidence(prop, side) {
    return {
      quote_source: prop.quote_source,
      quote_observed_at: prop.quote_observed_at,
      quote_token: (prop.quote_tokens && prop.quote_tokens[side]) || prop[side + '_quote_token'] || prop.quote_token,
      quote_book: (prop.quote_books && prop.quote_books[side]) || prop['best_' + side + '_book'] || '',
      quote_line: prop.line,
      quote_american_odds: asAmerican(prop[side + '_odds']),
    };
  }

  function quoteLabel(prop) {
    var time = Date.parse(prop.quote_observed_at || '');
    if (!Number.isFinite(time)) return 'Provider quote unavailable';
    var books = prop.quote_books || {};
    var book = prop.quote_book || (books.over && books.under && books.over !== books.under
      ? 'Over: ' + books.over + ' / Under: ' + books.under : books.over || books.under) || prop.best_over_book || prop.best_under_book || '';
    return 'The Odds API' + (book ? ' · ' + book : '') + ' · observed ' +
      new Date(time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' }) + ' ET';
  }

  function currentQuote(leg) {
    if (!leg.player_name || propsError || !Array.isArray(allProps)) return null;
    return allProps.find(function (prop) {
      var sameEvent = prop.selection_key && leg.selection_key
        ? prop.selection_key === leg.selection_key : gameKey(prop) === gameKey(leg);
      return sameEvent && prop.player === leg.player_name && prop.market === leg.prop_type && freshQuote(prop) &&
        asAmerican(prop[leg.bet_type + '_odds']) !== null && quoteEvidence(prop, leg.bet_type).quote_token;
    }) || null;
  }

  function quoteReviews() {
    return slip.filter(function (leg) { return !leg.manual_entry; }).map(function (leg) {
      var current = findGameForLeg(leg) ? currentQuote(leg) : null;
      var changed = !current || current.line !== leg.prop_line ||
        asAmerican(current[leg.bet_type + '_odds']) !== leg.american_odds;
      return changed ? { leg: leg, current: current } : null;
    }).filter(Boolean);
  }

  function refreshSlipOdds() {
    // A refresh only changes the market board; recorded selections stay intact.
    renderSlip();
    if (quoteReviews().length) setFeedback('A selected quote changed or is unavailable. Your recorded line and odds are unchanged; review them before saving.', 'info');
  }

  function finishSaving() {
    saving = false;
    lockedControls.forEach(function (entry) { entry.element.disabled = entry.disabled; });
    lockedControls = [];
    submitBtn.disabled = false;
    submitBtn.textContent = 'Save bet';
  }

  function reviewQuotes(items) {
    reviewItems = items;
    var list = document.getElementById('ub-review-list');
    list.replaceChildren();
    items.forEach(function (item) {
      var row = document.createElement('li');
      var leg = item.leg;
      row.textContent = (leg.player_name || leg.picked_team || 'Game selection') + ': kept ' + leg.bet_type.toUpperCase() + ' ' +
        (leg.prop_line == null ? '' : leg.prop_line + ' ') + fmtOdds(leg.american_odds) +
        (item.current ? ' · now ' + item.current.line + ' at ' + fmtOdds(item.current[leg.bet_type + '_odds']) : ' · current quote unavailable or expired');
      list.appendChild(row);
    });
    document.getElementById('ub-review-update').disabled = items.some(function (item) { return !item.current; });
    finishSaving();
    if (!reviewDialog.open) reviewDialog.showModal();
  }

  function closeReview() {
    reviewDialog.close();
    submitBtn.focus();
  }

  document.getElementById('ub-review-cancel').addEventListener('click', closeReview);
  reviewDialog.addEventListener('close', function () { submitBtn.focus(); });
  document.getElementById('ub-review-original').addEventListener('click', function () {
    reviewItems.forEach(function (item) {
      item.leg.manual_entry = true;
      delete item.leg.quote_token;
    });
    closeReview();
    renderSlip();
    setFeedback('Original selections kept as manual historical records. Review your stake, then save.', 'info');
  });
  document.getElementById('ub-review-update').addEventListener('click', function () {
    reviewItems.forEach(function (item) {
      if (!item.current || !freshQuote(item.current)) return;
      Object.assign(item.leg, quoteEvidence(item.current, item.leg.bet_type));
      item.leg.prop_line = item.current.line;
      item.leg.american_odds = asAmerican(item.current[item.leg.bet_type + '_odds']);
      item.leg.recorded_odds_edited = false;
    });
    closeReview();
    renderSlip();
    setFeedback('Current quotes selected. Review the updated lines and prices, then save.', 'info');
  });

  function submissionFingerprint(payload) {
    var keys = ['team_a', 'team_b', 'match_date', 'game_id', 'selection_key', 'bet_type',
      'player_name', 'prop_type', 'prop_line', 'over_under_line', 'picked_team', 'american_odds', 'manual_entry'];
    return JSON.stringify(Object.assign({}, payload, { legs: payload.legs.map(function (leg) {
      var fields = {};
      keys.forEach(function (key) { fields[key] = leg[key]; });
      return fields;
    }) }));
  }

  async function submitSlip() {
    if (saving) return;
    clearFeedback();
    if (!slip.length) { setFeedback('Add at least one selection.', 'danger'); return; }
    var stake = Number((stakeEl || {}).value || '');
    if (!Number.isFinite(stake) || stake <= 0) {
      setFeedback('Enter a valid stake amount.', 'danger'); stakeEl.focus(); return;
    }
    if (slip.some(function (leg) { return asAmerican(leg.american_odds) === null; })) {
      setFeedback('Enter the recorded American odds for every selection.', 'danger'); return;
    }
    var units = Number((unitsEl || {}).value || '');
    if (!Number.isFinite(units) || units <= 0) units = null;
    var payload = { stake: stake, units: units, is_parlay: slip.length > 1, legs: slip };
    if (rrActive && slip.length >= 3) payload.round_robin = { size: Number(rrSizeEl.value || '2') };
    var fingerprint = submissionFingerprint(payload);
    saving = true;
    lockedControls = Array.from(root.querySelectorAll('input, select, button')).map(function (element) {
      var entry = { element: element, disabled: element.disabled };
      element.disabled = true;
      return entry;
    });
    submitBtn.textContent = 'Checking selections…';
    try {
      if (!pendingSubmission || pendingSubmission.fingerprint !== fingerprint) {
        pendingSubmission = null;
        if (slip.some(function (leg) { return !leg.manual_entry; })) {
          await Promise.all([loadGames(), ensurePropsLoaded(true)]);
        }
        var reviews = quoteReviews();
        if (reviews.length) { reviewQuotes(reviews); return; }
        slip.forEach(function (leg) {
          if (!leg.manual_entry) {
            Object.assign(leg, quoteEvidence(currentQuote(leg), leg.bet_type));
            leg.recorded_odds_edited = false;
          }
        });
        payload.request_id = window.crypto.randomUUID();
        pendingSubmission = { fingerprint: fingerprint, body: JSON.stringify(payload) };
      }
      // An uncertain retry reuses the exact body and key, including its signed
      // evidence. The server can return the previously saved record safely.
      submitBtn.textContent = 'Saving…';
      var controller = new AbortController();
      var timeout = setTimeout(function () { controller.abort(); }, 20000);
      var response;
      try {
        response = await fetch(PLACE_BETS_URL, {
          method: 'POST', signal: controller.signal,
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
          body: pendingSubmission.body,
        });
      } finally { clearTimeout(timeout); }
      var data = await response.json();
      if (data && data.success) {
        window.location.href = '/bets';
        return;
      }
      // A server error can follow a committed write; keep its retry identity.
      if (response.status < 500) pendingSubmission = null;
      setFeedback((typeof data.error === 'string' ? data.error : data.message) || 'Unable to save your bet. Please retry.', 'danger');
      finishSaving();
    } catch (_error) {
      setFeedback('Network error: save could not be confirmed. Retry with the same selections to check safely; your recorded prices are kept.', 'danger');
      finishSaving();
    }
  }

  function rememberView(replace) {
    var url = new URL(window.location.href);
    ['game_id', 'team_a', 'team_b', 'match_date', 'player_name', 'prop_type'].forEach(function (key) { url.searchParams.delete(key); });
    if (selectedGame) url.searchParams.set('game', gameSelectValue(selectedGame));
    else url.searchParams.delete('game');
    if (propFilter !== 'all') url.searchParams.set('market', propFilter);
    else url.searchParams.delete('market');
    if (playerSearch && playerSearch.value.trim()) url.searchParams.set('player', playerSearch.value.trim());
    else url.searchParams.delete('player');
    if (url.href !== window.location.href) window.history[replace ? 'replaceState' : 'pushState']({}, '', url);
  }

  function selectGameByValue(value, fromHistory) {
    selectedGame = games.find(function (game) { return gameSelectValue(game) === value; }) || null;
    gamePicker.value = selectedGame ? gameSelectValue(selectedGame) : '';
    if (!fromHistory) rememberView();
    renderGameMeta();
    renderProps();
    if (selectedGame && selectedGame.market_state !== 'unavailable' && !String(selectedGame.selection_key || '').startsWith('balldontlie:')) ensurePropsLoaded().then(renderProps);
  }

  function restoreView() {
    var params = new URL(window.location.href).searchParams;
    var filter = params.get('market') || params.get('prop_type') || 'all';
    if (!['all', 'player_points', 'player_rebounds', 'player_assists', 'player_threes'].includes(filter)) filter = 'all';
    if (playerSearch) playerSearch.value = (params.get('player') || params.get('player_name') || '').slice(0, 100);
    setFilter(filter);
    var value = params.get('game') || '';
    if (!value && params.get('game_id')) {
      var matches = games.filter(function (game) { return game.game_id && String(game.game_id) === params.get('game_id'); });
      if (matches.length === 1) value = gameSelectValue(matches[0]);
    }
    selectGameByValue(value, true);
  }

  function fetchJson(url) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 10000);
    return fetch(url, { headers: { Accept: 'application/json' }, cache: 'no-store', signal: controller.signal })
      .then(function (response) {
        if (!response.ok || response.redirected) throw new Error('Unavailable');
        return response.json();
      }).finally(function () { clearTimeout(timeout); });
  }

  function loadGames() {
    return fetchJson(UPCOMING_GAMES_URL)
      .then(function (data) {
        if (!Array.isArray(data)) throw new Error('Invalid game list');
        gamesError = false;
        games = data;
        games.sort(function (a, b) {
          return String(a.match_date || '').localeCompare(String(b.match_date || '')) || String(a.label || '').localeCompare(String(b.label || ''));
        });
        return games;
      })
      .catch(function () {
        gamesError = true;
        games = [];
        return games;
      });
  }

  function ensurePropsLoaded(forceRefresh) {
    if (propsPromise) return propsPromise;
    if (!forceRefresh && Array.isArray(allProps) && !propsError) return Promise.resolve(allProps);
    propsStatus.textContent = 'Loading available player props…';
    propsPromise = fetchJson(ALL_PROPS_URL)
      .then(function (data) {
        if (!Array.isArray(data)) throw new Error('Invalid prop list');
        allProps = data;
        propsError = false;
        return allProps;
      })
      .catch(function () {
        allProps = [];
        propsError = true;
        return allProps;
      }).finally(function () { propsPromise = null; });
    return propsPromise;
  }

  function refreshData(manual) {
    if (refreshing) return refreshing;
    refreshBtn.disabled = true;
    refreshBtn.textContent = 'Refreshing…';
    var selectedValue = selectedGame ? gameSelectValue(selectedGame) : '';
    var needsProps = (selectedGame && selectedGame.market_state !== 'unavailable' && !String(selectedGame.selection_key || '').startsWith('balldontlie:')) || slip.some(function (leg) { return !leg.manual_entry; });
    refreshing = Promise.all([loadGames(), needsProps ? ensurePropsLoaded(true) : Promise.resolve()])
      .then(function () {
        selectedGame = games.find(function (game) { return gameSelectValue(game) === selectedValue; }) || null;
        populateGamePicker();
        renderGameMeta();
        renderProps();
        refreshSlipOdds();
        if (!gamesError && !propsError) setLastUpdated(Date.now());
        if (manual && !quoteReviews().length) setFeedback(gamesError || propsError
          ? 'Unable to refresh all markets. Your selections and recorded prices are unchanged. Retry when ready.'
          : 'Markets refreshed. Your recorded selections are unchanged.', 'info');
      }).finally(function () {
        refreshing = null;
        refreshBtn.disabled = false;
        refreshBtn.textContent = 'Refresh markets';
      });
    return refreshing;
  }

  window.addEventListener('popstate', restoreView);
  if (playerSearch) playerSearch.addEventListener('input', function () { renderProps(); rememberView(true); });

  if (gamePicker) {
    gamePicker.addEventListener('change', function () {
      selectGameByValue(gamePicker.value || '');
    });
  }
  if (refreshBtn) {
    refreshBtn.addEventListener('click', function () {
      refreshData(true);
    });
  }


  if (filterAllBtn) filterAllBtn.addEventListener('click', function () { setFilter('all'); rememberView(); });
  if (filterPtsBtn) filterPtsBtn.addEventListener('click', function () { setFilter('player_points'); rememberView(); });
  if (filterRebBtn) filterRebBtn.addEventListener('click', function () { setFilter('player_rebounds'); rememberView(); });
  if (filterAstBtn) filterAstBtn.addEventListener('click', function () { setFilter('player_assists'); rememberView(); });
  if (filter3pmBtn) filter3pmBtn.addEventListener('click', function () { setFilter('player_threes'); rememberView(); });

  if (stakeEl) stakeEl.addEventListener('input', updatePayoutPreview);
  if (submitBtn) submitBtn.addEventListener('click', submitSlip);
  if (clearSlipBtn) clearSlipBtn.addEventListener('click', clearSlip);

  function manualField(name) {
    return document.getElementById('ub-manual-' + name);
  }

  function updateManualFields() {
    if (!manualForm) return;
    var market = manualField('market').value;
    var moneyline = market === 'moneyline';
    var player = !moneyline && market !== 'total';
    var side = manualField('side');
    var options = moneyline ? [['away', 'Away team'], ['home', 'Home team']] : [['over', 'Over'], ['under', 'Under']];
    side.replaceChildren();
    options.forEach(function (item) {
      var option = document.createElement('option');
      option.value = item[0];
      option.textContent = item[1];
      side.appendChild(option);
    });
    document.getElementById('ub-manual-player-wrap').hidden = !player;
    manualField('player').required = player;
    document.getElementById('ub-manual-line-wrap').hidden = moneyline;
    manualField('line').required = !moneyline;
  }

  if (manualForm) {
    document.getElementById('ub-manual-entry').addEventListener('toggle', function (event) {
      if (!event.target.open || !selectedGame) return;
      [['away', 'team_a'], ['home', 'team_b'], ['date', 'match_date']].forEach(function (pair) {
        if (!manualField(pair[0]).value) manualField(pair[0]).value = selectedGame[pair[1]] || '';
      });
    });
    manualField('market').addEventListener('change', updateManualFields);
    updateManualFields();
    manualForm.addEventListener('submit', function (event) {
      event.preventDefault();
      if (!manualForm.reportValidity()) return;
      var market = manualField('market').value;
      var moneyline = market === 'moneyline';
      var player = !moneyline && market !== 'total';
      var away = manualField('away').value.trim();
      var home = manualField('home').value.trim();
      var odds = Number(manualField('odds').value);
      var line = moneyline ? null : Number(manualField('line').value);
      if (!away || !home || away.toLowerCase() === home.toLowerCase()) {
        setFeedback('Enter two different teams for the manual record.', 'danger');
        return;
      }
      if (!Number.isInteger(odds) || Math.abs(odds) < 100 || Math.abs(odds) > 2147483647) {
        setFeedback('Enter recorded American odds, such as -110 or +130.', 'danger');
        return;
      }
      if (!moneyline && (!Number.isFinite(line) || line < 0)) {
        setFeedback('Enter a valid non-negative line.', 'danger');
        return;
      }
      var playerName = player ? manualField('player').value.trim() : null;
      if (player && !playerName) {
        setFeedback('Enter the player for this prop.', 'danger');
        return;
      }
      var side = manualField('side').value;
      var added = addLeg({
        team_a: away, team_b: home, match_date: manualField('date').value,
        game_id: '', bet_type: moneyline ? 'moneyline' : side,
        player_name: playerName, prop_type: player ? market : null,
        prop_line: player ? line : null, over_under_line: market === 'total' ? line : null,
        picked_team: moneyline ? (side === 'home' ? home : away) : null,
        american_odds: odds, manual_entry: true,
      });
      if (added) setFeedback('Manual selection added. Set your stake, then save the record.', 'success');
    });
  }

  renderGameMeta();
  renderSlip();
  setLastUpdated(null);

  refreshData(false).then(restoreView);

  // Refresh only the board while visible; never rewrite recorded selections.
  var oddsRefreshInterval = null;
  function resumeBoardRefresh() {
    if (oddsRefreshInterval !== null) return;
    oddsRefreshInterval = setInterval(function () {
      if (!document.hidden && !saving && !reviewDialog.open) refreshData(false);
    }, 60000);
  }
  resumeBoardRefresh();
  window.addEventListener('pagehide', function () {
    clearInterval(oddsRefreshInterval);
    oddsRefreshInterval = null;
  });
  window.addEventListener('pageshow', function (event) {
    resumeBoardRefresh();
    if (event.persisted) {
      allProps = null;
      renderProps();
      refreshData(false);
    }
  });

  // Round-robin records represent tagged groups; no wagers are executed.
  var rrSection  = document.getElementById('ub-rr-section');
  var rrToggle   = document.getElementById('ub-rr-toggle');
  var rrConfig   = document.getElementById('ub-rr-config');
  var rrSizeEl   = document.getElementById('ub-rr-size');
  var rrSummary  = document.getElementById('ub-rr-summary');
  var rrActive   = false;

  function comb(n, k) {
    if (k < 0 || k > n) return 0;
    if (k === 0 || k === n) return 1;
    var result = 1;
    for (var i = 0; i < k; i++) {
      result = result * (n - i) / (i + 1);
    }
    return Math.round(result);
  }

  function updateRrSection() {
    if (!rrSection) return;
    if (slip.length >= 3) {
      rrSection.classList.remove('d-none');
    } else {
      rrSection.classList.add('d-none');
      deactivateRr();
    }
  }

  function deactivateRr() {
    rrActive = false;
    if (rrToggle) { rrToggle.classList.remove('active'); }
    if (rrConfig) { rrConfig.classList.add('d-none'); }
  }

  function populateRrSizes() {
    if (!rrSizeEl) return;
    rrSizeEl.innerHTML = '';
    var n = slip.length;
    for (var k = 2; k <= n - 1; k++) {
      var opt = document.createElement('option');
      opt.value = k;
      opt.textContent = k + '-team RR (' + comb(n, k) + ' combos)';
      rrSizeEl.appendChild(opt);
    }
  }

  function updateRrSummary() {
    if (!rrSummary || !rrSizeEl) return;
    var n = slip.length;
    var k = parseInt(rrSizeEl.value, 10);
    var stake = parseFloat((stakeEl || {}).value || '') || 0;
    if (!k || !stake) { rrSummary.textContent = 'Select size to preview combinations.'; return; }
    var combos = comb(n, k);
    rrSummary.textContent =
      combos + ' combinations of ' + k + ' · Preview only — bets recorded as tagged group';
  }

  if (rrToggle) {
    rrToggle.addEventListener('click', function () {
      if (rrActive) {
        deactivateRr();
      } else {
        rrActive = true;
        rrToggle.classList.add('active');
        populateRrSizes();
        updateRrSummary();
        if (rrConfig) rrConfig.classList.remove('d-none');
      }
    });
  }
  if (rrSizeEl) {
    rrSizeEl.addEventListener('change', updateRrSummary);
  }
  // Keep the combination preview in sync with the stake.
  if (stakeEl) stakeEl.addEventListener('input', updateRrSummary);
})();
