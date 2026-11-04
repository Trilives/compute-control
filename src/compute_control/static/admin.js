'use strict';
// Administrator forms use existing lifecycle APIs; authorization stays on the server.
window.AdminAccounts = (() => {
  async function openAdopt(machineId, username) {
    const form = $('#adopt-form'); form.reset();
    const people = await api('/admin/applicants?include_deleted=true');
    if (!people.length) throw new Error('请先登记用于关联台账的姓名');
    form.elements.person_id.replaceChildren(...people.map(person => new Option(person.name + (person.deleted_at ? '（已移除）' : ''), person.id)));
    form.elements.machine_id.replaceChildren(...state.machines.map(machine => new Option(machine.name, machine.id)));
    if (machineId) form.elements.machine_id.value = machineId;
    updateCandidates(); if (username) form.elements.username.value = username; Dialogs.open('#adopt-dialog');
  }
  function updateCandidates() {
    const form = $('#adopt-form'), machine = state.machines.find(item => item.id === form.elements.machine_id.value);
    const candidates = (machine?.snapshot?.users || []).filter(user => user.uid >= 1000 && !['root', 'admin', 'nobody', machine.username].includes(user.username) && !(state.accounts || []).some(account => account.machine_id === machine.id && account.username === user.username));
    form.elements.username.replaceChildren(...candidates.map(user => new Option(`${user.username} · UID ${user.uid} · ${user.home}`, user.username)));
    form.querySelector('[type=submit]').disabled = !candidates.length;
    $('#adopt-hint').textContent = candidates.length ? '接管只记录归属，不改密码、不结束正在运行的工作。' : '当前快照没有可接管的普通账号，请刷新机器后重试。';
  }
  function openAssignment(account) {
    const form = $('#assignment-form'); form.dataset.accountId = account.id;
    const people = state.people.filter(person => person.enabled && person.id !== account.person_id);
    if (!people.length) throw new Error('没有其他已启用的接收人，请先维护姓名名单');
    form.elements.person_id.replaceChildren(...people.map(person => new Option(person.name, person.id)));
    $('#assignment-title').textContent = `重新分配 ${account.machine_name} / ${account.username}`;
    Dialogs.open('#assignment-dialog');
  }
  function openPassword(account) {
    const form = $('#admin-password-form'); form.reset(); form.dataset.accountId = account.id;
    form.elements.password.minLength = 1;
    $('#admin-password-title').textContent = `管理 ${account.machine_name} / ${account.username} 的密码`;
    Dialogs.open('#admin-password-dialog');
  }
  function controls(account) {
    const group = el('div', undefined, 'actions');
    if (account.occupies) group.append(button('重新分配', () => openAssignment(account)), button('管理密码', () => openPassword(account)));
    if (!account.owner_enabled || account.owner_deleted_at) group.append(button('查看留存密码', async () => {
      const data = await api(`/admin/accounts/${account.id}/password/reveal`, 'POST', { confirmed: true });
      $('#revealed-password').value = data.password;
      $('#reveal-title').textContent = `${account.machine_name} / ${account.username} 的留存密码`;
      Dialogs.open('#reveal-dialog');
    }));
    const operations = account.state === 'home_cleanup_pending' ? [['cleanup', '校验并清理原 home']] : ['archive_failed', 'needs_reconcile'].includes(account.state) ? [['reconcile', '核对远端结果'], ['restore', '恢复原登录状态']] : [];
    for (const [operation, text] of operations) group.append(button(text, async () => {
      if (confirm(`${text}：${account.machine_name} 上的 ${account.username}？${operation === 'cleanup' ? '将永久删除已归档且内容未变化的原 home。' : ''}`)) { await api(`/accounts/${account.id}/${operation}`, 'POST', { confirmed: true }); notify('已提交后台任务'); await load(); }
    }, operation === 'cleanup' ? 'danger' : ''));
    return group;
  }
  function initialize() {
    $('#adopt-form').elements.machine_id.addEventListener('change', updateCandidates);
    onSubmit('#adopt-form', async form => {
      const data = Object.fromEntries(new FormData(form)); if (!data.password) delete data.password;
      await api('/admin/accounts/adopt', 'POST', data); form.reset(); $('#adopt-dialog').close(); state.tab = 'jobs'; await load();
    });
    onSubmit('#assignment-form', async form => {
      await api(`/admin/accounts/${form.dataset.accountId}/assignment`, 'PUT', { person_id: form.elements.person_id.value });
      $('#assignment-dialog').close(); await load(); notify('台账归属已更新；需要变更登录密码时请使用管理密码。');
    });
    $('#admin-password-form').elements.operation.addEventListener('change', event => {
      $('#admin-password-form').elements.password.minLength = event.target.value === 'reset' ? 3 : 1;
    });
    onSubmit('#admin-password-form', async form => {
      const operation = form.elements.operation.value;
      if (operation === 'reset' && !confirm('确认修改此 Linux 账号的登录密码？不会终止已有进程。')) return;
      await api(`/admin/accounts/${form.dataset.accountId}/password/${operation}`, operation === 'reset' ? 'POST' : 'PUT', { password: form.elements.password.value });
      form.reset(); $('#admin-password-dialog').close(); notify(operation === 'reset' ? '密码重设任务已提交' : '已加密记录，未验证密码正确性'); await load();
    });
    $('#reveal-dialog').addEventListener('close', () => { $('#revealed-password').value = ''; });
  }
  async function reboot(machine) {
    if (!confirm(`确认重启 ${machine.name}（${machine.host}）？将立即中断当前计算任务和 SSH 连接，并关闭新申请。`)) return;
    const check = await api(`/admin/machines/${machine.id}/reboot/precheck`, 'POST');
    let force = false;
    if (check.busy) {
      const names = check.user_processes.slice(0, 8).map(item => `${item.username}: ${item.name} (PID ${item.pid})`).join('\n');
      force = confirm(`第三次确认：发现 ${check.platform_jobs.length} 个平台任务、${check.user_processes.length} 个用户进程、${check.gpu_process_count} 个 GPU 进程。\n${names}\n坚持重启将中断这些任务，管理员是否仍然立即重启？`);
      if (!force) return;
    }
    await api(`/admin/machines/${machine.id}/reboot`, 'POST', { confirmed: true, force, force_confirmed: force });
    notify('重启指令已提交，平台任务不会自动重复执行。'); state.tab = 'jobs'; await load();
  }
  return { openAdopt, controls, initialize, reboot };
})();
