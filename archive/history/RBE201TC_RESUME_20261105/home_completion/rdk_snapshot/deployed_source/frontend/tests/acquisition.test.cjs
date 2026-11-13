'use strict';
// SOFTWARE_FIXTURE_NOT_COURSE_DATA: all rows, tokens, responses and DOM nodes
// below exist only in memory. No network, hardware, output CSV or file writes.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {AcquisitionClient, parseStationCSV, acquisitionReading} = require('../acquisition.js');

const FIXTURE = 'SOFTWARE_FIXTURE_NOT_COURSE_DATA';
const RUN_A = FIXTURE + '_RUN_A', RUN_B = FIXTURE + '_RUN_B', RUN_C = FIXTURE + '_RUN_C';
const TOKEN_OLD = FIXTURE + '_TOKEN_OLD', TOKEN_NEW = FIXTURE + '_TOKEN_NEW';
const KEY_OLD = FIXTURE + '_KEY_OLD', KEY_NEW = FIXTURE + '_KEY_NEW';
const STATION = FIXTURE + '_STATION';
const HEADER = 'StationID,X_m,Y_m,SensorHeight_m,Notes';
const turn = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
async function settleUntil(predicate, description) {
  for (let i = 0; i < 30 && !predicate(); i++) await turn();
  assert.ok(predicate(), description);
}
function runInfo(runId) {
  return {run_id:runId, purpose:'bench_diagnostic', events:[], event_count:0,
    status_counts:{}, closed_at:null, interrupted:false};
}
function snapshot(run = RUN_A, extra = {}) {
  return {enabled:true, experiment_key:KEY_OLD, active_run_id:run, busy:false,
    operation:null, committing:false, closing:false, error:null,
    runs:[RUN_A, RUN_B, RUN_C].map(runInfo), selected_run:run ? runInfo(run) : null,
    stations:[STATION], ...extra};
}
function response(data = snapshot()) {
  return {ok:true, status:200, json:async () => ({data})};
}
function errorResponse(code, status = 409) {
  return {ok:false, status, json:async () => ({error:{code}})};
}
function fixtureClient(handler = () => response()) {
  const calls = [];
  const client = new AcquisitionClient({fetch:(url, options = {}) => {
    const call = {url, ...options, method:options.method || 'GET',
      headers:{...options.headers}, data:options.body ? JSON.parse(options.body) : null};
    calls.push(call);
    return Promise.resolve(handler(call, calls.length));
  }});
  return {client, calls};
}
function seed(client, run = RUN_A) {
  client.token = TOKEN_OLD;
  client.apply(snapshot(run));
}
function capture() { return {action:'capture', station_id:STATION}; }

