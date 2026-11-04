'use strict';
// A machine menu routes to the existing forms and confirmations; opening it does not write.
window.MachineAdmin = (() => {
  function open(machine) {
    if (state.role !== 'admin') return;
    const dialog = $('#machine-management-dialog'), content = $('#machine-management-content');
    $('#machine-management-title').textContent = `${machine.name} · 管理机器`;
    content.replaceChildren(el('p', `${machine.host}:${machine.port} · SSH 管理账号：${machine.username}`, 'muted'));
    function group(title, actions, dangerous = false) {
      const section = el('section', undefined, 'admin-action-group' + (dangerous ? ' admin-danger-group' : ''));
      section.append(el('h3', title));
      const grid = el('div', undefined, 'admin-action-grid');
      for (const [control, description] of actions) {
        const label = control.textContent; control.classList.add('admin-action');
        control.setAttribute('aria-label', label);
        control.replaceChildren(el('strong', label), el('span', description));
        control.addEventListener('click', () => dialog.close(), { capture: true });
        grid.append(control);
      }
      section.append(grid); content.append(section);
    }
    group('申请与账号', [
      [button(machine.available ? '关闭申请' : '开放申请', async () => {
        await api(`/admin/machines/${machine.id}`, 'PATCH', { available: !machine.available }); await load();
      }), '调整新账号申请的开放状态，已有账号和任务继续保留。'],
      [button('机器账号与 sudo 权限', () => openMachineUsers(machine)), '查看机器账号；为已登记的普通账号管理 sudo 权限。'],
    ]);
    group('连接与密码', [
      [button('更新凭据', () => openMachine(machine)), '更新系统保存的连接资料，并检查目标机器身份。'],
      [Credentials.machineButton(machine), machine.management_password_pending ? '核查原改密结果，验证连接后同步凭据。' : '实际修改接入时录入的 SSH 管理账号密码，并验证新连接。'],
    ]);
    group('重启维护', [[button('重启机器', () => AdminAccounts.reboot(machine), 'danger'), '会中断计算任务和 SSH；继续前需要确认，忙碌时额外确认。']], true);
    Dialogs.open(dialog);
  }
  function initialize() {
    $('#machine-management-dialog').addEventListener('close', () => $('#machine-management-content').replaceChildren());
  }
  return { open, initialize };
})();
