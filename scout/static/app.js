// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  initiatives: [],
  selectedId: null,
  currentDetail: null,
  sort: { by: 'score', dir: 'desc' },
  currentDb: 'scout',
  customColumns: [],
  columnOrder: null, // null = default order; array of column keys when customised
};

const _COL_KEY_RE = /^[a-zA-Z0-9_-]+$/;

// ---------------------------------------------------------------------------
// Utility (defined early — used by column renderers and all HTML builders)
// ---------------------------------------------------------------------------
const _escDiv = document.createElement('div');
function esc(s) {
  if (!s) return '';
  _escDiv.textContent = String(s);
  return _escDiv.innerHTML;
}

function escAttr(s) { return esc(s || '').replace(/'/g, '&#39;'); }

function humanize(s) { return (s || '').replace(/_/g, ' '); }

function safeUrl(url) {
  if (!url) return '#';
  const s = String(url).trim();
  if (/^(javascript|data|vbscript):/i.test(s)) return '#';
  return s;
}

function gradeColorClass(grade) {
  if (!grade) return '';
  const c = grade.charAt(0).toUpperCase();
  if (c === 'A') return 'grade-a';
  if (c === 'B') return 'grade-b';
  if (c === 'C') return 'grade-c';
  return 'grade-d';
}

function gradeBadge(grade) {
  if (!grade) return '<span class="text-faint">\u2014</span>';
  return `<span class="grade-badge ${gradeColorClass(grade)}">${esc(grade)}</span>`;
}

const VERDICT_SHORT = { reach_out_now: 'NOW', reach_out_soon: 'SOON', monitor: 'MON', skip: 'SKIP' };
const VERDICT_LONG = { reach_out_now: 'Reach Out Now', reach_out_soon: 'Reach Out Soon', monitor: 'Monitor', skip: 'Skip' };

function renderPills(str, cssClass) {
  if (!str) return '';
  return str.split(';').map(s => s.trim()).filter(Boolean)
    .map(t => `<span class="domain-pill ${cssClass}">${esc(humanize(t))}</span>`).join('');
}

function signalCard(value, label) {
  return value ? `<div class="card card--compact"><div class="signal-value">${esc(String(value))}</div><div class="card-label">${esc(label)}</div></div>` : '';
}

function listSection(title, items) {
  if (!items?.length) return '';
  return `<div class="detail-section"><h3>${esc(title)}</h3><ul>${items.map(i => `<li>${esc(i)}</li>`).join('')}</ul></div>`;
}

const SIGNALS = [
  ['member_count','Members'],['github_repo_count','Repos'],['github_contributors','Contributors'],
  ['github_commits_90d','Commits (90d)'],['huggingface_model_hits','HF Models'],['openalex_hits','OpenAlex'],
  ['semantic_scholar_hits','Semantic Scholar'],['linkedin_hits','LinkedIn'],['researchgate_hits','ResearchGate'],
];

function _colOrderStorageKey() {
  return `scout-col-order-${state.currentDb}`;
}

function loadColumnOrder() {
  const saved = JSON.parse(localStorage.getItem(_colOrderStorageKey()) || 'null');
  state.columnOrder = Array.isArray(saved) ? saved : null;
}

function saveColumnOrder() {
  localStorage.setItem(_colOrderStorageKey(), JSON.stringify(state.columnOrder));
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------
async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`${resp.status}: ${err}`);
  }
  return resp.json();
}

// ---------------------------------------------------------------------------
// Toast notifications (replaces alert())
// ---------------------------------------------------------------------------
function showToast(message, type) {
  type = type || 'info';
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(function() {
    toast.classList.add('dismissing');
    toast.addEventListener('animationend', function() { toast.remove(); });
  }, 4000);
}

// ---------------------------------------------------------------------------
// Modal dialogs (replaces confirm() and prompt())
// ---------------------------------------------------------------------------
function showConfirmModal(message) {
  return new Promise(function(resolve) {
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    // Build modal DOM safely using createElement + textContent
    var box = document.createElement('div');
    box.className = 'modal-box';
    var p = document.createElement('p');
    p.textContent = message;
    box.appendChild(p);
    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-sm';
    cancelBtn.textContent = 'Cancel';
    var confirmBtn = document.createElement('button');
    confirmBtn.className = 'btn btn-sm btn-danger';
    confirmBtn.textContent = 'Confirm';
    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    function cleanup(result) { overlay.remove(); resolve(result); }
    cancelBtn.onclick = function() { cleanup(false); };
    confirmBtn.onclick = function() { cleanup(true); };
    overlay.addEventListener('click', function(e) { if (e.target === overlay) cleanup(false); });
  });
}

function showPromptModal(title, label, placeholder) {
  return new Promise(function(resolve) {
    var overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    // Build modal DOM safely using createElement + textContent
    var box = document.createElement('div');
    box.className = 'modal-box';
    var h3 = document.createElement('h3');
    h3.textContent = title;
    box.appendChild(h3);
    var lbl = document.createElement('label');
    lbl.className = 'card-label';
    lbl.textContent = label;
    box.appendChild(lbl);
    var input = document.createElement('input');
    input.type = 'text';
    input.placeholder = placeholder || '';
    box.appendChild(input);
    var actions = document.createElement('div');
    actions.className = 'modal-actions';
    var cancelBtn = document.createElement('button');
    cancelBtn.className = 'btn btn-sm';
    cancelBtn.textContent = 'Cancel';
    var okBtn = document.createElement('button');
    okBtn.className = 'btn btn-sm btn-primary';
    okBtn.textContent = 'OK';
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    box.appendChild(actions);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    function cleanup(val) { overlay.remove(); resolve(val); }
    cancelBtn.onclick = function() { cleanup(null); };
    okBtn.onclick = function() { cleanup(input.value.trim()); };
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter') cleanup(input.value.trim());
      if (e.key === 'Escape') cleanup(null);
    });
    overlay.addEventListener('click', function(e) { if (e.target === overlay) cleanup(null); });
    setTimeout(function() { input.focus(); }, 50);
  });
}

