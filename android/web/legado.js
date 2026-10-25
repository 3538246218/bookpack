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
      ? '已连接 Legado ' + (info.version || '')
      : native ? '尚未安装 Legado' : 'Legado 连接在安卓应用内使用';
    $("reader-engine-note").textContent = engine() === 'legado'
      ? '书包把原始 TXT / EPUB 交给 Legado。进度、书签和排版由 Legado 保存；首次打开可能需要选择书籍位置。'
      : '使用书包内置的简洁阅读器，阅读进度与书签保存在书包。';
    document.querySelectorAll('[data-reader-engine]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.readerEngine === engine()));
    });
    $("library-reader-label").textContent = engine() === 'legado'
      ? info.available ? '用 Legado 继续阅读' : native ? 'Legado 阅读 · 待安装' : 'Legado 阅读 · 网页预览'
      : '内置阅读 · 进度保存在书包';
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
    $("legado-dialog-message").textContent = text || '这台手机还没有安装 Legado。安装后返回书包，再点一次书籍即可打开。';
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
        : '当前是网页测试，浏览器无法检测或启动安卓 Legado。一键送书需要在安卓应用内验证；这里可以先用内置阅读体验正文。');
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