// Pure CSV/parser tests. Even the intentionally invalid samples are marked.
test('acquisition CSV handles BOM, CRLF, escaped quotes, commas and embedded newlines', () => {
  const csv = '\uFEFF' + HEADER + '\r\n"' + STATION + '",0,-1,0.5,"' + FIXTURE + ', ""quoted""\nsecond line"\r\n';
  assert.deepEqual(parseStationCSV(csv), [{station_id:STATION, x_m:0, y_m:-1,
    height_m:0.5, notes:FIXTURE + ', "quoted"\nsecond line'}]);
});
test('acquisition CSV accepts signed decimal/exponent numbers without inventing coordinates', () => {
  const rows = parseStationCSV('StationID,X_m,Y_m,SensorHeight_m\n' + STATION + ',-1e0,.25,+0.5');
  assert.deepEqual(rows, [{station_id:STATION, x_m:-1, y_m:0.25, height_m:0.5, notes:''}]);
});
test('acquisition CSV refuses blank, NaN, infinite, hexadecimal and complex coordinates', () => {
  for (const invalid of ['', ' ', 'NaN', 'Infinity', '-Infinity', '0x10', '1+2i', 'true', '1e999']) {
    assert.throws(() => parseStationCSV(HEADER + '\n' + STATION + ',' + invalid + ',1,0.5,' + FIXTURE), undefined, JSON.stringify(invalid));
  }
});
test('acquisition CSV refuses header-only, missing/duplicate headers and wrong row widths', () => {
  for (const csv of ['', HEADER, HEADER + '\n\n',
    'StationID,X_m,Y_m\n' + STATION + ',1,2',
    'StationID,X_m,Y_m,SensorHeight_m,X_m\n' + STATION + ',1,2,0.5,1',
    HEADER + '\n' + STATION + ',1,2,0.5',
    HEADER + '\n' + STATION + ',1,2,0.5,' + FIXTURE + ',extra']) assert.throws(() => parseStationCSV(csv));
});
test('acquisition CSV rejects unclosed, misplaced and trailing quote content', () => {
  for (const row of ['"' + STATION + ',1,2,0.5,' + FIXTURE,
    STATION + '"bad,1,2,0.5,' + FIXTURE,
    '"' + STATION + '"x,1,2,0.5,' + FIXTURE,
    '"' + STATION + '" ",1,2,0.5,' + FIXTURE]) assert.throws(() => parseStationCSV(HEADER + '\n' + row));
});
test('acquisition CSV rejects duplicate/blank IDs, negative heights and size limits', () => {
  const row = STATION + ',1,2,0.5,' + FIXTURE;
  assert.throws(() => parseStationCSV(HEADER + '\n' + row + '\n ' + row));
  assert.throws(() => parseStationCSV(HEADER + '\n ,1,2,0.5,' + FIXTURE));
  assert.throws(() => parseStationCSV(HEADER + '\n' + STATION + ',1,2,-0.5,' + FIXTURE));
  assert.throws(() => parseStationCSV(HEADER + '\n' + FIXTURE.repeat(4) + ',1,2,0.5,' + FIXTURE));
  assert.throws(() => parseStationCSV(HEADER + '\n' + STATION + ',1,2,0.5,' + FIXTURE.repeat(40)));
  assert.throws(() => parseStationCSV(FIXTURE.repeat(1000)));
  const tooMany = Array.from({length:101}, (_, i) => STATION + i + ',1,2,0.5,' + FIXTURE);
  assert.throws(() => parseStationCSV(HEADER + '\n' + tooMany.join('\n')));
});
test('acquisition CSV preserves formula-like notes as literal raw data, not executable HTML', () => {
  const notes = '=' + FIXTURE + '("<img>")';
  const quoted = '"' + notes.replaceAll('"', '""') + '"';
  assert.equal(parseStationCSV(HEADER + '\n' + STATION + ',1,2,0.5,' + quoted)[0].notes, notes);
});
test('acquisition readings keep real zero and finite number/nonempty-string values', () => {
  for (const [value, formatted] of [[0,'0.0'], ['0','0.0'], [' 12.5 ','12.5'], [-2.5,'-2.5']]) {
    assert.equal(acquisitionReading({Status:'success', value}, 'value'), formatted);
  }
});
test('acquisition readings never turn whitespace, missing values or coercible objects into zero', () => {
  for (const value of ['', ' ', '\t\n', null, undefined, NaN, 'NaN', Infinity,
    'Infinity', false, true, [], [1], {}, {valueOf:() => 2}]) {
    assert.equal(acquisitionReading({Status:'success', value}, 'value'), '—', String(value));
  }
});
test('acquisition non-success readings stay blank even when old numeric values exist', () => {
  for (const Status of ['pending','sensor_failure','request_failure','timeout','cancelled','unknown']) {
    assert.equal(acquisitionReading({Status, value:23.5}, 'value'), '—');
  }
});

