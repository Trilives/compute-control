'use strict';
// Each restore targets a selected archive. Passwords are cleared whenever the dialog closes.
window.ArchiveAdmin = (() => {
  function open(record) {
    const form = $('#archive-restore-form'); form.reset(); form.dataset.archiveId = record.id;
    $('#archive-restore-target').textContent = `${record.machine_name} / ${record.username} · 归档于 ${date(record.created)}`;
    $('#archive-password-label').textContent = record.password_available ? '新登录密码（留空沿用留存密码）' : '恢复后的登录密码（旧记录未留存，必填）';
    form.elements.password.required = !record.password_available;
    $('#archive-restore-hint').textContent = '恢复此条归档中的账号和主目录，会占用人员额度。请先启用所属人员；同名账号、UID 或目标目录冲突时不会覆盖。恢复后核查 sudo 权限。';
    Dialogs.open('#archive-restore-dialog');
  }
  function controls(record) {
    const group = el('div', undefined, 'actions');
    const restore = button('恢复账号', () => open(record));
    restore.disabled = !record.can_restore;
    if (restore.disabled) restore.title = record.deleted ? '归档已删除，无法恢复' : '账号仍在使用、正在恢复或有未完成任务，不能重复恢复';
    group.append(restore);
    if (!record.deleted) group.append(button('删除归档', async () => {
      if (!confirm(`永久删除 ${record.name} / ${record.username} 的这条归档？此后无法从该归档恢复。`)) return;
      await api(`/admin/archives/${record.id}/delete`, 'POST', { confirmed: true }); notify('归档删除任务已提交'); await load();
    }, 'danger'));
    return group;
  }
  function initialize() {
    onSubmit('#archive-restore-form', async form => {
      const data = { confirmed: true }; if (form.elements.password.value) data.password = form.elements.password.value;
      await api(`/admin/archives/${form.dataset.archiveId}/restore`, 'POST', data);
      $('#archive-restore-dialog').close(); state.tab = 'jobs'; await load(); notify('恢复任务已提交，请查看后台任务结果。');
    });
    $('#archive-restore-dialog').addEventListener('close', () => { $('#archive-restore-form').reset(); $('#archive-restore-form').dataset.archiveId = ''; $('#archive-restore-target').textContent = ''; });
  }
  return { controls, initialize };
})();
