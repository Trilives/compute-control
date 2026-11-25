'use strict';
window.Jobs = (() => {
  const reconciliationResults = {
    active: '远端账号仍存在，回执状态为可使用',
    suspended: '远端账号仍存在，回执状态为已暂停使用',
    retired: '远端账号已删除，原主目录待清理',
    cleaned: '远端账号已删除，原主目录已清理',
    not_created: '远端未确认创建该账号',
  };

  function reconciliationResult(result) {
    if (!result) return '历史任务仅保留完成状态';
    const details = [reconciliationResults[result.state] || `远端状态 ${result.state}`];
    if (result.uid != null) details.push(`UID ${result.uid}`);
    if (result.home) details.push(`主目录 ${result.home}`);
    if ('is_admin' in result) details.push(result.is_admin == null ? 'sudo 权限待确认' : result.is_admin ? '有 sudo 权限' : '无 sudo 权限');
    return details.join(' · ');
  }

  function watchReconciliations(pending, revision) {
    setTimeout(async () => {
      if (revision !== loadRevision) return;
      try {
        const latest = new Map((await api('/jobs')).map(job => [job.id, job.state]));
        if (revision !== loadRevision) return;
        if (pending.some(job => latest.get(job.id) !== job.state)) return await load();
      } catch {
        // Retry temporary network failures while this view is open.
      }
      if (revision === loadRevision) watchReconciliations(pending, revision);
    }, 2000);
  }

  function render(jobs, accounts, content) {
    if (!jobs.length) {
      content.append(empty('暂无后台任务', '创建与回收任务会在这里显示执行结果。'));
      return;
    }
    const isAdmin = state.role === 'admin';
    const pending = jobs.filter(job => job.kind === 'reconcile' && ['queued', 'running'].includes(job.state));
    if (isAdmin && pending.length) watchReconciliations(pending, loadRevision);
    const accountsById = new Map(accounts.map(account => [account.id, account]));
    const seenAccounts = new Set();
    const kinds = { create: '创建账号', retire: '回收账号', cleanup: '清理 home', restore: '恢复账号', suspend: '暂停账号', resume: '启用账号', reconcile: '核对结果', delete_archive: '删除归档', adopt: '接管账号', set_password: '重设密码', reboot: '重启机器', set_privilege: '变更sudo 权限' };
    const rows = jobs.map(job => {
      const account = accountsById.get(job.account_id);
      const latestForAccount = !seenAccounts.has(job.account_id);
      seenAccounts.add(job.account_id);
      let control;
      if (isAdmin && latestForAccount && job.state === 'needs_reconcile' && ['retire', 'reconcile'].includes(job.kind)) {
        control = AdminAccounts.reconcileControl(account, job.kind === 'retire' || account?.state === 'archive_failed');
      } else if (isAdmin && job.kind === 'reboot' && !['boot_verified', 'admin_checked'].includes(job.step)) {
        control = button('核查重启', async () => {
          if (!confirm('确认已检查机器当前可用？系统会先测试连接，再解除重启维护状态。')) return;
          await api(`/admin/machines/${job.machine_id}/reboot/check`, 'POST', { confirmed: true });
          await load();
        });
      } else if (job.state === 'waiting_password') {
        control = button('重新输入密码', () => {
          $('#password-form').dataset.jobId = job.id;
          $('#password-form').reset();
          Dialogs.open('#password-dialog');
        });
      }
      const kind = kinds[job.kind] || job.kind;
      const target = isAdmin && account ? ` · ${account.machine_name} / ${account.username}` : '';
      let status = (labels[job.state] || job.state) + (job.error ? ' · ' + job.error : '');
      if (isAdmin && job.kind === 'reconcile' && job.state === 'succeeded') {
        status += ' · ' + reconciliationResult(job.result);
      }
      const step = job.kind === 'reconcile' ? ({ queued: '等待执行', remote: '远端核对', verified: '核对完成' })[job.step] || job.step : job.step;
      return [job.id, kind + target, step, status, control || '—'];
    });
    content.append(table(['任务编号', '类型', '阶段', '状态 / 提示', '操作'], rows));
  }
  return { render };
})();
