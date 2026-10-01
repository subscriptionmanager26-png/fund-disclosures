// ==UserScript==
// @name         Fund Holdings — AMC auto-capture
// @namespace    https://github.com/subscriptionmanager26-png/fund-disclosures
// @version      1.1.0
// @description  One-tap residential capture: auto-runs the API chain on Navi, Union, Abakkus after you tap Capture once.
// @match        https://navi.com/*
// @match        https://*.navi.com/*
// @match        https://unionmf.com/*
// @match        https://*.unionmf.com/*
// @match        https://abakkusmf.com/*
// @match        https://*.abakkusmf.com/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==

(function () {
  let state;
  try {
    state = JSON.parse(window.name || "{}");
  } catch {
    return;
  }
  if (!state.w || !state.p || !state.step) return;
  if (sessionStorage.getItem("rh_chain_lock")) return;

  const stepHosts = {
    navi: /(^|\.)navi\.com$/i,
    union: /(^|\.)unionmf\.com$/i,
    abakkus: /(^|\.)abakkusmf\.com$/i,
  };
  if (!stepHosts[state.step]?.test(location.hostname)) return;

  sessionStorage.setItem("rh_chain_lock", "1");
  const w = encodeURIComponent(state.w);
  const p = encodeURIComponent(state.p);
  const s = document.createElement("script");
  s.type = "module";
  s.textContent =
    `import { runChain } from '${state.w}/chain.mjs?w=${w}&p=${p}';` +
    `runChain().catch(e=>alert(e.message)).finally(()=>sessionStorage.removeItem('rh_chain_lock'));`;
  (document.head || document.documentElement).appendChild(s);
})();
