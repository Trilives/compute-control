'use strict';
// Persisted 24-hour samples are fetched only when a machine is expanded.
window.ComputeCharts = (() => {
  const histories = new Map();
  const svgNS = 'http://www.w3.org/2000/svg';
  function number(value) {
    if (value === null || value === undefined || String(value).trim() === '') return null;
    const result = Number(value);
    return Number.isFinite(result) ? result : null;
  }
  function node(tag, text, className) {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function shape(tag, attributes) {
    const result = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) result.setAttribute(key, value);
    return result;
  }
  function observe(machine) {
    if (!machine.snapshot || !machine.sampled) return;
    const samples = histories.get(machine.id) || [];
    if (samples.length && samples.at(-1).time >= machine.sampled) return;
    const snapshot = machine.snapshot, total = number(snapshot.memory?.MemTotal), available = number(snapshot.memory?.MemAvailable);
    const gpus = {};
    for (const gpu of snapshot.gpus || []) gpus[gpu.uuid || gpu.index] = {
      utilization: number(gpu.gpu_percent), used: number(gpu.memory_used_mib), total: number(gpu.memory_total_mib),
    };
    samples.push({ time: machine.sampled, cpu: number(snapshot.cpu_percent),
      memory: total > 0 && available !== null ? 100 * (total - available) / total : null, gpus });
    histories.set(machine.id, samples.filter(sample => machine.sampled - sample.time <= 86400));
  }
  function chart(machineId, title, pick, unit = '%', maximum = 100) {
    const samples = histories.get(machineId) || [], values = samples.map(sample => ({ time: sample.time, value: number(pick(sample)) }));
    const card = node('div', undefined, 'mini-chart'), header = node('div', undefined, 'chart-header');
    const latest = values.at(-1)?.value;
    header.append(node('span', title), node('strong', latest === null || latest === undefined ? '未知' : `${latest.toFixed(1)}${unit}`));
    card.append(header);
    if (!values.some(point => point.value !== null)) {
      card.append(node('p', '等待有效采样', 'chart-empty')); return card;
    }
    const first = values[0].time, last = values.at(-1).time, span = Math.max(5, last - first);
    const max = Math.max(1, maximum || 0, ...values.map(point => point.value || 0));
    const svg = shape('svg', { viewBox: '0 0 320 96', role: 'img', 'aria-label': `${title}折线图`, class: 'line-chart' });
    for (const y of [12, 46, 80]) svg.append(shape('line', { x1: 5, y1: y, x2: 315, y2: y, class: 'chart-gridline' }));
    let path = '', previous = null; const dots = [];
    for (const point of values) {
      if (point.value === null) { previous = null; continue; }
      const x = 5 + 310 * (point.time - first) / span, y = 80 - 68 * Math.max(0, Math.min(max, point.value)) / max;
      const move = previous === null || point.time - previous > 9000 ? 'M' : 'L';
      path += `${move}${x.toFixed(2)},${y.toFixed(2)} `;
      previous = point.time;
      const dot = shape('circle', { cx: x, cy: y, r: 2, class: 'chart-dot', 'data-sampled-at': point.time });
      const title = shape('title', {}); title.textContent = `${new Date(point.time * 1000).toLocaleTimeString()} · ${point.value.toFixed(1)}${unit}`;
      dot.append(title); dots.push(dot);
    }
    svg.append(shape('path', { d: path, class: 'chart-line' }));
    svg.append(...dots);
    card.append(svg);
    const caption = node('div', undefined, 'chart-caption');
    caption.append(node('span', new Date(first * 1000).toLocaleTimeString()), node('span', new Date(last * 1000).toLocaleTimeString()));
    card.append(caption); return card;
  }
  function host(machine) {
    const grid = node('div', undefined, 'chart-grid');
    grid.append(chart(machine.id, 'CPU 使用率', sample => sample.cpu), chart(machine.id, '内存使用率', sample => sample.memory));
    return grid;
  }
  function gpu(machine, gpu) {
    const key = gpu.uuid || gpu.index, grid = node('div', undefined, 'chart-grid');
    grid.append(chart(machine.id, 'GPU 利用率', sample => sample.gpus[key]?.utilization),
      chart(machine.id, '显存占用', sample => sample.gpus[key]?.used, ' MiB', number(gpu.memory_total_mib)));
    return grid;
  }
  function details(machine) {
    const detail = node('details', undefined, 'history-details'); detail.append(node('summary', '最近 24 小时历史折线图（展开 / 收起）'));
    const content = node('div', undefined, 'history-content'); detail.append(content);
    let loaded = false;
    async function loadHistory() {
      content.replaceChildren(node('p', '正在读取历史数据…')); histories.delete(machine.id);
      try {
        const history = await api(`/machines/${machine.id}/history`);
        for (const row of history.samples) observe({ id: machine.id, sampled: row.sampled, snapshot: row.snapshot });
        const text = `${history.samples.length} 次真实探测 · 后台每小时采样，手动刷新也会记点；未采到的时段不补点。`;
        content.replaceChildren(node('p', text, 'muted'), button('更新历史显示', loadHistory), host(machine));
        for (const item of machine.snapshot?.gpus || []) { content.append(node('h3', `GPU ${item.index} · ${item.name}`), gpu(machine, item)); }
        loaded = true;
      } catch (error) { content.replaceChildren(node('p', error.message), button('重试读取历史', loadHistory)); }
    }
    detail.addEventListener('toggle', () => { if (detail.open && !loaded) loadHistory(); }); return detail;
  }
  return { observe, host, gpu, details, clear: () => histories.clear() };
})();
