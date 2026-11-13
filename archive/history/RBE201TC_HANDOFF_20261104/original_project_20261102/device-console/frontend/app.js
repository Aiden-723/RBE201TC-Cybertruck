'use strict';
function bytes(value) {
  if (!Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  const unit = Math.min(4, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** unit).toFixed(1)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
}
function percent(used, total) {
  return Number.isFinite(used) && Number.isFinite(total) && total > 0 && used >= 0 && used <= total ? used / total * 100 : null;
}
function encoderState(telemetry = {}, elapsed = 0) {
  const labels = {disabled:'被动串口未启用', not_sampled:'等待原始帧', disconnected:'串口断开', stale:'数据过期'};
  const stale = telemetry.status === 'observed' && (!Number.isFinite(telemetry.age_seconds) || telemetry.age_seconds + elapsed > (telemetry.stale_after_seconds ?? 2));
  return `${stale ? '数据过期' : labels[telemetry.status] || (telemetry.status === 'observed' ? '收到原始帧' : '尚无遥测')} · 编码器未验证`;
}
class Poller {
  constructor({fetch = globalThis.fetch.bind(globalThis), change = () => {}, interval = 2000, timeout = 5000} = {}) {
    this.fetch = fetch; this.change = change; this.interval = interval; this.timeout = timeout;
    this.token = ''; this.paused = false; this.busy = false; this.generation = 0;
    this.state = {phase:'waiting', data:null, received:0, message:'等待首次读取；暂无测量数据'};
  }
  emit() { this.change(this.state); }
  pause() {
    this.paused = true; this.pending = false; this.generation++;
    clearTimeout(this.timer); this.controller?.abort();
    this.state.phase = 'paused'; this.state.message = '自动刷新已暂停；显示最后快照，不代表当前状态'; this.emit();
  }
  resume() { this.paused = false; if (this.busy) this.pending = true; else return this.refresh(); }
  setToken(token) {
    this.token = token; this.generation++; this.controller?.abort();
    if (this.busy) this.pending = true; else return this.refresh();
  }
  async refresh() {
    if (this.busy) return;
    clearTimeout(this.timer); this.busy = true;
    const generation = this.generation;
    const controller = this.controller = new AbortController();
    let timedOut = false;
    const deadline = setTimeout(() => { timedOut = true; controller.abort(); }, this.timeout);
    this.emit();
    try {
      const response = await this.fetch('/api/v1/status', {method:'GET', cache:'no-store', credentials:'omit', redirect:'error', signal:controller.signal,
        headers: this.token ? {Authorization:`Bearer ${this.token}`} : {}});
      if (generation !== this.generation) return;
      if (response.status === 401) {
        this.state.phase = 'auth'; this.state.message = '需要访问令牌：打开「连接设置」输入令牌后重试。'; return;
      }
      if (!response.ok) throw Error('http');
      if (!response.headers.get('Content-Type')?.includes('application/json')) throw Error('format');
      const payload = await response.json();
      if (generation !== this.generation) return;
      if (payload.schema_version !== '1.0' || !Number.isFinite(Date.parse(payload.generated_at)) ||
          !['health','system','devices','telemetry'].every(key => payload.data?.[key] && typeof payload.data[key] === 'object' && !Array.isArray(payload.data[key]))) throw Error('schema');
      this.state.data = payload; this.state.received = performance.now();
      this.state.phase = this.paused ? 'paused' : 'live';
      this.state.message = this.paused ? '单次刷新完成；自动刷新仍暂停' : '只读监控 · 每次完成后等待 2 秒再读取';
    } catch (_) {
      if (generation === this.generation) {
        this.state.phase = 'error'; this.state.message = timedOut ? '请求超时；保留的快照已过期，请检查服务连接。' : '读取失败或响应格式不兼容；保留的快照已过期。';
      }
    } finally {
      clearTimeout(deadline); this.busy = false; this.emit();
      if (this.pending) { this.pending = false; this.refresh(); }
      else if (!this.paused && this.state.phase !== 'auth') this.timer = setTimeout(() => this.refresh(), this.interval);
    }
  }
}
function thermalSummary(zones) {
  const values = Array.isArray(zones) ? zones.filter(zone => Number.isFinite(zone?.celsius)).map(zone => zone.celsius) : [];
  return values.length ? `${values.length} 个区域 · ${Math.min(...values).toFixed(1)}–${Math.max(...values).toFixed(1)} °C` : '—';
}
if (typeof module !== 'undefined') module.exports = {bytes, percent, encoderState, Poller, thermalSummary};

function mount() {
  const $ = id => document.getElementById(id);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const value = observation => observation?.status === 'ok' ? observation.value : null;
  const local = ['localhost','127.0.0.1','[::1]'].includes(location.hostname);
  $('host-kind').textContent = local ? 'LOCAL · 非 RDK 身份声明' : '远端服务 · 身份未验证';
  $('host-address').textContent = location.host;
  let rendered = null;
  function resource(label, text, note, ratio) {
    const item = element('div', undefined, 'resource');
    const top = element('div', undefined, 'resource-top');
    top.append(element('span', label), element('span', text, 'resource-value'));
    item.append(top);
    if (ratio !== undefined && ratio !== null) {
      const bar = element('progress'); bar.max = 100; bar.value = ratio;
      bar.setAttribute('aria-label', `${label}使用率 ${ratio.toFixed(1)}%`); item.append(bar);
    }
    item.append(element('p', note, 'resource-note'));
    return item;
  }
  function renderData(payload) {
    const {system, devices, telemetry} = payload.data;
    const memory = value(system.memory_bytes), disk = value(system.disk_bytes);
    const used = memory && Number.isFinite(memory.total) && Number.isFinite(memory.available) ? memory.total - memory.available : null;
    const memoryRatio = percent(used, memory?.total), diskRatio = percent(disk?.used, disk?.total);
    const load = value(system.load_average), uptime = value(system.uptime_seconds), thermal = value(system.thermal);
    $('resources').replaceChildren(
      resource('内存', memoryRatio === null ? '—' : `${memoryRatio.toFixed(1)}%`, memoryRatio === null ? '指标不可用' : `${bytes(used)} 已用 / ${bytes(memory.total)} 总量`, memoryRatio),
      resource('根文件系统', diskRatio === null ? '—' : `${diskRatio.toFixed(1)}%`, diskRatio === null ? '指标不可用' : `${bytes(disk.used)} 已用 / ${bytes(disk.total)} 总量`, diskRatio),
      resource('系统负载', Array.isArray(load) && load.length === 3 && load.every(Number.isFinite) ? load.map(n => n.toFixed(2)).join(' / ') : '—', '1 / 5 / 15 分钟平均负载，非 CPU 使用率'),
      resource('运行时长', Number.isFinite(uptime) ? `${Math.floor(uptime / 86400)} 天 ${Math.floor(uptime % 86400 / 3600)} 时 ${Math.floor(uptime % 3600 / 60)} 分` : '—', '后端主机自启动以来'),
      resource('温度区域', thermalSummary(thermal), '各区域最低–最高实测值；完整区域读数见原始响应')
    );
    const rows = [];
    function group(text) { const row = element('tr', undefined, 'group'); const th = element('th', text); th.colSpan = 3; th.scope = 'rowgroup'; row.append(th); rows.push(row); }
    function row(name, config, status) {
      const tr = element('tr');
      [name,config,status].forEach((text,index) => { const td = element('td', text); td.dataset.label = ['', '路径 / 配置', '状态'][index]; tr.append(td); }); rows.push(tr);
    }
    group('节点发现 / 仅枚举，不打开设备');
    for (const [key,label] of [['serial','串口'],['video','视频节点']]) {
      const entries = value(devices[key]);
      if (!Array.isArray(entries) || !entries.length) row(label, '—', Array.isArray(entries) ? '未发现匹配节点' : '目录信息不可用');
      else for (const item of entries) row(label, [item.node, ...(Array.isArray(item.by_id) ? item.by_id : []), item.usb_vendor_id ? `USB ${item.usb_vendor_id}:${item.usb_product_id ?? '—'}` : ''].filter(Boolean).join(' · '), '已发现 · 功能未验证');
    }
    group('配置接口 / 未进行采样');
    row('MIPI 摄像头', devices.camera?.interface ?? '配置未知', '已配置 · 未采样');
    row('循迹传感器', Array.isArray(devices.line_sensors?.physical_pins) ? `物理针脚 ${devices.line_sensors.physical_pins.join(' / ')}` : '配置未知', '已配置 · 未采样');
    row('DHT11 温湿度', devices.dht11?.linux_gpio != null ? `Linux GPIO ${devices.dht11.linux_gpio}` : '配置未知', '已配置 · 未采样');
    $('devices').replaceChildren(...rows);
    const counts = telemetry.raw_counts;
    $('encoders').replaceChildren(...[0,1,2,3].map(index => {
      const cell = element('div'); cell.append(element('span', `M${index+1}`), element('strong', Array.isArray(counts) && Number.isInteger(counts[index]) ? String(counts[index]) : '—')); return cell;
    }));
    $('raw').textContent = JSON.stringify(payload, null, 2);
  }
  function render(state) {
    const elapsed = state.data ? Math.max(0, (performance.now() - state.received) / 1000) : 0;
    const phase = state.phase === 'live' && elapsed > 5 ? 'stale' : state.phase;
    const labels = {waiting:'等待连接', live:'API 已连接', auth:'需要令牌', error:'连接失败', paused:'已暂停', stale:'数据过期'};
    $('connection').textContent = labels[phase]; $('connection').className = `badge ${phase}`;
    $('notice').textContent = phase === 'stale' ? '快照超过 5 秒未更新；当前数据已过期。' : state.message;
    $('notice').className = ['live','waiting'].includes(phase) ? 'notice' : 'notice attention';
    $('refresh').disabled = poll.busy;
    $('refresh').textContent = poll.busy ? '读取中…' : '立即刷新';
    $('pause').textContent = poll.paused ? '恢复刷新' : '暂停刷新';
    $('pause').setAttribute('aria-pressed', String(poll.paused));
    $('settings').classList.toggle('primary', phase === 'auth');
    document.body.classList.toggle('snapshot-stale', phase !== 'live');
    $('snapshot-label').textContent = !state.data ? '暂无数据' : phase === 'live' ? '最近成功快照' : '旧快照 · 非当前读数';
    if (!state.data) return;
    if (state.data !== rendered) { renderData(state.data); rendered = state.data; }
    $('updated').textContent = `响应生成 ${new Date(state.data.generated_at).toLocaleTimeString('zh-CN', {hour12:false})} · ${Math.floor(elapsed)} 秒前接收`;
    const telemetry = state.data.data.telemetry;
    $('encoder-status').textContent = (phase !== 'live' ? '快照非实时 · ' : '') + encoderState(telemetry, elapsed);
    $('encoder-age').textContent = Number.isFinite(telemetry.age_seconds) ? `最后原始帧：${(telemetry.age_seconds + elapsed).toFixed(1)} 秒前 · 接收时间 ${telemetry.received_at ?? '未知'}` : '尚无原始帧；未将未知值替换为 0';
  }
  const poll = new Poller({change:render});
  $('refresh').addEventListener('click', () => poll.refresh());
  $('pause').addEventListener('click', () => poll.paused ? poll.resume() : poll.pause());
  $('settings').addEventListener('click', () => {
    const open = $('settings-panel').hidden;
    $('settings-panel').hidden = !open; $('settings').setAttribute('aria-expanded', String(open));
    if (open) $('token').focus();
  });
  $('settings-panel').addEventListener('keydown', event => {
    if (event.key === 'Escape') { $('settings-panel').hidden = true; $('settings').setAttribute('aria-expanded','false'); $('settings').focus(); }
  });
  $('settings-form').addEventListener('submit', event => { event.preventDefault(); const token = $('token').value; $('token').value = ''; poll.setToken(token); });
  $('clear-token').addEventListener('click', () => { $('token').value = ''; poll.setToken(''); });
  const clock = setInterval(() => render(poll.state), 500);
  window.addEventListener('pagehide', () => { clearInterval(clock); poll.pause(); poll.token = ''; $('token').value = ''; });
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  poll.refresh();
}
if (typeof document !== 'undefined') mount();