// ---------------------------------------------------------------------------
// Loading skeletons
// ---------------------------------------------------------------------------
function showListSkeleton() {
  var tbody = document.getElementById('list-body');
  var rows = [];
  for (var i = 0; i < 8; i++) {
    rows.push('<tr><td colspan="99"><div class="skeleton-row">' +
      '<div class="skeleton skeleton-cell w-name"></div>' +
      '<div class="skeleton skeleton-cell w-sm"></div>' +
      '<div class="skeleton skeleton-cell w-md"></div>' +
      '<div class="skeleton skeleton-cell w-sm"></div>' +
      '<div class="skeleton skeleton-cell w-sm"></div>' +
      '<div class="skeleton skeleton-cell w-sm"></div>' +
      '<div class="skeleton skeleton-cell w-lg"></div>' +
      '</div></td></tr>');
  }
  // Skeleton rows contain no user data — only static CSS class names
  tbody.innerHTML = rows.join('');
}

function showDetailSkeleton() {
  document.getElementById('detail-empty').style.display = 'none';
  var el = document.getElementById('detail-content');
  el.style.display = 'block';
  el.className = '';
  // Skeleton contains no user data — only static placeholder shapes
  el.innerHTML = '<div class="detail-skeleton">' +
    '<div class="skeleton" style="height:24px;width:60%"></div>' +
    '<div class="skeleton" style="height:14px;width:40%;margin-top:8px"></div>' +
    '<div class="skeleton" style="height:80px;width:100%;margin-top:20px"></div>' +
    '<div class="skeleton" style="height:14px;width:70%;margin-top:16px"></div>' +
    '<div class="skeleton" style="height:14px;width:50%;margin-top:8px"></div>' +
    '</div>';
}

// ---------------------------------------------------------------------------
// Button loading helper
// ---------------------------------------------------------------------------
function btnLoading(btn, loading) {
  if (loading) {
    btn.disabled = true;
    btn._origText = btn.textContent.trim();
    // Clear and rebuild button content safely
    btn.textContent = '';
    var spinner = document.createElement('span');
    spinner.className = 'spinner';
    btn.appendChild(spinner);
    btn.appendChild(document.createTextNode(btn._origText));
  } else if (btn._origText !== undefined) {
    btn.textContent = btn._origText;
    btn.disabled = false;
    delete btn._origText;
  }
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------
async function loadInitiatives() {
  showListSkeleton();
  const f = getFilters();
  let url = `/api/initiatives?sort_by=${state.sort.by}&sort_dir=${state.sort.dir}&per_page=500`;
  if (f.verdict) url += `&verdict=${encodeURIComponent(f.verdict)}`;
  if (f.classification) url += `&classification=${encodeURIComponent(f.classification)}`;
  if (f.uni) url += `&uni=${encodeURIComponent(f.uni)}`;
  if (f.search) url += `&search=${encodeURIComponent(f.search)}`;
  const data = await api('GET', url);
  state.initiatives = data.items;
  renderList();
  loadStats();
}

async function loadStats() {
  const stats = await api('GET', '/api/stats');
  document.getElementById('stat-total').textContent = stats.total;
  document.getElementById('stat-enriched').textContent = stats.enriched;
  document.getElementById('stat-scored').textContent = stats.scored;
  document.getElementById('stat-now').textContent = `${stats.by_verdict.reach_out_now || 0} reach out now`;
  document.getElementById('stat-soon').textContent = `${stats.by_verdict.reach_out_soon || 0} soon`;
  document.getElementById('stat-monitor').textContent = `${stats.by_verdict.monitor || 0} monitor`;
  document.getElementById('btn-enrich-all').disabled = stats.total === 0;
  document.getElementById('btn-score-all').disabled = stats.total === 0;
}

async function loadDetail(id) {
  showDetailSkeleton();
  const data = await api('GET', `/api/initiatives/${id}`);
  state.selectedId = id;
  state.currentDetail = data;
  renderDetail(data);
  document.querySelectorAll('.list-table tbody tr').forEach(tr => {
    tr.classList.toggle('selected', parseInt(tr.dataset.id) === id);
  });
}

// ---------------------------------------------------------------------------
// Filters & sort
// ---------------------------------------------------------------------------
function getFilters() {
  return {
    verdict: document.getElementById('filter-verdict').value,
    classification: document.getElementById('filter-class').value,
    uni: document.getElementById('filter-uni').value,
    search: document.getElementById('filter-search').value,
  };
}

let _filterTimeout;
function applyFilters() {
  clearTimeout(_filterTimeout);
  _filterTimeout = setTimeout(() => loadInitiatives(), 200);
  updateFilterIndicators();
}

function updateFilterIndicators() {
  const f = getFilters();
  const hasAny = f.verdict || f.classification || f.uni || f.search;
  document.getElementById('filter-verdict').classList.toggle('filter-active', !!f.verdict);
  document.getElementById('filter-class').classList.toggle('filter-active', !!f.classification);
  document.getElementById('filter-uni').classList.toggle('filter-active', !!f.uni);
  document.getElementById('filter-search').classList.toggle('filter-active', !!f.search);
  document.getElementById('btn-clear-filters').classList.toggle('visible', !!hasAny);
}

function clearFilters() {
  document.getElementById('filter-verdict').value = '';
  document.getElementById('filter-class').value = '';
  document.getElementById('filter-uni').value = '';
  document.getElementById('filter-search').value = '';
  updateFilterIndicators();
  loadInitiatives();
}

function sortBy(field) {
  if (state.sort.by === field) {
    state.sort.dir = state.sort.dir === 'desc' ? 'asc' : 'desc';
  } else {
    state.sort.by = field;
    state.sort.dir = field === 'name' ? 'asc' : 'desc';
  }
  loadInitiatives();
}

// ---------------------------------------------------------------------------
// Column definitions & inline editing
// ---------------------------------------------------------------------------
// NOTE: esc() and escAttr() sanitise all user-supplied data before insertion
// into the DOM. The innerHTML assignments below only contain content that has
// passed through esc()/escAttr(), which converts <, >, &, and quotes to their
// HTML entity equivalents, preventing script injection.

function editAttr(id, field, value, type) {
  return ` data-edit-id="${id}" data-edit-field="${field}" data-edit-value="${escAttr(value)}"${type ? ` data-edit-type="${type}"` : ''}`;
}
function editFromAttr(el, e) {
  if (e) e.stopPropagation();
  inlineEdit(el, +el.dataset.editId, el.dataset.editField, el.dataset.editValue, el.dataset.editType);
}

const CORE_COLUMNS = [
  { key: 'name', label: 'Initiative', sort: 'name',
    render: i => `<td class="name-cell editable" title="${esc(i.name)}"${editAttr(i.id,'name',i.name)} ondblclick="editFromAttr(this,event)">${esc(i.name)}</td>` },
  { key: 'uni', label: 'Uni', sort: 'uni',
    render: i => `<td class="uni-cell editable"${editAttr(i.id,'uni',i.uni,'select-uni')} ondblclick="editFromAttr(this,event)">${esc(i.uni)}</td>` },
  { key: 'verdict', label: 'Verdict', sort: 'verdict',
    render: i => { const v = i.verdict || 'unscored'; return `<td><span class="verdict-badge verdict-${v}">${VERDICT_SHORT[v] || '\u2014'}</span></td>`; } },
  { key: 'team', label: 'Team', sort: 'grade_team',
    render: i => `<td>${gradeBadge(i.grade_team)}</td>` },
  { key: 'tech', label: 'Tech', sort: 'grade_tech',
    render: i => `<td>${gradeBadge(i.grade_tech)}</td>` },
  { key: 'opp', label: 'Opp', sort: 'grade_opportunity', cssClass: 'col-opp',
    render: i => `<td class="col-opp">${gradeBadge(i.grade_opportunity)}</td>` },
  { key: 'class', label: 'Class', sort: null, cssClass: 'col-class',
    render: i => `<td class="col-class"><span class="class-badge">${esc(humanize(i.classification))}</span></td>` },
];

function getColumns() {
  const cols = [...CORE_COLUMNS];
  state.customColumns
    .filter(cc => cc.show_in_list)
    .sort((a, b) => a.sort_order - b.sort_order)
    .forEach(cc => {
      cols.push({
        key: `custom_${cc.key}`, label: cc.label, sort: null, customColumnId: cc.id,
        render: i => {
          const val = (i.custom_fields || {})[cc.key] || '';
          return `<td class="editable"${editAttr(i.id, 'custom:' + cc.key, val)} ondblclick="editFromAttr(this,event)">${esc(val)}</td>`;
        }
      });
    });
  return cols;
}

function inlineEdit(el, id, field, currentValue, type) {
  if (el.querySelector('.inline-input, .inline-select')) return;
  const original = el.innerHTML;
  let input;

  if (type === 'select-uni') {
    input = document.createElement('select');
    input.className = 'inline-select';
    ['TUM', 'LMU', 'HM', 'TUM/LMU', 'TUM/HM', 'LMU/HM'].forEach(opt => {
      const o = document.createElement('option');
      o.value = opt; o.textContent = opt;
      if (opt === currentValue) o.selected = true;
      input.appendChild(o);
    });
  } else if (type === 'textarea') {
    input = document.createElement('textarea');
    input.className = 'inline-input';
    input.value = currentValue || '';
    input.rows = 4;
  } else {
    input = document.createElement('input');
    input.className = 'inline-input';
    input.type = 'text';
    input.value = currentValue || '';
  }

  el.innerHTML = '';
  el.appendChild(input);
  input.focus();
  if (input.select) input.select();

  let done = false;
  async function save() {
    if (done) return;
    done = true;
    const newVal = input.value.trim();
    // Prevent saving empty required fields
    if (field === 'name' && !newVal) { el.innerHTML = original; return; }
    if (newVal === (currentValue || '').trim()) { el.innerHTML = original; return; }
    try {
      let body;
      if (field.startsWith('custom:')) {
        const customKey = field.slice(7);
        body = { custom_fields: { [customKey]: newVal || null } };
      } else {
        body = { [field]: newVal };
      }
      await api('PUT', `/api/initiatives/${id}`, body);
      loadInitiatives();
      if (state.selectedId === id) loadDetail(id);
    } catch (err) {
      el.innerHTML = original;
      showToast('Save failed: ' + err.message, 'error');
    }
  }
  function cancel() { done = true; el.innerHTML = original; }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && type !== 'textarea') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); cancel(); }
  });
}

