'use strict';
window.Guides = (() => {
  function inline(parent, text) {
    const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
    let start = 0;
    for (const match of text.matchAll(pattern)) {
      parent.append(document.createTextNode(text.slice(start, match.index)));
      if (match[1]) parent.append(el('strong', match[1]));
      else if (match[2]) parent.append(el('code', match[2]));
      else if (/^https?:\/\//.test(match[4])) {
        const link = el('a', match[3]); link.href = match[4]; link.target = '_blank'; link.rel = 'noopener noreferrer'; parent.append(link);
      } else parent.append(document.createTextNode(`${match[3]}（${match[4]}）`));
      start = match.index + match[0].length;
    }
    parent.append(document.createTextNode(text.slice(start)));
  }
  function markdown(text) {
    const root = el('div', undefined, 'operation-guide'), lines = text.split('\n');
    let list = null;
    for (let index = 0; index < lines.length; index++) {
      const line = lines[index];
      if (!line.trim()) { list = null; continue; }
      if (line.startsWith('```')) {
        const contents = []; while (++index < lines.length && !lines[index].startsWith('```')) contents.push(lines[index]);
        const pre = el('pre'); pre.append(el('code', contents.join('\n'))); root.append(pre); list = null; continue;
      }
      const image = line.trim().match(/^!\[([^\]]*)\]\(\.\.\/src\/compute_control\/static\/guide-images\/([a-z0-9][a-z0-9_-]*\.png)\)$/);
      if (image) {
        const figure = el('figure', undefined, 'guide-figure'), picture = el('img');
        picture.src = '/static/guide-images/' + image[2]; picture.alt = image[1]; picture.loading = 'lazy'; picture.decoding = 'async';
        figure.append(picture, el('figcaption', image[1])); root.append(figure); list = null; continue;
      }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) { const node = el('h' + heading[1].length); inline(node, heading[2]); root.append(node); list = null; continue; }
      if (line.startsWith('|') && lines[index + 1]?.match(/^\|[\s:|\-]+\|$/)) {
        const cells = value => value.replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
        const headings = cells(line), rows = []; index++;
        while (index + 1 < lines.length && lines[index + 1].startsWith('|')) rows.push(cells(lines[++index]));
        root.append(table(headings, rows.map(row => row.map(cell => { const node = el('span'); inline(node, cell); return node; })))); list = null; continue;
      }
      const item = line.match(/^(?:([-*])|\d+\.)\s+(.+)$/);
      if (item) {
        const kind = item[1] ? 'UL' : 'OL';
        if (!list || list.tagName !== kind) { list = el(kind.toLowerCase()); root.append(list); }
        const li = el('li'); inline(li, item[2]); list.append(li); continue;
      }
      const paragraph = el('p'); inline(paragraph, line); root.append(paragraph); list = null;
    }
    return root;
  }
  function open() {
    Dialogs.open('#guide-dialog');
  }
  async function development(systemOnly) {
    const guide = await api('/guides'); $('#guide-title').textContent = systemOnly ? '需要管理员安装的系统依赖' : guide.title;
    const rows = systemOnly ? guide.rows.filter(row => row.level.includes('系统级')) : guide.rows;
    $('#guide-content').replaceChildren(table(['工具', '推荐层级', '说明'], rows.map(row => [row.tool, row.level, row.description])), el('p', guide.system_note, 'muted'));
    open();
  }
  async function operation() {
    const guide = await api('/operation-guide'); $('#guide-title').textContent = guide.title;
    $('#guide-content').replaceChildren(markdown(guide.markdown));
    open();
  }
  return { development, operation, markdown };
})();
