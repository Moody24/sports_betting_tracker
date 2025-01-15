// Confirm Bet Deletion
document.querySelectorAll('.delete-bet-form').forEach(form => {
    form.addEventListener('submit', function (e) {
        if (!confirm('Are you sure you want to delete this bet?')) {
            e.preventDefault();
        }
    });
});

// Theme Toggle Functionality
const themeSwitchButton = document.getElementById('theme-switch');
if (themeSwitchButton) {
    themeSwitchButton.addEventListener('click', () => {
        const isDarkMode = document.body.classList.toggle('dark-mode');
        localStorage.setItem('darkMode', isDarkMode ? 'enabled' : 'disabled');
    });

    // Initialize theme from localStorage
    if (localStorage.getItem('darkMode') === 'enabled') {
        document.body.classList.add('dark-mode');
    }
}

// Real-Time Clock Display
const clockElement = document.getElementById('clock');
if (clockElement) {
    const updateClock = () => {
        const now = new Date();
        clockElement.textContent = now.toLocaleTimeString();
    };
    updateClock();
    setInterval(updateClock, 1000);
}

// Toggle Navbar on Mobile
const navbarToggle = document.getElementById('navbar-toggle');
const navbarMenu = document.getElementById('navbar-menu');
if (navbarToggle && navbarMenu) {
    navbarToggle.addEventListener('click', () => {
        navbarMenu.classList.toggle('open');
    });
}

// Highlight Active Navigation Link
const navLinks = document.querySelectorAll('nav a');
navLinks.forEach(link => {
    if (link.href === window.location.href) {
        link.classList.add('active');
    }
});
