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
  cursor: { row: 0, col: 0 },
  mode: 'grid', // 'grid' | 'detail'
};

const _COL_KEY_RE = /^[a-zA-Z0-9_-]+$/;

// ---------------------------------------------------------------------------
// Inline Lucide SVG icons (no dependency needed)
// ---------------------------------------------------------------------------
function _svg(size, inner) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
}

const _ICONS = {
  sparkles: _svg(14, '<path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/><path d="M5 3v4"/><path d="M19 17v4"/><path d="M3 5h4"/><path d="M17 19h4"/>'),
  target:   _svg(14, '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>'),
  users:    _svg(14, '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
  plus:     _svg(14, '<path d="M5 12h14"/><path d="M12 5v14"/>'),
  pencil:   _svg(14, '<path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/>'),
  trash:    _svg(14, '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'),
  save:     _svg(14, '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>'),
  copy:     _svg(14, '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'),
  x:        _svg(14, '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>'),
  pause:    _svg(12, '<rect x="14" y="4" width="4" height="16" rx="1"/><rect x="6" y="4" width="4" height="16" rx="1"/>'),
  play:     _svg(12, '<polygon points="6 3 20 12 6 21 6 3"/>'),
  download: _svg(14, '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>'),
  upload:   _svg(14, '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>'),
  sliders:  _svg(14, '<line x1="4" x2="4" y1="21" y2="14"/><line x1="4" x2="4" y1="10" y2="3"/><line x1="12" x2="12" y1="21" y2="12"/><line x1="12" x2="12" y1="8" y2="3"/><line x1="20" x2="20" y1="21" y2="16"/><line x1="20" x2="20" y1="12" y2="3"/><line x1="2" x2="6" y1="14" y2="14"/><line x1="10" x2="14" y1="8" y2="8"/><line x1="18" x2="22" y1="16" y2="16"/>'),
  terminal: _svg(14, '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>'),
  refresh:  _svg(14, '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'),
  archive:  _svg(14, '<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>'),
  undo:     _svg(14, '<path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>'),
};

function _icon(name) { return _ICONS[name] || ''; }

// Inject icons into static HTML elements with data-icon attributes
document.querySelectorAll('[data-icon]').forEach(function(el) {
  var svg = _ICONS[el.dataset.icon];
  if (svg) el.insertAdjacentHTML('afterbegin', svg);
});

// ---------------------------------------------------------------------------
// Revision polling (live UI updates from MCP / other processes)
// ---------------------------------------------------------------------------
let _lastRevision = null;
let _revisionTimer = null;
let _revisionPaused = false;
let _refreshInFlight = false;

async function syncRevision() {
  try {
    const r = await fetch('/api/revision').then(r => r.json());
    _lastRevision = r.revision;
  } catch (e) { /* ignore */ }
}

async function pollRevision() {
  if (_revisionPaused || _refreshInFlight) return;
  try {
    const r = await fetch('/api/revision').then(r => r.json());
    const changed = _lastRevision !== null && r.revision !== _lastRevision;
    _lastRevision = r.revision;
    if (changed) {
      _refreshInFlight = true;
      try {
        await loadInitiatives();
        populateUniFilter();
        populateClassFilter();
        await loadStats();
        if (state.selectedId) await loadDetail(state.selectedId);
      } finally { _refreshInFlight = false; }
    }
  } catch (e) { /* silently ignore network errors during polling */ }
}

function startRevisionPolling() {
  if (_revisionTimer) return;
  _revisionTimer = setInterval(pollRevision, 3000);
}

// Pause polling when tab is hidden to avoid wasting resources
document.addEventListener('visibilitychange', function() {
  _revisionPaused = document.hidden;
});

async function refreshUI(detailId) {
  await loadInitiatives();
  await loadStats();
  if (detailId) loadDetail(detailId);
  syncRevision();
}

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

function toggleEnrichment(btn) {
  const el = btn.previousElementSibling;
  const full = el.dataset.full;
  if (btn.textContent === '[show more]') {
    el.textContent = full;
    btn.textContent = '[show less]';
  } else {
    el.textContent = full.slice(0, 300) + '\u2026';
    btn.textContent = '[show more]';
  }
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
    btn._origHTML = btn.innerHTML;
    var label = btn.textContent.trim();
    btn.innerHTML = '';
    var spinner = document.createElement('span');
    spinner.className = 'spinner';
    btn.appendChild(spinner);
    btn.appendChild(document.createTextNode(label));
  } else if (btn._origHTML !== undefined) {
    btn.innerHTML = btn._origHTML;
    btn.disabled = false;
    delete btn._origHTML;
  }
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------
async function loadInitiatives() {
  showListSkeleton();
  const f = getFilters();
  const compact = 'id,name,uni,faculty,verdict,score,classification,grade_team,grade_tech,grade_opportunity,enriched,custom_fields';
  let url = `/api/initiatives?sort_by=${state.sort.by}&sort_dir=${state.sort.dir}&per_page=500&fields=${compact}`;
  if (f.verdict) url += `&verdict=${encodeURIComponent(f.verdict)}`;
  if (f.classification) url += `&classification=${encodeURIComponent(f.classification)}`;
  if (f.uni) url += `&uni=${encodeURIComponent(f.uni)}`;
  if (f.faculty) url += `&faculty=${encodeURIComponent(f.faculty)}`;
  if (f.search) url += `&search=${encodeURIComponent(f.search)}`;
  const data = await api('GET', url);
  state.initiatives = data.items;
  renderList();
  updateResultCount(data.items.length, data.total);
}

async function loadStats() {
  const stats = await api('GET', '/api/stats');
  if (!stats || stats.error) return;
  const _s = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  const _d = (id, v) => { const el = document.getElementById(id); if (el) el.disabled = v; };
  _s('stat-total', stats.total);
  _s('stat-enriched', stats.enriched);
  _s('stat-scored', stats.scored);
  _s('stat-now', `${stats.by_verdict.reach_out_now || 0} reach out now`);
  _s('stat-soon', `${stats.by_verdict.reach_out_soon || 0} soon`);
  _s('stat-monitor', `${stats.by_verdict.monitor || 0} monitor`);
  _d('btn-enrich-all', stats.total === 0);
  const unscored = stats.total - stats.scored;
  _d('btn-score-unscored', unscored === 0);
  const scoreBtn = document.getElementById('btn-score-unscored');
  if (scoreBtn) scoreBtn.innerHTML = _icon('target') + (unscored > 0 ? `Score ${unscored} Unscored` : 'All Scored');
  _d('btn-rescore-all', stats.total === 0);
}

let _loadDetailInFlight = false;
async function loadDetail(id) {
  if (_loadDetailInFlight) return;
  _loadDetailInFlight = true;
  showDetailSkeleton();
  try {
    const data = await api('GET', `/api/initiatives/${id}`);
    state.mode = 'detail';
    state.selectedId = id;
    state.currentDetail = data;
    renderDetail(data);
    document.querySelectorAll('.list-table tbody tr').forEach(tr => {
      tr.classList.toggle('selected', parseInt(tr.dataset.id) === id);
    });
  } catch (err) {
    showToast('Failed to load detail: ' + err.message, 'error');
  } finally {
    _loadDetailInFlight = false;
  }
}

// ---------------------------------------------------------------------------
// Filters & sort
// ---------------------------------------------------------------------------
function getFilters() {
  return {
    verdict: document.getElementById('filter-verdict').value,
    classification: document.getElementById('filter-class').value,
    uni: document.getElementById('filter-uni').value,
    faculty: document.getElementById('filter-faculty').value,
    search: document.getElementById('filter-search').value,
  };
}

let _filterTimeout;
function applyFilters() {
  clearTimeout(_filterTimeout);
  _filterTimeout = setTimeout(() => loadInitiatives(), 200);
  updateFilterIndicators();
  updateExportLink();
}

function updateFilterIndicators() {
  const f = getFilters();
  const hasAny = f.verdict || f.classification || f.uni || f.faculty || f.search;
  document.getElementById('filter-verdict').classList.toggle('filter-active', !!f.verdict);
  document.getElementById('filter-class').classList.toggle('filter-active', !!f.classification);
  document.getElementById('filter-uni').classList.toggle('filter-active', !!f.uni);
  document.getElementById('filter-faculty').classList.toggle('filter-active', !!f.faculty);
  document.getElementById('filter-search').classList.toggle('filter-active', !!f.search);
  document.getElementById('btn-clear-filters').classList.toggle('visible', !!hasAny);
}

function clearFilters() {
  document.getElementById('filter-verdict').value = '';
  document.getElementById('filter-class').value = '';
  document.getElementById('filter-uni').value = '';
  document.getElementById('filter-faculty').value = '';
  document.getElementById('filter-search').value = '';
  updateFilterIndicators();
  loadInitiatives();
}

function updateResultCount(shown, total) {
  let el = document.getElementById('result-count');
  if (!el) {
    el = document.createElement('div');
    el.id = 'result-count';
    el.className = 'result-count';
    const listPanel = document.getElementById('list-panel');
    listPanel.insertBefore(el, listPanel.firstChild);
  }
  if (total === 0) {
    el.textContent = 'No results';
  } else if (shown < total) {
    el.textContent = `Showing ${shown} of ${total} initiatives`;
  } else {
    el.textContent = `${total} initiatives`;
  }
}

async function populateFacultyFilter() {
  const sel = document.getElementById('filter-faculty');
  const current = sel.value;
  try {
    const faculties = await api('GET', '/api/faculties');
    sel.innerHTML = '<option value="">All Faculties</option>' +
      faculties.map(f => `<option value="${escAttr(f)}"${f === current ? ' selected' : ''}>${esc(f)}</option>`).join('');
  } catch (e) {
    // Fallback: derive from current page data
    const faculties = [...new Set(state.initiatives.map(i => i.faculty).filter(Boolean))].sort();
    sel.innerHTML = '<option value="">All Faculties</option>' +
      faculties.map(f => `<option value="${escAttr(f)}"${f === current ? ' selected' : ''}>${esc(f)}</option>`).join('');
  }
}

function populateUniFilter() {
  const sel = document.getElementById('filter-uni');
  const current = sel.value;
  const unis = [...new Set(state.initiatives.map(i => i.uni).filter(Boolean))].sort();
  // Safe: esc() and escAttr() sanitize user content (same pattern as populateFacultyFilter)
  sel.innerHTML = '<option value="">All Unis</option>' +
    unis.map(u => `<option value="${escAttr(u)}"${u === current ? ' selected' : ''}>${esc(u)}</option>`).join('');
}

function populateClassFilter() {
  const sel = document.getElementById('filter-class');
  const current = sel.value;
  const classes = [...new Set(state.initiatives.map(i => i.classification).filter(Boolean))].sort();
  // Safe: esc() and escAttr() sanitize user content (same pattern as populateFacultyFilter)
  sel.innerHTML = '<option value="">All Types</option>' +
    classes.map(c => `<option value="${escAttr(c)}"${c === current ? ' selected' : ''}>${esc(humanize(c))}</option>`).join('');
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
    render: i => `<td class="uni-cell editable"${editAttr(i.id,'uni',i.uni)} ondblclick="editFromAttr(this,event)">${esc(i.uni)}</td>` },
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
  if (el.querySelector('.inline-input, .inline-select') || el.dataset.saving) return;
  const original = el.innerHTML;
  let input;

  if (type === 'textarea') {
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
    input.disabled = true;
    const newVal = input.value.trim();
    // Prevent saving empty required fields
    if (field === 'name' && !newVal) { el.innerHTML = original; return; }
    if (newVal === (currentValue || '').trim()) { el.innerHTML = original; return; }
    el.dataset.saving = 'true';
    try {
      let body;
      if (field.startsWith('custom:')) {
        const customKey = field.slice(7);
        body = { custom_fields: { [customKey]: newVal || null } };
      } else {
        body = { [field]: newVal };
      }
      const updated = await api('PUT', `/api/initiatives/${id}`, body);
      // Update local state from PUT response instead of re-fetching
      const idx = state.initiatives.findIndex(i => i.id === id);
      if (idx !== -1) Object.assign(state.initiatives[idx], updated);
      renderList();
      if (state.selectedId === id) {
        state.currentDetail = updated;
        renderDetail(updated);
      }
    } catch (err) {
      el.innerHTML = original;
      showToast('Save failed: ' + err.message, 'error');
    } finally {
      delete el.dataset.saving;
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
  updateCursor();
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
        <span class="editable"${ea('uni',d.uni)} ondblclick="editFromAttr(this)">${esc(d.uni)}</span>
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

  // Enrichments
  if (d.enrichments && d.enrichments.length > 0) {
    html += `<div class="detail-section"><h3>Enrichment Sources</h3>`;
    d.enrichments.forEach(e => {
      const label = esc(humanize(e.source_type));
      const date = esc(e.fetched_at.split('T')[0]);
      html += `<div class="enrichment-card">`;
      const srcLink = e.source_url
        ? `<a href="${escAttr(safeUrl(e.source_url))}" target="_blank" rel="noopener" class="enrichment-source">${label}</a>`
        : `<span class="enrichment-source">${label}</span>`;
      html += `<div class="enrichment-card-header">${srcLink}<span class="enrichment-date">${date}</span></div>`;
      if (e.summary) {
        const short = e.summary.length > 300;
        const text = short ? e.summary.slice(0, 300) + '\u2026' : e.summary;
        html += `<div class="enrichment-summary"${short ? ` data-full="${escAttr(e.summary)}"` : ''}>${esc(text)}</div>`;
        if (short) {
          html += `<button class="btn-link" onclick="toggleEnrichment(this)">[show more]</button>`;
        }
      }
      html += `</div>`;
    });
    html += `</div>`;
  }

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
      html += `<button class="btn btn-sm" onclick="scoreProject(${parseInt(p.id)})" title="Score">${_icon('target')}Score</button>`;
      html += `<button class="btn btn-sm" onclick="showProjectForm(${parseInt(d.id)}, ${parseInt(p.id)})" title="Edit">${_icon('pencil')}Edit</button>`;
      html += `<button class="btn btn-sm text-red" onclick="deleteProject(${parseInt(p.id)}, ${parseInt(d.id)})" title="Delete">${_icon('trash')}Del</button>`;
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
  html += `<button class="btn btn-sm mt-2" onclick="showProjectForm(${parseInt(d.id)})">${_icon('plus')}Add Project</button>`;
  html += `</div>`;

  // Actions
  html += `
    <div class="detail-actions">
      <button class="btn btn-sm" id="btn-enrich-one" onclick="enrichOne(${parseInt(d.id)})">${_icon('sparkles')}Enrich</button>
      <button class="btn btn-sm btn-primary" id="btn-score-one" onclick="scoreOne(${parseInt(d.id)})">${_icon('target')}Score</button>
      <button class="btn btn-sm" id="btn-find-similar" onclick="findSimilar(${parseInt(d.id)})">${_icon('users')}Find Similar</button>
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
// Escape handler for overlays is part of the unified keyboard handler below

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
    refreshUI();
  } catch (err) {
    showToast('Import failed: ' + err.message, 'error');
  }
}

function updateExportLink() {
  const a = document.getElementById('export-link');
  if (!a) return;
  let url = '/api/export?include_scores=true&include_enrichments=true';
  const f = getFilters();
  if (f.verdict) url += `&verdict=${encodeURIComponent(f.verdict)}`;
  if (f.uni) url += `&uni=${encodeURIComponent(f.uni)}`;
  a.href = url;
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
    refreshUI(id);
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
    refreshUI(id);
  } catch (err) {
    showToast('Score failed: ' + err.message, 'error');
  } finally {
    if (btn) btnLoading(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Find Similar
// ---------------------------------------------------------------------------
async function findSimilar(id) {
  const btn = document.getElementById('btn-find-similar');
  if (btn) btnLoading(btn, true);
  try {
    const data = await api('GET', `/api/similar/${id}?limit=10`);
    if (!data.results || data.results.length === 0) {
      showToast(data.hint || 'No similar initiatives found. Run embeddings first.', 'info');
      return;
    }
    // Show results in a section below the detail actions
    const el = document.getElementById('detail-content');
    const existing = document.getElementById('similar-results');
    if (existing) existing.remove();
    const section = document.createElement('div');
    section.id = 'similar-results';
    section.className = 'detail-section';
    let html = '<h3>Similar Initiatives</h3><ul class="similar-list">';
    data.results.forEach(r => {
      html += `<li class="similar-item" onclick="loadDetail(${parseInt(r.id)})">`;
      html += `<span class="similar-name">${esc(r.name)}</span>`;
      html += `<span class="similar-meta">${esc(r.uni)} &mdash; ${(r.similarity * 100).toFixed(0)}% match</span>`;
      html += `</li>`;
    });
    html += '</ul>';
    section.innerHTML = html;
    el.appendChild(section);
  } catch (err) {
    if (err.message.includes('501')) {
      showToast('Embeddings not available. Install model2vec and run POST /api/embed', 'error');
    } else {
      showToast('Find similar failed: ' + err.message, 'error');
    }
  } finally {
    if (btn) btnLoading(btn, false);
  }
}

// ---------------------------------------------------------------------------
// Batch operations via SSE (streaming fetch with pause/cancel + live refresh)
// ---------------------------------------------------------------------------
let _batchReader = null;
let _batchPaused = false;
let _batchCancelled = false;

async function streamBatch(url, body) {
  const container = document.getElementById('progress-container');
  const label = document.getElementById('progress-label');
  const fill = document.getElementById('progress-fill');
  const actions = document.getElementById('progress-actions');
  const pauseBtn = document.getElementById('btn-batch-pause');
  const dbSelector = document.getElementById('db-selector');
  container.classList.add('active');
  fill.style.width = '0%';
  if (actions) actions.style.display = 'flex';
  if (pauseBtn) { pauseBtn.innerHTML = _icon('pause') + 'Pause'; pauseBtn.disabled = false; }
  _batchPaused = false;
  _batchCancelled = false;
  let gotComplete = false;
  let lastProgressIdx = 0;

  if (dbSelector) dbSelector.disabled = true;
  _revisionPaused = true;

  try {
    const resp = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });

    if (!resp.ok) {
      const err = await resp.text();
      throw new Error(`${resp.status}: ${err}`);
    }

    _batchReader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      // Pause loop: wait until unpaused or cancelled
      while (_batchPaused && !_batchCancelled) {
        await new Promise(r => setTimeout(r, 200));
      }
      if (_batchCancelled) {
        await _batchReader.cancel();
        label.textContent = `Cancelled at ${lastProgressIdx} items`;
        break;
      }

      const { done, value } = await _batchReader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        try {
          const event = JSON.parse(line.slice(6));
          if (event.type === 'progress') {
            lastProgressIdx = event.current;
            const pct = Math.round((event.current / event.total) * 100);
            fill.style.width = pct + '%';
            label.textContent = `${event.current}/${event.total} \u2014 ${event.name}`;
            // Live refresh: reload list + stats after each scored item
            loadInitiatives();
            loadStats();
          } else if (event.type === 'complete') {
            gotComplete = true;
            const s = event.stats;
            label.textContent = `Done! ${s.scored || s.enriched || 0} succeeded, ${s.failed} failed`;
          }
        } catch (e) { console.warn('SSE parse error:', e, line); }
      }
    }
    if (!gotComplete && !_batchCancelled) {
      label.textContent = 'Stream ended';
    }
  } catch (err) {
    label.textContent = `Error: ${err.message}`;
  } finally {
    _batchReader = null;
    if (actions) actions.style.display = 'none';
    if (dbSelector) dbSelector.disabled = false;
    _revisionPaused = false;
    await refreshUI();
    setTimeout(() => container.classList.remove('active'), _batchCancelled ? 0 : 3000);
  }
}

function batchPause() {
  _batchPaused = !_batchPaused;
  const btn = document.getElementById('btn-batch-pause');
  if (btn) btn.innerHTML = _batchPaused ? _icon('play') + 'Resume' : _icon('pause') + 'Pause';
}

function batchCancel() {
  _batchCancelled = true;
  _batchPaused = false;
}

function enrichBatch() { streamBatch('/api/enrich/batch', null); }

function scoreUnscored() { streamBatch('/api/score/batch', { only_unscored: true }); }

async function rescoreAll() {
  const total = parseInt(document.getElementById('stat-total').textContent) || 0;
  const ok = await showConfirmModal(`This will re-score all ${total} initiatives using LLM API calls. Continue?`);
  if (!ok) return;
  streamBatch('/api/score/batch', null);
}

// ---------------------------------------------------------------------------
// Keyboard cursor (CSS-only, no re-render)
// ---------------------------------------------------------------------------
function updateCursor() {
  document.querySelectorAll('.cell-active').forEach(el => el.classList.remove('cell-active'));
  document.querySelectorAll('.row-active').forEach(el => el.classList.remove('row-active'));
  const rows = document.querySelectorAll('#list-body tr');
  if (!rows.length) return;
  state.cursor.row = Math.max(0, Math.min(state.cursor.row, rows.length - 1));
  const row = rows[state.cursor.row];
  row.classList.add('row-active');
  const cells = row.querySelectorAll('td');
  state.cursor.col = Math.max(0, Math.min(state.cursor.col, cells.length - 1));
  if (cells[state.cursor.col]) cells[state.cursor.col].classList.add('cell-active');
  row.scrollIntoView({ block: 'nearest' });
}

// ---------------------------------------------------------------------------
// Unified keyboard handler
// ---------------------------------------------------------------------------
document.addEventListener('keydown', e => {
  const tag = document.activeElement.tagName;
  const inInput = tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA';

  // Escape always works — close overlays or switch to grid
  if (e.key === 'Escape') {
    if (inInput) { document.activeElement.blur(); return; }
    const importOpen = !document.getElementById('import-overlay').classList.contains('hidden');
    const promptOpen = !document.getElementById('prompt-overlay').classList.contains('hidden');
    const mcpOpen = !!_mcpOverlay;
    const hotkeyOpen = !!_hotkeyOverlay;
    const backupOpen = !!_backupOverlay;
    if (importOpen || promptOpen || mcpOpen || hotkeyOpen || backupOpen) {
      hideImport(); hidePrompts(); hideMcpSetup(); hideBackups();
      if (_hotkeyOverlay) { _hotkeyOverlay.remove(); _hotkeyOverlay = null; }
    } else {
      state.mode = 'grid';
      updateCursor();
    }
    return;
  }

  // Skip all other hotkeys when typing in inputs
  if (inInput) return;

  // Global hotkeys (both modes)
  if (e.key === '/') { e.preventDefault(); document.getElementById('filter-search').focus(); return; }
  if (e.key === '?') { e.preventDefault(); showHotkeyHelp(); return; }

  const items = state.initiatives;
  if (items.length === 0) return;

  if (state.mode === 'grid') {
    const rows = document.querySelectorAll('#list-body tr');
    if (!rows.length) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      state.cursor.row = Math.min(state.cursor.row + 1, rows.length - 1);
      updateCursor();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      state.cursor.row = Math.max(state.cursor.row - 1, 0);
      updateCursor();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      const cells = rows[state.cursor.row]?.querySelectorAll('td');
      if (cells) state.cursor.col = Math.min(state.cursor.col + 1, cells.length - 1);
      updateCursor();
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      state.cursor.col = Math.max(state.cursor.col - 1, 0);
      updateCursor();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const id = items[state.cursor.row]?.id;
      if (id != null) loadDetail(id);
    } else if (e.key === 'e') {
      const id = items[state.cursor.row]?.id;
      if (id != null) enrichOne(id);
    } else if (e.key === 's') {
      const id = items[state.cursor.row]?.id;
      if (id != null) scoreOne(id);
    } else if (e.key === 'i') {
      showImport();
    }
  } else if (state.mode === 'detail') {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const idx = items.findIndex(i => i.id === state.selectedId);
      let next;
      if (e.key === 'ArrowDown') next = Math.min(idx + 1, items.length - 1);
      else next = Math.max(idx - 1, 0);
      if (next >= 0 && next < items.length) {
        state.cursor.row = next;
        loadDetail(items[next].id);
        updateCursor();
      }
    } else if (e.key === '\\') {
      e.preventDefault();
      state.mode = 'grid';
      updateCursor();
    } else if (e.key === 'e' && state.selectedId) {
      enrichOne(state.selectedId);
    } else if (e.key === 's' && state.selectedId) {
      scoreOne(state.selectedId);
    } else if (e.key === 'f' && state.selectedId) {
      findSimilar(state.selectedId);
    }
  }
});

// ---------------------------------------------------------------------------
// Hotkey help overlay
// ---------------------------------------------------------------------------
var _hotkeyOverlay = null;
function showHotkeyHelp() {
  if (_hotkeyOverlay) { _hotkeyOverlay.remove(); _hotkeyOverlay = null; }
  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  var box = document.createElement('div');
  box.className = 'modal-box';
  box.style.maxWidth = '500px';
  var h3 = document.createElement('h3');
  h3.textContent = 'Keyboard Shortcuts';
  box.appendChild(h3);
  var grid = document.createElement('div');
  grid.className = 'hotkey-help';
  var bindings = [
    ['Grid mode', ''],
    ['\u2190 \u2191 \u2192 \u2193', 'Move cursor through cells'],
    ['Enter', 'Open detail for selected row'],
    ['e', 'Enrich selected initiative'],
    ['s', 'Score selected initiative'],
    ['i', 'Open import'],
    ['Detail mode', ''],
    ['\u2191 \u2193', 'Browse prev/next initiative'],
    ['\\', 'Return to grid'],
    ['e', 'Enrich current initiative'],
    ['s', 'Score current initiative'],
    ['f', 'Find similar'],
    ['Global', ''],
    ['/', 'Focus search'],
    ['Esc', 'Close overlay / return to grid'],
    ['?', 'Show this help'],
  ];
  bindings.forEach(function(b) {
    if (!b[1]) {
      var heading = document.createElement('div');
      heading.className = 'hotkey-section-label';
      heading.textContent = b[0];
      grid.appendChild(heading);
      grid.appendChild(document.createElement('div'));
    } else {
      var key = document.createElement('kbd');
      key.className = 'hotkey-key';
      key.textContent = b[0];
      var desc = document.createElement('span');
      desc.textContent = b[1];
      grid.appendChild(key);
      grid.appendChild(desc);
    }
  });
  box.appendChild(grid);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  _hotkeyOverlay = overlay;
  function cleanup() { overlay.remove(); _hotkeyOverlay = null; }
  overlay.addEventListener('click', function(ev) { if (ev.target === overlay) cleanup(); });
  overlay.addEventListener('keydown', function(ev) { if (ev.key === 'Escape') cleanup(); });
  overlay.tabIndex = -1;
  overlay.focus();
}

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
        <button class="btn btn-sm btn-primary" onclick="submitProject(${parseInt(initiativeId)}, ${projectId ? parseInt(projectId) : 'null'})">${_icon('save')}${projectId ? 'Update' : 'Create'}</button>
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
  state.mode = 'grid';
  state.cursor = { row: 0, col: 0 };
  document.getElementById('detail-content').style.display = 'none';
  document.getElementById('detail-content').className = '';
  document.getElementById('detail-empty').style.display = 'flex';
}

async function _activateDb(dbName) {
  state.currentDb = dbName;
  localStorage.setItem('scout-selected-db', dbName);
  _lastRevision = null;
  _resetDetailPanel();
  loadColumnOrder();
  await loadCustomColumns();
  await refreshUI();
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
    await _activateDb(result.current);
  } catch (err) {
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
  const entityType = await showPromptModal('Entity Type', 'Type: initiative or professor', 'initiative');
  const et = (entityType || 'initiative').trim().toLowerCase();
  try {
    const result = await api('POST', '/api/databases/create', { name, entity_type: et });
    await loadDatabases();
    await _activateDb(result.current);
  } catch (err) {
    showToast('Failed: ' + err.message, 'error');
  }
}

async function deleteDatabase() {
  const name = state.currentDb;
  if (!name) return;
  const sel = document.getElementById('db-selector');
  const allDbs = Array.from(sel.options).map(o => o.value);
  const fallback = allDbs.find(d => d !== name);
  if (!fallback) {
    showToast('Cannot delete the only database.', 'error');
    return;
  }
  const typed = await showPromptModal(
    'Delete Database',
    `Type "${name}" to confirm deletion. This cannot be undone.`,
    name,
  );
  if (typed !== name) {
    if (typed !== null) showToast('Name did not match. Deletion cancelled.', 'error');
    return;
  }
  try {
    await api('POST', '/api/databases/select', { name: fallback });
    await api('POST', '/api/databases/delete', { name });
    await loadDatabases();
    await _activateDb(fallback);
    showToast(`Database "${name}" deleted`, 'success');
  } catch (err) {
    showToast('Delete failed: ' + err.message, 'error');
  }
}

async function backupDatabase() {
  const name = state.currentDb;
  if (!name) return;
  try {
    const result = await api('POST', '/api/databases/backup', { name });
    showToast(`Backup created: ${result.backup}`, 'success');
    await loadBackupsList();
  } catch (err) {
    showToast('Backup failed: ' + err.message, 'error');
  }
}

// ---------------------------------------------------------------------------
// Backups panel
// ---------------------------------------------------------------------------
let _backupOverlay = null;

async function showBackups() {
  if (_backupOverlay) { hideBackups(); return; }
  const overlay = document.createElement('div');
  overlay.className = 'import-overlay';
  overlay.innerHTML = `
    <div class="prompt-editor-box" style="max-width:640px;">
      <div class="prompt-header">
        <h2>${_icon('archive')}Backups</h2>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-sm btn-primary" onclick="backupDatabase()" data-icon="download">Backup Now</button>
          <button class="btn btn-sm" onclick="hideBackups()" data-icon="x">Close</button>
        </div>
      </div>
      <p class="prompt-description">
        Create backups and restore previous versions of your databases.
      </p>
      <div id="backups-list"><p class="text-faint">Loading...</p></div>
    </div>
  `;
  // inject icons into the dynamically created buttons
  overlay.querySelectorAll('[data-icon]').forEach(function(el) {
    var svg = _ICONS[el.dataset.icon];
    if (svg) el.insertAdjacentHTML('afterbegin', svg);
  });
  overlay.addEventListener('click', function(e) { if (e.target === overlay) hideBackups(); });
  document.body.appendChild(overlay);
  _backupOverlay = overlay;
  await loadBackupsList();
}

function hideBackups() {
  if (_backupOverlay) { _backupOverlay.remove(); _backupOverlay = null; }
}

function _formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function _formatDate(iso) {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
      + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  } catch (e) { return iso; }
}

async function loadBackupsList() {
  const container = document.getElementById('backups-list');
  if (!container) return;
  try {
    const data = await api('GET', '/api/databases/backups');
    const backups = data.backups || [];
    if (backups.length === 0) {
      container.innerHTML = '<p class="text-faint" style="padding:16px 0;">No backups yet. Click "Backup Now" to create one.</p>';
      return;
    }
    container.innerHTML = '<div class="backups-table">' + backups.map(function(b) {
      return `<div class="backup-row">
        <div class="backup-info">
          <span class="backup-origin">${esc(b.origin)}</span>
          <span class="backup-meta">${_formatDate(b.created)} &middot; ${_formatBytes(b.size_bytes)}</span>
        </div>
        <div class="backup-actions">
          <button class="btn btn-xs" onclick="restoreBackup('${escAttr(b.name)}')" title="Restore this backup">${_icon('undo')}Restore</button>
          <button class="btn btn-xs btn-muted" onclick="deleteBackup('${escAttr(b.name)}')" title="Delete this backup">${_icon('trash')}Delete</button>
        </div>
      </div>`;
    }).join('') + '</div>';
  } catch (err) {
    container.innerHTML = `<p class="text-red">Failed to load backups: ${esc(err.message)}</p>`;
  }
}

async function restoreBackup(backupName) {
  const parts = backupName.split('-backup-');
  const origin = parts[0] || backupName;
  if (state.currentDb === origin) {
    showToast(`Cannot restore over the active database "${origin}". Switch to another database first.`, 'error');
    return;
  }
  if (!confirm(`Restore backup to database "${origin}"?\n\nThis will overwrite "${origin}" with the backup data.`)) return;
  try {
    await api('POST', '/api/databases/restore', { backup_name: backupName });
    showToast(`Restored "${origin}" from backup`, 'success');
    await loadDatabases();
  } catch (err) {
    showToast('Restore failed: ' + err.message, 'error');
  }
}

async function deleteBackup(backupName) {
  if (!confirm(`Delete backup "${backupName}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', '/api/databases/backups/' + encodeURIComponent(backupName));
    showToast('Backup deleted', 'success');
    await loadBackupsList();
  } catch (err) {
    showToast('Delete failed: ' + err.message, 'error');
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
          <button class="btn btn-sm" onclick="savePrompt('${escAttr(p.key)}')">${_icon('save')}Save</button>
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
      html += '<button class="mcp-copy-btn" onclick="copyMcpCode(\'' + id + '\')">' + _icon('copy') + 'Copy</button></div>';
    }
    html += '</div>';
  });
  document.getElementById('mcp-tab-content').innerHTML = html;
}

function copyMcpCode(blockId) {
  var block = document.getElementById(blockId);
  if (!block) return;
  // Collect only text nodes to exclude the button text
  var text = '';
  block.childNodes.forEach(function(node) {
    if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
  });
  text = text.trim();
  navigator.clipboard.writeText(text).then(function() {
    var btn = block.querySelector('.mcp-copy-btn');
    if (btn) {
      btn.innerHTML = _icon('copy') + 'Copied!';
      setTimeout(function() { btn.innerHTML = _icon('copy') + 'Copy'; }, 1500);
    }
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function initApp() {
  await loadDatabases();
  // Restore last selected database from localStorage
  const savedDb = localStorage.getItem('scout-selected-db');
  if (savedDb && savedDb !== state.currentDb) {
    try { await switchDatabase(savedDb); } catch (_) { /* fallback to default */ }
  }
  await loadCustomColumns();
  await loadInitiatives();
  populateFacultyFilter();
  populateUniFilter();
  populateClassFilter();
  loadStats();
  updateExportLink();
  await syncRevision();
  startRevisionPolling();
}
initApp();