// ---------------------------------------------------------------------------
// Column drag-and-drop
// ---------------------------------------------------------------------------
function initColumnDnD() {
  let dragKey = null;
  document.querySelectorAll('#list-head th[draggable="true"]').forEach(th => {
    th.addEventListener('dragstart', e => {
      dragKey = th.dataset.colKey;
      e.dataTransfer.effectAllowed = 'move';
      th.style.opacity = '0.4';
    });
    th.addEventListener('dragend', () => {
      th.style.opacity = '1';
      document.querySelectorAll('#list-head th').forEach(h => h.classList.remove('drag-over'));
    });
    th.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      th.classList.add('drag-over');
    });
    th.addEventListener('dragleave', () => th.classList.remove('drag-over'));
    th.addEventListener('drop', e => {
      e.preventDefault();
      th.classList.remove('drag-over');
      const toKey = th.dataset.colKey;
      if (dragKey === toKey) return;
      const order = [...(state.columnOrder || getColumns().map(c => c.key))];
      const fromPos = order.indexOf(dragKey);
      const toPos = order.indexOf(toKey);
      if (fromPos < 0 || toPos < 0) return;
      order.splice(fromPos, 1);
      order.splice(toPos, 0, dragKey);
      state.columnOrder = order;
      saveColumnOrder();
      renderList();
    });
  });
}

