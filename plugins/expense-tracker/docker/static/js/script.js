/* ── Helpers ────────────────────────────────────────────────────────────── */
const $  = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c =>
  ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
const fmt = n => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = (a, b) => b > 0 ? Math.min((a / b) * 100, 100).toFixed(1) : 0;
let _categories = [];

// Always returns today's date in YYYY-MM-DD using the *local* timezone, not UTC
function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function api(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${method} ${path} → ${r.status}`);
  return r.status === 204 ? null : r.json();
}

function toast(msg, type = 'ok') {
  const t = $('toast');
  t.textContent = msg;
  t.className = `toast show ${type}`;
  t.style.display = 'block';
  setTimeout(() => { t.style.display = 'none'; t.className = 'toast'; }, 3000);
}

function renderList(id, items, emptyText = 'No insights yet.') {
  const node = $(id);
  if (!node) return;
  node.innerHTML = items && items.length
    ? items.map(item => `<li>${esc(item)}</li>`).join('')
    : `<li class="empty compact">${esc(emptyText)}</li>`;
}

async function loadCategories() {
  let rows;
  try { rows = await api('GET', '/api/categories'); }
  catch { return; }
  _categories = rows.map(r => r.name);
  const options = _categories.map(c => `<option value="${esc(c)}"></option>`).join('');
  $('categoryOptions').innerHTML = options;
  const filter = $('filterCategory');
  const current = filter.value;
  filter.innerHTML = '<option value="">All Categories</option>' +
    _categories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
  filter.value = current;
}

/* ── Navigation ─────────────────────────────────────────────────────────── */
const VIEWS = ['dashboard', 'transactions', 'budgets', 'analytics', 'ask', 'recurring'];

function navigate(view) {
  VIEWS.forEach(v => {
    $(`view-${v}`).classList.toggle('active', v === view);
  });
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.view === view);
  });
  window.location.hash = view;
  if (view === 'dashboard')    loadDashboard();
  if (view === 'transactions') loadTransactions();
  if (view === 'budgets')      loadBudget();
  if (view === 'analytics')    loadAnalytics();
  if (view === 'ask')          focusAsk();
  if (view === 'recurring')    loadRecurring();
}

document.querySelectorAll('.nav-link').forEach(a =>
  a.addEventListener('click', e => { e.preventDefault(); navigate(a.dataset.view); }));

$('hamburger').addEventListener('click', () => $('nav').classList.toggle('open'));

/* ── Dashboard ───────────────────────────────────────────────────────────── */
let pieChartInst = null;

async function loadDashboard() {
  let data;
  try { data = await api('GET', '/api/dashboard'); }
  catch (e) { toast('Failed to load dashboard', 'err'); return; }

  const s = data.status;
  $('dashBalance').textContent   = fmt(data.balance || 0);
  $('bankBalanceInput').value    = data.balance || 0;
  $('dashIncome').textContent    = fmt(s.income);
  $('dashExpenses').textContent  = fmt(s.total_expenses);
  $('dashRemaining').textContent = fmt(s.remaining);
  $('dashSavings').textContent   = fmt(s.savings);

  // Budget bar
  const fill = $('budgetFill');
  fill.style.width = s.budget_pct + '%';
  fill.classList.toggle('warn', s.alert);
  $('budgetBarLabel').textContent = s.expense_limit > 0
    ? `${fmt(s.total_expenses)} of ${fmt(s.expense_limit)} (${s.budget_pct}%)`
    : 'No expense limit set';
  if (data.recurring && data.recurring.total > 0) {
    $('budgetBarLabel').textContent += ` · includes ${fmt(data.recurring.total)} projected recurring`;
  }

  // Recent expenses
  const ul = $('recentList');
  ul.innerHTML = data.recent.length ? data.recent.map(e => `
    <li>
      <div class="item-info">
        <strong>${esc(e.description || e.category)}</strong>
        <span>${e.date} · ${esc(e.category)}</span>
      </div>
      <span class="${e.amount < 0 ? 'amt-neg' : 'amt-pos'}">${fmt(Math.abs(e.amount))}</span>
    </li>`).join('') : '<li class="empty">No expenses this month.</li>';

  // Pie chart
  const labels = data.breakdown.map(b => b.category);
  const values = data.breakdown.map(b => b.total);
  const colors = labels.map((_, i) => `hsl(${(i * 47) % 360},65%,55%)`);

  if (pieChartInst) pieChartInst.destroy();
  pieChartInst = new Chart($('pieChart'), {
    type: 'doughnut',
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderColor: '#1e293b', borderWidth: 2 }] },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom', labels: { color: '#94a3b8', font: { size: 11 } } } },
    },
  });

  renderList('dashboardInsights', data.insights?.descriptions);
  renderList('dashboardInvestments', data.insights?.investment_suggestions);
}

$('balanceForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    const result = await api('POST', '/api/balance', { balance: parseFloat(fd.get('balance')) || 0 });
    $('dashBalance').textContent = fmt(result.balance);
    $('bankBalanceInput').value = result.balance;
    toast('Balance saved');
  } catch { toast('Failed to save balance', 'err'); }
});

// Quick-add form — default to local today
$('quickDate').value = localToday();
$('quickForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  try {
    await api('POST', '/api/expenses', {
      date:        fd.get('date'),
      amount:      parseFloat(fd.get('amount')),
      type:        'expense',
      category:    fd.get('category'),
      description: fd.get('description'),
      cardholder:  fd.get('cardholder'),
    });
    toast('Expense added');
    e.target.reset();
    $('quickDate').value = localToday();
    loadDashboard();
    loadCategories();
  } catch { toast('Failed to add expense', 'err'); }
});

async function predictInto(form, hintId) {
  const desc = form.elements['description']?.value || '';
  if (desc.trim().length < 3) return;
  let result;
  try { result = await api('POST', '/api/predict-category', { description: desc }); }
  catch { return; }
  if (!form.elements['category'].value && result.category) {
    form.elements['category'].value = result.category;
  }
  const hint = $(hintId);
  if (hint) {
    const source = result.source === 'ml' ? 'ML' : 'rules';
    const conf = Math.round((result.confidence || 0) * 100);
    hint.textContent = result.category ? `${source} suggests ${result.category}${conf ? ` (${conf}%)` : ''}` : '';
  }
}

let quickPredictTimer = null;
$('quickDescription').addEventListener('input', () => {
  clearTimeout(quickPredictTimer);
  quickPredictTimer = setTimeout(() => predictInto($('quickForm'), 'quickPrediction'), 350);
});

/* ── Transactions ────────────────────────────────────────────────────────── */
async function loadTransactions() {
  const month      = $('filterMonth').value;
  const category   = $('filterCategory').value;
  const cardholder = $('filterCardholder').value;
  const params     = new URLSearchParams();
  if (month)      params.set('month', month);
  if (category)   params.set('category', category);
  if (cardholder) params.set('cardholder', cardholder);

  let rows;
  try { rows = await api('GET', '/api/expenses?' + params); }
  catch { toast('Failed to load transactions', 'err'); return; }

  const tbody = $('txBody');
  $('txEmpty').style.display = rows.length ? 'none' : 'block';
  tbody.innerHTML = rows.map(r => `
    <tr>
      <td>${esc(r.date)}</td>
      <td class="${r.amount < 0 ? 'amt-neg' : 'amt-pos'}">${fmt(Math.abs(r.amount))}</td>
      <td>${esc(r.category)}</td>
      <td>${esc(r.description)}</td>
      <td>${esc(r.cardholder)}</td>
      <td class="item-actions">
        <button class="btn btn-sm btn-secondary" onclick="openEdit(${r.id})">Edit</button>
        <button class="btn btn-sm btn-danger"    onclick="delExpense(${r.id})">Del</button>
      </td>
    </tr>`).join('');
}

['filterMonth', 'filterCategory', 'filterCardholder'].forEach(id =>
  $(id).addEventListener('change', loadTransactions));

$('openAddModal').addEventListener('click', () => {
  $('expenseModalTitle').textContent = 'Add Expense';
  $('expenseForm').reset();
  $('expensePrediction').textContent = '';
  $('expenseForm').elements['id'].value = '';
  $('expenseForm').elements['date'].value = localToday();
  openModal('expenseModal');
});

async function openEdit(id) {
  let rows;
  try { rows = await api('GET', `/api/expenses?month=`); }
  catch { toast('Failed to load expense', 'err'); return; }
  const r = rows.find(x => x.id === id);
  if (!r) return;
  const f = $('expenseForm');
  f.elements['id'].value          = r.id;
  f.elements['date'].value        = r.date;
  f.elements['amount'].value      = Math.abs(r.amount);
  f.elements['type'].value        = r.amount < 0 ? 'expense' : 'income';
  f.elements['category'].value    = r.category;
  f.elements['description'].value = r.description;
  f.elements['cardholder'].value  = r.cardholder;
  $('expensePrediction').textContent = '';
  $('expenseModalTitle').textContent = 'Edit Expense';
  openModal('expenseModal');
}

$('expenseForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const id = fd.get('id');
  const body = {
    date: fd.get('date'), amount: parseFloat(fd.get('amount')),
    type: fd.get('type'), category: fd.get('category'),
    description: fd.get('description'), cardholder: fd.get('cardholder'),
  };
  try {
    if (id) await api('PUT', `/api/expenses/${id}`, body);
    else    await api('POST', '/api/expenses', body);
    toast(id ? 'Updated' : 'Added');
    closeModal('expenseModal');
    loadTransactions();
    loadDashboard();
    loadCategories();
  } catch { toast('Save failed', 'err'); }
});

async function delExpense(id) {
  if (!confirm('Delete this expense?')) return;
  try { await api('DELETE', `/api/expenses/${id}`); toast('Deleted'); loadTransactions(); loadDashboard(); loadCategories(); }
  catch { toast('Delete failed', 'err'); }
}

let expensePredictTimer = null;
$('expenseForm').elements['description'].addEventListener('input', () => {
  clearTimeout(expensePredictTimer);
  expensePredictTimer = setTimeout(() => predictInto($('expenseForm'), 'expensePrediction'), 350);
});

/* ── Budgets ─────────────────────────────────────────────────────────────── */
async function loadBudget() {
  let s;
  try { s = await api('GET', '/api/budget'); }
  catch { toast('Failed to load budget', 'err'); return; }

  $('bIncome').value  = s.income  || '';
  $('bLimit').value   = s.expense_limit || '';
  $('bEmFund').value  = s.emergency_fund || '';
  $('bCost').value    = s.investment_goal || '';

  $('budgetStatus').innerHTML = [
    ['Income',           fmt(s.income)],
    ['Total Expenses',   fmt(s.total_expenses)],
    ['Remaining',        fmt(s.remaining)],
    ['Savings',          fmt(s.savings)],
    ['Budget Used',      `${s.budget_pct}%`],
    ['Months to Goal',   s.months_to_goal > 0 ? `${s.months_to_goal} months` : '—'],
  ].map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');

  $('budgetAlert').style.display = s.alert ? 'block' : 'none';
}

$('budgetForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd   = new FormData(e.target);
  const body = {};
  ['income', 'expense_limit', 'emergency_fund', 'investment_goal'].forEach(k => {
    body[k] = parseFloat(fd.get(k)) || 0;
  });
  try { await api('POST', '/api/budget', body); toast('Budget saved'); loadBudget(); loadDashboard(); }
  catch { toast('Save failed', 'err'); }
});

/* ── Analytics ───────────────────────────────────────────────────────────── */
let trendInst = null, barInst = null;

async function loadAnalytics() {
  let data;
  try { data = await api('GET', '/api/analytics?months=6'); }
  catch { toast('Failed to load analytics', 'err'); return; }

  // Trend chart
  const tLabels = data.trends.map(t => t.month);
  const tVals   = data.trends.map(t => t.expenses);
  if (trendInst) trendInst.destroy();
  trendInst = new Chart($('trendChart'), {
    type: 'line',
    data: {
      labels: tLabels,
      datasets: [{
        label: 'Expenses', data: tVals, borderColor: '#6366f1',
        backgroundColor: 'rgba(99,102,241,.15)', fill: true, tension: .4,
      }, {
        label: 'Projected recurring', data: data.trends.map(t => t.recurring || 0),
        borderColor: '#f59e0b', backgroundColor: 'rgba(245,158,11,.12)', tension: .35,
      }],
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#94a3b8' } } },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
        y: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
      },
    },
  });

  // Bar chart
  const bLabels = data.breakdown.map(b => b.category);
  const bVals   = data.breakdown.map(b => b.total);
  const bColors = bLabels.map((_, i) => `hsl(${(i * 47) % 360},60%,55%)`);
  if (barInst) barInst.destroy();
  barInst = new Chart($('barChart'), {
    type: 'bar',
    data: { labels: bLabels, datasets: [{ label: 'Spent', data: bVals, backgroundColor: bColors }] },
    options: {
      indexAxis: 'y', responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#94a3b8' }, grid: { color: '#334155' } },
        y: { ticks: { color: '#94a3b8', font: { size: 11 } }, grid: { color: '#334155' } },
      },
    },
  });

  // Category breakdown list
  $('catBreakdown').innerHTML = data.breakdown.length ? data.breakdown.map(b => `
    <li>
      <div class="item-info"><strong>${esc(b.category)}</strong></div>
      <span class="amt-neg">${fmt(b.total)}</span>
    </li>`).join('') : '<li class="empty">No data yet.</li>';
  renderList('analyticsInsights', data.insights?.descriptions);
  renderList('analyticsInvestments', data.insights?.investment_suggestions);
}

/* ── Ask ────────────────────────────────────────────────────────────────── */
function focusAsk() {
  const prompt = $('askPrompt');
  if (prompt && !prompt.value) prompt.focus();
}

function renderAskRows(rows) {
  if (!rows || !rows.length) return '';
  const keys = Object.keys(rows[0]);
  return `
    <div class="table-wrap ask-table">
      <table>
        <thead><tr>${keys.map(k => `<th>${esc(k.replaceAll('_', ' '))}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map(row => `<tr>${keys.map(k => {
            const value = row[k];
            const isAmount = ['amount', 'spent', 'total', 'this_month', 'last_month', 'change', 'suggested_reduction', 'goal', 'monthly_savings'].includes(k);
            return `<td>${isAmount && typeof value === 'number' ? fmt(value) : esc(value ?? '—')}</td>`;
          }).join('')}</tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

function renderAskEvidence(toolResults) {
  if (!toolResults || !toolResults.length) return '';
  return toolResults.map(item => {
    const result = item.result;
    let body = '';
    if (Array.isArray(result)) {
      body = renderAskRows(result);
    } else if (result && Array.isArray(result.rows)) {
      body = renderAskRows(result.rows);
    } else if (result && Array.isArray(result.category_breakdown)) {
      body = renderAskRows(result.category_breakdown);
    } else if (result && Array.isArray(result.recent_expenses)) {
      body = renderAskRows(result.recent_expenses);
    } else if (result && typeof result === 'object') {
      body = `<pre class="ask-json">${esc(JSON.stringify(result, null, 2))}</pre>`;
    }
    return `<div class="ask-evidence"><strong>${esc(item.tool || 'tool')}</strong>${body}</div>`;
  }).join('');
}

async function askExpenses(promptText) {
  const prompt = (promptText || $('askPrompt').value || '').trim();
  if (!prompt) return;
  $('askPrompt').value = prompt;
  $('askResultBox').style.display = 'block';
  $('askSource').textContent = '';
  $('askAnswer').textContent = 'Thinking locally...';
  $('askRows').innerHTML = '';
  $('askSuggestions').innerHTML = '';
  let result;
  try { result = await api('POST', '/api/ask', { prompt }); }
  catch { toast('Ask failed', 'err'); $('askAnswer').textContent = 'I could not answer that right now.'; return; }
  $('askSource').textContent = `${result.source || 'local_agent'} · ${result.model || 'local model'}`;
  $('askAnswer').textContent = result.answer || '';
  $('askRows').innerHTML = renderAskEvidence(result.tool_results) || renderAskRows(result.rows);
  $('askSuggestions').innerHTML = (result.suggestions || []).map(s => `
    <button type="button" class="btn btn-sm btn-secondary" data-prompt="${esc(s)}">${esc(s)}</button>
  `).join('');
}

$('askForm').addEventListener('submit', e => {
  e.preventDefault();
  askExpenses();
});

document.addEventListener('click', e => {
  const btn = e.target.closest('[data-prompt]');
  if (!btn) return;
  askExpenses(btn.dataset.prompt);
});

/* ── Recurring ───────────────────────────────────────────────────────────── */
let _recurringRows = [];

async function loadRecurring() {
  let rows;
  try { rows = await api('GET', '/api/recurring'); }
  catch { toast('Failed to load recurring', 'err'); return; }

  _recurringRows = rows;
  $('recurringEmpty').style.display = rows.length ? 'none' : 'block';
  $('recurringList').innerHTML = rows.map(r => `
    <div class="item-row">
      <div class="item-info">
        <strong>${esc(r.description)}</strong>
        <span>${esc(r.frequency)} · ${esc(r.category)} · Next: ${esc(r.next_due)}</span>
      </div>
      <div class="item-actions">
        <span class="amt-neg">${fmt(r.amount)}</span>
        <button class="btn btn-sm btn-secondary" onclick="openEditRecurring(${r.id})">Edit</button>
        <button class="btn btn-sm btn-danger"    onclick="delRecurring(${r.id})">Del</button>
      </div>
    </div>`).join('');
}

$('openRecurringModal').addEventListener('click', () => {
  $('recurringForm').reset();
  $('recurringForm').elements['id'].value = '';
  $('recurringModalTitle').textContent = 'Add Recurring Template';
  openModal('recurringModal');
});

function openEditRecurring(id) {
  const r = _recurringRows.find(x => x.id === id);
  if (!r) return;
  const f = $('recurringForm');
  f.elements['id'].value          = r.id;
  f.elements['description'].value = r.description;
  f.elements['amount'].value      = r.amount;
  f.elements['category'].value    = r.category;
  f.elements['cardholder'].value  = r.cardholder;
  f.elements['frequency'].value   = r.frequency;
  f.elements['next_due'].value    = r.next_due;
  $('recurringModalTitle').textContent = 'Edit Recurring Template';
  openModal('recurringModal');
}

$('recurringForm').addEventListener('submit', async e => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const id = fd.get('id');
  const body = {
    description: fd.get('description'), amount: parseFloat(fd.get('amount')),
    category:    fd.get('category'),    cardholder: fd.get('cardholder'),
    frequency:   fd.get('frequency'),   next_due: fd.get('next_due'),
  };
  try {
    if (id) await api('PUT', `/api/recurring/${id}`, body);
    else    await api('POST', '/api/recurring', body);
    toast(id ? 'Template updated' : 'Template created');
    closeModal('recurringModal');
    loadRecurring();
    loadCategories();
    loadDashboard();
  } catch { toast('Save failed', 'err'); }
});

async function delRecurring(id) {
  if (!confirm('Delete this recurring template?')) return;
  try { await api('DELETE', `/api/recurring/${id}`); toast('Deleted'); loadRecurring(); loadCategories(); loadDashboard(); }
  catch { toast('Delete failed', 'err'); }
}

/* ── Modal helpers ───────────────────────────────────────────────────────── */
function openModal(id)  { $(id).classList.add('open'); }
function closeModal(id) { $(id).classList.remove('open'); }

document.querySelectorAll('[data-close]').forEach(btn =>
  btn.addEventListener('click', () => closeModal(btn.dataset.close)));
document.querySelectorAll('.modal').forEach(m =>
  m.addEventListener('click', e => { if (e.target === m) closeModal(m.id); }));

/* ── Boot ────────────────────────────────────────────────────────────────── */
const initView = (window.location.hash.slice(1) || 'dashboard');
loadCategories();
navigate(VIEWS.includes(initView) ? initView : 'dashboard');