test('acquisition constructor and refresh only GET status without automatic writes', async () => {
  const {client, calls} = fixtureClient();
  assert.equal(calls.length, 0);
  await client.refresh();
  assert.ok(calls.length > 0);
  assert.ok(calls.every(call => call.method === 'GET' && call.data === null));
  assert.equal(client.state.connected, true);
  assert.equal(client.key, KEY_OLD);
  assert.ok(calls.every(call => call.credentials === 'omit' && call.cache === 'no-store' && call.redirect === 'error'));
});
test('acquisition overlapping refreshes serialize their network requests', async t => {
  const first = deferred();
  t.after(() => first.resolve(response()));
  let inflight = 0, maximum = 0;
  const {client, calls} = fixtureClient(async (_, number) => {
    maximum = Math.max(maximum, ++inflight);
    try { return number === 1 ? await first.promise : response(); }
    finally { inflight--; }
  });
  const refresh = client.refresh();
  client.refresh(); client.refresh();
  assert.equal(calls.length, 1);
  first.resolve(response()); await refresh; await turn();
  assert.equal(maximum, 1);
});
test('acquisition successful GET clears only network error, not an operation failure', async () => {
  let fail = true;
  const {client} = fixtureClient(() => {if (fail) throw Error(FIXTURE + '_NETWORK'); return response();});
  await client.refresh();
  assert.equal(client.networkError, FIXTURE + '_NETWORK');
  assert.equal(client.state.connected, false);
  client.error = 'record_persistence_failed'; fail = false;
  await client.refresh();
  assert.equal(client.state.connected, true);
  assert.ok(!client.networkError);
  assert.equal(client.error, 'record_persistence_failed');
});
test('acquisition simultaneous commands issue only one explicit POST', async t => {
  const post = deferred();
  t.after(() => post.resolve(response()));
  const {client, calls} = fixtureClient(call => call.method === 'POST' ? post.promise : response());
  seed(client);
  const first = client.command(capture());
  await assert.rejects(client.command(capture()));
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  post.resolve(response()); await first;
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(client.commanding, false);
});
test('acquisition origin rejection refreshes its key but never automatically replays POST', async () => {
  const {client, calls} = fixtureClient(call => call.method === 'POST' ?
    errorResponse('experiment_origin_rejected', 403) : response(snapshot(RUN_A, {experiment_key:KEY_NEW})));
  seed(client);
  await assert.rejects(client.command(capture()), /experiment_origin_rejected/);
  await turn();
  assert.equal(calls.filter(call => call.method === 'POST').length, 1);
  assert.equal(client.key, KEY_NEW);
});
test('acquisition oversized UTF-8 command body is rejected before any POST', async () => {
  const {client, calls} = fixtureClient(); seed(client);
  await assert.rejects(client.command({action:'start_run', purpose:'bench_diagnostic', notes:FIXTURE + '测'.repeat(12000)}));
  assert.equal(calls.filter(call => call.method === 'POST').length, 0);
});
test('acquisition selection is pending and blocks downloads/captures until the selected GET finishes', async t => {
  const selected = deferred(); t.after(() => selected.resolve(response(snapshot(RUN_B))));
  const {client, calls} = fixtureClient(() => selected.promise); seed(client);
  const changing = client.select(RUN_B);
  assert.equal(client.selectionPending, true);
  await assert.rejects(client.download('csv'));
  await assert.rejects(client.download('manifest'));
  await assert.rejects(client.command(capture()));
  assert.equal(calls.filter(call => call.method === 'POST').length, 0);
  selected.resolve(response(snapshot(RUN_B))); await changing;
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B && !client.selectionPending, 'new run must be confirmed before exports');
});
test('acquisition selection during an old GET queues the latest view and rejects stale data', async t => {
  const old = deferred(); t.after(() => old.resolve(response(snapshot(RUN_A))));
  const observed = [];
  const {client, calls} = fixtureClient((call, n) => n === 1 ? old.promise : response(snapshot(RUN_C)));
  seed(client);
  client.change = data => {if (!client.selectionPending) observed.push(data.selected_run?.run_id);};
  const reading = client.refresh();
  client.select(RUN_B); client.select(RUN_C);
  assert.equal(client.selectionPending, true);
  assert.equal(calls.length, 1);
  observed.length = 0;
  old.resolve(response(snapshot(RUN_A))); await reading;
  await settleUntil(() => client.state.selected_run?.run_id === RUN_C && !client.selectionPending, 'latest selection must receive a queued GET');
  assert.ok(calls.slice(1).some(call => call.url === '/api/v1/experiments/' + RUN_C));
  assert.ok(!observed.includes(RUN_A), 'stale A must never be emitted as the chosen run again');
});
test('acquisition setToken clears old key/data and fences a late old-token GET', async t => {
  const old = deferred(); t.after(() => old.resolve(response()));
  const {client, calls} = fixtureClient(call => call.headers.Authorization === 'Bearer ' + TOKEN_OLD ?
    old.promise : response(snapshot(RUN_B, {experiment_key:KEY_NEW})));
  seed(client);
  const reading = client.refresh(), epoch = client.epoch;
  const changed = client.setToken(TOKEN_NEW);
  assert.ok(client.epoch > epoch);
  assert.equal(client.key, '');
  assert.equal(client.state.selected_run, null);
  assert.deepEqual(client.state.runs, []);
  old.resolve(response(snapshot(RUN_A))); await reading; await changed;
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'new-token status must replace discarded old data');
  assert.equal(client.key, KEY_NEW);
  assert.equal(client.token, TOKEN_NEW);
  assert.ok(calls.some(call => call.headers.Authorization === 'Bearer ' + TOKEN_NEW));
});
test('acquisition stale old-token GET errors cannot mark a recovered new token offline', async t => {
  const old = deferred(); t.after(() => old.reject(Error(FIXTURE + '_OLD_NETWORK')));
  const {client} = fixtureClient(call => call.headers.Authorization === 'Bearer ' + TOKEN_OLD ?
    old.promise : response(snapshot(RUN_B, {experiment_key:KEY_NEW})));
  seed(client);
  const reading = client.refresh(); client.setToken(TOKEN_NEW);
  old.reject(Error(FIXTURE + '_OLD_NETWORK')); await reading;
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'new-token refresh must be queued');
  assert.equal(client.state.connected, true);
  assert.ok(!client.networkError);
});
test('acquisition old POST response cannot restore previous-token state or key', async t => {
  const oldPost = deferred(); t.after(() => oldPost.resolve(response()));
  const {client, calls} = fixtureClient(call => call.method === 'POST' ? oldPost.promise :
    response(snapshot(RUN_B, {experiment_key:KEY_NEW})));
  seed(client);
  const command = client.command(capture());
  client.setToken(TOKEN_NEW);
  oldPost.resolve(response(snapshot(RUN_A))); await Promise.allSettled([command]);
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'new token must remain the selected state');
  assert.equal(client.key, KEY_NEW);
  assert.equal(client.token, TOKEN_NEW);
  assert.equal(calls.filter(call => call.method === 'POST').length, 1, 'never replay an accepted/uncertain mutation');
});
test('acquisition old mutation finally cannot clear a newer POST commanding state', async t => {
  const oldPost = deferred(), newPost = deferred();
  t.after(() => {oldPost.resolve(response()); newPost.resolve(response(snapshot(RUN_B,{experiment_key:KEY_NEW})));});
  let latest = RUN_B;
  const {client, calls} = fixtureClient(call => call.method === 'POST' ?
    (call.headers.Authorization === 'Bearer ' + TOKEN_OLD ? oldPost.promise : newPost.promise) :
    response(snapshot(latest, {experiment_key:KEY_NEW})));
  seed(client);
  const oldCommand = client.command(capture()); client.setToken(TOKEN_NEW);
  await settleUntil(() => client.key === KEY_NEW && !client.selectionPending, 'new token must be confirmed before the next explicit command');
  const newCommand = client.command(capture());
  assert.equal(client.commanding, true);
  assert.equal(calls.filter(call => call.method === 'POST').length, 2);
  oldPost.resolve(response(snapshot(RUN_A))); await oldCommand;
  assert.equal(client.commanding, true, 'old mutation must not unlock a pending newer mutation');
  assert.equal(client.key, KEY_NEW);
  assert.equal(client.state.selected_run.run_id, RUN_B);
  latest = RUN_C; newPost.resolve(response(snapshot(RUN_C,{experiment_key:KEY_NEW})));
  await newCommand;
  assert.equal(client.commanding, false);
  assert.equal(client.state.selected_run.run_id, RUN_C);
  assert.equal(calls.filter(call => call.method === 'POST').length, 2);
});
test('acquisition old POST failure cannot overwrite a new-token operation error', async t => {
  const oldPost = deferred(); t.after(() => oldPost.resolve(errorResponse('unauthorized', 401)));
  const {client} = fixtureClient(call => call.method === 'POST' ? oldPost.promise : response(snapshot(RUN_B, {experiment_key:KEY_NEW})));
  seed(client);
  const command = client.command(capture()); client.setToken(TOKEN_NEW);
  client.error = FIXTURE + '_NEW_OPERATION_ERROR';
  oldPost.resolve(errorResponse('unauthorized', 401)); await Promise.allSettled([command]);
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'new-token GET should succeed');
  assert.equal(client.error, FIXTURE + '_NEW_OPERATION_ERROR');
  assert.ok(!client.networkError);
});
test('acquisition token change during key prefetch must prevent the old command from sending POST', async t => {
  const oldKey = deferred(); t.after(() => oldKey.resolve(response()));
  const {client, calls} = fixtureClient(call => call.headers.Authorization === 'Bearer ' + TOKEN_OLD ?
    oldKey.promise : response(snapshot(RUN_B, {experiment_key:KEY_NEW})));
  client.token = TOKEN_OLD;
  const command = client.command({action:'start_run', purpose:'bench_diagnostic', notes:FIXTURE});
  assert.equal(calls.length, 1); assert.equal(calls[0].method, 'GET');
  client.setToken(TOKEN_NEW); oldKey.resolve(response());
  await Promise.allSettled([command]);
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'new token status must load');
  assert.equal(calls.filter(call => call.method === 'POST').length, 0);
});
test('acquisition downloads authenticate by header only and never put secrets in filenames or URLs', async () => {
  const {client, calls} = fixtureClient(() => ({ok:true, blob:async () => new Blob([FIXTURE])}));
  seed(client);
  for (const kind of ['csv', 'manifest']) {
    const file = await client.download(kind);
    const call = calls.at(-1);
    assert.equal(call.url, '/api/v1/experiments/' + RUN_A + '/' + kind);
    assert.equal(call.method, 'GET');
    assert.equal(call.headers.Authorization, 'Bearer ' + TOKEN_OLD);
    assert.equal(call.credentials, 'omit'); assert.equal(call.cache, 'no-store'); assert.equal(call.redirect, 'error');
    for (const secret of [TOKEN_OLD, KEY_OLD]) {
      assert.ok(!call.url.includes(secret)); assert.ok(!file.filename.includes(secret));
    }
    assert.equal(await file.blob.text(), FIXTURE);
  }
});
test('acquisition failed or invalid downloads never expose a blob or send a mutation', async () => {
  let blobs = 0;
  const {client, calls} = fixtureClient(() => ({ok:false, status:401, blob:async () => {blobs++; return new Blob([FIXTURE]);}}));
  await assert.rejects(client.download('csv')); assert.equal(calls.length, 0);
  seed(client);
  await assert.rejects(client.download('unknown')); assert.equal(calls.length, 0);
  await assert.rejects(client.download('csv')); assert.equal(blobs, 0);
  assert.ok(calls.every(call => call.method === 'GET'));
});
test('acquisition source has no HTML injection, token persistence or motor-endpoint call', () => {
  const source = fs.readFileSync(path.join(__dirname, '../acquisition.js'), 'utf8');
  assert.doesNotMatch(source, /localStorage|sessionStorage|indexedDB|document\.cookie|innerHTML|outerHTML|insertAdjacentHTML/);
  assert.doesNotMatch(source, /\/api\/v1\/control|console\.log/);
});

