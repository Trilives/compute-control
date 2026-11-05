'use strict';
const $ = (selector) => document.querySelector(selector);
const state = { csrf: '', role: 'anonymous', tab: 'machines', machines: [], people: [], mode: 'applicant', showCleared: false };
const titles = { machines: '当前状态', announcements: '公告', permissions: 'sudo 权限申请', contacts: '管理员名单', people: '申请者与额度', accounts: '账号台账', jobs: '后台任务', archives: '归档记录', audit: '操作记录' };
const labels = { active: '可使用', creating: '创建中', waiting_password: '等待重新输入密码', recycle_requested: '回收排队中', archive_failed: '回收暂停，请管理员核查', needs_reconcile: '待核查远端结果', home_cleanup_pending: '账号已删除，原 home 待清理', queued: '排队中', running: '执行中', succeeded: '已完成', cancelled: '已取消', retired: '已回收', not_created: '未创建，额度已释放' };
let loadRevision = 0;
function applyTheme(theme) {
  const value = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = value;
  $('#theme-toggle').textContent = value === 'light' ? '夜间模式' : '白天模式';
  $('#theme-toggle').setAttribute('aria-label', value === 'light' ? '切换至夜间模式' : '切换至白天模式');
}
try { applyTheme(localStorage.getItem('compute-control-theme')); } catch { applyTheme('light'); }
$('#theme-toggle').addEventListener('click', () => {
  const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  applyTheme(theme); try { localStorage.setItem('compute-control-theme', theme); } catch { /* Storage can be unavailable. */ }
});
function notify(message, error = false) { Dialogs.notify(message, error); }

function el(tag, text, className) {
  const node = document.createElement(tag); if (text !== undefined) node.textContent = text;
  if (className) node.className = className; return node;
}
function button(text, action, className) {
  const node = el('button', text, className); node.type = 'button';
  node.addEventListener('click', () => Promise.resolve().then(action).catch(error => notify(error.message, true))); return node;
}
async function api(path, method = 'GET', data) {
  const options = { method, headers: { 'X-CSRF-Token': state.csrf } };
  if (data !== undefined) { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(data); }
  const response = await fetch('/api' + path, options);
  const body = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.role !== 'anonymous') { state.role = 'anonymous'; await boot(); }
    let message = body.detail || '请求失败';
    if (Array.isArray(body.admin_contacts)) { const contacts = [body.admin_contact, ...body.admin_contacts.map(item => `${item.name}：${item.contact}`)].filter(Boolean).join('\n'); message += '\n\n管理员联系方式\n' + (contacts || '暂未配置联系方式，请通过公司已有渠道联系管理员。'); }
    throw new Error(message);
  }
  return body;
}
function date(value) { return value ? new Date(value * 1000).toLocaleString() : '尚未采集'; }
function empty(title, detail) { const box = el('div', undefined, 'empty'); box.append(el('h2', title), el('p', detail)); return box; }
function table(headers, rows) {
  const wrap = el('div', undefined, 'table-wrap'), grid = el('table'), head = el('tr');
  headers.forEach(title => head.append(el('th', title))); const thead = el('thead'); thead.append(head); grid.append(thead);
  const body = el('tbody'); rows.forEach(values => { const row = el('tr'); values.forEach(value => { const cell = el('td'); cell.append(value instanceof Node ? value : document.createTextNode(String(value ?? '—'))); row.append(cell); }); body.append(row); });
  grid.append(body); wrap.append(grid); return wrap;
}
async function boot() {
  loadRevision++;
  const previousRole = state.role;
  const data = await api('/auth/me'); Object.assign(state, data);
  if (previousRole !== state.role) { ComputeCharts.clear(); state.showCleared = false; $('#guide-content').replaceChildren(); }
  if (state.role === 'anonymous') {
    Dialogs.clearAlerts();
    document.querySelectorAll('dialog[open]').forEach(dialog => dialog.close());
    document.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
    $('#revealed-password').value = '';
  }
  $('#operation-guide').hidden = state.role === 'anonymous'; $('#operation-guide').textContent = state.role === 'admin' ? '管理员指南' : '用户指南';
  $('#contact-admin').hidden = state.role !== 'applicant';
  $('#development-guide').hidden = state.role === 'anonymous'; $('#system-dependencies').hidden = state.role !== 'applicant';
  $('#welcome').hidden = state.role !== 'anonymous'; $('#console').hidden = state.role === 'anonymous';
  $('#identity').replaceChildren(); $('#bootstrap').hidden = data.initialized;
  $('#login-form button[type=submit]').disabled = false; $('#admin-entry').disabled = false;
  if (state.role === 'anonymous' && (previousRole !== 'anonymous' || state.mode !== 'applicant')) selectMode('applicant');
  if (state.role !== 'anonymous') {
    if (state.role === 'admin') $('#identity').append(Credentials.webButton());
    $('#identity').append(el('span', state.role === 'admin' ? '管理员' : data.name), button('退出', async () => { await api('/auth/logout', 'POST'); await boot(); }));
    $('#role-label').textContent = state.role === 'admin' ? 'ADMIN WORKSPACE' : 'APPLICANT WORKSPACE';
    $('#login-form').hidden = true;
    state.tab = 'machines'; await load();
  }
}

