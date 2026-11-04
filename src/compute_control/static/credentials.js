'use strict';
// Forms never preload a stored password; closing either form clears every secret input.
window.Credentials = (() => {
  function webButton() {
    return button('修改网页管理密码', () => { $('#web-password-form').reset(); Dialogs.open('#web-password-dialog'); });
  }
  function machineButton(machine) {
    if (machine.management_password_pending) return button('核查管理密码变更', async () => {
      const result = await api(`/admin/machines/${machine.id}/password/check`, 'POST');
      await load(); notify(result.message, result.state === 'failed');
    });
    return button('修改机器管理密码', () => {
      const form = $('#management-password-form'); form.reset(); form.dataset.machineId = machine.id;
      $('#management-password-target').textContent = `${machine.name} · ${machine.host}:${machine.port} · SSH 管理账号：${machine.username}`;
      Dialogs.open('#management-password-dialog');
    });
  }
  function values(form) {
    const data = Object.fromEntries(new FormData(form));
    if (data.new_password !== data.confirm_password) throw new Error('两次输入的新密码不一致');
    return data;
  }
  function initialize() {
    for (const selector of ['#web-password-dialog', '#management-password-dialog']) {
      $(selector).addEventListener('close', () => $(selector).querySelector('form').reset());
    }
    onSubmit('#web-password-form', async form => {
      await api('/admin/password', 'PUT', values(form)); form.reset(); $('#web-password-dialog').close();
      await boot(); notify('网页管理密码已修改，请使用新密码重新登录。');
    });
    onSubmit('#management-password-form', async form => {
      const data = values(form);
      if (!confirm(`确认实际修改 ${$('#management-password-target').textContent} 的密码？其他使用该账号的新连接也需要新密码。`)) return;
      const result = await api(`/admin/machines/${form.dataset.machineId}/password`, 'POST', data);
      form.reset(); $('#management-password-dialog').close(); await load(); notify(result.message, result.state === 'failed');
    });
  }
  return { initialize, webButton, machineButton };
})();
