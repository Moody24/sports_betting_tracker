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

// Owned replacements for the small Bootstrap behavior surface used by templates.
(function () {
  'use strict';

  var activeModal = null;
  var modalReturnFocus = null;

  function closeDropdowns(except) {
    document.querySelectorAll('.dropdown-menu.show').forEach(function (menu) {
      if (menu === except) return;
      menu.classList.remove('show');
      var trigger = menu.closest('.dropdown')?.querySelector('[data-ui-toggle="dropdown"]');
      if (trigger) trigger.setAttribute('aria-expanded', 'false');
    });
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.remove('show');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('body-scroll-lock');
    activeModal = null;
    if (modalReturnFocus && document.contains(modalReturnFocus)) modalReturnFocus.focus();
    modalReturnFocus = null;
  }

  function openModal(modal) {
    if (!modal) return;
    modalReturnFocus = document.activeElement;
    activeModal = modal;
    modal.classList.add('show');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('body-scroll-lock');
    var focusTarget = modal.querySelector('[autofocus], button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
    if (focusTarget) focusTarget.focus();
  }

  function showToast(toast, options) {
    if (!toast) return;
    var delay = options?.delay || 2200;
    toast.classList.add('show');
    window.clearTimeout(toast._edgeTimer);
    toast._edgeTimer = window.setTimeout(function () {
      toast.classList.remove('show');
    }, delay);
  }

  window.EdgeUI = {
    closeModal: closeModal,
    openModal: openModal,
    showToast: showToast
  };

  document.addEventListener('click', function (event) {
    var dropdownTrigger = event.target.closest('[data-ui-toggle="dropdown"]');
    if (dropdownTrigger) {
      event.preventDefault();
      var menu = dropdownTrigger.closest('.dropdown')?.querySelector('.dropdown-menu');
      if (!menu) return;
      var opening = !menu.classList.contains('show');
      closeDropdowns(menu);
      menu.classList.toggle('show', opening);
      dropdownTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
      return;
    }

    var collapseTrigger = event.target.closest('[data-ui-toggle="collapse"]');
    if (collapseTrigger) {
      event.preventDefault();
      var selector = collapseTrigger.getAttribute('data-ui-target');
      var target = selector ? document.querySelector(selector) : null;
      if (!target) return;
      var opening = !target.classList.contains('show');
      target.classList.toggle('show', opening);
      collapseTrigger.classList.toggle('collapsed', !opening);
      collapseTrigger.setAttribute('aria-expanded', opening ? 'true' : 'false');
      return;
    }

    var modalDismiss = event.target.closest('[data-ui-dismiss="modal"]');
    if (modalDismiss) {
      closeModal(modalDismiss.closest('.modal'));
      return;
    }

    if (activeModal && event.target === activeModal) {
      closeModal(activeModal);
      return;
    }

    if (!event.target.closest('.dropdown')) closeDropdowns();
  });

  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') return;
    if (activeModal) {
      closeModal(activeModal);
    } else {
      closeDropdowns();
    }
  });
})();