for (const change of ['token','selection']) test('acquisition download rejects a late blob after ' + change + ' changes', async t => {
  const blob = deferred(); t.after(() => blob.resolve(new Blob([FIXTURE])));
  const {client, calls} = fixtureClient(call => call.url.endsWith('/csv') ?
    {ok:true,blob:() => blob.promise} : response(snapshot(RUN_B,{experiment_key:KEY_NEW})));
  seed(client);
  const downloading = client.download('csv'); await turn();
  if (change === 'token') client.setToken(TOKEN_NEW); else client.select(RUN_B);
  blob.resolve(new Blob([FIXTURE + '_OLD_SNAPSHOT']));
  await assert.rejects(downloading, /变化/);
  await settleUntil(() => client.state.selected_run?.run_id === RUN_B, 'replacement access/view must load');
  assert.ok(calls.every(call => call.method === 'GET'));
});
test('acquisition malformed data responses are rejected instead of marked connected', async () => {
  for (const bad of [null, [], FIXTURE]) {
    const {client} = fixtureClient(() => ({ok:true,json:async () => ({data:bad})}));
    await client.refresh();
    assert.equal(client.state.connected, false);
    assert.match(client.networkError, /格式/);
  }
});
test('acquisition missing selected run falls back through one read-only queued refresh', async () => {
  const {client, calls} = fixtureClient(call => call.url.endsWith('/'+RUN_B) ?
    errorResponse('experiment_not_found',404) : response());
  seed(client); await client.select(RUN_B);
  await settleUntil(() => client.state.connected && !client.selectionPending, 'missing selection should recover to the default view');
  assert.equal(client.viewRun, null);
  assert.equal(client.state.selected_run.run_id, RUN_A);
  assert.ok(!client.networkError);
  assert.equal(calls.at(-1).url, '/api/v1/experiments');
  assert.ok(calls.every(call => call.method === 'GET'));
});