// ---------------------------------------------------------------------------
// Render list
// ---------------------------------------------------------------------------
function renderList() {
  const tbody = document.getElementById('list-body');
  const thead = document.getElementById('list-head');
  const empty = document.getElementById('empty-state');
  const COLUMNS = getColumns();

  if (state.initiatives.length === 0) {
    tbody.innerHTML = '';
    thead.innerHTML = '';
    empty.style.display = 'flex';
    return;
  }
  empty.style.display = 'none';

  // Build ordered columns by key (stable across add/remove)
  const colByKey = new Map(COLUMNS.map((c, i) => [c.key, i]));
  let orderedKeys;
  if (state.columnOrder) {
    // Start with saved keys that still exist
    orderedKeys = state.columnOrder.filter(k => colByKey.has(k));
    // Append any new columns not in saved order
    COLUMNS.forEach(c => { if (!orderedKeys.includes(c.key)) orderedKeys.push(c.key); });
  } else {
    orderedKeys = COLUMNS.map(c => c.key);
  }
  state.columnOrder = orderedKeys;

  // Render header — all values passed through esc() / escAttr()
  thead.innerHTML = orderedKeys.map(key => {
    const col = COLUMNS[colByKey.get(key)];
    const isCustom = col.customColumnId != null;
    const cls = col.cssClass ? ` class="${col.cssClass}"` : '';
    return `<th draggable="true" data-col-key="${esc(col.key)}"${cls} ${col.sort ? `onclick="sortBy('${col.sort}')"` : ''}${isCustom ? ` oncontextmenu="removeCustomColumn(event, ${parseInt(col.customColumnId)})"` : ''}>${esc(col.label)}</th>`;
  }).join('') + `<th class="add-col-th" onclick="addCustomColumn()" title="Add custom column">+</th>`;

  // Render body — cell content is escaped inside each column's render()
  const orderedIndices = orderedKeys.map(k => colByKey.get(k));
  tbody.innerHTML = state.initiatives.map(i => {
    const cells = orderedIndices.map(idx => COLUMNS[idx].render(i)).join('');
    return `<tr data-id="${parseInt(i.id)}" onclick="loadDetail(${parseInt(i.id)})" class="${i.id === state.selectedId ? 'selected' : ''}">${cells}<td></td></tr>`;
  }).join('');

  initColumnDnD();
}

