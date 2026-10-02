// The page starts empty. We only fill these values after the user uploads a CSV.
const state = { results: [], report: null, csv: '', fileName: '', selectedId: null };
const $ = (id) => document.getElementById(id);
const LABELS = { product_quality: 'Product quality', delivery: 'Delivery', support: 'Customer support', pricing: 'Pricing', usability: 'Ease of use', other: 'Other' };
const SENTIMENTS = ['positive', 'mixed', 'neutral', 'negative'];

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function setNotice(message, kind = '') {
  $('notice').className = `notice ${kind}`.trim();
  $('notice-text').textContent = message;
}

function percent(count, total) { return total ? Math.round(count * 100 / total) : 0; }
function titleCase(value) { return value ? value.charAt(0).toUpperCase() + value.slice(1) : ''; }

function showEmptyState(fileName = '') {
  // A new upload clears the old chart and table, so they never describe the wrong file.
  $('empty-state').classList.remove('is-hidden');
  for (const section of document.querySelectorAll('.metrics, .insight-grid, .review-grid')) section.classList.add('is-hidden');
  $('empty-title').textContent = fileName ? `${fileName} is ready.` : 'Start with your reviews.';
  $('empty-copy').textContent = fileName
    ? 'Your file is uploaded. Select Analyze reviews to create your insights.'
    : 'Upload a CSV file with id,text columns. FeedbackLens will turn your customer feedback into clear, actionable insights.';
  $('empty-upload').querySelector('span').textContent = fileName ? 'Choose another CSV' : 'Choose CSV file';
  $('workspace-count').textContent = fileName ? 'File ready to analyze' : 'No reviews loaded';
  $('analyze-btn').disabled = !fileName;
}

function renderMetrics() {
  // Report counts come from Python; the browser only draws them.
  const report = state.report;
  const total = report.total_reviews;
  const positive = report.sentiments.positive || 0;
  const attention = report.priorities.high || 0;
  const top = Object.entries(report.aspect_mentions).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0];
  $('metric-total').textContent = total;
  $('metric-positive').textContent = positive;
  $('metric-positive-percent').textContent = `${percent(positive, total)}% of total`;
  $('metric-attention').textContent = attention;
  $('metric-attention-percent').textContent = `${percent(attention, total)}% of total`;
  $('metric-theme').textContent = top ? LABELS[top[0]] || top[0] : 'None yet';
  $('workspace-count').textContent = `${total} ${total === 1 ? 'review' : 'reviews'} loaded`;
}

function renderSentiment() {
  const bar = $('sentiment-bar');
  const legend = $('sentiment-legend');
  bar.replaceChildren(); legend.replaceChildren();
  const total = state.report.total_reviews;
  bar.setAttribute('aria-label', SENTIMENTS.map(s => `${titleCase(s)} ${state.report.sentiments[s] || 0}`).join(', '));
  for (const sentiment of SENTIMENTS) {
    const count = state.report.sentiments[sentiment] || 0;
    const share = percent(count, total);
    if (count) {
      const segment = node('div', `sentiment-segment ${sentiment}`, share >= 12 ? `${share}%` : '');
      segment.style.width = `${count / total * 100}%`;
      bar.append(segment);
    }
    const item = node('div', 'legend-item');
    const dot = node('span', `legend-dot ${sentiment}`);
    const copy = node('div');
    copy.append(node('strong', '', titleCase(sentiment)), node('small', '', `${count} ${count === 1 ? 'review' : 'reviews'} (${share}%)`));
    item.append(dot, copy); legend.append(item);
  }
}

function renderAspects() {
  const container = $('aspect-bars'); container.replaceChildren();
  const values = Object.entries(state.report.aspect_mentions).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 5);
  const max = values.length ? values[0][1] : 1;
  if (!values.length) { container.append(node('p', '', 'No specific themes identified.')); return; }
  for (const [name, count] of values) {
    const row = node('div', 'aspect-row');
    const track = node('div', 'aspect-track');
    const fill = node('div', 'aspect-fill'); fill.style.width = `${count / max * 100}%`;
    track.append(fill);
    row.append(node('span', 'aspect-name', LABELS[name] || name), track, node('span', 'aspect-count', `${count} ${count === 1 ? 'mention' : 'mentions'}`));
    container.append(row);
  }
}

function pill(value, priority = false) { return node('span', priority ? `pill priority ${value}` : `pill ${value}`, titleCase(value)); }

function sortedResults() {
  const items = [...state.results];
  const sort = $('sort-select').value;
  if (sort === 'priority') {
    const rank = { high: 0, medium: 1, low: 2 };
    items.sort((a, b) => rank[a.analysis.priority] - rank[b.analysis.priority]);
  } else if (sort === 'sentiment') {
    const rank = { negative: 0, mixed: 1, neutral: 2, positive: 3 };
    items.sort((a, b) => rank[a.analysis.sentiment] - rank[b.analysis.sentiment]);
  }
  return items;
}

