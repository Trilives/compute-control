'use strict';
// Shared modal layout keeps headings/close controls outside the scrolling content.
window.Dialogs = (() => {
  const alerts = []; let noticeTimer, ignoredAlertCloses = 0;
  function initialize() {
    for (const dialog of document.querySelectorAll('dialog')) {
      if (dialog.dataset.layoutReady) continue;
      const title = dialog.querySelector('h2');
      if (!title) continue;
      title.id ||= dialog.id + '-title'; title.tabIndex = -1; title.autofocus = true;
      const header = document.createElement('div'); header.className = 'dialog-heading';
      const close = document.createElement('button'); close.type = 'button'; close.textContent = dialog.id === 'alert-dialog' ? '确定' : '关闭';
      close.addEventListener('click', () => dialog.close());
      header.append(title, close);
      for (const button of dialog.querySelectorAll('[data-close]')) {
        if (button.textContent.trim().startsWith('关闭')) button.remove();
      }
      for (const footer of dialog.querySelectorAll('footer')) if (!footer.children.length) footer.remove();
      const body = document.createElement('div'); body.className = 'dialog-body';
      body.append(...dialog.childNodes); dialog.replaceChildren(header, body);
      dialog.setAttribute('aria-labelledby', title.id); dialog.dataset.layoutReady = 'true';
      if (dialog.id === 'alert-dialog') dialog.addEventListener('close', () => { if (ignoredAlertCloses) { ignoredAlertCloses--; return; } showNextAlert(); });
    }
  }
  function show(dialog) {
    dialog.showModal();
    dialog.querySelector('.dialog-heading h2').focus({ preventScroll: true });
    dialog.scrollTop = 0; dialog.querySelector('.dialog-body').scrollTop = 0;
  }
  function open(target) {
    const dialog = typeof target === 'string' ? document.querySelector(target) : target;
    show(dialog);
    const alert = document.querySelector('#alert-dialog');
    if (dialog !== alert && alert.open) { ignoredAlertCloses++; alert.close(); show(alert); }
  }
  function showNextAlert() {
    const dialog = document.querySelector('#alert-dialog');
    if (dialog.open || !alerts.length) return;
    const item = alerts.shift(); document.querySelector('#alert-title').textContent = item.error ? '操作未完成' : '操作提示';
    document.querySelector('#alert-message').textContent = item.message; open(dialog);
  }
  function notify(message, error = false) {
    clearTimeout(noticeTimer); const node = document.querySelector('#notice'); node.textContent = message;
    node.classList.toggle('error', error);
    if (error) { node.hidden = true; alerts.push({ message, error }); showNextAlert(); return; }
    node.hidden = false; noticeTimer = setTimeout(() => { node.hidden = true; }, 5000);
  }
  return { initialize, open, notify, clearAlerts: () => { alerts.length = 0; } };
})();
