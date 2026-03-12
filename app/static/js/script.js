// Confirm bet deletion
(function () {
  'use strict';

  document.querySelectorAll('.delete-bet-form').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      if (!confirm('Are you sure you want to delete this bet?')) {
        e.preventDefault();
      }
    });
  });

  var toggle = document.getElementById('sidebar-toggle');
  var sidebar = document.getElementById('sidebar');
  if (!toggle || !sidebar) return;

  var overlay = document.createElement('button');
  overlay.type = 'button';
  overlay.className = 'sidebar-overlay';
  overlay.setAttribute('aria-label', 'Close navigation');
  overlay.hidden = true;
  document.body.appendChild(overlay);

  var lastFocused = null;

  function isMobileDrawer() {
    return window.matchMedia('(max-width: 991.98px)').matches;
  }

  function getFocusableInSidebar() {
    return Array.from(
      sidebar.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')
    ).filter(function (el) {
      return !el.hasAttribute('disabled') && el.offsetParent !== null;
    });
  }

  function lockBody() {
    document.body.classList.add('body-scroll-lock');
  }

  function unlockBody() {
    document.body.classList.remove('body-scroll-lock');
  }

  function openSidebar() {
    if (!isMobileDrawer()) return;
    lastFocused = document.activeElement;
    sidebar.classList.add('open');
    overlay.hidden = false;
    overlay.classList.add('show');
    toggle.setAttribute('aria-expanded', 'true');
    lockBody();
    var focusables = getFocusableInSidebar();
    if (focusables.length) focusables[0].focus();
  }

  function closeSidebar(restoreFocus) {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
    overlay.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    unlockBody();
    if (restoreFocus !== false && isMobileDrawer()) {
      (lastFocused && document.contains(lastFocused) ? lastFocused : toggle).focus();
    }
  }

  function trapFocus(e) {
    if (!isMobileDrawer() || !sidebar.classList.contains('open') || e.key !== 'Tab') return;
    var focusables = getFocusableInSidebar();
    if (!focusables.length) return;

    var first = focusables[0];
    var last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  toggle.addEventListener('click', function () {
    if (sidebar.classList.contains('open')) {
      closeSidebar();
    } else {
      openSidebar();
    }
  });

  overlay.addEventListener('click', function () {
    closeSidebar();
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar.classList.contains('open')) {
      closeSidebar();
      return;
    }
    trapFocus(e);
  });

  sidebar.querySelectorAll('.nav-link').forEach(function (link) {
    link.addEventListener('click', function () {
      if (isMobileDrawer()) closeSidebar(false);
    });
  });

  window.addEventListener('resize', function () {
    if (!isMobileDrawer()) {
      closeSidebar(false);
      overlay.hidden = true;
      overlay.classList.remove('show');
      unlockBody();
    }
  });
})();
