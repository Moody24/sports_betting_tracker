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
