/* bet_builder.js — powers the 3-tab bet builder at /bets/new */
(function () {
  'use strict';

  // ── Tab switching ─────────────────────────────────────────────────
  const tabs = document.querySelectorAll('[data-bb-tab]');
  const panels = document.querySelectorAll('[data-bb-panel]');

  function showTab(name) {
    tabs.forEach(t => t.classList.toggle('active', t.dataset.bbTab === name));
    panels.forEach(p => p.classList.toggle('d-none', p.dataset.bbPanel !== name));
  }

  tabs.forEach(t => t.addEventListener('click', () => showTab(t.dataset.bbTab)));

  // Show the tab highlighted in the URL hash, or default to 'single'
  const hash = location.hash.replace('#', '') || 'single';
  showTab(['single', 'prop', 'parlay'].includes(hash) ? hash : 'single');

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
    .catch(() => {}); // silently ignore if API is unavailable

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

  // Wire up each tab's picker
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
  const ouGroup = document.getElementById('single-ou-group');
  const pickedGroup = document.getElementById('single-picked-group');

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
    // Game picker autofill
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

    // Bet type toggle
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

    // Remove button
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

    // Add first leg on load
    legsContainer.insertAdjacentHTML('beforeend', makeLegHTML(legCount++));
    bindLegEvents(legsContainer.lastElementChild);
  }

  // ── Parlay submit ─────────────────────────────────────────────────
  const parlayForm = document.getElementById('parlay-form');
  const parlayFeedback = document.getElementById('parlay-feedback');

  if (parlayForm) {
    parlayForm.addEventListener('submit', function (e) {
      e.preventDefault();
      const stake = parseFloat(document.getElementById('parlay-stake').value);
      const outcome = document.getElementById('parlay-outcome').value;

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
        const teamA = legEl.querySelector('.leg-team-a').value.trim();
        const teamB = legEl.querySelector('.leg-team-b').value.trim();
        const date  = legEl.querySelector('.leg-date').value;
        const sel   = legEl.querySelector('.leg-bet-type');
        const betType = sel.value;
        const isProp  = sel.options[sel.selectedIndex].dataset.prop === '1';

        if (!teamA || !teamB || !date) {
          valid = false;
          return;
        }

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
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': CSRF_TOKEN,
        },
        body: JSON.stringify({ stake, outcome, legs }),
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
})();
