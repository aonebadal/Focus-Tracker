(() => {
  const navToggle = document.getElementById("navToggle");
  const navLinks = document.getElementById("navLinks");
  const contactSection = document.getElementById("contact");
  const greetingEl = document.getElementById("contactGreeting");

  function closeMenu() {
    if (!navLinks || !navToggle) return;
    navLinks.classList.remove("open");
    navToggle.setAttribute("aria-expanded", "false");
  }

  function openMenu() {
    if (!navLinks || !navToggle) return;
    navLinks.classList.add("open");
    navToggle.setAttribute("aria-expanded", "true");
  }

  function toggleMenu() {
    if (!navLinks) return;
    if (navLinks.classList.contains("open")) {
      closeMenu();
    } else {
      openMenu();
    }
  }

  function getGreetingByTime() {
    const hour = new Date().getHours();
    if (hour < 12) {
      return "Good Morning. Stay focused and make today productive.";
    }
    if (hour < 17) {
      return "Good Afternoon. Keep working towards your goals.";
    }
    return "Good Evening. Take a deep breath and reset your focus.";
  }

  function renderGreeting() {
    if (!greetingEl) return;
    greetingEl.textContent = getGreetingByTime();
  }

  if (navToggle) {
    navToggle.addEventListener("click", toggleMenu);
    navToggle.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggleMenu();
      }
    });
  }

  if (navLinks) {
    navLinks.querySelectorAll("a").forEach((link) => {
      link.addEventListener("click", closeMenu);
    });
  }

  if (contactSection && greetingEl && "IntersectionObserver" in window) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            renderGreeting();
          }
        });
      },
      { threshold: 0.25 }
    );
    observer.observe(contactSection);
  } else {
    renderGreeting();
  }
})();
