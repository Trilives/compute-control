'use strict';
window.Portal = (() => {
  const requestLabels = { pending: '待管理员处理', help_install: '已转管理员协助安装', grant: '已安排授权', reject: '已拒绝' };
  function announcementCard(item, controls) {
    const card = el('article', undefined, 'panel announcement');
    card.append(el('h2', `${item.pinned ? '置顶 · ' : ''}${item.title}`), el('p', date(item.created), 'muted'), el('p', item.content, 'plain-text'));
    if (controls) card.append(button(item.pinned ? '取消置顶' : '置顶', async () => { await api(`/admin/announcements/${item.id}`, 'PATCH', { pinned: !item.pinned }); await load(); }), button('删除公告', async () => { if (confirm(`删除公告“${item.title}”？`)) { await api(`/admin/announcements/${item.id}`, 'DELETE'); await load(); } }, 'danger'));
    return card;
  }
  async function pinned() {
    const items = await api('/announcements?pinned=true');
    if (!items.length) return;
    $('#pinned-content').replaceChildren(...items.map(item => announcementCard(item, false)));
    Dialogs.open('#pinned-dialog');
  }
  async function openContacts() {
    const [contacts, me] = await Promise.all([api('/admin-contacts'), api('/auth/me')]);
    $('#contact-text').textContent = me.admin_contact || '';
    $('#contact-directory').replaceChildren(...contacts.map(item => {
      const node = el('div'); node.append(el('strong', item.name), el('p', item.contact, 'plain-text')); return node;
    }));
    $('#contact-empty').hidden = Boolean(contacts.length || me.admin_contact);
    Dialogs.open('#contact-admin-dialog');
  }
  function permissionSwitch(account) {
    const label = el('label', undefined, 'check permission-switch'), input = el('input'); input.type = 'checkbox'; input.setAttribute('role', 'switch');
    input.checked = Boolean(account.is_admin); input.disabled = account.state !== 'active';
    input.setAttribute('aria-label', `${account.username} 管理员权限`);
    const hint = el('span', account.is_admin == null ? '权限未确认，可选择开通或关闭' : account.is_admin ? '管理员权限：开' : '管理员权限：关');
    input.addEventListener('change', async () => {
      const enabled = input.checked;
      if (!confirm(`确认${enabled ? '开通' : '关闭'} ${account.machine_name} / ${account.username} 的 sudo 管理员权限？`)) { input.checked = Boolean(account.is_admin); return; }
      input.disabled = true;
      try { await api(`/admin/accounts/${account.id}/privilege`, 'PUT', { enabled }); notify('权限变更已提交，请在后台任务核对结果'); state.tab = 'jobs'; await load(); }
      catch (error) { input.checked = Boolean(account.is_admin); input.disabled = account.state !== 'active'; notify(error.message, true); }
    });
    label.append(input, hint);
    // Unknown is not shown as a verified off state. Both explicit targets remain available.
    if (account.is_admin == null && account.state === 'active') label.append(button('明确关闭权限', async () => {
      if (confirm(`确认核查并关闭 ${account.username} 的 sudo 权限？`)) { await api(`/admin/accounts/${account.id}/privilege`, 'PUT', { enabled: false }); state.tab = 'jobs'; await load(); }
    }));
    return label;
  }
  function machineAccountCount(machineId, personId) { return (state.accounts || []).filter(account => account.machine_id === machineId && account.occupies && (!personId || account.person_id === personId)).length; }
  function updateAccountMachineLabels() {
    const form = $('#account-form'), owner = state.role === 'admin' ? form.elements.person_id.value : state.person_id;
    for (const option of form.elements.machine_id.options) { const machine = state.machines.find(item => item.id === option.value); if (machine) option.textContent = `${machine.name}（已有 ${machineAccountCount(machine.id, owner)} 个账号）`; }
  }
  async function openNotes(machine, preserveDraft = false) {
    const form = $('#machine-notes-form'), draft = preserveDraft ? form.elements.content.value : '';
    const notes = await api(`/machines/${machine.id}/notes`); form.reset(); form.elements.content.value = draft; form.dataset.machineId = machine.id;
    $('#machine-notes-title').textContent = `${machine.name} · 机器备注`;
    $('#machine-notes-history').replaceChildren(...(notes.length ? notes.map(note => {
      const item = el('article', undefined, 'announcement'); item.append(el('strong', `${note.author_name} · ${date(note.created)}`), el('p', note.content, 'plain-text'));
      if (note.can_delete) item.append(button('删除备注', async () => {
        if (!confirm(`删除 ${note.author_name} 发布的这条备注？删除后无法在页面恢复。`)) return;
        await api(`/machines/${machine.id}/notes/${note.id}`, 'DELETE');
        await load(); await openNotes(machine, true); notify('备注已删除');
      }, 'danger'));
      return item;
    }) : [el('p', '暂无备注，可以补充这台机器的使用情况或注意事项。', 'muted')]));
    if (!$('#machine-notes-dialog').open) Dialogs.open('#machine-notes-dialog');
  }
  function notesButton(machine) { return button('机器备注', () => openNotes(machine)); }
  function accountControls(account) {
    const group = state.role === 'admin' ? AdminAccounts.controls(account) : el('div', undefined, 'actions'); group.classList.add('row-actions');
    const ledger = ledgerControls(account);
    if (ledger) group.prepend(ledger);
    else if (['active', 'archive_failed', 'needs_reconcile'].includes(account.state)) group.append(button('回收', async () => {
      if (confirm(`回收 ${account.machine_name} 上的 ${account.username}？\n主目录：${account.home || '待核查'}\n请先结束工作。完整归档验证后才删除账号；原目录将等待管理员清理。`)) { await api(`/accounts/${account.id}/retire`, 'POST', { confirmed: true }); notify('已提交回收，查看后台任务'); await load(); }
    }, 'danger'));
    return group.childElementCount ? group : '—';
  }
  function ledgerControls(account) {
    if (!account.ledger_cleared_at && !account.can_clear_ledger) return null;
    const restore = Boolean(account.ledger_cleared_at);
    return button(restore ? '恢复显示' : '清理台账', async () => {
      if (!restore && !confirm(`将 ${account.machine_name} / ${account.username} 从当前身份的台账列表移出？归档、任务和审计记录会保留，可通过“显示已清理账号”找回。`)) return;
      await api(`/accounts/${account.id}/ledger/${restore ? 'restore' : 'clear'}`, 'POST', { confirmed: true });
      await load(); notify(restore ? '已恢复台账显示' : '已从当前台账列表清理，历史记录保留');
    });
  }
  function openPermission() {
    const accounts = (state.accounts || []).filter(item => item.state === 'active' && item.occupies);
    if (!accounts.length) throw new Error('请先申请并开通一个机器账号，再申请管理权限');
    const form = $('#permission-form'); form.reset();
    form.elements.account_id.replaceChildren(...accounts.map(item => new Option(`${item.machine_name} / ${item.username}`, item.id)));
    Dialogs.open('#permission-dialog');
  }
  async function render(tab, content, actions, revision) {
    if (tab === 'announcements') {
      if (state.role === 'admin') actions.append(button('发布公告', () => { $('#announcement-form').reset(); Dialogs.open('#announcement-dialog'); }, 'primary'));
      const items = await api('/announcements'); if (revision !== loadRevision) return; content.append(...(items.length ? items.map(item => announcementCard(item, state.role === 'admin')) : [empty('暂无公告', '发布的当前和历史公告会在这里显示。')]));
    } else if (tab === 'contacts') {
      actions.append(button('添加管理员联系人', () => openContact(), 'primary'));
      const [contacts, settings] = await Promise.all([api('/admin-contacts'), api('/admin/settings')]); if (revision !== loadRevision) return;
      content.append(contacts.length ? table(['姓名', '联系方式', '操作'], contacts.map(item => [item.name, el('p', item.contact, 'plain-text'), (() => { const group = el('div', undefined, 'actions'); group.append(button('编辑', () => openContact(item)), button('删除联系人', async () => { if (confirm(`删除联系人 ${item.name}？`)) { await api(`/admin/contacts/${item.id}`, 'DELETE'); await load(); } }, 'danger')); return group; })()])) : empty('尚无管理员联系人', '联系人名单展示给用户，不会创建控制台登录账号。'));
      const contact = el('div', undefined, 'panel settings-panel'), text = el('textarea');
      text.value = settings.admin_contact || ''; text.rows = 3; text.maxLength = 2000; text.setAttribute('aria-label', '补充联系说明');
      contact.append(el('h2', '补充联系说明'), text, button('保存联系方式', async () => { await api('/admin/settings/contact', 'PUT', { text: text.value }); notify('联系方式已保存'); })); content.append(contact);
    } else {
      if (state.role === 'applicant') actions.append(button('申请管理权限', openPermission, 'primary'));
      const items = await api('/permission-requests'); if (revision !== loadRevision) return;
      content.append(items.length ? table(['姓名 / 账号', '机器', '申请事由', '处理结果', '操作'], items.map(item => [`${item.name} / ${item.username}`, item.machine_name, el('p', item.reason, 'plain-text'), `${requestLabels[item.state]}${item.response ? ' · ' + item.response : ''}${item.job_id ? ' · 任务 ' + item.job_id : ''}`, state.role === 'admin' && item.state === 'pending' ? button('处理申请', () => { const form = $('#permission-decision-form'); form.reset(); form.dataset.requestId = item.id; $('#permission-reason').textContent = item.reason; Dialogs.open('#permission-decision-dialog'); }) : '—'])) : empty('暂无权限申请', '说明事由后，由管理员决定协助安装、授予 sudo 权限或拒绝申请。'));
    }
  }
  function openContact(item) {
    const form = $('#contact-form'); form.reset(); form.dataset.contactId = item?.id || '';
    if (item) { form.elements.name.value = item.name; form.elements.contact.value = item.contact; }
    Dialogs.open('#contact-dialog');
  }
  function initialize() {
    $('#account-form').elements.person_id.addEventListener('change', updateAccountMachineLabels);
    $('#contact-admin').addEventListener('click', () => openContacts().catch(error => notify(error.message, true)));
    onSubmit('#machine-notes-form', async form => { await api(`/machines/${form.dataset.machineId}/notes`, 'POST', { content: form.elements.content.value }); form.reset(); $('#machine-notes-dialog').close(); await load(); notify('备注已发布，可见该机器的人都可以查看'); });
    $('#system-dependencies').addEventListener('click', () => Promise.resolve().then(openPermission).catch(error => notify(error.message, true)));
    onSubmit('#announcement-form', async form => { await api('/admin/announcements', 'POST', { title: form.elements.title.value, content: form.elements.content.value, pinned: form.elements.pinned.checked }); $('#announcement-dialog').close(); await load(); });
    onSubmit('#contact-form', async form => { const id = form.dataset.contactId; await api('/admin/contacts' + (id ? '/' + id : ''), id ? 'PUT' : 'POST', { name: form.elements.name.value, contact: form.elements.contact.value }); $('#contact-dialog').close(); await load(); });
    onSubmit('#permission-form', async form => { await api('/permission-requests', 'POST', { account_id: form.elements.account_id.value, reason: form.elements.reason.value }); $('#permission-dialog').close(); state.tab = 'permissions'; await load(); });
    onSubmit('#permission-decision-form', async form => { const action = form.elements.action.value; if (action === 'grant' && !confirm('确认向申请账号授予 sudo 管理员权限？')) return; await api(`/admin/permission-requests/${form.dataset.requestId}/decision`, 'POST', { action, response: form.elements.response.value }); $('#permission-decision-dialog').close(); await load(); });
  }
  return { initialize, pinned, render, openContacts, permissionSwitch, ledgerControls, accountControls, notesButton, machineAccountCount, updateAccountMachineLabels };
})();