function metric(label, value) { const node = el('div', undefined, 'metric'); node.append(el('span', label), el('strong', value)); return node; }
async function load() {
  if (state.role === 'anonymous') return;
  $('#machine-info-dialog').close(); $('#machine-management-dialog').close();
  const revision = ++loadRevision, role = state.role;
  const machines = await api('/machines');
  const people = role === 'admin' ? await api('/admin/applicants') : null;
  const me = role === 'applicant' ? await api('/auth/me') : null;
  if (revision !== loadRevision) return;
  state.machines = machines;
  if (people) state.people = people;
  if (me) { state.person = me.person; state.admin_contact = me.admin_contact; }
  const tabs = ['machines', 'accounts', 'permissions', 'jobs', ...(state.role === 'admin' ? ['people'] : []), 'announcements', ...(state.role === 'admin' ? ['contacts', 'archives', 'audit'] : [])];
  $('#tabs').replaceChildren(...tabs.map(tab => button(titles[tab], async () => { state.tab = tab; await load(); }, tab === state.tab ? 'active' : '')));
  await render(revision);
}
async function render(revision) {
  $('#page-title').textContent = titles[state.tab];
  const accounts = await api('/accounts' + (state.showCleared ? '?include_cleared=true' : '')); if (revision !== loadRevision) return; state.accounts = accounts;
  const actions = $('#page-actions'); actions.replaceChildren();
  actions.append(button('更新显示', load));
  if (state.tab === 'machines' && state.role === 'admin') actions.append(button('+ 添加机器', () => { openMachine(); }, 'primary'));
  if (state.tab === 'accounts') {
    const filter = el('label', undefined, 'check'), input = el('input'); input.type = 'checkbox'; input.checked = state.showCleared;
    input.addEventListener('change', () => { state.showCleared = input.checked; load().catch(error => notify(error.message, true)); });
    filter.append(input, el('span', '显示已清理账号')); actions.append(filter);
  }
  if (state.tab === 'accounts' && state.role === 'admin') actions.append(button('接管已有账号', AdminAccounts.openAdopt));
  if (state.tab === 'accounts' || (state.tab === 'machines' && state.role === 'applicant')) actions.append(button('+ 申请账号', () => openAccount(), 'primary'));
  $('#summary').replaceChildren(metric('可见机器', state.machines.length), metric(state.role === 'admin' ? '台账可见账号' : '已用账号 / 个人上限', state.role === 'admin' ? accounts.length : `${state.person.used} / ${state.person.effective_quota}`), metric('开放申请', state.machines.filter(machine => machine.available).length));
  const content = $('#content'); content.replaceChildren();
  if (['announcements', 'permissions', 'contacts'].includes(state.tab)) { await Portal.render(state.tab, content, actions, revision); return; }
  if (state.tab === 'machines') renderMachines(content);
  else if (state.tab === 'people') {
    const settings = await api('/admin/settings'); if (revision !== loadRevision) return; const box = el('div', undefined, 'actions people-controls');
    const input = el('input'); input.type = 'number'; input.min = '0'; input.max = '1000000'; input.value = settings.default_quota; input.setAttribute('aria-label', '全局默认账号额度');
    box.append(el('span', '全局默认额度'), input, button('保存默认额度', async () => { await api('/admin/settings/quota', 'PUT', { value: Number(input.value) }); notify('默认额度已更新'); await load(); }), button('+ 添加姓名', () => openPerson(), 'primary'));
    content.append(box, state.people.length ? table(['姓名', '状态', '已用 / 上限', '额度来源', '操作'], state.people.map(person => [person.name, person.enabled ? '启用' : '停用', `${person.used} / ${person.effective_quota}`, person.quota === null ? '默认' : '个人设置', personActions(person)])) : empty('尚无登记姓名', '添加姓名后，该人员即可立即申请机器账号。'));
  } else if (state.tab === 'accounts') {
    renderAccountGroups(accounts, content);
  } else if (state.tab === 'jobs') {
    const jobs = await api('/jobs'); if (revision !== loadRevision) return;
    content.append(jobs.length ? table(['任务编号', '类型', '阶段', '状态 / 提示', '操作'], jobs.map(job => [job.id, ({ create: '创建账号', retire: '回收账号', cleanup: '清理 home', restore: '恢复登录', reconcile: '核对结果', delete_archive: '删除归档', adopt: '接管账号', set_password: '重设密码', reboot: '重启机器', set_privilege: '变更sudo 权限' })[job.kind] || job.kind, job.step, `${labels[job.state] || job.state}${job.error ? ' · ' + job.error : ''}`, job.kind === 'reboot' && state.role === 'admin' && !['boot_verified', 'admin_checked'].includes(job.step) ? button('核查重启', async () => { if (confirm('确认已检查机器当前可用？系统会先测试连接，再解除重启维护状态。')) { await api(`/admin/machines/${job.machine_id}/reboot/check`, 'POST', { confirmed: true }); await load(); } }) : job.state === 'waiting_password' ? button('重新输入密码', () => { $('#password-form').dataset.jobId = job.id; $('#password-form').reset(); Dialogs.open('#password-dialog'); }) : '—'])) : empty('暂无后台任务', '创建与回收任务会在这里显示执行结果。'));
  } else if (state.tab === 'archives') {
    const rows = await api('/admin/archives'); if (revision !== loadRevision) return; content.append(rows.length ? table(['姓名 / 账号', '机器', '归档路径', '大小', '校验 / 状态', '操作'], rows.map(row => [`${row.name} / ${row.username}`, row.machine_name, row.path, `${(row.size / 1048576).toFixed(1)} MiB`, row.deleted ? '已删除' : row.checksum, row.deleted ? '—' : button('删除归档', async () => { if (confirm(`永久删除 ${row.name} / ${row.username} 的归档？请先完成原 home 清理。此操作不可恢复。`)) { await api(`/admin/archives/${row.id}/delete`, 'POST', { confirmed: true }); notify('归档删除任务已提交'); await load(); } }, 'danger')])) : empty('暂无归档', '归档默认永久保留，记录不会因账号回收而消失。'));
  } else {
    const rows = await api('/admin/audit'); if (revision !== loadRevision) return; content.append(table(['时间', '身份', '动作', '对象', '结果'], rows.map(row => [date(row.ts), row.actor, row.action, row.target, row.result])));
  }
  [...$('#tabs').children].forEach(node => node.classList.toggle('active', node.textContent === titles[state.tab]));
}
function lastActive(account) {
  if (account.last_active == null) return el('span', '暂无记录');
  const value = el('time', date(account.last_active)); value.dateTime = new Date(account.last_active * 1000).toISOString();
  value.title = '最近一次可观测的成功登录；不代表进程结束时间'; return value;
}
function renderAccountGroups(accounts, content) {
  if (!accounts.length) { content.append(empty('尚无登记账号', '申请后，这里按机器保存姓名与账号的对应关系。')); return; }
  const groups = new Map(), isAdmin = state.role === 'admin';
  for (const account of accounts) { if (!groups.has(account.machine_id)) groups.set(account.machine_id, []); groups.get(account.machine_id).push(account); }
  for (const rows of [...groups.values()].sort((a, b) => a[0].machine_name.localeCompare(b[0].machine_name, 'zh-CN', { numeric: true }))) {
    const machine = rows[0], group = el('section', undefined, 'panel account-machine-group'), heading = el('div', undefined, 'account-machine-heading');
    const title = el('div'); title.append(el('h2', machine.machine_name), el('p', `${machine.host}:${machine.port}`, 'muted'));
    heading.append(title, el('span', `占用额度 ${rows.filter(account => account.occupies).length} 个 · 台账 ${rows.length} 条`, 'badge')); group.append(heading);
    group.append(table([...(isAdmin ? ['姓名'] : []), '用户名', '状态', 'sudo 权限', ...(isAdmin ? ['最后活跃时间', '创建时间'] : []), '操作'], rows.map(account => [...(isAdmin ? [account.name] : []), account.username, (labels[account.state] || account.state) + (account.ledger_cleared_at ? '（已清理）' : ''), isAdmin ? Portal.permissionSwitch(account) : account.is_admin == null ? '未确认' : account.is_admin ? '有' : '无', ...(isAdmin ? [lastActive(account), date(account.created)] : []), Portal.accountControls(account)])));
    content.append(group);
  }
}
function renderMachines(content) {
  if (!state.machines.length) { content.append(empty('还没有可见的机器', state.role === 'admin' ? '从添加第一台机器开始。本机也需要手动添加。' : '管理员尚未开放机器，请联系管理员。')); return; }
  const grid = el('div', undefined, 'machine-grid');
  for (const machine of state.machines) {
    const card = el('article', undefined, 'panel'), head = el('div', undefined, 'machine-head');
    head.append(el('h2', machine.name), el('span', machine.available ? '可申请' : '暂不开放', 'badge')); card.append(head, el('p', `${machine.host}:${machine.port}`, 'muted'));
    if (state.role === 'applicant') {
      const reason = applicationBlocker(machine);
      const apply = button(reason || '申请账号', () => openAccount(machine.id), 'primary');
      apply.disabled = Boolean(reason); if (reason) apply.title = reason;
      head.append(apply);
    }
    if (state.role === 'applicant') card.append(el('p', `本机已有 ${Portal.machineAccountCount(machine.id, state.person_id)} 个账号`, 'muted machine-account-count'));
    const snapshot = machine.snapshot;
    card.append(monthlyCount(machine), el('p', `采集于 ${date(machine.sampled)}`, 'muted'));
    if (machine.stale || machine.error) card.append(el('p', machine.error || '数据已过期，等待采集', 'badge warning'));
    if (state.role === 'admin' && machine.creation_blockers?.length) card.append(el('p', `不能开放申请：${machine.creation_blockers.join('；')}`, 'badge warning'));
    if (snapshot) {
      card.append(el('p', `CPU 使用率 ${snapshot.cpu_percent == null ? '未知' : snapshot.cpu_percent + '%'} · 内存使用率 ${snapshot.memory.MemTotal ? (100 * (snapshot.memory.MemTotal - snapshot.memory.MemAvailable) / snapshot.memory.MemTotal).toFixed(1) + '%' : '未知'}`));
      card.append(el('p', `负载 ${snapshot.load.map(value => Number(value).toFixed(2)).join(' / ')} · 内存可用 ${((snapshot.memory.MemAvailable || 0) / 1073741824).toFixed(1)} GiB`));
      for (const gpu of snapshot.gpus) {
        const node = el('div', undefined, 'gpu'); node.append(el('p', `GPU ${gpu.index} · ${gpu.name}`), el('p', `显存 ${gpu.memory_used_mib} / ${gpu.memory_total_mib} MiB · GPU ${gpu.gpu_percent}%`), el('p', `温度 ${gpu.temperature}°C · 功耗 ${gpu.power_w} W`, 'muted'));
        const bar = el('progress'); bar.max = Number(gpu.memory_total_mib); bar.value = Number(gpu.memory_used_mib); node.append(bar); card.append(node);
      }
      if (snapshot.gpu_error) card.append(el('p', snapshot.gpu_error, 'muted'));
      for (const process of snapshot.processes) {
        const owner = snapshot.users.find(user => user.username === process.username);
        card.append(el('p', `${process.username}${owner?.person_name ? '（' + owner.person_name + '）' : ' · 无登记账号'} · PID ${process.pid} · ${process.name} · ${process.memory_mib} MiB`, 'muted'));
      }
    }
    if (machine.latest_note) card.append(el('p', `备注 · ${machine.latest_note.author_name}：${machine.latest_note.content.slice(0, 160)}${machine.latest_note.content.length > 160 ? '…' : ''}`, 'muted plain-text'));
    const actions = el('div', undefined, 'actions'); actions.append(Portal.notesButton(machine), button('申请刷新', async () => { await api(`/machines/${machine.id}/refresh`, 'POST'); notify('已申请后台刷新；采集完成后点击“更新显示”查看结果。'); }));
    const tools = snapshot?.system_tools;
    if (tools) {
      actions.append(button(`系统开发工具 ${tools.filter(tool => tool.installed).length} / ${tools.length} 项可用`, () => openMachineInfo(machine, '系统开发工具', table(['工具', '状态', '版本', '路径'], tools.map(tool => [tool.name, tool.installed ? '已安装' : '未检测到系统级安装', tool.version, tool.path])))));
    }
    card.append(actions);
    if (state.role === 'admin') {
      const management = el('div', undefined, 'actions machine-management');
      management.setAttribute('aria-label', '机器管理操作');
      management.append(button('管理机器', () => MachineAdmin.open(machine)));
      card.append(management);
    }
    card.append(ComputeCharts.details(machine)); grid.append(card);
  }
  content.append(grid);
}
function personActions(person) {
  const actions = el('div', undefined, 'actions'); actions.append(button('编辑', () => openPerson(person)), button('删除姓名', async () => {
    if (confirm(`将 ${person.name} 移出姓名名单并撤销进入权限？机器账号和历史台账会保留。`)) { await api(`/admin/applicants/${person.id}`, 'DELETE'); await load(); }
  }, 'danger')); return actions;
}
function monthlyCount(machine) {
  const node = el('div', undefined, 'monthly-count'); node.append(el('span', '近 30 天活跃账号（不含管理账号）'), el('strong', machine.active_users_30d), el('small', machine.snapshot?.login_status || '无法统计')); return node;
}
function openMachine(machine) {
  const form = $('#machine-form'); form.reset(); form.dataset.machineId = machine?.id || '';
  $('#machine-dialog .dialog-heading h2').textContent = machine ? '更新机器与凭据' : '添加算力机';
  form.querySelector('[type=submit]').textContent = machine ? '检查并保存' : '检查并添加';
  if (machine) {
    for (const key of ['name', 'host', 'port', 'username', 'archive_root']) form.elements[key].value = machine[key];
    form.elements.available.checked = Boolean(machine.available);
  }
  Credentials.machineMode(machine); Dialogs.open('#machine-dialog');
}
function openPerson(person) {
  const form = $('#person-form'); form.reset(); form.dataset.personId = person?.id || '';
  if (person) { form.elements.name.value = person.name; form.elements.quota.value = person.quota ?? ''; form.elements.enabled.checked = Boolean(person.enabled); }
  Dialogs.open('#person-dialog');
}
function applicationBlocker(machine) {
  if (!machine.available) return '暂未开放申请';
  if (state.person && state.person.used >= state.person.effective_quota) return '账号额度已满';
  return '';
}
function openAccount(machineId) {
  if (state.role === 'applicant' && state.person.used >= state.person.effective_quota) throw new Error('已达到账号上限，请联系管理员');
  const available = state.machines.filter(machine => machine.available && (state.role === 'admin' || !applicationBlocker(machine)));
  if (!available.length) throw new Error('当前没有开放申请的机器，请联系管理员');
  if (machineId && !available.some(machine => machine.id === machineId)) throw new Error('该机器当前不可申请，请刷新后重试');
  const form = $('#account-form'); form.reset();
  form.dataset.requestKey = Array.from(crypto.getRandomValues(new Uint8Array(16)), value => value.toString(16).padStart(2, '0')).join('');
  form.elements.machine_id.replaceChildren(...available.map(machine => new Option(machine.name, machine.id)));
  if (machineId) form.elements.machine_id.value = machineId;
  form.elements.person_id.replaceChildren(...state.people.filter(person => person.enabled).map(person => new Option(person.name, person.id)));
  $('#owner-wrap').hidden = state.role !== 'admin'; Portal.updateAccountMachineLabels();
  Dialogs.open('#account-dialog');
}
function onSubmit(id, handler) {
  $(id).addEventListener('submit', async event => {
    event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type=submit]'); submit.disabled = true;
    try { await handler(form); } catch (error) { notify(error.message, true); } finally { submit.disabled = false; }
  });
}
function selectMode(mode) {
  state.mode = mode; $('#login-form').hidden = false; $('#login-form').reset();
  $('#login-password-wrap').hidden = mode !== 'admin'; $('#login-password').required = mode === 'admin';
  $('#login-label').textContent = mode === 'admin' ? '管理员用户名' : '登记姓名（英文字母不区分大小写）';
  $('#login-title').textContent = mode === 'admin' ? '管理员登录' : '申请者模式';
  $('#admin-entry').textContent = mode === 'admin' ? '申请者入口' : '管理员入口'; $('#login-name').focus();
}
Dialogs.initialize();
$('#admin-entry').addEventListener('click', () => selectMode(state.mode === 'admin' ? 'applicant' : 'admin'));
$('#machine-info-dialog').addEventListener('close', () => $('#machine-info-content').replaceChildren());
document.querySelectorAll('[data-close]').forEach(node => node.addEventListener('click', () => node.closest('dialog').close()));
onSubmit('#login-form', async form => { await api('/auth/login', 'POST', { role: state.mode, username: form.elements.username.value.trim(), password: form.elements.password.value }); form.reset(); await boot(); if (state.role === 'applicant') await Portal.pinned(); });
onSubmit('#person-form', async form => { const data = { name: form.elements.name.value.trim(), quota: form.elements.quota.value === '' ? null : Number(form.elements.quota.value), enabled: form.elements.enabled.checked }; await api('/admin/applicants' + (form.dataset.personId ? '/' + form.dataset.personId : ''), form.dataset.personId ? 'PUT' : 'POST', data); $('#person-dialog').close(); await load(); });
onSubmit('#machine-form', async form => { const data = Object.fromEntries(new FormData(form)); data.port = Number(data.port); data.available = form.elements.available.checked; data.separate_sudo_password = form.elements.separate_sudo_password.checked; await api('/admin/machines' + (form.dataset.machineId ? '/' + form.dataset.machineId : ''), form.dataset.machineId ? 'PUT' : 'POST', data); await load(); form.reset(); $('#machine-dialog').close(); });
onSubmit('#account-form', async form => { const data = Object.fromEntries(new FormData(form)); if (state.role !== 'admin') delete data.person_id; data.request_key = form.dataset.requestKey; await api('/accounts', 'POST', data); form.reset(); $('#account-dialog').close(); state.tab = 'jobs'; await load(); notify('已提交。若提示等待密码，请先确认 worker 已启动，再在原任务重新输入。'); });
onSubmit('#password-form', async form => { await api(`/jobs/${form.dataset.jobId}/password`, 'POST', { password: form.elements.password.value }); form.reset(); $('#password-dialog').close(); await load(); });
$('#development-guide').addEventListener('click', () => Guides.development(false).catch(error => notify(error.message, true)));
$('#operation-guide').addEventListener('click', () => Guides.operation().catch(error => notify(error.message, true)));
new ResizeObserver(() => document.documentElement.style.setProperty('--topbar-height', `${$('.topbar').getBoundingClientRect().height}px`)).observe($('.topbar'));
AdminAccounts.initialize(); Portal.initialize(); Credentials.initialize(); MachineAdmin.initialize();
boot().catch(error => notify(error.message, true));

function openMachineInfo(machine, title, content) {
  $('#machine-info-title').textContent = `${machine.name} · ${title}`;
  $('#machine-info-content').replaceChildren(content);
  Dialogs.open('#machine-info-dialog');
}
function openMachineUsers(machine) {
  const users = machine.snapshot?.users || [];
  const content = table(['账号', '姓名', 'sudo 权限'], users.map(user => {
    const account = state.accounts.find(item => item.machine_id === machine.id && item.username === user.username && item.occupies);
    const status = el('div', `${user.is_admin == null ? '未确认' : user.is_admin ? '有' : '无'}`);
    if (user.uid < 1000 || ['root', 'admin', 'nobody', machine.username].includes(user.username)) status.append(el('span', '（受保护账号）'));
    else if (!account) status.append(button('接管后管理 sudo 权限', async () => { $('#machine-info-dialog').close(); await AdminAccounts.openAdopt(machine.id, user.username); }));
    return [user.username, user.is_machine_admin ? '已登记 · 机器管理员' : user.person_name || '无登记', account ? Portal.permissionSwitch(account) : status];
  }));
  openMachineInfo(machine, '机器账号与 sudo 权限', users.length ? content : el('p', '当前没有账号快照，请先申请刷新并更新显示。'));
}
