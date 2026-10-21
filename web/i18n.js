'use strict';
// Chinese/English switching: defaults to the browser language (Chinese -> Chinese, otherwise English); the URL ?lang=en|zh can force it, and the choice is remembered.
// Static text: an element carries data-i18n="sNNNN" (text) or data-i18n-attr="attribute:sNNNN;..." (alt / aria-label / title).
// The original Chinese text is stored in data-i18n-zh / data-i18n-zh-<attribute> before the first application; switching back to Chinese restores it verbatim (zero regression for Chinese).
// The English dictionary comes from web/i18n/en.js (tools/build_i18n.py generates it from docs/i18n/zh_en_map.csv, verbatim and unrewritten).
(function () {
  const STORE = 'goodglow-lang';
  const dict = () => window.I18N_EN || {};
  let lang = pick();

  function pick() {
    try {
      const q = new URLSearchParams(location.search).get('lang');
      if (q === 'en' || q === 'zh') return q;
    } catch (_) { /* works without URL parameters too */ }
    try {
      const s = localStorage.getItem(STORE);
      if (s === 'en' || s === 'zh') return s;
    } catch (_) { /* works even if the browser blocks storage */ }
    return /^zh/i.test(navigator.language || '') ? 'zh' : 'en';
  }

  // When the original Chinese text has leading/trailing whitespace (to be attached after another sentence), the English must carry the same whitespace, otherwise the two sentences stick together
  function edge(zh, en) {
    const a = zh.match(/^\s*/)[0], b = zh.match(/\s*$/)[0];
    return (a && !/^\s/.test(en) ? a : '') + en + (b && !/\s$/.test(en) ? b : '');
  }

  // Normal lookup: swap only in English mode and when the dictionary has this key, otherwise keep the Chinese given at the call site
  function t(id, zh) {
    if (lang !== 'en') return zh;
    const v = dict()[id];
    return v === undefined ? zh : edge(zh, v);
  }

  function applyStatic(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      // The code (app.js) has changed this element's text itself (e.g. button text that varies by mode) -> do not overwrite again; leave it to app.js to handle with t()
      if (el.__i18nLast !== undefined && el.textContent !== el.__i18nLast) return;
      if (!el.hasAttribute('data-i18n-zh')) el.setAttribute('data-i18n-zh', el.textContent);
      el.textContent = t(el.getAttribute('data-i18n'), el.getAttribute('data-i18n-zh'));
      el.__i18nLast = el.textContent;
    });
    scope.querySelectorAll('[data-i18n-attr]').forEach((el) => {
      el.getAttribute('data-i18n-attr').split(';').forEach((pair) => {
        const i = pair.indexOf(':');
        if (i < 0) return;
        const attr = pair.slice(0, i), id = pair.slice(i + 1), keep = `data-i18n-zh-${attr}`;
        el.__i18nLastAttr = el.__i18nLastAttr || {};
        if (el.__i18nLastAttr[attr] !== undefined && el.getAttribute(attr) !== el.__i18nLastAttr[attr]) return;   // Do not overwrite if the code has changed it
        if (!el.hasAttribute(keep)) el.setAttribute(keep, el.getAttribute(attr) || '');
        el.setAttribute(attr, t(id, el.getAttribute(keep)));
        el.__i18nLastAttr[attr] = el.getAttribute(attr);
      });
    });
  }

  // Switch language: strings inside code such as app.js decide the language at load time, so save the preference and reload the page (the switch button is only on the home page, where there is no data in progress)
  function setLang(next, persist) {
    if (next !== 'en' && next !== 'zh') return;
    if (persist === false) {                      // For tests: temporarily switch static text on this page only, without reloading
      lang = next;
      document.documentElement.lang = next === 'en' ? 'en' : 'zh-Hant';
      applyStatic();
      syncButton();
      return;
    }
    try { localStorage.setItem(STORE, next); } catch (_) { /* works even if it cannot be remembered */ }
    const u = new URL(location.href);
    u.searchParams.set('lang', next);             // When it cannot be remembered, pass it along via a URL parameter
    location.replace(u.toString());
  }

  function syncButton() {
    const b = document.getElementById('lang-toggle');
    if (!b) return;
    b.textContent = lang === 'en' ? '中' : 'EN';
    b.setAttribute('aria-label', lang === 'en' ? '切換成中文 / Switch to Chinese' : 'Switch to English / 切換成英文');
    b.title = b.getAttribute('aria-label');
  }

  // For in-code strings: __t('sNNNN', 'Chinese') gets a normal string; __T('sNNNN', () => `Chinese template`, () => `English template`) gets a template string (both are closures at the call site, so variables work as usual)
  window.__t = t;
  window.__T = (id, zhFn, enFn) => (lang === 'en' ? edge(zhFn(), enFn()) : zhFn());
  // Between sentences: Chinese adds no space, English adds one space (used where two translated strings are joined)
  window.__sp = () => (lang === 'en' ? ' ' : '');

  window.I18N = {
    t, applyStatic, setLang, lang: () => lang,
  };

  document.documentElement.lang = lang === 'en' ? 'en' : 'zh-Hant';
  applyStatic();
  syncButton();
  const b = document.getElementById('lang-toggle');
  if (b) b.addEventListener('click', () => setLang(lang === 'en' ? 'zh' : 'en'));
})();