// A deliberately small VM DOM exercises production listeners and text sinks.
// These are Node event/default simulations, NOT actual browser/layout tests.
class MemoryElement {
  constructor(id = '', tagName = 'DIV') {
    this.id = id; this.tagName = tagName; this.value = ''; this.dataset = {};
    this.children = []; this.listeners = new Map(); this.disabled = false; this.hidden = false;
    this.classList = {toggle:() => {}}; this._text = '';
  }
  set innerHTML(_) { throw Error('SOFTWARE_FIXTURE_NOT_COURSE_DATA: unsafe HTML sink'); }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
  addEventListener(type, listener) {if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(listener);}
  append(...nodes) {for (const node of nodes) this.children.push(...(node.tagName === '#FRAGMENT' ? node.children : [node]));}
  replaceChildren(...nodes) {this.children = []; this._text = ''; this.append(...nodes);}
  setAttribute(name, value) {this[name] = String(value);}
  matches(selector) {return this.tagName === 'TEXTAREA' && selector.includes('textarea') || this.tagName === 'INPUT' && !this.type && selector.includes('input:not([type])');}
  closest() {return ['INPUT', 'TEXTAREA', 'SELECT'].includes(this.tagName) ? this : null;}
  setRangeText(text, start, end) {this.value = this.value.slice(0,start) + text + this.value.slice(end); this.selectionStart = this.selectionEnd = start + text.length;}
  click() {return Promise.all((this.listeners.get('click') || []).map(listener => listener({target:this})));}
}
function memoryPage({motorEnabled = false, acquisitionState = snapshot(null), handler} = {}) {
  const elements = new Map(), calls = [], intervalTasks = new Map(), timeoutTasks = new Map();
  const windowListeners = new Map(), documentListeners = new Map(); let sequence = 0, client;
  const register = (map, type, listener) => {if (!map.has(type)) map.set(type, []); map.get(type).push(listener);};
  const element = id => {
    if (!elements.has(id)) {
      const tag = ['acq-notes','acq-stations'].includes(id) ? 'TEXTAREA' :
        ['acq-group','acq-room'].includes(id) ? 'INPUT' :
        ['acq-purpose','acq-run','acq-station'].includes(id) ? 'SELECT' : 'DIV';
      elements.set(id, new MemoryElement(id, tag));
    }
    return elements.get(id);
  };
  element('acq-purpose').value = 'bench_diagnostic';
  const document = {hidden:false, getElementById:element, querySelectorAll:() => [],
    createElement:tag => new MemoryElement('', tag.toUpperCase()),
    createDocumentFragment:() => new MemoryElement('', '#FRAGMENT'),
    addEventListener:(type, listener) => register(documentListeners, type, listener)};
  Object.defineProperty(document, 'cookie', {get(){throw Error('cookie access forbidden');}, set(){throw Error('cookie write forbidden');}});
  const forbiddenStorage = new Proxy({}, {get(){throw Error('persistent storage forbidden');}, set(){throw Error('persistent storage forbidden');}});
  const context = vm.createContext({console, module:{exports:{}}, AbortController, TextEncoder, Blob,
    localStorage:forbiddenStorage, sessionStorage:forbiddenStorage,
    fetch:async (url, options = {}) => {
      const call = {url, ...options, method:options.method || 'GET', headers:{...options.headers}, data:options.body ? JSON.parse(options.body) : null};
      calls.push(call);
      if (handler) {const handled = handler(call); if (handled !== undefined) return handled;}
      if (url === '/api/v1/control') return response({enabled:motorEnabled, armed:false, state:motorEnabled?'stopped':'disabled', control_key:FIXTURE+'_CONTROL_KEY'});
      if (url.startsWith('/api/v1/experiments')) return response(acquisitionState);
      throw Error('Unexpected mocked URL: ' + url);
    },
    setTimeout:(callback, delay) => {const id=++sequence; timeoutTasks.set(id, {callback,delay}); return id;},
    clearTimeout:id => timeoutTasks.delete(id),
    setInterval:(callback, delay) => {const id=++sequence; intervalTasks.set(id, {callback,delay}); return id;},
    clearInterval:id => intervalTasks.delete(id),
    URL:{createObjectURL:() => 'blob:SOFTWARE_FIXTURE_NOT_COURSE_DATA', revokeObjectURL(){}},
    window:{addEventListener:(type, listener) => register(windowListeners,type,listener)}});
  // Evaluate with no document first so the test can observe the created client.
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../control.js'),'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../acquisition.js'),'utf8'), context);
  const prototype = context.module.exports.AcquisitionClient.prototype;
  const originalRefresh = prototype.refresh;
  prototype.refresh = function(...args) {client = this; return originalRefresh.apply(this,args);};
  context.document = document;
  vm.runInContext('mountWheelControl(); mountAcquisition();', context);
  function key(code, target, extras = {}) {
    const event = {code, target, isComposing:false, defaultPrevented:false, propagationStopped:false,
      preventDefault(){this.defaultPrevented=true;}, stopPropagation(){this.propagationStopped=true;}, ...extras};
    for (const listener of element('acq-start-form').listeners.get('keydown') || []) listener(event);
    if (!event.propagationStopped) for (const listener of windowListeners.get('keydown') || []) listener(event);
    if (code === 'Space' && !event.defaultPrevented && target.matches('textarea,input:not([type])')) target.setRangeText(' ',target.selectionStart,target.selectionEnd);
    return event;
  }
  return {element, elements, calls, key,
    keyUp:code=>{for(const listener of windowListeners.get('keyup')||[])listener({code,preventDefault(){}});},
    heartbeat:async()=>{for(const [id,task] of [...timeoutTasks])if(task.delay===100){timeoutTasks.delete(id);await task.callback();}},
    get client(){return client;},
    dispatchToken:token => {for (const listener of documentListeners.get('rdk-token') || []) listener({detail:token});},
    close:() => {for (const listener of windowListeners.get('pagehide') || []) listener({}); intervalTasks.clear(); timeoutTasks.clear();}};
}

