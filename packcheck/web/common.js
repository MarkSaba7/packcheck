/* Shared by every page: translated strings, login, small helpers.

   Every sentence a page shows comes from strings.json, so that the tool can be offered in
   French and English (and more) without touching the pages. In the HTML, an element with
   data-t="some.key" gets that sentence as its text; in JavaScript, t("some.key") returns it.
*/

const $ = (id) => document.getElementById(id);

/* ---- the top bar, the same on every page -------------------------------

   Each page has <div id="app-bar" data-active="home"></div> and calls mountAppBar() first
   thing. One copy of the bar here instead of six slightly different ones in the pages.
   data-nav="none" (the phone page) leaves the links out: a worker scanning should not be
   one tap away from wandering off. */

// Line icons, 24 x 24, drawn with the text colour (stroke="currentColor").
const ICONS = {
  mark: '<circle cx="12" cy="12" r="6.5"/><path d="M12 1.5v21M1.5 12h21"/>',
  home: '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
  scan: '<path d="M3 7V4h3M18 4h3v3M21 17v3h-3M6 20H3v-3"/><rect x="7" y="7" width="10" height="10" rx="1.5"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7.5V12l3 2"/>',
  artwork: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
  add: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M12 8.5v7M8.5 12h7"/>',
  phone: '<rect x="6.5" y="2.5" width="11" height="19" rx="2.5"/><path d="M11 18.5h2"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1z"/><circle cx="12" cy="13.5" r="3.5"/>',
  upload: '<path d="M12 15V4M7.5 8.5 12 4l4.5 4.5"/><path d="M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  check: '<path d="M4.5 12.5 9.5 17.5 19.5 6.5"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="1.5"/><path d="M7 14h10v7H7z"/>',
  report: '<path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 12h7M9 16h7"/>',
  inbox: '<path d="M3 13h5l1.5 3h5L16 13h5"/><path d="M5.5 5h13L21 13v6H3v-6z"/>',
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
}

function mountAppBar() {
  const bar = $("app-bar");
  const active = bar.dataset.active;
  const links = [["home", "/", "nav.home"], ["scan", "/desk", "nav.scan"], ["history", "/history", "nav.history"], ["artwork", "/artworks/new", "nav.artworks"]];
  const nav = bar.dataset.nav === "none" ? "" : `<nav class="main-nav">${links.map(([name, href, key]) =>
    `<a href="${href}"${name === active ? ' aria-current="page"' : ""}>${icon(name)}<span data-t="${key}"></span></a>`).join("")}</nav>`;
  bar.className = "app-bar";
  bar.innerHTML = `<div class="app-bar-inner">
      <a class="brand" href="/">${icon("mark")}<span>PackCheck</span></a>
      ${nav}
      <div class="app-bar-tools">
        <span id="signed-in" hidden><span data-t="login.signed_in_as"></span> <b id="signed-in-name"></b>
          <button type="button" id="sign-out" data-t="login.sign_out"></button></span>
        <div class="lang"><button type="button" data-language="en">EN</button><button type="button" data-language="fr">FR</button></div>
      </div></div>`;

  // The sign-in box, on pages that have a place for it: <section id="login"></section>.
  const login = $("login");
  if (login) {
    login.className = "card login-card";
    login.hidden = true;
    login.innerHTML = `<h2 data-t="login.title"></h2>
      <label for="login-name" data-t="login.name"></label><input id="login-name" type="text" autocomplete="username">
      <label for="login-pin" data-t="login.pin"></label><input id="login-pin" type="password" inputmode="numeric" autocomplete="current-password">
      <p id="login-error" class="error-text" hidden></p>
      <div class="actions"><button class="button" id="login-button" type="button" data-t="login.button"></button></div>`;
  }
  // Every element with data-icon="name" gets that icon in front of its text. Its data-t moves
  // to an inner <span>, because translating sets textContent, which would erase the icon.
  for (const element of document.querySelectorAll("[data-icon]")) {
    const key = element.dataset.t;
    element.removeAttribute("data-t");
    element.innerHTML = icon(element.dataset.icon) + (key ? `<span data-t="${key}"></span>` : `<span>${element.innerHTML}</span>`);
  }
}

const Strings = { all: null, language: "en" };

async function loadStrings() {
  // "no-cache": ask the server whether the sentences changed instead of trusting an old copy.
  // (The pages load this file and packcheck.css as "?v=2": raise that number when either
  // changes, so browsers that cached the old one before the server sent no-cache fetch it anew.)
  Strings.all = await (await fetch("/static/strings.json", { cache: "no-cache" })).json();
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

/* ---- one scanned pack as a row of a list (home page, desk) ------------- */

function scanRow(pack) {
  const li = document.createElement("li");
  li.innerHTML = `<span class="scan-name"><b></b><span></span></span><span class="pill ${pack.verdict}"></span>
    <a class="button small secondary" target="_blank" rel="noopener"></a>`;
  li.querySelector("b").textContent = pack.product_name;
  li.querySelector(".scan-name span").textContent = [pack.product_code, (pack.created_at || "").replace("T", " ").slice(0, 16), pack.inspector].filter(Boolean).join(" · ");
  li.querySelector(".pill").textContent = t("r79.verdict." + pack.verdict);
  const link = li.querySelector("a");
  link.href = `/report/pack/${pack.pack_id}?lang=${Strings.language}`;
  link.innerHTML = `${icon("report")}<span>${t("history.view")}</span>`;
  return li;
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
