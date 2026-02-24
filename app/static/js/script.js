// Confirm bet deletion
document.querySelectorAll('.delete-bet-form').forEach(function (form) {
  form.addEventListener('submit', function (e) {
    if (!confirm('Are you sure you want to delete this bet?')) {
      e.preventDefault();
    }
  });
});

// Mobile sidebar toggle
(function () {
  var toggle = document.getElementById('sidebar-toggle');
  var sidebar = document.getElementById('sidebar');
  if (!toggle || !sidebar) return;

  // Create overlay element
  var overlay = document.createElement('div');
  overlay.className = 'sidebar-overlay';
  document.body.appendChild(overlay);

  function openSidebar() {
    sidebar.classList.add('open');
    overlay.classList.add('show');
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('show');
  }

  toggle.addEventListener('click', function () {
    if (sidebar.classList.contains('open')) {
      closeSidebar();
    } else {
      openSidebar();
    }
  });

  overlay.addEventListener('click', closeSidebar);
})();