// ---------------------------------------------------------------------------
// Render detail — all user content passed through esc()/escAttr()/safeUrl()
// ---------------------------------------------------------------------------
function renderDetail(d) {
  document.getElementById('detail-empty').style.display = 'none';
  const el = document.getElementById('detail-content');
  el.style.display = 'block';
  el.className = 'detail-content-enter';

  const v = d.verdict || 'unscored';
  const vLabel = VERDICT_LONG[v] || 'Not Scored';

  const ea = (f,v,t) => editAttr(d.id,f,v,t);
  let html = `
    <div class="detail-header">
      <h2 class="editable"${ea('name',d.name)} ondblclick="editFromAttr(this)">${esc(d.name)}</h2>
      <div class="meta">
        <span class="editable"${ea('uni',d.uni,'select-uni')} ondblclick="editFromAttr(this)">${esc(d.uni)}</span>
        <span class="editable"${ea('sector',d.sector)} ondblclick="editFromAttr(this)">${esc(d.sector) || 'Sector'}</span>
        <span class="editable"${ea('mode',d.mode)} ondblclick="editFromAttr(this)">${esc(d.mode) || 'Mode'}</span>
        <span class="editable"${ea('relevance',d.relevance)} ondblclick="editFromAttr(this)">Rating: ${esc(d.relevance) || '\u2014'}</span>
        ${d.enriched ? `<span>Enriched</span>` : '<span class="text-amber">Not enriched</span>'}
      </div>
    </div>`;

  // Verdict card
  if (d.verdict) {
    html += `
    <div class="detail-verdict ${esc(v)}">
      <div class="verdict-label">${esc(vLabel)} \u2014 ${d.score?.toFixed(1) || '?'}/5 \u2014 ${esc(humanize(d.classification))}</div>
      <div class="reasoning">${esc(d.reasoning || '')}</div>
    </div>`;
  }

  // Dimension grades
  if (d.grade_team || d.grade_tech || d.grade_opportunity) {
    html += `<div class="grade-cards">${[['grade_team','Team'],['grade_tech','Tech'],['grade_opportunity','Opportunity']].map(
      ([k,l]) => `<div class="card card--compact"><div class="grade-value ${gradeColorClass(d[k])}">${esc(d[k]||'\u2014')}</div><div class="card-label">${l}</div></div>`
    ).join('')}</div>`;
  }

  // Contact
  if (d.contact_who) {
    html += `
    <div class="card">
      <div class="card-label">Contact</div>
      <div>${esc(d.contact_who)}</div>
    </div>`;
  }

  // Engagement hook
  if (d.engagement_hook) {
    html += `<div class="card card--accent-left">${esc(d.engagement_hook)}</div>`;
  }

  html += listSection('Key Evidence', d.key_evidence);
  html += listSection('Data Gaps', d.data_gaps);

  // Description
  html += `<div class="detail-section"><h3>Description</h3><p class="editable"${ea('description',d.description,'textarea')} ondblclick="editFromAttr(this)">${esc(d.description) || 'Double-click to add description...'}</p></div>`;

  // Domain tags
  const hasDomains = d.technology_domains || d.market_domains || d.categories;
  if (hasDomains) {
    html += `<div class="detail-section"><h3>Domains</h3><div>`;
    html += renderPills(d.technology_domains, 'tech');
    html += renderPills(d.market_domains, 'market');
    if (d.categories) html += `<span class="domain-pill cat">${esc(d.categories)}</span>`;
    html += `</div></div>`;
  }

  // Pre-computed scores
  if (d.outreach_now_score != null || d.venture_upside_score != null) {
    html += `<div class="detail-section"><h3>Pipeline Scores</h3>`;
    if (d.outreach_now_score != null) {
      const pct = Math.min(d.outreach_now_score / 5 * 100, 100);
      html += `<div class="score-bar-row"><span class="score-bar-label">Outreach</span><div class="score-bar"><div class="bar-fill green" style="width:${pct}%"></div></div><span class="score-bar-val">${d.outreach_now_score.toFixed(1)}</span></div>`;
    }
    if (d.venture_upside_score != null) {
      const pct = Math.min(d.venture_upside_score / 5 * 100, 100);
      html += `<div class="score-bar-row"><span class="score-bar-label">Venture Upside</span><div class="score-bar"><div class="bar-fill blue" style="width:${pct}%"></div></div><span class="score-bar-val">${d.venture_upside_score.toFixed(1)}</span></div>`;
    }
    html += `</div>`;
  }

  // Signal cards
  const signalsHtml = SIGNALS.map(([k,l]) => signalCard(d[k], l)).join('');
  if (signalsHtml) html += `<div class="detail-section"><h3>Signals</h3><div class="signal-grid">${signalsHtml}</div></div>`;

  // Due diligence
  if (d.dd_is_investable || d.dd_key_roles || d.dd_references_count) {
    html += `<div class="detail-section"><h3>Due Diligence</h3><ul>`;
    if (d.dd_is_investable) html += `<li class="text-green">Investable</li>`;
    if (d.dd_references_count) html += `<li>References: ${esc(String(d.dd_references_count))}</li>`;
    if (d.dd_key_roles) html += `<li>Key roles: ${esc(humanize(d.dd_key_roles))}</li>`;
    if (d.member_roles) html += `<li>Member roles: ${esc(humanize(d.member_roles))}</li>`;
    html += `</ul></div>`;
  }

  // Links (with edit pencils)
  const LINK_FIELDS = [['Website','website',d.website,d.website],['Email','email',d.email,'mailto:'+d.email],['LinkedIn','linkedin',d.linkedin,d.linkedin],['GitHub','github_org',d.github_org,d.github_org],['Team','team_page',d.team_page,d.team_page]];
  html += `<div class="detail-section"><h3>Links</h3><div class="link-row">`;
  LINK_FIELDS.forEach(([label, field, raw, url]) => {
    if (raw) {
      html += `<a href="${esc(safeUrl(url))}" target="_blank" class="link-pill">${esc(label)}</a>`;
      html += `<span class="edit-pencil"${editAttr(d.id, field, raw)} onclick="editFromAttr(this)">&#9998;</span>`;
    } else {
      html += `<span class="link-pill editable text-faint" style="cursor:pointer"${editAttr(d.id, field, '')} ondblclick="editFromAttr(this)">+ ${label}</span>`;
    }
  });
  html += `</div></div>`;

  // Extra info (editable)
  html += `<div class="detail-section"><h3>Additional Info</h3><ul>`;
  [['team_size','Team size'],['key_repos','Key repos'],['sponsors','Sponsors'],['competitions','Competitions']].forEach(([f,l]) => {
    html += `<li class="editable"${ea(f,d[f])} ondblclick="editFromAttr(this)">${l}: ${esc(d[f]) || '\u2014'}</li>`;
  });
  html += `</ul></div>`;

  // Custom fields
  if (state.customColumns.length > 0) {
    html += `<div class="detail-section"><h3>Custom Fields</h3><ul>`;
    state.customColumns.forEach(cc => {
      const val = (d.custom_fields || {})[cc.key] || '';
      html += `<li class="editable"${editAttr(d.id, 'custom:' + cc.key, val)} ondblclick="editFromAttr(this)">${esc(cc.label)}: ${esc(val) || '\u2014'}</li>`;
    });
    html += `</ul></div>`;
  }

  // Enrichments
  if (d.enrichments && d.enrichments.length > 0) {
    html += `<div class="detail-section"><h3>Enrichment Sources</h3><ul>`;
    d.enrichments.forEach(e => {
      html += `<li>${esc(e.source_type)} \u2014 ${esc(e.fetched_at.split('T')[0])}</li>`;
    });
    html += `</ul></div>`;
  }

  // Projects
  html += `<div class="detail-section"><h3>Projects</h3>`;
  html += `<div id="project-form-slot"></div>`;
  if (d.projects && d.projects.length > 0) {
    d.projects.forEach(p => {
      const pv = p.verdict || 'unscored';
      const pvLabel = VERDICT_SHORT[pv] || '';
      html += `<div class="card" id="project-${parseInt(p.id)}">`;
      html += `<div class="project-card-header">`;
      html += `<h4>${esc(p.name)} ${pvLabel ? `<span class="verdict-badge verdict-${esc(pv)} ml-2 text-xs">${pvLabel}</span>` : ''}</h4>`;
      html += `<div class="project-card-actions">`;
      html += `<button class="btn btn-sm" onclick="scoreProject(${parseInt(p.id)})" title="Score">Score</button>`;
      html += `<button class="btn btn-sm" onclick="showProjectForm(${parseInt(d.id)}, ${parseInt(p.id)})" title="Edit">Edit</button>`;
      html += `<button class="btn btn-sm text-red" onclick="deleteProject(${parseInt(p.id)}, ${parseInt(d.id)})" title="Delete">Del</button>`;
      html += `</div></div>`;
      const meta = [];
      if (p.description) meta.push(p.description);
      if (p.team) meta.push(`Team: ${p.team}`);
      if (meta.length > 0) html += `<div class="project-meta">${esc(meta.join(' \u2014 '))}</div>`;
      const pLinks = [];
      if (p.website) pLinks.push(`<a href="${esc(safeUrl(p.website))}" target="_blank" class="link-pill">Website</a>`);
      if (p.github_url) pLinks.push(`<a href="${esc(safeUrl(p.github_url))}" target="_blank" class="link-pill">GitHub</a>`);
      if (p.extra_links) {
        Object.entries(p.extra_links).forEach(([k, v]) => {
          if (v) pLinks.push(`<a href="${esc(safeUrl(v))}" target="_blank" class="link-pill">${esc(k)}</a>`);
        });
      }
      if (pLinks.length > 0) html += `<div class="link-row">${pLinks.join('')}</div>`;
      if (p.grade_team || p.grade_tech || p.grade_opportunity) {
        html += `<div class="project-grades">`;
        html += `<span class="pg-item">Team ${gradeBadge(p.grade_team)}</span>`;
        html += `<span class="pg-item">Tech ${gradeBadge(p.grade_tech)}</span>`;
        html += `<span class="pg-item">Opp ${gradeBadge(p.grade_opportunity)}</span>`;
        html += `</div>`;
      }
      html += `</div>`;
    });
  } else {
    html += `<div class="card card--dashed">No projects yet — click below to add one</div>`;
  }
  html += `<button class="btn btn-sm mt-2" onclick="showProjectForm(${parseInt(d.id)})">+ Add Project</button>`;
  html += `</div>`;

  // Actions
  html += `
    <div class="detail-actions">
      <button class="btn btn-sm" id="btn-enrich-one" onclick="enrichOne(${parseInt(d.id)})">Enrich</button>
      <button class="btn btn-sm btn-primary" id="btn-score-one" onclick="scoreOne(${parseInt(d.id)})">Score</button>
    </div>`;

  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------
function showImport() {
  document.getElementById('import-overlay').classList.remove('hidden');
}
function hideImport() {
  document.getElementById('import-overlay').classList.add('hidden');
}

document.getElementById('import-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('import-overlay')) hideImport();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { hideImport(); hidePrompts(); hideMcpSetup(); }
});

