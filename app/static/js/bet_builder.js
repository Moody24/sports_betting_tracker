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
    player_points_rebounds_assists: 'PTS+REB+AST',
    player_points_rebounds: 'PTS+REB',
    player_points_assists: 'PTS+AST',
    player_rebounds_assists: 'REB+AST',
  };

  // ── Tab switching ─────────────────────────────────────────────────
  const tabs   = document.querySelectorAll('[data-bb-tab]');
  const panels = document.querySelectorAll('[data-bb-panel]');

  function showTab(name) {
    const validTabs = ['single', 'prop', 'parlay', 'screenshot'];
    tabs.forEach(t => t.classList.toggle('active', t.dataset.bbTab === name));
    panels.forEach(p => p.classList.toggle('d-none', p.dataset.bbPanel !== name));
  }

  tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.bbTab)));

  const hash = location.hash.replace('#', '') || 'single';
  showTab(['single', 'prop', 'parlay', 'screenshot'].includes(hash) ? hash : 'single');

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

  function autofillFromPicker(inputEl, teamAEl, teamBEl, dateEl, gameIdEl, ouLineEl) {
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
    });
  }

  autofillFromPicker(
    document.getElementById('single-game-picker'),
    document.getElementById('single-team-a'),
    document.getElementById('single-team-b'),
    document.getElementById('single-match-date'),
    document.getElementById('single-game-id'),
    document.getElementById('single-ou-line')
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
    });
  }

  function renumberLegs() {
    document.querySelectorAll('.parlay-leg').forEach((el, i) => {
      const label = el.querySelector('.small.fw-semibold');
      if (label) label.textContent = `Leg ${i + 1}`;
    });
  }

  const legsContainer = document.getElementById('parlay-legs');
  const addLegBtn = document.getElementById('add-leg-btn');

  if (addLegBtn && legsContainer) {
    addLegBtn.addEventListener('click', function () {
      legsContainer.insertAdjacentHTML('beforeend', makeLegHTML(legCount++));
      const newLeg = legsContainer.lastElementChild;
      bindLegEvents(newLeg);
    });

    legsContainer.insertAdjacentHTML('beforeend', makeLegHTML(legCount++));
    bindLegEvents(legsContainer.lastElementChild);
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
        body: JSON.stringify({ stake, outcome, legs, bonus_multiplier: bonusMult }),
      })
        .then(r => r.json())
        .then(data => {
          if (data.success) {
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

  if (loadPropsBtn) {
    loadPropsBtn.addEventListener('click', function () {
      if (allPropsLoaded) {
        propsBrowser.style.display = '';
        propsSearchInp.style.display = '';
        return;
      }

      loadPropsBtn.disabled = true;
      loadPropsBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Loading...';

      fetch(ALL_PROPS_URL)
        .then(r => r.json())
        .then(data => {
          allPropsData = data;
          allPropsLoaded = true;
          renderPropsBrowser(data);
          propsBrowser.style.display = '';
          propsSearchInp.style.display = '';
          loadPropsBtn.innerHTML = '<i class="bi bi-check-lg me-1"></i>Loaded ' + data.length + ' props';
        })
        .catch(() => {
          loadPropsBtn.disabled = false;
          loadPropsBtn.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Failed — retry';
          showFeedback('Could not load props. Check that ODDS_API_KEY is set.', 'warning');
        });
    });
  }

  if (propsSearchInp) {
    propsSearchInp.addEventListener('input', function () {
      if (!allPropsData) return;
      var q = this.value.toLowerCase().trim();
      var filtered = q
        ? allPropsData.filter(p =>
            p.player.toLowerCase().includes(q) ||
            (MARKET_LABELS[p.market] || p.market).toLowerCase().includes(q))
        : allPropsData;
      renderPropsBrowser(filtered);
    });
  }

  function renderPropsBrowser(props) {
    if (!propsBrowser) return;
    if (!props.length) {
      propsBrowser.innerHTML = '<p class="small text-secondary text-center py-2">No props match your search.</p>';
      return;
    }

    var html = '<table class="table table-sm table-dark table-hover mb-0" style="font-size:.8rem">';
    html += '<thead><tr>'
      + '<th>Player</th><th>Market</th><th>Line</th>'
      + '<th class="text-success">Over</th><th class="text-danger">Under</th>'
      + '<th></th>'
      + '</tr></thead><tbody>';

    props.forEach(function (p) {
      var marketLabel = MARKET_LABELS[p.market] || p.market.replace('player_', '');
      var overOdds  = p.over_odds  > 0 ? '+' + p.over_odds  : p.over_odds;
      var underOdds = p.under_odds > 0 ? '+' + p.under_odds : p.under_odds;
      html += '<tr>'
        + '<td>' + p.player + '</td>'
        + '<td class="text-secondary">' + marketLabel + '</td>'
        + '<td>' + p.line + '</td>'
        + '<td class="text-success">' + overOdds + '</td>'
        + '<td class="text-danger">' + underOdds + '</td>'
        + '<td>'
        + '<button class="btn btn-xs btn-outline-success me-1 prop-browse-btn"'
        + ' data-player="' + p.player + '" data-market="' + p.market + '"'
        + ' data-line="' + p.line + '" data-odds="' + p.over_odds + '"'
        + ' data-side="over"'
        + ' data-team-a="' + p.team_a + '" data-team-b="' + p.team_b + '"'
        + ' data-date="' + p.match_date + '" data-game-id="' + p.game_id + '">O</button>'
        + '<button class="btn btn-xs btn-outline-danger prop-browse-btn"'
        + ' data-player="' + p.player + '" data-market="' + p.market + '"'
        + ' data-line="' + p.line + '" data-odds="' + p.under_odds + '"'
        + ' data-side="under"'
        + ' data-team-a="' + p.team_a + '" data-team-b="' + p.team_b + '"'
        + ' data-date="' + p.match_date + '" data-game-id="' + p.game_id + '">U</button>'
        + '</td>'
        + '</tr>';
    });

    html += '</tbody></table>';
    propsBrowser.innerHTML = html;

    propsBrowser.querySelectorAll('.prop-browse-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        // Autofill the prop form
        const side = btn.dataset.side;
        const propTypeEl = document.getElementById('prop-prop-type');
        const playerEl   = document.getElementById('prop-player-name');
        const lineEl     = document.getElementById('prop-prop-line');
        const betTypeEl  = document.getElementById('prop-bet-type');
        const teamAEl    = document.getElementById('prop-team-a');
        const teamBEl    = document.getElementById('prop-team-b');
        const dateEl     = document.getElementById('prop-match-date');
        const gameIdEl   = document.getElementById('prop-game-id');

        if (playerEl)   playerEl.value   = btn.dataset.player;
        if (propTypeEl) propTypeEl.value  = btn.dataset.market;
        if (lineEl)     lineEl.value      = btn.dataset.line;
        if (betTypeEl)  betTypeEl.value   = side;
        if (teamAEl)    teamAEl.value     = btn.dataset.teamA;
        if (teamBEl)    teamBEl.value     = btn.dataset.teamB;
        if (dateEl)     dateEl.value      = btn.dataset.date;
        if (gameIdEl)   gameIdEl.value    = btn.dataset.gameId;

        // Scroll to form and focus stake
        document.getElementById('prop-stake').scrollIntoView({ behavior: 'smooth', block: 'center' });
        document.getElementById('prop-stake').focus();

        // Visual feedback
        btn.classList.add('active');
        setTimeout(() => btn.classList.remove('active'), 800);
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
        setVal('ocr-prop-line', data.prop_line);
        setVal('ocr-odds',      data.american_odds);
        setVal('ocr-stake',     data.stake);
        setVal('ocr-team-a',    data.team_a);
        setVal('ocr-team-b',    data.team_b);

        const ocrBetType = document.getElementById('ocr-bet-type');
        if (ocrBetType && data.bet_type) ocrBetType.value = data.bet_type;

        const ocrPropType = document.getElementById('ocr-prop-type');
        if (ocrPropType && data.prop_type) ocrPropType.value = data.prop_type;

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

})();
