"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const dialog = $("legado-dialog");
  let info = {available:false}, pending = null, requestedBook = null;
  const engine = () => window.BookpackAppearance?.get().readerEngine || (window.AndroidHost ? 'legado' : 'builtin');

  function refresh() {
    try { info = JSON.parse(window.AndroidHost?.getLegadoInfo?.() || '{"available":false}'); }
    catch { info = {available:false}; }
    const native = !!window.AndroidHost?.openLegado;
    $("legado-status").textContent = info.available
      ? (info.embedded ? '已内置 Legado ' : '已连接 Legado ') + (info.version || '')
      : native ? '阅读器暂不可用' : '内置 Legado 在安卓应用内使用';
    $("reader-engine-note").textContent = engine() === 'legado'
      ? 'Legado 已包含在安卓融合版中，支持音量键翻页、排版和 EPUB 插图。阅读副本、进度与书签保存在书包内。'
      : '使用原有的简洁阅读器，进度与书签和 Legado 阅读分别保存。';
    document.querySelectorAll('[data-reader-engine]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.readerEngine === engine()));
    });
    $("library-reader-label").textContent = engine() === 'legado'
      ? info.available ? '用内置 Legado 继续阅读' : native ? 'Legado 阅读 · 暂不可用' : 'Legado 阅读 · 网页预览'
      : '简洁阅读 · 进度保存在书包';
    return {...info};
  }

  function choose(value) {
    if (!['builtin','legado'].includes(value)) return;
    window.BookpackAppearance?.save({readerEngine:value});
    refresh();
    window.BookpackReader?.loadLibrary();
  }

  function showUnavailable(identity, text) {
    requestedBook = identity;
    $("legado-dialog-message").textContent = text || '内置阅读器暂时无法打开，请重新打开书包后再试。';
    if (!dialog.open) dialog.showModal();
  }

  function markOpening(identity, opening) {
    document.querySelectorAll('[data-read-book]').forEach(button => {
      if (button.dataset.readBook === identity) {
        button.setAttribute('aria-busy', String(opening));
        button.classList.toggle('is-opening', opening);
      }
    });
  }

  async function open(identity) {
    if (pending) return false;
    if (!refresh().available || !window.AndroidHost?.openLegado) {
      showUnavailable(identity, window.AndroidHost?.openLegado ? undefined
        : '当前是网页测试，浏览器无法运行内置的安卓 Legado。这里可以先用简洁阅读体验正文；完整阅读器需要安装安卓融合版。');
      return false;
    }
    markOpening(identity, true);
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        if (pending?.identity !== identity) return;
        pending = null;
        markOpening(identity, false);
        showUnavailable(identity, 'Legado 暂未打开，请返回书包重试；也可以改用内置阅读。');
        resolve(false);
      }, 60000);
      pending = {identity, resolve, timer};
      try { window.AndroidHost.openLegado(identity); }
      catch {
        clearTimeout(timer); pending = null; markOpening(identity, false);
        showUnavailable(identity, '暂时无法打开 Legado，请重新打开书包后再试。'); resolve(false);
      }
    });
  }

  window.onLegadoResult = result => {
    if (!pending || result.id && result.id !== pending.identity) return;
    const {identity, resolve, timer} = pending;
    clearTimeout(timer); pending = null; markOpening(identity, false);
    if (!result.ok) showUnavailable(identity, result.message);
    resolve(!!result.ok);
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-reader-engine]');
    if (button) choose(button.dataset.readerEngine);
  });
  $("legado-dialog-close").addEventListener('click', () => dialog.close());
  $("reader-license").addEventListener('click', () => {
    if (window.AndroidHost?.showReaderLicense) window.AndroidHost.showReaderLicense();
    else window.open('https://github.com/LegadoTeam/legado/blob/master/LICENSE','_blank','noopener');
  });
  $("legado-use-builtin").addEventListener('click', () => {
    const identity = requestedBook;
    dialog.close(); choose('builtin');
    if (identity) window.BookpackReader?.openBuiltin(identity);
  });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.BookpackLegado = {engine, refresh, open, choose, back:() => {
    if (!dialog.open) return false;
    dialog.close(); return true;
  }};
  if (!window.AndroidHost) document.querySelectorAll('a[href="https://github.com/LegadoTeam/legado/releases/latest"]').forEach(link => {
    link.target = '_blank'; link.rel = 'noopener';
  });
  refresh();
})();
