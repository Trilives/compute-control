'use strict';
window.AccountInventory = (() => {
  const columns = ['账号', '姓名', 'sudo 权限'];

  function accountCells(user, permissionControl, detail) {
    const owner = user.is_machine_admin ? '已登记 · 机器管理员' :
      user.person_name || (user.same_name_record ? '同名台账待核查' : '无登记');
    const status = el('div', user.is_admin == null ? '未确认' : user.is_admin ? '有' : '无');
    if (detail) status.append(detail);
    return [user.username, owner, permissionControl || status];
  }

  function adoptButton(machineId, username) {
    return button('接管后管理 sudo 权限', async () => {
      $('#machine-info-dialog').close();
      await AdminAccounts.openAdopt(machineId, username);
    });
  }

  function machineView(machine) {
    const users = machine.snapshot?.users || [];
    if (!users.length) return el('p', '当前没有账号快照，请先申请刷新并更新显示。');
    return table(columns, users.map(user => {
      const account = user.registered && state.accounts.find(item =>
        item.machine_id === machine.id && item.username === user.username && item.occupies &&
        item.uid === user.uid && item.home === user.home
      );
      const protectedAccount = user.is_machine_admin || user.username === 'admin';
      let detail;
      if (protectedAccount) detail = el('span', '（受保护账号）');
      else if (!account && !user.same_name_record && user.is_admin != null && user.home === '/home/' + user.username) {
        detail = adoptButton(machine.id, user.username);
      }
      return accountCells(user, account && Portal.permissionSwitch(account), detail);
    }));
  }

  function render(machines) {
    const section = el('section', undefined, 'panel account-machine-group');
    section.append(el('h2', '未接管账号'));
    if (!machines.length) {
      section.append(el('p', '暂无已接入的机器。'));
      return section;
    }

    const filters = el('div', undefined, 'inventory-controls');
    const machineFilter = el('select'), query = el('input');
    machineFilter.setAttribute('aria-label', '按机器筛选未接管账号');
    machineFilter.append(new Option('全部机器', ''), ...machines.map(machine => (
      new Option(`${machine.name} · ${machine.accounts.length} 个`, machine.id)
    )));
    query.type = 'search';
    query.setAttribute('aria-label', '搜索未接管账号');
    query.placeholder = '搜索账号、UID 或主目录';
    const selectedMachines = () => machines.filter(machine => !machineFilter.value || machine.id === machineFilter.value);
    const refresh = button('申请刷新采集', async () => {
      await Promise.all(selectedMachines().map(machine => api(`/machines/${machine.id}/refresh`, 'POST')));
      notify('已申请后台采集。完成后点击“更新显示”查看结果。');
    });
    filters.append(machineFilter, query, refresh);
    const status = el('p', undefined, 'muted'), content = el('div');
    const warnings = el('ul', undefined, 'muted inventory-warnings');
    section.append(filters, status, warnings, content);

    function update() {
      const selected = selectedMachines(), filter = query.value.trim().toLowerCase();
      const rows = selected.flatMap(machine => machine.accounts.filter(account =>
        [account.username, account.uid, account.home].some(value => String(value ?? '').toLowerCase().includes(filter))
      ).map(account => [
        machine.name, ...accountCells({ ...account, is_admin: account.sudo_known ? false : null }, null,
          account.can_adopt ? adoptButton(machine.id, account.username) : null),
      ]));
      status.textContent = `${selected.length} 台机器 · 显示 ${rows.length} 个账号`;
      const notices = selected.filter(machine => machine.stale || machine.has_error || !machine.complete_sample);
      warnings.replaceChildren(...notices.map(machine => el('li', `${machine.name} · ${!machine.sampled || !machine.complete_sample ? '尚无账号采集' :
        machine.has_error ? '最近采集失败，显示历史结果' : '采集已过期'}`)));
      warnings.hidden = !notices.length;
      if (!rows.length) {
        content.replaceChildren(el('p', filter ? '没有匹配的账号。' : '当前采集未发现未接管账号。'));
        return;
      }
      const inventory = table(['机器', ...columns], rows);
      inventory.classList.add('inventory-table');
      content.replaceChildren(inventory);
    }
    machineFilter.addEventListener('change', update);
    query.addEventListener('input', update);
    update();
    return section;
  }
  return { render, machineView };
})();
