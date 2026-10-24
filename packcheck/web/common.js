/* Shared by every page: translated strings, login, small helpers.

   Every sentence a page shows comes from strings.json, so that the tool can be offered in
   French and English (and more) without touching the pages. In the HTML, an element with
   data-t="some.key" gets that sentence as its text; in JavaScript, t("some.key") returns it.
*/

const $ = (id) => document.getElementById(id);

const Strings = { all: null, language: "en" };

async function loadStrings() {
  Strings.all = await (await fetch("/static/strings.json")).json();
  let saved = null;
  try { saved = localStorage.getItem("packcheck_language"); } catch (error) { /* private mode: fine */ }
  const browser = (navigator.language || "en").slice(0, 2);
  Strings.language = saved || (Strings.all[browser] ? browser : "en");
  applyStrings();
}

function t(key, values = {}) {
  const table = Strings.all ? (Strings.all[Strings.language] || Strings.all.en) : {};
  let sentence = table[key] || (Strings.all && Strings.all.en[key]) || key;
  for (const [name, value] of Object.entries(values)) sentence = sentence.replaceAll(`{${name}}`, value);
  return sentence;
}

function applyStrings() {
  document.documentElement.lang = Strings.language;
  for (const element of document.querySelectorAll("[data-t]")) element.textContent = t(element.dataset.t);
  for (const element of document.querySelectorAll("[data-t-placeholder]")) element.placeholder = t(element.dataset.tPlaceholder);
  for (const element of document.querySelectorAll("[data-t-title]")) document.title = t(element.dataset.tTitle);
  for (const button of document.querySelectorAll("[data-language]")) {
    button.setAttribute("aria-pressed", String(button.dataset.language === Strings.language));
  }
}

function setLanguage(language) {
  Strings.language = language;
  try { localStorage.setItem("packcheck_language", language); } catch (error) { /* ignore */ }
  applyStrings();
  document.dispatchEvent(new CustomEvent("languagechange"));
}

function wireLanguageButtons() {
  for (const button of document.querySelectorAll("[data-language]")) {
    button.addEventListener("click", () => setLanguage(button.dataset.language));
  }
}

/* ---- login ------------------------------------------------------------- */

const Login = { required: false, user: null };

async function refreshLogin() {
  const me = await (await fetch("/api/me")).json();
  Login.required = me.login_required;
  Login.user = me.user;
  renderLogin();
  return Login;
}

/* Pages that need a login put these elements in their HTML:
     #login (section with #login-name, #login-pin, #login-button, #login-error)
     #signed-in (with #signed-in-name and #sign-out)
   Pages without them simply skip the login UI. */
function renderLogin() {
  const loginBox = $("login");
  const signedIn = $("signed-in");
  if (loginBox) loginBox.hidden = !(Login.required && !Login.user);
  if (signedIn) {
    signedIn.hidden = !Login.user;
    if (Login.user) $("signed-in-name").textContent = Login.user;
  }
  document.dispatchEvent(new CustomEvent("loginchange"));
}

function wireLogin() {
  if (!$("login-button")) return;
  $("login-button").addEventListener("click", async () => {
    $("login-error").hidden = true;
    const response = await fetch("/api/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("login-name").value, pin: $("login-pin").value }),
    });
    if (!response.ok) { $("login-error").textContent = t("login.failed"); $("login-error").hidden = false; return; }
    $("login-pin").value = "";
    await refreshLogin();
  });
  $("login-pin").addEventListener("keydown", (event) => { if (event.key === "Enter") $("login-button").click(); });
  if ($("sign-out")) $("sign-out").addEventListener("click", async () => { await fetch("/api/logout", { method: "POST" }); await refreshLogin(); });
}

/* Can the current visitor act (inspect, decide, edit zones)? */
function mayAct() {
  return !Login.required || Boolean(Login.user);
}

/* ---- small helpers ----------------------------------------------------- */

function remember(key, value) {
  try { localStorage.setItem("packcheck_" + key, value); } catch (error) { /* ignore */ }
}

function recall(key, fallback = "") {
  try { return localStorage.getItem("packcheck_" + key) ?? fallback; } catch (error) { return fallback; }
}

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

function readingsVerdict(status) {
  return { ok: "readings.ok", invalid: "readings.invalid", check_by_hand: "readings.check_by_hand" }[status];
}

function readingClass(valid) {
  return valid === true ? "valid" : valid === false ? "invalid" : "check";
}

function readingWord(valid) {
  return t(valid === true ? "readings.valid" : valid === false ? "readings.invalid_short" : "readings.check");
}