const importBox = document.getElementById('import-box');
importBox.addEventListener('dragover', e => { e.preventDefault(); importBox.classList.add('dragover'); });
importBox.addEventListener('dragleave', () => importBox.classList.remove('dragover'));
importBox.addEventListener('drop', e => {
  e.preventDefault();
  importBox.classList.remove('dragover');
  const file = e.dataTransfer.files[0];
  if (file) uploadFile(file);
});

document.getElementById('file-input').addEventListener('change', e => {
  const file = e.target.files[0];
  if (file) uploadFile(file);
});

async function uploadFile(file) {
  if (!file.name.endsWith('.xlsx')) {
    showToast('Please select an .xlsx file', 'error');
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  try {
    const result = await api('POST', '/api/import', fd);
    hideImport();
    showToast(`Imported ${result.total_imported} initiatives (${result.spin_off_count} spin-off, ${result.duplicates_updated} updated)`, 'success');
    loadInitiatives();
  } catch (err) {
    showToast('Import failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Enrich & Score (single)
// ---------------------------------------------------------------------------
async function enrichOne(id) {
  const btn = document.getElementById('btn-enrich-one');
  if (btn) btnLoading(btn, true);
  try {
    const r = await api('POST', `/api/enrich/${id}`);
    showToast(`Added ${r.enrichments_added} enrichments`, 'success');
    loadDetail(id);
    loadInitiatives();
  } catch (err) {
    showToast('Enrich failed: ' + err.message, 'error');
  } finally {
    if (btn) btnLoading(btn, false);
  }
}

async function scoreOne(id) {
  const btn = document.getElementById('btn-score-one');
  if (btn) btnLoading(btn, true);
  try {
    const r = await api('POST', `/api/score/${id}`);
    showToast(`Verdict: ${r.verdict} (${r.score})`, 'success');
    loadDetail(id);
    loadInitiatives();
  } catch (err) {
    showToast('Score failed: ' + err.message, 'error');
  } finally {
    if (btn) btnLoading(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Batch operations via SSE (streaming fetch)
// ---------------------------------------------------------------------------
async function streamBatch(url, body) {
  const container = document.getElementById('progress-container');
  const label = document.getElementById('progress-label');
  const fill = document.getElementById('progress-fill');
  const dbSelector = document.getElementById('db-selector');
  container.classList.add('active');
  fill.style.width = '0%';
  let gotComplete = false;

  // Prevent DB switching while a batch operation is in-flight
  if (dbSelector) dbSelector.disabled = true;

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'progress') {
            const pct = Math.round((event.current / event.total) * 100);
            fill.style.width = pct + '%';
            label.textContent = `${event.current}/${event.total} \u2014 ${event.name}`;
          } else if (event.type === 'complete') {
            gotComplete = true;
            label.textContent = `Done! ${JSON.stringify(event.stats)}`;
            setTimeout(() => container.classList.remove('active'), 3000);
            loadInitiatives();
          }
        } catch {}
      }
    }
    // Stream ended without a complete event — clean up
    if (!gotComplete) {
      label.textContent = 'Stream ended unexpectedly';
      setTimeout(() => container.classList.remove('active'), 3000);
      loadInitiatives();
    }
  } catch (err) {
    label.textContent = `Error: ${err.message}`;
    setTimeout(() => container.classList.remove('active'), 5000);
  } finally {
    if (dbSelector) dbSelector.disabled = false;
  }
}

function enrichBatch() { streamBatch('/api/enrich/batch', null); }
function scoreBatch() { streamBatch('/api/score/batch', null); }

// ---------------------------------------------------------------------------
// Keyboard navigation
// ---------------------------------------------------------------------------
document.addEventListener('keydown', e => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    const items = state.initiatives;
    if (items.length === 0) return;
    const idx = items.findIndex(i => i.id === state.selectedId);
    let next;
    if (e.key === 'ArrowDown') next = Math.min(idx + 1, items.length - 1);
    else next = Math.max(idx - 1, 0);
    if (next >= 0 && next < items.length) {
      loadDetail(items[next].id);
      const row = document.querySelector(`tr[data-id="${items[next].id}"]`);
      if (row) row.scrollIntoView({ block: 'nearest' });
    }
  }
});

// ---------------------------------------------------------------------------
// Projects
// ---------------------------------------------------------------------------
function showProjectForm(initiativeId, projectId) {
  const slot = document.getElementById('project-form-slot');
  if (!slot) return;

  let p = { name: '', description: '', website: '', github_url: '', team: '' };
  if (projectId && state.currentDetail && state.currentDetail.id === initiativeId) {
    const found = (state.currentDetail.projects || []).find(x => x.id === projectId);
    if (found) p = found;
  }

  slot.innerHTML = `
    <div class="card card--form">
      <div class="pf-row"><label class="card-label">Name *</label><input id="pf-name" value="${escAttr(p.name)}"></div>
      <div class="pf-row"><label class="card-label">Description</label><textarea id="pf-desc">${esc(p.description)}</textarea></div>
      <div class="pf-row"><label class="card-label">Website</label><input id="pf-website" value="${escAttr(p.website)}"></div>
      <div class="pf-row"><label class="card-label">GitHub URL</label><input id="pf-github" value="${escAttr(p.github_url)}"></div>
      <div class="pf-row"><label class="card-label">Team</label><input id="pf-team" value="${escAttr(p.team)}"></div>
      <div class="pf-actions">
        <button class="btn btn-sm btn-primary" onclick="submitProject(${parseInt(initiativeId)}, ${projectId ? parseInt(projectId) : 'null'})">${projectId ? 'Update' : 'Create'}</button>
        <button class="btn btn-sm" onclick="document.getElementById('project-form-slot').innerHTML=''">Cancel</button>
      </div>
    </div>`;
  slot.querySelector('#pf-name').focus();
}

