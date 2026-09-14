/**
 * Drives login.html: the Log In / Sign Up tabs, plus three panels reached
 * from a link rather than a tab -- Forgot Password, and the two pages an
 * emailed link opens (set a new password, confirm an address). Standalone
 * from app.js: this page is served before any session exists, whereas app.js
 * only ever runs once a session cookie (or a /u/<slug>/ link) is already
 * good -- there's no shared state to coordinate between the two.
 */
document.addEventListener("DOMContentLoaded", () => {
    const tabRow = document.querySelector(".auth-tabs");
    const tabs = document.querySelectorAll(".auth-tab");
    const panels = document.querySelectorAll(".auth-panel");
    const status = document.getElementById("auth-status");

    // Tabs highlight when their panel shows. Every other panel hides the tab
    // row altogether: someone choosing a new password isn't also choosing
    // between logging in and signing up.
    function showPanel(name) {
        const tab = Array.from(tabs).find(t => t.dataset.tab === name);
        tabs.forEach(t => t.classList.toggle("active", t === tab));
        tabRow.hidden = !tab;
        panels.forEach(p => { p.hidden = p.dataset.panel !== name; });
        setStatus("");
    }

    tabs.forEach(tab => {
        tab.addEventListener("click", () => showPanel(tab.dataset.tab));
    });
    document.querySelectorAll("[data-show]").forEach(button => {
        button.addEventListener("click", () => showPanel(button.dataset.show));
    });

    function setStatus(message, isError) {
        status.textContent = message;
        status.hidden = !message;
        status.classList.toggle("error", !!isError);
    }

    function setFormBusy(form, busy) {
        form.querySelectorAll("input, button").forEach(el => { el.disabled = busy; });
    }

    async function postJson(url, body) {
        const response = await fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
        const data = await response.json().catch(() => ({}));
        return { ok: response.ok, data };
    }

    // By default a successful submit means the server just set the session
    // cookie, so the practice app takes it from here -- including a brand new
    // account's tier picker (fetchNewSession sees "onboarded": false).
    async function submitAuth(form, url, body, onSuccess = () => { window.location.href = "/"; }) {
        setFormBusy(form, true);
        setStatus("");
        try {
            const { ok, data } = await postJson(url, body);
            if (!ok) {
                setStatus(data.error || "Something went wrong. Please try again.", true);
                setFormBusy(form, false);
                return;
            }
            onSuccess(data);
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
            email: document.getElementById("signup-email").value.trim(),
            password: document.getElementById("signup-password").value
        });
    });

    const forgotForm = document.getElementById("forgot-form");
    forgotForm.addEventListener("submit", (e) => {
        e.preventDefault();
        submitAuth(forgotForm, "/api/password/forgot", {
            email: document.getElementById("forgot-email").value.trim()
        }, () => {
            setFormBusy(forgotForm, false);
            // Worded to be true either way: the server answers identically
            // whether or not the address belongs to an account.
            setStatus("If that address is confirmed on an account, a reset link is on its way. It works for one hour.");
        });
    });

    let resetToken = "";
    const resetForm = document.getElementById("reset-form");
    resetForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const password = document.getElementById("reset-password").value;
        if (password !== document.getElementById("reset-confirm").value) {
            setStatus("The two passwords don't match.", true);
            return;
        }
        submitAuth(resetForm, "/api/password/reset", { token: resetToken, password });
    });

    async function confirmEmail(token) {
        const message = document.getElementById("verify-message");
        try {
            const { ok, data } = await postJson("/api/email/verify", { token });
            message.textContent = ok
                ? `Confirmed. Password reset links for your account will go to ${data.email}.`
                : (data.error || "That link didn't work.");
            message.classList.toggle("error", !ok);
        } catch (err) {
            console.error(err);
            message.textContent = "Can't reach JuziGenius right now. Open the link again in a moment.";
            message.classList.add("error");
        }
        document.getElementById("verify-continue").hidden = false;
    }

    // Emailed links carry their token in the URL fragment (/login#reset=...),
    // which browsers never send to any server -- so it stays out of access
    // logs and Referer headers. Read it once, then scrub it from the address
    // bar and history so it isn't left sitting in either.
    const [hashKey, hashValue = ""] = window.location.hash.slice(1).split("=", 2);
    if (hashKey === "reset" || hashKey === "verify") {
        history.replaceState(null, "", window.location.pathname);
    }

    if (hashKey === "signup") {
        // Landing page's Sign Up button links to /login#signup so it opens
        // straight onto the right form instead of Log In by default.
        showPanel("signup");
    } else if (hashKey === "reset" && hashValue) {
        resetToken = hashValue;
        showPanel("reset");
    } else if (hashKey === "verify" && hashValue) {
        showPanel("verify");
        confirmEmail(hashValue);
    }
});
