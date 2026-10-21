'use strict';
// 中英切換：預設依瀏覽器語言（中文→中文，其他→英文），網址 ?lang=en|zh 可強制，選過會記住。
// 靜態字：元素帶 data-i18n="sNNNN"（文字）或 data-i18n-attr="屬性:sNNNN;…"（alt／aria-label／title）。
// 中文原文第一次套用前先存在 data-i18n-zh／data-i18n-zh-<屬性>，切回中文逐字還原（中文零回歸）。
// 英文字典來自 web/i18n/en.js（tools/build_i18n.py 由 docs/i18n/zh_en_map.csv 產生，逐字不改寫）。
(function () {
  const STORE = 'goodglow-lang';
  const dict = () => window.I18N_EN || {};
  let lang = pick();

  function pick() {
    try {
      const q = new URLSearchParams(location.search).get('lang');
      if (q === 'en' || q === 'zh') return q;
    } catch (_) { /* 沒有網址參數也能用 */ }
    try {
      const s = localStorage.getItem(STORE);
      if (s === 'en' || s === 'zh') return s;
    } catch (_) { /* 瀏覽器擋儲存也能用 */ }
    return /^zh/i.test(navigator.language || '') ? 'zh' : 'en';
  }

  // 中文原文前後有空白（用來接在別的句子後面）時，英文也要帶同樣的空白，否則兩句會黏在一起
  function edge(zh, en) {
    const a = zh.match(/^\s*/)[0], b = zh.match(/\s*$/)[0];
    return (a && !/^\s/.test(en) ? a : '') + en + (b && !/\s$/.test(en) ? b : '');
  }

  // 一般取字：英文模式且字典有這一鍵才換，否則維持呼叫處給的中文
  function t(id, zh) {
    if (lang !== 'en') return zh;
    const v = dict()[id];
    return v === undefined ? zh : edge(zh, v);
  }

  function applyStatic(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      // 程式（app.js）自己改過這個元素的文字（例如依模式變動的按鈕字）→ 不再覆寫，交給 app.js 自己用 t() 處理
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
        if (el.__i18nLastAttr[attr] !== undefined && el.getAttribute(attr) !== el.__i18nLastAttr[attr]) return;   // 程式改過就不覆寫
        if (!el.hasAttribute(keep)) el.setAttribute(keep, el.getAttribute(attr) || '');
        el.setAttribute(attr, t(id, el.getAttribute(keep)));
        el.__i18nLastAttr[attr] = el.getAttribute(attr);
      });
    });
  }

  // 切換語言：app.js 等程式內的字串在載入時就決定語言，所以存好偏好後重新載入頁面（切換鈕只在首頁，沒有進行中的資料）
  function setLang(next, persist) {
    if (next !== 'en' && next !== 'zh') return;
    if (persist === false) {                      // 測試用：只在這一頁暫時切換靜態字，不重載
      lang = next;
      document.documentElement.lang = next === 'en' ? 'en' : 'zh-Hant';
      applyStatic();
      syncButton();
      return;
    }
    try { localStorage.setItem(STORE, next); } catch (_) { /* 記不住也能用 */ }
    const u = new URL(location.href);
    u.searchParams.set('lang', next);             // 記不住時，用網址參數帶過去
    location.replace(u.toString());
  }

  function syncButton() {
    const b = document.getElementById('lang-toggle');
    if (!b) return;
    b.textContent = lang === 'en' ? '中' : 'EN';
    b.setAttribute('aria-label', lang === 'en' ? '切換成中文 / Switch to Chinese' : 'Switch to English / 切換成英文');
    b.title = b.getAttribute('aria-label');
  }

  // 程式內字串用：__t('sNNNN', '中文') 取一般字串；__T('sNNNN', () => `中文樣板`, () => `英文樣板`) 取樣板字串（兩個都是呼叫處的閉包，變數照常可用）
  window.__t = t;
  window.__T = (id, zhFn, enFn) => (lang === 'en' ? edge(zhFn(), enFn()) : zhFn());
  // 句子之間：中文不加空格，英文加一個空格（用在把兩段翻譯過的字串接起來的地方）
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