async function submitProject(initiativeId, projectId) {
  const name = document.getElementById('pf-name').value.trim();
  if (!name) { showToast('Name is required', 'error'); return; }

  const body = {
    name,
    description: document.getElementById('pf-desc').value.trim(),
    website: document.getElementById('pf-website').value.trim(),
    github_url: document.getElementById('pf-github').value.trim(),
    team: document.getElementById('pf-team').value.trim(),
  };

  try {
    if (projectId) {
      await api('PUT', `/api/projects/${projectId}`, body);
    } else {
      await api('POST', `/api/initiatives/${initiativeId}/projects`, body);
    }
    loadDetail(initiativeId);
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

async function deleteProject(projectId, initiativeId) {
  const ok = await showConfirmModal('Delete this project and its scores?');
  if (!ok) return;
  try {
    await api('DELETE', `/api/projects/${projectId}`);
    loadDetail(initiativeId);
  } catch (err) {
    showToast('Delete failed: ' + err.message, 'error');
  }
}

async function scoreProject(projectId) {
  try {
    const r = await api('POST', `/api/projects/${projectId}/score`);
    showToast(`Project verdict: ${r.verdict} (${r.score})`, 'success');
    if (state.selectedId) loadDetail(state.selectedId);
  } catch (err) {
    showToast('Score failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Database management
// ---------------------------------------------------------------------------
function _resetDetailPanel() {
  state.selectedId = null;
  state.currentDetail = null;
  document.getElementById('detail-content').style.display = 'none';
  document.getElementById('detail-content').className = '';
  document.getElementById('detail-empty').style.display = 'flex';
}

async function loadDatabases() {
  const data = await api('GET', '/api/databases');
  const sel = document.getElementById('db-selector');
  sel.innerHTML = data.databases.map(name =>
    `<option value="${esc(name)}" ${name === data.current ? 'selected' : ''}>${esc(name)}</option>`
  ).join('');
  state.currentDb = data.current;
  loadColumnOrder();
}

async function switchDatabase(name) {
  try {
    const result = await api('POST', '/api/databases/select', { name });
    state.currentDb = result.current;
    _resetDetailPanel();
    loadColumnOrder();
    await loadCustomColumns();
    await loadInitiatives();
  } catch (err) {
    // Revert dropdown to the actual current DB
    document.getElementById('db-selector').value = state.currentDb;
    showToast('Switch failed: ' + err.message, 'error');
  }
}

async function showCreateDb() {
  const name = await showPromptModal('New Database', 'Database name', 'letters, numbers, hyphens');
  if (!name) return;
  if (!_COL_KEY_RE.test(name)) {
    showToast('Invalid name. Use only letters, numbers, hyphens, and underscores.', 'error');
    return;
  }
  try {
    const result = await api('POST', '/api/databases/create', { name });
    state.currentDb = result.current;
    _resetDetailPanel();
    loadColumnOrder();
    await loadDatabases();
    await loadCustomColumns();
    await loadInitiatives();
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Custom columns
// ---------------------------------------------------------------------------
async function loadCustomColumns() {
  state.customColumns = await api('GET', '/api/custom-columns');
}

async function addCustomColumn() {
  const key = await showPromptModal('Add Column', 'Column key (no spaces)', 'e.g. funding_stage');
  if (!key) return;
  if (!_COL_KEY_RE.test(key)) {
    showToast('Invalid key. Use only letters, numbers, hyphens, and underscores.', 'error');
    return;
  }
  const label = await showPromptModal('Add Column', 'Display label', 'e.g. Funding Stage');
  if (!label) return;
  try {
    await api('POST', '/api/custom-columns', { key, label, show_in_list: true, col_type: 'text' });
    await loadCustomColumns();
    // Reset column order so new column appears
    state.columnOrder = null;
    localStorage.removeItem(_colOrderStorageKey());
    renderList();
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

async function removeCustomColumn(e, columnId) {
  e.preventDefault();
  const col = state.customColumns.find(c => c.id === columnId);
  if (!col) return;
  const ok = await showConfirmModal(`Remove column "${col.label}"?`);
  if (!ok) return;
  try {
    await api('DELETE', `/api/custom-columns/${columnId}`);
    await loadCustomColumns();
    // Remove from saved column order
    if (state.columnOrder) {
      const removedKey = `custom_${col.key}`;
      state.columnOrder = state.columnOrder.filter(k => k !== removedKey);
      saveColumnOrder();
    }
    renderList();
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Scoring Prompts
// ---------------------------------------------------------------------------
async function showPrompts() {
  const overlay = document.getElementById('prompt-overlay');
  const container = document.getElementById('prompt-editors');
  overlay.classList.remove('hidden');
  container.innerHTML = '<p class="text-faint">Loading prompts...</p>';

  try {
    const prompts = await api('GET', '/api/scoring-prompts');
    container.innerHTML = prompts.map(p => `
      <div class="prompt-section" data-prompt-key="${escAttr(p.key)}">
        <div class="prompt-section-header">
          <label class="card-label">${esc(p.label)}</label>
          <button class="btn btn-sm" onclick="savePrompt('${escAttr(p.key)}')">Save</button>
        </div>
        <textarea class="prompt-textarea" id="prompt-${escAttr(p.key)}">${esc(p.content)}</textarea>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<p class="text-red">Failed to load prompts: ${esc(err.message)}</p>`;
  }
}

function hidePrompts() {
  document.getElementById('prompt-overlay').classList.add('hidden');
}

async function savePrompt(key) {
  const textarea = document.getElementById(`prompt-${key}`);
  if (!textarea) return;
  try {
    await api('PUT', `/api/scoring-prompts/${key}`, { content: textarea.value });
    showToast(`Prompt "${key}" saved`, 'success');
  } catch (err) {
    showToast('Save failed: ' + err.message, 'error');
  }
}

document.getElementById('prompt-overlay').addEventListener('click', e => {
  if (e.target === document.getElementById('prompt-overlay')) hidePrompts();
});

// ---------------------------------------------------------------------------
// MCP Setup popover
// ---------------------------------------------------------------------------
const MCP_TOOLS = {
  'mcp-claude-desktop': {
    label: 'Claude Desktop',
    configPath: {
      mac: '~/Library/Application Support/Claude/claude_desktop_config.json',
      win: '%APPDATA%\\Claude\\claude_desktop_config.json',
    },
    steps: function(cfg) {
      return [
        { title: 'Open the config file', detail: 'On macOS: <code>' + esc(this.configPath.mac) + '</code><br>On Windows: <code>' + esc(this.configPath.win) + '</code>' },
        { title: 'Add Scout to mcpServers', detail: 'Merge this into your config (keep existing servers):', code: cfg },
        { title: 'Restart Claude Desktop', detail: 'Quit and reopen Claude Desktop for changes to take effect.' },
      ];
    },
  },
  'mcp-claude-code': {
    label: 'Claude Code',
    steps: function() {
      return [
        { title: 'Run one command in your terminal', detail: 'This registers Scout globally for all projects:', code: 'claude mcp add -s user scout -- scout-mcp' },
        { title: 'Or use the project config', detail: 'The repo includes a <code>.mcp.json</code> file that auto-configures Scout when you open this project in Claude Code. No action needed.' },
      ];
    },
  },
  'mcp-cursor': {
    label: 'Cursor',
    configPath: '~/.cursor/mcp.json',
    steps: function(cfg) {
      return [
        { title: 'Open the config file', detail: '<code>' + esc(this.configPath) + '</code>' },
        { title: 'Add Scout to mcpServers', detail: 'Merge this into your config:', code: cfg },
        { title: 'Restart Cursor', detail: 'Reload the window or restart Cursor.' },
      ];
    },
  },
  'mcp-windsurf': {
    label: 'Windsurf',
    configPath: '~/.codeium/windsurf/mcp_config.json',
    steps: function(cfg) {
      return [
        { title: 'Open the config file', detail: '<code>' + esc(this.configPath) + '</code>' },
        { title: 'Add Scout to mcpServers', detail: 'Merge this into your config:', code: cfg },
        { title: 'Restart Windsurf', detail: 'Reload the window or restart Windsurf.' },
      ];
    },
  },
};

function _mcpConfigSnippet() {
  return JSON.stringify({ mcpServers: { scout: { command: 'scout-mcp' } } }, null, 2);
}

var _mcpOverlay = null;

function showMcpSetup() {
  if (_mcpOverlay) _mcpOverlay.remove();

  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';

  var box = document.createElement('div');
  box.className = 'mcp-setup-box';

  // Header
  var header = document.createElement('div');
  header.className = 'prompt-header';
  var h2 = document.createElement('h2');
  h2.textContent = 'MCP Setup';
  var closeBtn = document.createElement('button');
  closeBtn.className = 'btn btn-sm';
  closeBtn.textContent = 'Close';
  header.appendChild(h2);
  header.appendChild(closeBtn);
  box.appendChild(header);

  // Description
  var desc = document.createElement('p');
  desc.className = 'prompt-description';
  desc.textContent = 'Connect Scout to your AI assistant. Pick your tool, copy the config, and paste it into the right file.';
  box.appendChild(desc);

  // Tabs
  var tabs = document.createElement('div');
  tabs.className = 'mcp-tabs';
  var tabKeys = ['mcp-claude-desktop', 'mcp-claude-code', 'mcp-cursor', 'mcp-windsurf'];
  tabKeys.forEach(function(key) {
    var tab = document.createElement('button');
    tab.className = 'mcp-tab' + (key === 'mcp-claude-desktop' ? ' active' : '');
    tab.textContent = MCP_TOOLS[key].label;
    tab.addEventListener('click', function() { switchMcpTab(tab, key); });
    tabs.appendChild(tab);
  });
  box.appendChild(tabs);

  // Tab content
  var tabContent = document.createElement('div');
  tabContent.id = 'mcp-tab-content';
  box.appendChild(tabContent);

  // Footer
  var footer = document.createElement('div');
  footer.className = 'mcp-footer';
  var footerP = document.createElement('p');
  footerP.appendChild(document.createTextNode('Or run '));
  var code = document.createElement('code');
  code.textContent = 'scout-setup all';
  footerP.appendChild(code);
  footerP.appendChild(document.createTextNode(' from your terminal to configure all tools at once.'));
  footer.appendChild(footerP);
  box.appendChild(footer);

  overlay.appendChild(box);
  document.body.appendChild(overlay);
  _mcpOverlay = overlay;

  function cleanup() { overlay.remove(); _mcpOverlay = null; }
  closeBtn.onclick = cleanup;
  overlay.addEventListener('click', function(e) { if (e.target === overlay) cleanup(); });

  // Initialize first tab
  switchMcpTab(tabs.querySelector('.mcp-tab'), 'mcp-claude-desktop');
}

function hideMcpSetup() {
  if (_mcpOverlay) { _mcpOverlay.remove(); _mcpOverlay = null; }
}

function switchMcpTab(btn, toolKey) {
  document.querySelectorAll('.mcp-tab').forEach(function(t) { t.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  var tool = MCP_TOOLS[toolKey];
  var cfg = _mcpConfigSnippet();
  var steps = tool.steps(cfg);
  var html = '';
  steps.forEach(function(s, i) {
    html += '<div class="mcp-step">';
    html += '<div class="mcp-step-title"><span class="mcp-step-num">' + (i + 1) + '</span>' + esc(s.title) + '</div>';
    html += '<div class="mcp-step-detail">' + s.detail + '</div>';
    if (s.code) {
      var id = 'mcp-code-' + i;
      html += '<div class="mcp-code-block" id="' + id + '">' + esc(s.code);
      html += '<button class="mcp-copy-btn" onclick="copyMcpCode(\'' + id + '\')">Copy</button></div>';
    }
    html += '</div>';
  });
  document.getElementById('mcp-tab-content').innerHTML = html;
}

function copyMcpCode(blockId) {
  var block = document.getElementById(blockId);
  if (!block) return;
  var text = block.textContent.replace('Copy', '').trim();
  navigator.clipboard.writeText(text).then(function() {
    var btn = block.querySelector('.mcp-copy-btn');
    if (btn) {
      btn.textContent = 'Copied!';
      setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
    }
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function initApp() {
  await loadDatabases();
  await loadCustomColumns();
  await loadInitiatives();
}
initApp();
