'use strict';
// Review all accounts before revoking a person and scheduling archive-backed retirement.
window.AdminPeople = (() => {
  let review;
  const actionLabels = { retire: '归档后删除账号和原 home', cleanup: '校验归档后清理原 home', cancel_create: '取消尚未执行的创建', continue: '继续已提交的回收任务', keep_history: '保留历史记录', blocked: '需先核查，暂不可删除' };
  async function open(person) {
    review = await api(`/admin/applicants/${person.id}/deletion`);
    const form = $('#person-delete-form'); form.reset(); form.dataset.personId = person.id;
    $('#person-delete-title').textContent = `删除姓名 · ${review.name}`;
    $('#person-delete-accounts').replaceChildren(review.accounts.length ? table(['机器', '账号', '状态', '主目录', '处理方式'], review.accounts.map(account => [account.machine_name, account.username, labels[account.state] || account.state, account.home || '尚未创建', actionLabels[account.action]])) : el('p', '该人员暂无账号，只撤销网页访问并移出名单。'));
    $('#person-delete-blocked').textContent = review.blocked.join('\n');
    form.querySelector('[type=submit]').disabled = review.blocked.length > 0;
    Dialogs.open('#person-delete-dialog');
  }
  function initialize() {
    onSubmit('#person-delete-form', async form => {
      const result = await api(`/admin/applicants/${form.dataset.personId}`, 'DELETE', { confirmed: true, review_token: review.review_token });
      $('#person-delete-dialog').close();
      state.tab = result.retirement_jobs ? 'jobs' : 'people'; await load();
      notify(`姓名已移出，已提交 ${result.retirement_jobs} 个回收 / 清理任务；完成结果请查看后台任务。`);
    });
    $('#person-delete-dialog').addEventListener('close', () => { review = null; $('#person-delete-accounts').replaceChildren(); $('#person-delete-blocked').textContent = ''; });
  }
  return { open, initialize };
})();
