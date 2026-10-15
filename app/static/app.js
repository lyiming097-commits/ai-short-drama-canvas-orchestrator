const state = { projectId: null, runId: null, events: [] };
const $ = (id) => document.getElementById(id);
const labels = { running: '生成中', waiting_user: '等待确认', generating_media: '生成媒体', completed: '已完成', partial_failed: '部分失败', canceled: '已取消', failed: '失败' };
const stageLabels = { script: '剧本', characters: '角色与场景', scenes: '角色与场景', storyboard: '分镜', prompts: '分镜', media: '媒体资产', completed: '已完成' };

async function request(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch { data = { detail: text }; }
  if (!response.ok) throw new Error(data.detail || `请求失败（HTTP ${response.status}）`);
  return data;
}

function setStatus(status) {
  const badge = $('status-badge');
  badge.textContent = labels[status] || status;
  badge.className = `status ${status === 'completed' ? 'done' : status === 'waiting_user' ? 'waiting' : status === 'running' || status === 'generating_media' ? 'running' : 'idle'}`;
}

function setProgress(stage) {
  const order = ['script', 'characters', 'storyboard', 'media'];
  const current = stage === 'completed' ? 4 : Math.max(0, order.indexOf(stage));
  document.querySelectorAll('.progress-step').forEach((item, index) => {
    item.classList.toggle('active', index === current && stage !== 'completed');
    item.classList.toggle('done', index < current || stage === 'completed');
  });
}

function renderContent(run) {
  const data = run.state || {};
  const cards = [];
  if (data.brief) cards.push(`<article class="content-card wide"><h4>创作简报</h4><p><b>${data.brief.title}</b> · ${data.brief.genre} · ${data.brief.tone}</p><p>${data.brief.logline}</p></article>`);
  if (data.script) cards.push(`<article class="content-card wide"><h4>剧本 · 第 ${data.script.version} 版</h4>${(data.script.beats || []).map((beat, i) => `<div class="shot-card"><div class="shot-number">${String(i + 1).padStart(2, '0')}</div><div><strong>${beat.location} · ${beat.time}</strong><p>${beat.action}</p>${(beat.dialogue || []).map((line) => `<p>“${line}”</p>`).join('')}</div></div>`).join('')}</article>`);
  if (data.characters?.length) cards.push(`<article class="content-card"><h4>角色设定</h4>${data.characters.map((item) => `<p><b>${item.name}</b> · ${item.appearance} · ${item.clothing}</p><p>${item.personality}</p>`).join('')}</article>`);
  if (data.scenes?.length) cards.push(`<article class="content-card"><h4>场景设定</h4>${data.scenes.map((item) => `<p><b>${item.name}</b> · ${item.time} · ${item.weather}</p><p>${item.description}</p>`).join('')}</article>`);
  if (data.shots?.length) cards.push(`<article class="content-card wide"><h4>分镜规划 · ${data.shots.length} 个镜头</h4>${data.shots.map((shot) => `<div class="shot-card"><div class="shot-number">${String(shot.sequence).padStart(2, '0')}</div><div><strong>${shot.shot_size} · ${shot.duration_seconds} 秒 · ${shot.camera_motion}</strong><p>${shot.action}</p>${shot.voice_text ? `<p>配音：${shot.voice_text}</p>` : ''}<p class="muted">视频 Prompt：${shot.video_prompt || '待生成'}</p></div></div>`).join('')}</article>`);
  $('content-cards').innerHTML = cards.join('') || '<p class="muted">正在整理内容……</p>';
}

function renderApproval(run) {
  const waiting = run.status === 'waiting_user';
  $('approval-banner').classList.toggle('hidden', !waiting);
  if (!waiting) return;
  const isScript = run.pending_approval === 'script';
  $('approval-title').textContent = isScript ? '剧本已生成，请确认创作方向' : '分镜已生成，请确认镜头规划';
  $('approval-text').textContent = isScript ? '确认后系统会继续生成角色、场景和分镜。' : '确认后系统会创建图片、Seedance 视频和配音任务。';
  $('approve-btn').innerHTML = isScript ? '确认并生成分镜 <span>→</span>' : '确认并生成素材 <span>→</span>';
}

function renderActivity(detail) {
  $('activity-panel').classList.remove('hidden');
  const events = state.events;
  $('event-count').textContent = `${events.length} 条事件`;
  $('timeline').innerHTML = events.slice(-10).reverse().map((event) => `<div class="event"><strong>${event.type}</strong><small>事件 ${event.sequence}</small></div>`).join('') || '<p class="muted">暂无事件</p>';
  $('asset-list').innerHTML = (detail.assets || []).map((asset) => `<div class="asset"><span>${asset.shot_id || '项目资产'} · ${asset.media_type}</span><span>已归档</span></div>`).join('') || '<p class="muted">媒体任务将在分镜确认后创建。</p>';
}