function renderReviews() {
  // Text is added with textContent so uploaded reviews are shown safely.
  const body = $('review-body'); body.replaceChildren();
  for (const item of sortedResults()) {
    const row = node('tr', item.id === state.selectedId ? 'selected' : '');
    row.tabIndex = 0;
    row.setAttribute('aria-label', `Review ${item.id}, ${item.analysis.sentiment}, ${item.analysis.priority} priority`);
    const textCell = node('td'); textCell.append(node('div', 'review-text', `“${item.text}”`));
    const sentimentCell = node('td'); sentimentCell.append(pill(item.analysis.sentiment));
    const priorityCell = node('td'); priorityCell.append(pill(item.analysis.priority, true));
    const summaryCell = node('td'); summaryCell.append(node('div', 'key-insight', item.analysis.summary));
    row.append(textCell, sentimentCell, priorityCell, summaryCell);
    row.addEventListener('click', () => selectReview(item.id));
    row.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectReview(item.id); } });
    body.append(row);
  }
  $('table-count').textContent = `${state.results.length} ${state.results.length === 1 ? 'review' : 'reviews'}`;
}

function selectReview(id) { state.selectedId = id; renderReviews(); renderDetail(); }

function renderDetail() {
  const item = state.results.find(result => result.id === state.selectedId);
  const panel = $('detail'); panel.replaceChildren();
  if (!item) { panel.append(node('div', 'detail-placeholder', 'Select a review to see its evidence and suggested action.')); return; }
  const analysis = item.analysis;
  const header = node('div', 'detail-header'); header.append(node('h2', '', 'Review detail'), node('span', 'detail-id', item.id)); panel.append(header);
  const original = node('section', 'detail-section'); original.append(node('h3', '', 'Original review'), node('div', 'quote-box', `“${item.text}”`)); panel.append(original);
  const pair = node('div', 'detail-pair');
  for (const [label, value, isPriority] of [['Sentiment', analysis.sentiment, false], ['Priority', analysis.priority, true]]) {
    const section = node('section', 'detail-section'); section.append(node('h3', '', label), pill(value, isPriority)); pair.append(section);
  }
  panel.append(pair);
  const insight = node('section', 'detail-section'); insight.append(node('h3', '', 'Key insight'), node('div', 'detail-summary', analysis.summary)); panel.append(insight);
  const evidence = node('section', 'detail-section'); evidence.append(node('h3', '', 'Evidence from this review'));
  const list = node('div', 'evidence-list');
  if (analysis.aspects.length) analysis.aspects.forEach(aspect => list.append(node('div', 'evidence-item', `“${aspect.evidence}” · ${LABELS[aspect.name] || aspect.name}`)));
  else list.append(node('div', 'evidence-item', 'No specific aspect evidence identified.'));
  evidence.append(list); panel.append(evidence);
  const action = node('section', 'detail-section'); action.append(node('h3', '', 'Suggested action'));
  const actionBox = node('div', 'action-box'); actionBox.append(node('span', 'action-icon', '✦'), node('span', '', analysis.recommended_action)); action.append(actionBox); panel.append(action);
}

function renderAll(data, source) {
  state.results = data.results; state.report = data.report;
  state.selectedId = state.results[0]?.id || null;
  $('empty-state').classList.add('is-hidden');
  for (const section of document.querySelectorAll('.metrics, .insight-grid, .review-grid')) section.classList.remove('is-hidden');
  renderMetrics(); renderSentiment(); renderAspects(); renderReviews(); renderDetail();
}

async function analyzeReviews() {
  if (!state.csv) { setNotice('Upload a CSV first.', 'error'); return; }
  const button = $('analyze-btn'); button.disabled = true;
  button.querySelector('span').textContent = 'Analyzing…';
  setNotice(`Analyzing ${state.fileName}. This can take a few moments for each review.`, 'busy');
  try {
    const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ csv: state.csv }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Analysis failed');
    renderAll(data);
    const count = data.report.total_reviews;
    setNotice(`Analysis complete — ${count} ${count === 1 ? 'review' : 'reviews'} saved locally in results/.`);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) { setNotice(error.message, 'error'); }
  finally { button.disabled = false; button.querySelector('span').textContent = 'Analyze reviews'; }
}

function downloadResults() {
  if (!state.report) return;
  const output = JSON.stringify({ report: state.report, results: state.results }, null, 2);
  const url = URL.createObjectURL(new Blob([output], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'feedbacklens-results.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('upload-btn').addEventListener('click', () => $('csv-file').click());
$('empty-upload').addEventListener('click', () => $('csv-file').click());
$('csv-file').addEventListener('change', async event => {
  const file = event.target.files?.[0]; if (!file) return;
  if (file.size > 2_000_000) { setNotice('CSV must be under 2 MB.', 'error'); return; }
  state.csv = await file.text(); state.fileName = file.name;
  event.target.value = '';
  state.results = []; state.report = null; state.selectedId = null;
  showEmptyState(file.name);
  setNotice(`${file.name} is ready. Select “Analyze reviews” to create new insights.`);
});
$('analyze-btn').addEventListener('click', analyzeReviews);
$('sort-select').addEventListener('change', renderReviews);
$('download-btn').addEventListener('click', downloadResults);
document.querySelectorAll('[data-nav]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-nav]').forEach(item => item.classList.toggle('active', item === button));
  const section = button.dataset.nav;
  if (section === 'reviews') $('reviews').scrollIntoView({ behavior: 'smooth', block: 'start' });
  else if (section === 'analyze') { window.scrollTo({ top: 0, behavior: 'smooth' }); $('upload-btn').focus(); }
  else window.scrollTo({ top: 0, behavior: 'smooth' });
}));
showEmptyState();