test('acquisition page initialization and benign polling never write or start motors', async t => {
  const page = memoryPage(); t.after(page.close); await turn();
  assert.ok(page.calls.some(call => call.url === '/api/v1/experiments'));
  assert.ok(page.calls.some(call => call.url === '/api/v1/control'));
  assert.ok(page.calls.every(call => call.method === 'GET' && call.data === null));
  assert.equal(page.element('acq-purpose').value, 'bench_diagnostic');
  assert.equal(page.element('acq-stations').value, '');
});
for (const motorEnabled of [false,true]) test('acquisition Space inserts once and preserves emergency stop (motor enabled=' + motorEnabled + ')', async t => {
  const page = memoryPage({motorEnabled}); t.after(page.close); await turn();
  const input = page.element('acq-notes'); input.value = FIXTURE; input.selectionStart = input.selectionEnd = input.value.length;
  const event = page.key('Space',input); await turn();
  assert.equal(input.value, FIXTURE + ' ');
  assert.equal(event.defaultPrevented, true);
  assert.equal(event.propagationStopped, false);
  assert.deepEqual(page.calls.filter(call => call.method === 'POST').map(call => call.data.action), motorEnabled ? ['stop'] : []);
});
test('acquisition textarea WASD/arrows never become motor arm or drive commands', async t => {
  const page = memoryPage({motorEnabled:true}); t.after(page.close); await turn();
  const input = page.element('acq-notes');
  for (const code of ['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowLeft','ArrowDown','ArrowRight']) page.key(code,input);
  await turn(); assert.equal(page.calls.filter(call => call.method === 'POST').length, 0);
});
test('acquisition pending selection disables DOM actions and hides old event rows', async t => {
  const pending = deferred();
  const oldExplanation = FIXTURE + '_OLD_EVENT';
  const selected_run = {...runInfo(RUN_A), event_count:1,events:[{Status:'success',Explanation:oldExplanation}]};
  const page = memoryPage({acquisitionState:snapshot(RUN_A,{selected_run}), handler:call => call.url.endsWith('/'+RUN_B) ? pending.promise : undefined});
  t.after(() => {pending.resolve(response(snapshot(RUN_B))); page.close();}); await turn();
  assert.equal(page.element('acq-csv').disabled, false);
  assert.ok(page.element('acq-events').textContent.includes(oldExplanation));
  const selecting = page.client.select(RUN_B);
  assert.equal(page.element('acq-csv').disabled, true);
  assert.equal(page.element('acq-manifest').disabled, true);
  assert.equal(page.element('acq-capture').disabled, true);
  assert.ok(!page.element('acq-events').textContent.includes(oldExplanation));
  pending.resolve(response(snapshot(RUN_B))); await selecting; await turn();
  assert.equal(page.element('acq-csv').disabled, false);
});
test('acquisition token event stays in memory/header and never reaches DOM or write payload', async t => {
  const page = memoryPage(); t.after(page.close); await turn();
  page.dispatchToken(TOKEN_NEW); await turn();
  assert.equal(page.client.token,TOKEN_NEW);
  assert.ok(page.calls.some(call => call.url === '/api/v1/experiments' && call.headers.Authorization === 'Bearer '+TOKEN_NEW));
  for (const element of page.elements.values()) {
    assert.ok(!element.textContent.includes(TOKEN_NEW));
    assert.ok(!String(element.value).includes(TOKEN_NEW));
  }
  assert.ok(page.calls.every(call => !call.url.includes(TOKEN_NEW) && !String(call.body).includes(TOKEN_NEW)));
  assert.ok(page.calls.every(call => call.method === 'GET'));
});
test('acquisition result_committing conflict explicitly says cancellation was not accepted', async t => {
  let committing = false;
  const page = memoryPage({handler:call => {
    if (!call.url.startsWith('/api/v1/experiments')) return undefined;
    if (call.method === 'POST') {committing=true; return errorResponse('result_committing');}
    return response(snapshot(RUN_A,{busy:true,operation:'capture',committing}));
  }});
  t.after(page.close); await turn();
  assert.equal(page.element('acq-cancel').disabled,false);
  await page.element('acq-cancel').click();
  assert.equal(page.element('acq-cancel').disabled,true);
  assert.match(page.element('acq-message').textContent,/取消未被接受/);
  assert.equal(page.calls.filter(call => call.method === 'POST').length,1);
});
test('acquisition asynchronous close persistence stays visibly closing and blocks capture', async t => {
  const page = memoryPage({acquisitionState:snapshot(RUN_A,{busy:true,operation:'close',closing:true})});
  t.after(page.close); await turn();
  assert.equal(page.element('acq-close').disabled,true);
  assert.equal(page.element('acq-capture').disabled,true);
  assert.match(page.element('acq-message').textContent,/最终清单/);
});
test('acquisition async start persistence keeps configuration/start disabled', async t => {
  const page = memoryPage({acquisitionState:snapshot(null,{busy:true,operation:'start'})});
  t.after(page.close); await turn();
  assert.equal(page.element('acq-config').disabled,true);
  assert.equal(page.element('acq-start').disabled,true);
  assert.equal(page.element('acq-capture').disabled,true);
});
test('acquisition terminal commit disables cancel instead of promising a false cancellation', async t => {
  const page = memoryPage({acquisitionState:snapshot(RUN_A,{busy:true,operation:'capture',committing:true})});
  t.after(page.close); await turn();
  assert.equal(page.element('acq-capture').disabled,true);
  assert.equal(page.element('acq-cancel').disabled,true);
});
test('acquisition DOM renders untrusted station/event strings only as text', async t => {
  const payload = FIXTURE + '<img src=x onerror="globalThis.fixtureXSS=true">';
  const info = {...runInfo(RUN_A), events:[{'Detection point':1,'Trigger time':payload,'Temperature_°C':'20','Humidity_%RH':'50',Status:'success',Explanation:payload}],event_count:1};
  const page = memoryPage({acquisitionState:snapshot(RUN_A,{stations:[payload],selected_run:info})});
  t.after(page.close); await turn();
  assert.ok(page.element('acq-events').textContent.includes(payload));
  assert.equal(page.element('acq-station').children[0].textContent,payload);
  assert.ok(page.calls.every(call => call.method === 'GET'));
});
test('acquisition HTML defaults to bench with no synthetic station prefill or implicit control submits', () => {
  const html = fs.readFileSync(path.join(__dirname, '../index.html'),'utf8');
  assert.match(html, /<option value="bench_diagnostic">/);
  for (const id of ['acq-capture','acq-cancel','acq-close','acq-csv','acq-manifest']) assert.match(html, new RegExp('id="'+id+'" type="button"'));
  assert.match(html, /id="acq-start"[^>]*type="submit"/);
  assert.match(html, /id="acq-stations"[^>]*><\/textarea>/);
  assert.doesNotMatch(html, /(?:src|href)="https?:/);
});

test('sensor-only mode labels bench scope and disables formal selection without motor writes', async t => {
  const page=memoryPage({acquisitionState:snapshot(null,{sensor_only:true,allowed_purposes:['bench_diagnostic']})});
  t.after(page.close); await turn();
  assert.match(page.element('acq-mode').textContent,/桌面传感器测试/);
  assert.equal(page.element('acq-purpose-formal').disabled,true);
  page.element('acq-purpose').value='room_stationary';
  await page.client.refresh();
  assert.equal(page.element('acq-purpose').value,'bench_diagnostic');
  assert.equal(page.element('acq-formal').hidden,true);
  assert.ok(page.calls.every(call=>call.method==='GET'));
});

test('DOM key chords keep moving on remaining key and release only when all keys are up',async t=>{
  const page=memoryPage({motorEnabled:true,handler:call=>{
    if(call.url!=='/api/v1/control')return undefined;
    const action=call.data?.action;
    return response({enabled:true,armed:['arm','drive','release'].includes(action),state:action==='drive'?'driving':'stopped',control_key:'FIXTURE',...(action==='arm'?{session:'FIXTURE_SESSION'}:{})});
  }});
  t.after(page.close);await turn();page.element('drive-speed').value='200';
  const target=page.element('drive-panel');
  page.key('ArrowUp',target);await turn();
  page.key('ArrowRight',target);await page.heartbeat();await turn();
  assert.equal(page.calls.filter(c=>c.data?.action==='drive').at(-1).data.direction,'forward_right');
  page.keyUp('ArrowRight');await page.heartbeat();await turn();
  assert.equal(page.calls.filter(c=>c.data?.action==='drive').at(-1).data.direction,'forward');
  assert.equal(page.calls.filter(c=>c.data?.action==='release').length,0);
  page.keyUp('ArrowUp');await turn();
  assert.equal(page.calls.filter(c=>c.data?.action==='release').length,1);
});
