'use strict';
window.RetirementAdmin = (() => {
  let revision = 0;
  const specialKinds = { fifo: '命名管道 (FIFO)', socket: 'Unix 套接字', char: '字符设备', block: '块设备' };

  function confirmForcedRetirement(account, report, archiveSpecial) {
    const processes = report.process_review?.processes || [];
    const external = processes.filter(process => process.uid !== report.target_uid);
    const lines = [`确认强制回收 ${account.machine_name} 上的 ${account.username}？`];
    if (archiveSpecial) lines.push('主目录中的特殊对象将记录到归档清单，恢复时重建。');
    if (processes.length) lines.push(`系统将请求停止 ${processes.length} 个已核对的进程。未保存的数据可能丢失。`);
    if (external.length) {
      lines.push(`其中 ${external.length} 个进程属于其他账号，可能中断其他用户的工作或服务。`);
      for (const process of external) {
        lines.push(`${process.name.replace(/[\r\n\t]/g, ' ')}（PID ${process.pid}，UID ${process.uid}）`);
      }
    }
    return confirm(lines.join('\n'));
  }

  function jobAction(account, report, archiveSpecial) {
    const forced = Boolean(report.process_review || archiveSpecial);
    return button(forced ? '强制回收' : '重新回收', async () => {
      if (forced && !confirmForcedRetirement(account, report, archiveSpecial)) return;
      const dialog = $('#retirement-dialog');
      const controls = [...$('#retirement-action').querySelectorAll('button')];
      const current = revision;
      controls.forEach(control => { control.disabled = true; });
      try {
        const data = { confirmed: true, review_job_id: account.job_id };
        if (report.process_review) data.process_review = report.process_review;
        if (archiveSpecial) data.archive_special_objects = true;
        await api(`/accounts/${account.id}/retire`, 'POST', data);
        if (dialog.open && current === revision) dialog.close();
        state.tab = 'jobs';
        await load();
        notify('已提交回收，查看后台任务');
      } finally {
        controls.forEach(control => { control.disabled = false; });
      }
    }, 'danger');
  }

  async function open(account) {
    const dialog = $('#retirement-dialog');
    const content = $('#retirement-activity');
    const action = $('#retirement-action');
    const current = ++revision;
    $('#retirement-target').textContent = `${account.machine_name} / ${account.username} · ${account.home || '主目录待核查'}`;
    content.replaceChildren(el('p', '正在检查远端活动…'));
    const refresh = button('重新检查活动', () => open(account));
    refresh.disabled = true;
    action.replaceChildren(refresh);
    if (!dialog.open) Dialogs.open(dialog);
    let report;
    try {
      report = await api(`/admin/accounts/${account.id}/retirement-activity`);
    } catch (error) {
      if (dialog.open && current === revision) {
        content.replaceChildren(el('p', error.message, 'plain-text'));
      }
      return;
    } finally {
      refresh.disabled = false;
    }
    if (!dialog.open || current !== revision) return;

    content.replaceChildren();
    if (account.error) content.append(el('p', `上次回收暂停原因：${account.error}`, 'plain-text'));
    if (report.processes.length) {
      content.append(el('p', '以下进程属于该账号或正在使用其主目录。'), table(
        ['PID', '进程名', 'UID'],
        report.processes.map(process => [process.pid, process.name, process.uid])
      ));
    } else if (!report.retired) content.append(el('p', '当前没有占用进程。'));
    const external = report.processes.filter(process => report.target_uid != null && process.uid != null && process.uid !== report.target_uid);
    if (external.length) content.append(el('p', `其他账号的 ${external.length} 个进程仍在占用主目录。`));
    const special = report.special_objects || [];
    if (special.length) {
      content.append(el('p', '主目录中的特殊对象'), table(
        ['相对路径', '类型'],
        special.map(item => [item.path, specialKinds[item.kind] || item.kind])
      ));
    }
    if (report.special_scan_incomplete) content.append(el('p', '预览只显示已扫描到的对象，执行时会检查完整目录。'));
    if (report.filesystem_blocker) content.append(el('p', report.filesystem_blocker));
    if (report.schedule_blocker) content.append(el('p', report.schedule_blocker));
    if (report.schedule_blocker || report.filesystem_blocker) return;
    if (report.processes.length && !report.process_review) {
      content.append(el('p', '请先在主机处理这些进程，再重新检查活动。'));
      return;
    }

    if (report.retired) content.append(el('p', '远端账号已删除，重新回收会继续核对归档及原目录。'));
    const archiveSpecial = special.length > 0 || Boolean(report.special_scan_incomplete);
    action.append(jobAction(account, report, archiveSpecial));
  }

  function reviewLink() {
    return button('前往审阅', async () => { state.tab = 'permissions'; await load(); });
  }

  function reviewList(items, viewRevision) {
    const section = el('section', undefined, 'panel account-machine-group');
    const heading = el('div', undefined, 'account-machine-heading');
    const queue = el('div');
    heading.append(el('h2', '受阻回收'));
    section.append(heading, queue);
    function render(rows) {
      queue.replaceChildren(rows.length ? table(['机器', '用户名', '阻塞原因', '操作'], rows.map(item => [
        item.machine_name, item.username, item.error, button('审阅回收', () => open(item)),
      ])) : el('p', '暂无待审阅的回收任务。'));
    }
    async function refreshQueue() {
      if (viewRevision !== loadRevision) return;
      try {
        const rows = await api('/admin/retirement-reviews');
        if (viewRevision !== loadRevision) return;
        if (JSON.stringify(rows) !== JSON.stringify(items)) { items = rows; render(rows); }
      } catch {
        // Retry the review list while this view is open.
      }
      if (viewRevision === loadRevision) setTimeout(refreshQueue, 3000);
    }
    render(items);
    setTimeout(refreshQueue, 3000);
    return section;
  }

  function initialize() {
    $('#retirement-dialog').addEventListener('close', () => {
      if ($('#retirement-dialog').open) return;
      $('#retirement-target').textContent = '';
      $('#retirement-activity').replaceChildren();
      $('#retirement-action').replaceChildren();
    });
  }

  return { initialize, reviewLink, reviewList };
})();
