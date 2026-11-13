const {test} = require('node:test');
const assert = require('node:assert/strict');
const app = require('../app.js');
const response = () => ({ok:true, status:200, headers:{get:()=> 'application/json'}, json: async()=>({schema_version:'1.0', generated_at:'2026-10-09T00:00:00Z', data:{health:{},system:{},devices:{},telemetry:{}}})});
const turn = () => new Promise(resolve => setImmediate(resolve));

test('polling is serial, pause retains stale data, failure and 401 never stay live', async () => {
  assert.equal(typeof app.Poller, 'function');
  let finish, calls=0;
  const poll = new app.Poller({fetch:()=>{calls++; return new Promise(resolve=>{finish=resolve;});}, change:()=>{}, interval:100000});
  const first = poll.refresh();
  poll.refresh();
  assert.equal(calls, 1);
  finish(response()); await first;
  assert.ok(poll.state.data);
  assert.equal(poll.state.phase, 'live');
  poll.pause();
  assert.equal(poll.state.phase, 'paused');
  assert.ok(poll.state.data);
  poll.fetch = async()=>({ok:false,status:401});
  await poll.refresh();
  assert.equal(poll.state.phase, 'auth');
  assert.ok(poll.state.data);
  poll.fetch = async()=>{throw Error('network');};
  await poll.refresh();
  assert.equal(poll.state.phase, 'error');
  poll.pause();
});

test('timeout aborts request and token is header only, invalid JSON preserves last snapshot', async () => {
  assert.equal(typeof app.Poller, 'function');
  let seen;
  const poll = new app.Poller({fetch:async(url, options)=>{seen={url,options};return response();}, change:()=>{}, interval:100000, timeout:15});
  poll.setToken('secret-test'); await turn();
  assert.equal(seen.url, '/api/v1/status');
  assert.equal(seen.options.headers.Authorization, 'Bearer secret-test');
  const old = poll.state.data;
  poll.fetch = async()=>({...response(), json:async()=>({schema_version:'unexpected'})});
  await poll.refresh();
  assert.equal(poll.state.phase, 'error');
  assert.equal(poll.state.data, old);
  poll.fetch = (_, options)=>new Promise((_,reject)=>options.signal.addEventListener('abort',()=>reject(Error('abort'))));
  await poll.refresh();
  assert.equal(poll.state.phase, 'error');
  assert.match(poll.state.message, /超时/);
  poll.pause();
});

test('encoder state remains unverified and ages even without a new response', () => {
  assert.equal(typeof app.encoderState, 'function');
  assert.equal(app.encoderState({status:'observed', age_seconds:0, stale_after_seconds:2}, 3), '数据过期 · 编码器未验证');
  assert.equal(app.encoderState({status:'observed', age_seconds:0, stale_after_seconds:2}, 0), '收到原始帧 · 编码器未验证');
  assert.match(app.encoderState({status:'disabled'}, 0), /未启用/);
});

test('pause/resume rejects a late response and never overlaps the replacement', async () => {
  let finish, calls=0;
  const poll = new app.Poller({fetch:()=>{calls++; return new Promise(resolve=>{finish=resolve;});}, interval:100000});
  const first = poll.refresh();
  poll.pause(); poll.resume();
  assert.equal(calls,1);
  finish(response()); await first;
  assert.equal(poll.state.data,null);
  assert.equal(calls,2);
  finish(response()); await turn();
  assert.equal(poll.state.phase,'live');
  poll.pause();
});

test('thermal summary stays compact without inventing a CPU sensor', () => {
  assert.equal(typeof app.thermalSummary, 'function');
  assert.equal(app.thermalSummary(null), '—');
  assert.equal(app.thermalSummary([]), '—');
  assert.equal(app.thermalSummary([{zone:'a', celsius:21}, {zone:'b', celsius:45}]), '2 个区域 · 21.0–45.0 °C');
});

test('Chinese monitor has accessible controls and no external assets or token storage', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '../index.html'), 'utf8');
  for (const id of ['connection','refresh','pause','settings','token','resources','devices','encoders','raw']) assert.ok(html.includes(`id="${id}"`), id);
  assert.match(html, /type="password"/);
  assert.match(html, /lang="zh-CN"/);
  assert.match(html, /viewport/);
  assert.doesNotMatch(html, /(?:src|href)="https?:/);
  const js = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
  assert.doesNotMatch(js, /localStorage|sessionStorage|console\.log|innerHTML/);
});

test('unknown values never become zero and bars use real ratios', () => {
  assert.equal(typeof app.bytes, 'function');
  assert.equal(app.bytes(null), '—');
  assert.equal(app.bytes(0), '0 B');
  assert.equal(app.bytes(1024), '1.0 KiB');
  assert.equal(app.percent(null, 100), null);
  assert.equal(app.percent(30, 100), 30);
  assert.equal(app.percent(110, 100), null);
  assert.equal(app.percent(1, 0), null);
});
