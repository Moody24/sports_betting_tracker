/**
 * Bet Slip & Player Props — NBA Today
 *
 * Handles:
 *  - Loading player props per game via AJAX
 *  - Adding/removing props to a floating bet slip
 *  - Bonus bet multiplier with live payout preview
 *  - Placing single bets or parlays via JSON POST
 */
(function () {
  'use strict';

  // ── State ──────────────────────────────────────────────────────
  var slip = [];
  var propsCache = {};
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
    player_rebounds_assists: 'REB+AST'
  };

  // ── DOM refs ───────────────────────────────────────────────────
  var slipEl          = document.getElementById('bet-slip');
  var slipLegsEl      = document.getElementById('slip-legs');
  var slipEmptyEl     = document.getElementById('slip-empty');
  var slipControls    = document.getElementById('slip-controls');
  var slipCount       = document.getElementById('slip-count');
  var slipStake       = document.getElementById('slip-stake');
  var slipBonusMult   = document.getElementById('slip-bonus-multiplier');
  var slipBonusLabel  = document.getElementById('slip-bonus-label');
  var slipBonusPayout = document.getElementById('slip-bonus-payout');
  var slipPayoutInfo  = document.getElementById('slip-payout-info');
  var parlayToggle    = document.getElementById('parlay-toggle');
  var parlayOdds      = document.getElementById('parlay-odds-display');
  var slipBody        = document.getElementById('slip-body');
  var slipToggle      = document.getElementById('slip-toggle-btn');

  // ── Bet Slip: toggle collapse ──────────────────────────────────
  var slipCollapsed = false;
  slipToggle.addEventListener('click', function () {
    slipCollapsed = !slipCollapsed;
    slipBody.style.display = slipCollapsed ? 'none' : '';
    slipToggle.querySelector('i').className = slipCollapsed
      ? 'bi bi-chevron-up' : 'bi bi-chevron-down';
  });

  // ── Props: load on click ───────────────────────────────────────
  document.querySelectorAll('.props-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var espnId = btn.dataset.espnId;
      var container = document.getElementById('props-' + espnId);

      if (container.style.display !== 'none') {
        container.style.display = 'none';
        btn.classList.remove('active');
        return;
      }
      container.style.display = '';
      btn.classList.add('active');

      if (propsCache[espnId]) {
        renderProps(espnId, propsCache[espnId], btn);
        return;
      }

      var url = PROPS_URL.replace('__ESPN_ID__', espnId);
      fetch(url)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          propsCache[espnId] = data;
          renderProps(espnId, data, btn);
        })
        .catch(function () {
          container.innerHTML =
            '<div class="small text-danger py-2 text-center">' +
            '<i class="bi bi-exclamation-triangle me-1"></i>Failed to load props. ' +
            'Make sure <code>ODDS_API_KEY</code> is set.</div>';
        });
    });
  });

  function renderProps(espnId, data, btn) {
    var container = document.getElementById('props-' + espnId);
    var away = btn.dataset.away;
    var home = btn.dataset.home;
    var matchDate = btn.dataset.date;
    var markets = Object.keys(data);

    if (!markets.length) {
      container.innerHTML =
        '<div class="small text-secondary py-2 text-center">' +
        'No player props available for this game.</div>';
      return;
    }

    var html = '';
    markets.forEach(function (market) {
      var label = MARKET_LABELS[market] || market.replace('player_', '').replace(/_/g, ' ');
      html += '<div class="prop-market-group">';
      html += '<div class="prop-market-label">' + escapeHtml(label) + '</div>';

      data[market].forEach(function (prop) {
        var legId = espnId + '_' + market + '_' + prop.player.replace(/\s/g, '_');
        var inSlip = slip.some(function (l) { return l.id === legId; });

        html += '<div class="prop-row">';
        html += '  <div class="prop-player">' + escapeHtml(prop.player) + '</div>';
        html += '  <div class="prop-line">' + escapeHtml(prop.line) + '</div>';
        html += '  <div class="prop-actions">';

        var overActive = inSlip && slip.some(function (l) { return l.id === legId && l.bet_type === 'over'; });
        html += '    <button class="btn btn-xs prop-btn ' + (overActive ? 'prop-btn-active' : 'btn-outline-success') + '"'
          + ' data-leg-id="' + escapeHtml(legId) + '"'
          + ' data-side="over"'
          + ' data-player="' + escapeHtml(prop.player) + '"'
          + ' data-market="' + escapeHtml(market) + '"'
          + ' data-line="' + escapeHtml(prop.line) + '"'
          + ' data-odds="' + escapeHtml(prop.over_odds) + '"'
          + ' data-espn="' + escapeHtml(espnId) + '"'
          + ' data-away="' + escapeHtml(away) + '"'
          + ' data-home="' + escapeHtml(home) + '"'
          + ' data-date="' + escapeHtml(matchDate) + '"'
          + '>O ' + escapeHtml(formatOdds(prop.over_odds)) + '</button>';

        var underActive = inSlip && slip.some(function (l) { return l.id === legId && l.bet_type === 'under'; });
        html += '    <button class="btn btn-xs prop-btn ' + (underActive ? 'prop-btn-active' : 'btn-outline-danger') + '"'
          + ' data-leg-id="' + escapeHtml(legId) + '"'
          + ' data-side="under"'
          + ' data-player="' + escapeHtml(prop.player) + '"'
          + ' data-market="' + escapeHtml(market) + '"'
          + ' data-line="' + escapeHtml(prop.line) + '"'
          + ' data-odds="' + escapeHtml(prop.under_odds) + '"'
          + ' data-espn="' + escapeHtml(espnId) + '"'
          + ' data-away="' + escapeHtml(away) + '"'
          + ' data-home="' + escapeHtml(home) + '"'
          + ' data-date="' + escapeHtml(matchDate) + '"'
          + '>U ' + escapeHtml(formatOdds(prop.under_odds)) + '</button>';

        html += '  </div>';
        html += '</div>';
      });

      html += '</div>';
    });

    container.innerHTML = html;
    container.querySelectorAll('.prop-btn').forEach(function (b) {
      b.addEventListener('click', function () { handlePropClick(b); });
    });
  }

  function formatOdds(odds) {
    if (!odds) return '--';
    return odds > 0 ? '+' + odds : '' + odds;
  }

  // ── Add / Remove from slip ─────────────────────────────────────
  function handlePropClick(btn) {
    var legId = btn.dataset.legId;
    var side = btn.dataset.side;

    var existIdx = -1;
    slip.forEach(function (l, i) {
      if (l.id === legId && l.bet_type === side) existIdx = i;
    });

    if (existIdx >= 0) {
      slip.splice(existIdx, 1);
    } else {
      slip = slip.filter(function (l) { return l.id !== legId; });
      slip.push({
        id: legId,
        player_name: btn.dataset.player,
        prop_type: btn.dataset.market,
        prop_line: parseFloat(btn.dataset.line),
        bet_type: side,
        american_odds: parseInt(btn.dataset.odds, 10),
        game_id: btn.dataset.espn,
        team_a: btn.dataset.away,
        team_b: btn.dataset.home,
        match_date: btn.dataset.date
      });
    }

    refreshSlipUI();
    refreshPropButtons();
  }

  function refreshPropButtons() {
    document.querySelectorAll('.prop-btn').forEach(function (btn) {
      var legId = btn.dataset.legId;
      var side = btn.dataset.side;
      var active = slip.some(function (l) { return l.id === legId && l.bet_type === side; });
      if (active) {
        btn.classList.add('prop-btn-active');
        btn.classList.remove('btn-outline-success', 'btn-outline-danger');
      } else {
        btn.classList.remove('prop-btn-active');
        btn.classList.add(side === 'over' ? 'btn-outline-success' : 'btn-outline-danger');
      }
    });
  }

  // ── Render Slip ────────────────────────────────────────────────
  function refreshSlipUI() {
    slipCount.textContent = slip.length;

    if (slip.length === 0) {
      slipEl.style.display = 'none';
      slipEmptyEl.style.display = '';
      slipControls.style.display = 'none';
      slipLegsEl.innerHTML = '';
      return;
    }

    slipEl.style.display = '';
    slipEmptyEl.style.display = 'none';
    slipControls.style.display = '';

    var html = '';
    slip.forEach(function (leg, i) {
      var label = MARKET_LABELS[leg.prop_type] || leg.prop_type.replace('player_', '');
      html += '<div class="slip-leg">';
      html += '  <div class="d-flex justify-content-between align-items-start">';
      html += '    <div>';
      html += '      <div class="slip-leg-player">' + escapeHtml(leg.player_name) + '</div>';
      html += '      <div class="slip-leg-detail">'
        + escapeHtml(leg.bet_type.charAt(0).toUpperCase() + leg.bet_type.slice(1)) + ' '
        + escapeHtml(leg.prop_line) + ' ' + escapeHtml(label)
        + ' <span class="slip-leg-odds">' + escapeHtml(formatOdds(leg.american_odds)) + '</span></div>';
      html += '      <div class="slip-leg-game">' + escapeHtml(leg.team_a) + ' @ ' + escapeHtml(leg.team_b) + '</div>';
      html += '    </div>';
      html += '    <button class="btn btn-sm border-0 text-secondary slip-remove" data-index="' + i + '">';
      html += '      <i class="bi bi-x-lg"></i>';
      html += '    </button>';
      html += '  </div>';
      html += '</div>';
    });
    slipLegsEl.innerHTML = html;

    slipLegsEl.querySelectorAll('.slip-remove').forEach(function (btn) {
      btn.addEventListener('click', function () {
        slip.splice(parseInt(btn.dataset.index, 10), 1);
        refreshSlipUI();
        refreshPropButtons();
      });
    });

    updateParlayOdds();
    updatePayoutPreview();

    if (slip.length < 2) {
      parlayToggle.checked = false;
      parlayToggle.parentElement.style.display = 'none';
      parlayOdds.style.display = 'none';
    } else {
      parlayToggle.parentElement.style.display = '';
    }
  }

  // ── Parlay odds display ────────────────────────────────────────
  function updateParlayOdds() {
    if (!parlayToggle.checked || slip.length < 2) {
      parlayOdds.style.display = 'none';
      return;
    }

    var decimalProduct = 1;
    slip.forEach(function (leg) {
      var dec;
      if (leg.american_odds > 0) {
        dec = 1 + (leg.american_odds / 100);
      } else if (leg.american_odds < 0) {
        dec = 1 + (100 / Math.abs(leg.american_odds));
      } else {
        dec = 1;
      }
      decimalProduct *= dec;
    });

    var american;
    if (decimalProduct >= 2) {
      american = '+' + Math.round((decimalProduct - 1) * 100);
    } else {
      american = '-' + Math.round(100 / (decimalProduct - 1));
    }

    parlayOdds.style.display = '';
    parlayOdds.textContent = 'Parlay Odds: ' + american;
  }

  // ── Bonus multiplier + payout preview ─────────────────────────
  function getBonusMultiplier() {
    if (!slipBonusMult) return 1.0;
    var v = parseFloat(slipBonusMult.value);
    return (isNaN(v) || v < 1) ? 1.0 : v;
  }

  function updatePayoutPreview() {
    var stake = parseFloat(slipStake ? slipStake.value : '0') || 0;
    var mult = getBonusMultiplier();
    var isParlay = parlayToggle && parlayToggle.checked && slip.length >= 2;

    if (slipBonusLabel) {
      slipBonusLabel.textContent = mult > 1.0 ? '×' + mult.toFixed(2) + ' active' : '';
    }

    if (slipBonusPayout) {
      if (mult > 1.0 && stake > 0) {
        slipBonusPayout.style.display = '';
        slipBonusPayout.textContent = 'Bonus active — payouts multiplied by ' + mult.toFixed(2);
      } else {
        slipBonusPayout.style.display = 'none';
      }
    }

    if (!slipPayoutInfo) return;
    if (!stake || !slip.length) {
      slipPayoutInfo.style.display = 'none';
      return;
    }

    var payoutText = '';
    if (isParlay) {
      var decProd = 1;
      slip.forEach(function (leg) {
        if (leg.american_odds > 0) {
          decProd *= 1 + leg.american_odds / 100;
        } else if (leg.american_odds < 0) {
          decProd *= 1 + 100 / Math.abs(leg.american_odds);
        }
      });
      var profit = stake * (decProd - 1) * mult;
      payoutText = 'Est. profit: $' + profit.toFixed(2) +
        (mult > 1 ? ' (\xd7' + mult.toFixed(2) + ' bonus)' : '');
    } else if (slip.length === 1) {
      var leg = slip[0];
      var legProfit = 0;
      if (leg.american_odds > 0) {
        legProfit = stake * leg.american_odds / 100;
      } else if (leg.american_odds < 0) {
        legProfit = stake * 100 / Math.abs(leg.american_odds);
      }
      legProfit *= mult;
      payoutText = 'Est. profit: $' + legProfit.toFixed(2) +
        (mult > 1 ? ' (\xd7' + mult.toFixed(2) + ' bonus)' : '');
    } else {
      payoutText = slip.length + ' singles \xb7 $' + (stake * slip.length).toFixed(2) + ' total wagered';
    }

    slipPayoutInfo.style.display = '';
    slipPayoutInfo.textContent = payoutText;
  }

  if (slipBonusMult) {
    slipBonusMult.addEventListener('input', updatePayoutPreview);
  }
  if (slipStake) {
    slipStake.addEventListener('input', updatePayoutPreview);
  }
  parlayToggle.addEventListener('change', function () {
    updateParlayOdds();
    updatePayoutPreview();
  });

  // ── Clear slip ─────────────────────────────────────────────────
  document.getElementById('slip-clear').addEventListener('click', function () {
    slip = [];
    refreshSlipUI();
    refreshPropButtons();
  });

  // ── Submit bets ────────────────────────────────────────────────
  document.getElementById('slip-submit').addEventListener('click', function () {
    var stake = parseFloat(slipStake.value);
    if (!stake || stake <= 0) {
      alert('Enter a valid stake amount.');
      return;
    }

    var isParlay = parlayToggle.checked && slip.length >= 2;
    var bonusMult = getBonusMultiplier();

    var payload = {
      stake: stake,
      is_parlay: isParlay,
      bonus_multiplier: bonusMult,
      legs: slip.map(function (l) {
        return {
          player_name: l.player_name,
          prop_type: l.prop_type,
          prop_line: l.prop_line,
          bet_type: l.bet_type,
          american_odds: l.american_odds,
          team_a: l.team_a,
          team_b: l.team_b,
          game_id: l.game_id,
          match_date: l.match_date
        };
      })
    };

    var submitBtn = document.getElementById('slip-submit');
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Placing...';

    fetch(PLACE_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': CSRF_TOKEN
      },
      body: JSON.stringify(payload)
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (data.success) {
        slipLegsEl.innerHTML =
          '<div class="text-center py-3 text-success">'
          + '<i class="bi bi-check-circle" style="font-size:1.5rem"></i>'
          + '<p class="mb-0 mt-1 small">' + data.message + '</p>'
          + '</div>';
        slip = [];
        slipControls.style.display = 'none';
        slipCount.textContent = '0';
        refreshPropButtons();

        setTimeout(function () {
          refreshSlipUI();
        }, 2500);
      } else {
        alert(data.error || 'Something went wrong.');
      }
    })
    .catch(function () {
      alert('Network error. Please try again.');
    })
    .finally(function () {
      submitBtn.disabled = false;
      submitBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Place Bet';
    });
  });
})();