async function refresh() {
  if (!state.runId) return;
  const detail = await request(`/api/runs/${state.runId}`);
  const run = detail.run;
  $('canvas-title').textContent = run.state?.brief?.title || '短剧创作运行中';
  $('canvas-empty').classList.add('hidden');
  $('canvas-content').classList.remove('hidden');
  setStatus(run.status); setProgress(run.stage); renderContent(run); renderApproval(run); renderActivity(detail);
  return run;
}

async function loadEvents() {
  if (!state.runId) return;
  const data = await request(`/api/runs/${state.runId}/events?after=0`);
  state.events = data.events || [];
}

async function refreshUsage() {
  try {
    const usage = await request('/api/admin/usage' + (state.runId ? `?run_id=${state.runId}` : ''));
    $('usage-calls').textContent = usage.calls;
    $('usage-input').textContent = usage.input_tokens.toLocaleString();
    $('usage-output').textContent = usage.output_tokens.toLocaleString();
    $('usage-cost').textContent = Number(usage.actual_cost ?? usage.estimated_cost ?? 0).toFixed(6);
    $('usage-models').innerHTML = usage.by_model?.length ? usage.by_model.map((item) => `<div class="usage-model"><span>${item.provider} / ${item.model} · ${item.calls} 次 · ${item.tokens.toLocaleString()} Token</span><span>${Number(item.cost).toFixed(6)}</span></div>`).join('') : '<p class="muted">运行创作流程后会显示模型用量。</p>';
  } catch (error) { $('usage-models').innerHTML = `<p class="muted">用量暂时无法读取：${error.message}</p>`; }
}

async function loadProviders() {
  const data = await request('/api/admin/model-providers');
  $('provider-list').innerHTML = data.providers.length ? data.providers.map((item) => `<div class="provider-row"><div><strong>${item.name}</strong><small>${item.provider_type} · ${item.model_name} · ${item.masked_api_key}</small></div><div class="provider-actions"><span class="provider-ok">${item.enabled ? '已启用' : '已停用'}</span><button class="secondary compact" data-test-provider="${item.id}" type="button">测试连接</button></div></div>`).join('') : '<p class="muted">还没有配置模型连接。</p>';
  document.querySelectorAll('[data-test-provider]').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true; button.textContent = '测试中…';
    try { const result = await request(`/api/admin/model-providers/${button.dataset.testProvider}/test`, { method: 'POST' }); window.alert(result.message); }
    catch (error) { window.alert(error.message); }
    finally { button.disabled = false; button.textContent = '测试连接'; }
  }));
}

async function saveProvider() {
  const capabilities = [...document.querySelectorAll('.capabilities input:checked')].map((item) => item.value);
  const apiKey = $('provider-key').value;
  if (!apiKey && $('provider-type').value !== 'mock') { window.alert('请输入 API Key'); return; }
  try {
    await request('/api/admin/model-providers', { method: 'POST', body: JSON.stringify({ name: $('provider-name').value, provider_type: $('provider-type').value, capabilities, api_base: $('provider-base').value, model_name: $('provider-model').value, api_key: apiKey || 'demo-key' }) });
    $('provider-key').value = ''; await loadProviders(); window.alert('模型连接已保存，密钥已在服务端加密。');
  } catch (error) { window.alert(error.message); }
}

async function start() {
  $('start-btn').disabled = true; $('start-btn').textContent = '正在生成剧本…';
  try {
    const project = await request('/api/projects', { method: 'POST', body: JSON.stringify({ name: $('project-name').value, canvas_id: $('canvas-id').value }) });
    state.projectId = project.id;
    const detail = await request(`/api/projects/${state.projectId}/runs`, { method: 'POST', body: JSON.stringify({ prompt: $('story-prompt').value }) });
    state.runId = detail.run.id; await loadEvents(); await refresh();
    $('setup-hint').textContent = '请查看剧本，确认后进入角色、场景和分镜规划。';
  } catch (error) { $('setup-hint').textContent = error.message; }
  finally { $('start-btn').disabled = false; $('start-btn').innerHTML = '开始生成剧本 <span>↗</span>'; }
}

async function approve(action) {
  if (!state.runId) return;
  let feedback = null;
  if (action === 'revise') { feedback = window.prompt('请填写修改意见'); if (!feedback) return; }
  $('approve-btn').disabled = true; $('revise-btn').disabled = true;
  try { await request(`/api/runs/${state.runId}/approve`, { method: 'POST', body: JSON.stringify({ action, feedback }) }); await loadEvents(); await refresh(); }
  catch (error) { window.alert(error.message); }
  finally { $('approve-btn').disabled = false; $('revise-btn').disabled = false; }
}

$('start-btn').addEventListener('click', start);
$('approve-btn').addEventListener('click', () => approve('approve'));
$('revise-btn').addEventListener('click', () => approve('revise'));
$('save-provider').addEventListener('click', saveProvider);
$('refresh-providers').addEventListener('click', loadProviders);
$('refresh-usage').addEventListener('click', refreshUsage);
loadProviders(); refreshUsage();
setInterval(async () => { if (state.runId) { await loadEvents(); await refresh(); await refreshUsage(); } }, 2000);
