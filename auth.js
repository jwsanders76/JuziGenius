/**
 * Drives login.html's Log In / Sign Up forms. Standalone from app.js: this
 * page is served at the site root before any session exists, whereas app.js
 * only ever runs once a session cookie (or a /u/<slug>/ link) is already
 * good -- there's no shared state to coordinate between the two.
 */
document.addEventListener("DOMContentLoaded", () => {
    const tabs = document.querySelectorAll(".auth-tab");
    const panels = document.querySelectorAll(".auth-panel");
    const status = document.getElementById("auth-status");

    function activateTab(name) {
        const tab = Array.from(tabs).find(t => t.dataset.tab === name);
        if (!tab) return;
        tabs.forEach(t => t.classList.toggle("active", t === tab));
        panels.forEach(p => { p.hidden = p.dataset.panel !== name; });
        setStatus("");
    }

    tabs.forEach(tab => {
        tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });

    // Landing page's Sign Up button links to /login#signup so it opens
    // straight onto the right form instead of Log In by default.
    if (window.location.hash === "#signup") activateTab("signup");

    function setStatus(message, isError) {
        status.textContent = message;
        status.hidden = !message;
        status.classList.toggle("error", !!isError);
    }

    function setFormBusy(form, busy) {
        form.querySelectorAll("input, button").forEach(el => { el.disabled = busy; });
    }

    async function submitAuth(form, url, body) {
        setFormBusy(form, true);
        setStatus("");
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                setStatus(data.error || "Something went wrong. Please try again.", true);
                setFormBusy(form, false);
                return;
            }
            // The server just set the session cookie -- the practice app
            // takes it from here, including a brand new account's tier
            // picker (fetchNewSession sees "onboarded": false).
            window.location.href = "/";
        } catch (err) {
            console.error(err);
            setStatus("Can't reach JuziGenius right now. Please try again in a moment.", true);
            setFormBusy(form, false);
        }
    }

    const loginForm = document.getElementById("login-form");
    loginForm.addEventListener("submit", (e) => {
        e.preventDefault();
        submitAuth(loginForm, "/api/login", {
            username: document.getElementById("login-username").value.trim(),
            password: document.getElementById("login-password").value
        });
    });

    const signupForm = document.getElementById("signup-form");
    signupForm.addEventListener("submit", (e) => {
        e.preventDefault();
        submitAuth(signupForm, "/api/signup", {
            invite_code: document.getElementById("signup-invite").value.trim(),
            username: document.getElementById("signup-username").value.trim(),
            password: document.getElementById("signup-password").value
        });
    });
});
