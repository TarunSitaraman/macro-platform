// ── STATE MANAGEMENT ──
const state = {
    token: localStorage.getItem('token') || '',
    currentUser: null,
    activeTab: 'overview',
    chartInstance: null,    // legacy (kept for reference)
    chartInstances: [],     // explorer: one per indicator
    goldDataCache: {},      // explorer: keyed by indicator_code
    selectedIndicators: [], // explorer: active chip codes
    selectedCountries: [],  // explorer: active chip codes
    activeSessionId: null,
    sessions: [],
    indicators: [],
    indicatorsLoaded: false,
    sources: [],
    reviewQueue: [],
    crawlerSources: [],
    discoveredArticles: [],
    extractedRecords: [],
    anomalyAlerts: [],
    detectedAnomalies: [],
    compiledReport: null,
    anomalyPollInterval: null,
};

// API Base configuration
const API_URL = '/api/v1';

// ── SECURITY UTILITIES ──
function escHtml(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function safeHref(url) {
    if (!url) return '#';
    return /^https?:\/\//i.test(String(url)) ? String(url) : '#';
}

// Renders assistant message content safely: plain text with [Source: X] turned into DOM badges.
function renderMsgContent(el, text) {
    const SOURCE_RE = /\[Source: ([^\]]+)\]/g;
    let last = 0;
    let match;
    while ((match = SOURCE_RE.exec(text)) !== null) {
        if (match.index > last) {
            el.appendChild(document.createTextNode(text.slice(last, match.index)));
        }
        const badge = document.createElement('span');
        badge.className = 'badge badge-emerald';
        badge.textContent = match[1];
        el.appendChild(badge);
        last = SOURCE_RE.lastIndex;
    }
    if (last < text.length) {
        // Render remaining text with newline → <br> via DOM (no innerHTML on user data)
        text.slice(last).split('\n').forEach((line, i, arr) => {
            el.appendChild(document.createTextNode(line));
            if (i < arr.length - 1) el.appendChild(document.createElement('br'));
        });
    }
}

// Builds a complete .chat-message element safely for both user and assistant roles.
function buildMsgElement(role, content) {
    const isUser = role === 'user';
    const wrap = document.createElement('div');
    wrap.className = `chat-message ${isUser ? 'user' : 'assistant'}`;

    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    const icon = document.createElement('i');
    icon.setAttribute('data-lucide', isUser ? 'user' : 'bot');
    avatar.appendChild(icon);

    const msgContent = document.createElement('div');
    msgContent.className = 'msg-content';

    if (isUser) {
        msgContent.textContent = content;
    } else {
        renderMsgContent(msgContent, content);
    }

    wrap.append(avatar, msgContent);
    return wrap;
}

// Initialize App
document.addEventListener('DOMContentLoaded', () => {
    initLucide();
    setupEventListeners();
    checkAuth();
});

function initLucide() {
    if (window.lucide) {
        window.lucide.createIcons();
    }
}

// ── AUTHENTICATION FUNCTIONALITY ──
async function checkAuth() {
    if (!state.token) {
        showLogin();
        return;
    }
    
    showLoadingState();
    
    try {
        const response = await fetch(`${API_URL}/auth/me`, {
            headers: {
                'Authorization': `Bearer ${state.token}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();
            state.currentUser = data;
            document.getElementById('user-name').textContent = data.full_name;
            document.getElementById('user-role').textContent = data.role.toUpperCase();
            
            hideLogin();
            loadDashboardData();
        } else {
            // Token expired or invalid
            logout();
        }
    } catch (err) {
        console.error("Auth verification failed:", err);
        showError("Server connection lost. Please try again.");
    } finally {
        hideLoadingState();
    }
}

function showLogin() {
    document.getElementById('auth-overlay').classList.remove('hidden');
    document.getElementById('app-container').classList.add('hidden');
}

function hideLogin() {
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('app-container').classList.remove('hidden');
}

function logout() {
    state.token = '';
    state.currentUser = null;
    localStorage.removeItem('token');
    showLogin();
}

async function handleLogin(e) {
    e.preventDefault();
    const email = document.getElementById('login-email').value;
    const password = document.getElementById('login-password').value;
    const errorEl = document.getElementById('auth-error');
    
    errorEl.classList.add('hidden');
    
    try {
        const params = new URLSearchParams();
        params.append('username', email);
        params.append('password', password);
        
        const response = await fetch(`${API_URL}/auth/login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: params
        });
        
        if (response.ok) {
            const data = await response.json();
            state.token = data.access_token;
            localStorage.setItem('token', data.access_token);
            await checkAuth();
        } else {
            const errData = await response.json();
            showAuthError(errData.detail || "Incorrect email or password");
        }
    } catch (err) {
        console.error("Login failed:", err);
        showAuthError("Server unavailable. Please check backend connection.");
    }
}

function showAuthError(msg) {
    const errorEl = document.getElementById('auth-error');
    const textEl = document.getElementById('auth-error-text');
    textEl.textContent = msg;
    errorEl.classList.remove('hidden');
}

// ── NAVIGATION & LAYOUT ──
function setupEventListeners() {
    // Login form submit
    document.getElementById('login-form').addEventListener('submit', handleLogin);
    
    // Logout button
    document.getElementById('logout-btn').addEventListener('click', logout);
    
    // Sidebar Navigation Tabs
    const menuItems = document.querySelectorAll('.menu-item');
    menuItems.forEach(item => {
        item.addEventListener('click', () => {
            const tabId = item.getAttribute('data-tab');
            switchTab(tabId);
        });
    });
    
    // Pipeline Orchestrator Run buttons
    document.getElementById('run-ingestion-btn').addEventListener('click', runIngestionJob);
    document.getElementById('run-embeddings-btn').addEventListener('click', runEmbeddingsRefresher);
    
    // Explorer
    document.getElementById('exp-search-btn').addEventListener('click', visualizeExplorer);
    document.getElementById('exp-clear-btn').addEventListener('click', clearExplorerSelection);
    
    // Chatbot conversations
    document.getElementById('new-chat-btn').addEventListener('click', createNewChatSession);
    document.getElementById('chat-input-form').addEventListener('submit', sendChatMessage);
    
    // Summaries Report form
    document.getElementById('summary-form').addEventListener('submit', generateSummaryReport);
    document.getElementById('close-report-btn').addEventListener('click', () => {
        document.getElementById('report-view-card').classList.add('hidden');
    });
    
    // Lineage Tracker
    document.getElementById('lineage-search-btn').addEventListener('click', traceLineage);

    // Crawler Elements
    document.getElementById('crawler-source').addEventListener('change', updateCrawlerSourceDetails);
    document.getElementById('crawler-discover-btn').addEventListener('click', discoverCrawlerArticles);
    document.getElementById('crawler-run-btn').addEventListener('click', runWebCrawler);
    document.getElementById('crawler-push-btn').addEventListener('click', pushExtractedToPipeline);

    // Anomalies Elements
    document.getElementById('anomalies-refresh-btn').addEventListener('click', () => loadDetectedAnomalies(true));

    // Researcher Elements
    document.getElementById('research-form').addEventListener('submit', runAutonomousResearcher);
    document.getElementById('research-download-btn').addEventListener('click', downloadCompiledPDF);
}

function switchTab(tabId) {
    // Clear anomaly poll interval if switching tabs
    if (state.anomalyPollInterval) {
        clearInterval(state.anomalyPollInterval);
        state.anomalyPollInterval = null;
    }

    // Update active class in sidebar menu
    const menuItems = document.querySelectorAll('.menu-item');
    menuItems.forEach(item => {
        if (item.getAttribute('data-tab') === tabId) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });
    
    // Hide all tab content panes and show targeted one
    const tabPanes = document.querySelectorAll('.tab-pane');
    tabPanes.forEach(pane => {
        pane.classList.remove('active-pane');
    });
    
    const targetedPane = document.getElementById(`tab-${tabId}`);
    if (targetedPane) {
        targetedPane.classList.add('active-pane');
    }
    
    // Update header labels
    const titleEl = document.getElementById('page-title');
    const subtitleEl = document.getElementById('page-subtitle');
    
    state.activeTab = tabId;
    
    if (tabId === 'overview') {
        titleEl.textContent = 'Dashboard Overview';
        subtitleEl.textContent = 'Real-time medallion pipeline health & configuration registry.';
        loadOverviewStats();
        loadSources();
    } else if (tabId === 'crawler') {
        titleEl.textContent = 'Dynamic Crawler Product';
        subtitleEl.textContent = 'Scan index pages, discover articles, extract indicators and feed medallion layers.';
        loadCrawlerTab();
    } else if (tabId === 'explorer') {
        titleEl.textContent = 'Macro Data Explorer';
        subtitleEl.textContent = 'Visualize indicators trends and query production gold records.';
        loadExplorerSetup();
    } else if (tabId === 'anomalies') {
        titleEl.textContent = 'Anomaly Detection & Alerts';
        subtitleEl.textContent = 'Track automated system warnings and Prophetic deviation signals.';
        loadAnomaliesTab();
    } else if (tabId === 'review') {
        titleEl.textContent = 'Quality Review Queue';
        subtitleEl.textContent = 'Manually verify, promote, or reject ingestion pipeline anomalies.';
        loadReviewQueue();
    } else if (tabId === 'chatbot') {
        titleEl.textContent = 'AI Macro Assistant';
        subtitleEl.textContent = 'Real-time retrieval-augmented chatbot providing citations.';
        setupChatbotTab();
    } else if (tabId === 'summaries') {
        titleEl.textContent = 'Macro Summary Engine';
        subtitleEl.textContent = 'Generate detailed AI analytical briefs on country indicators.';
        loadSummariesTab();
    } else if (tabId === 'researcher') {
        titleEl.textContent = 'Senior Researcher Agent';
        subtitleEl.textContent = 'Generate investment-grade research documents utilizing multi-source search.';
        loadResearcherTab();
    } else if (tabId === 'audits') {
        titleEl.textContent = 'Audit Trail & Lineage';
        subtitleEl.textContent = 'Trace data transformation pathways and inspect pipeline audit logs.';
        loadAuditsTab();
    }
}

// ── LOADING DATA ──
function loadDashboardData() {
    loadOverviewStats();
    loadSources();
    loadIndicators();
    updatePendingBadge();
    loadDetectedAnomalies(false); // warm cache on login so tab loads instantly
}

async function loadOverviewStats() {
    try {
        const response = await fetch(`${API_URL}/overview-stats`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const data = await response.json();
            document.getElementById('kpi-gold').textContent = data.gold_records.toLocaleString();
            document.getElementById('kpi-bronze').textContent = data.total_ingested.toLocaleString();
            document.getElementById('kpi-pending').textContent = data.pending_review.toString();
            document.getElementById('kpi-sources').textContent = data.active_sources.toString();
            document.getElementById('kpi-quality').textContent = `${data.avg_dq_score.toFixed(1)}%`;
            
            // Set review badge count
            const badge = document.getElementById('review-badge');
            if (data.pending_review > 0) {
                badge.textContent = data.pending_review.toString();
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
    } catch (err) {
        console.error("Overview stats fetch failed:", err);
    }
}

async function loadSources() {
    const tableBody = document.getElementById('sources-table-body');
    try {
        const response = await fetch(`${API_URL}/sources`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const sources = await response.json();
            state.sources = sources;
            
            if (sources.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="7" class="loading-cell">No registered sources in database.</td></tr>`;
                return;
            }
            
            tableBody.innerHTML = sources.map(src => {
                const statusIcon = src.is_active ? '🟢 Active' : '🔴 Inactive';
                const lastRun = src.last_run_at ? new Date(src.last_run_at).toLocaleString() : 'Never';
                return `
                    <tr>
                        <td><strong>${statusIcon}</strong></td>
                        <td>${escHtml(src.source_name)}</td>
                        <td><code>${escHtml(src.source_code)}</code></td>
                        <td><span class="badge badge-purple">${escHtml(src.source_type)}</span></td>
                        <td>${escHtml(src.frequency)}</td>
                        <td><strong>${src.reputation_score.toFixed(0)}/100</strong></td>
                        <td>${escHtml(lastRun)}</td>
                    </tr>
                `;
            }).join('');
        }
    } catch (err) {
        console.error("Sources fetch failed:", err);
        tableBody.innerHTML = `<tr><td colspan="7" class="loading-cell text-danger">Failed to fetch data sources.</td></tr>`;
    }
}

async function loadIndicators() {
    try {
        const response = await fetch(`${API_URL}/indicators`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        state.indicatorsLoaded = true;
        if (response.ok) {
            state.indicators = await response.json();
            if (state.activeTab === 'explorer') {
                loadExplorerSetup();
            }
        }
    } catch (err) {
        state.indicatorsLoaded = true;
        console.error("Indicators fetch failed:", err);
    }
}

async function updatePendingBadge() {
    try {
        const response = await fetch(`${API_URL}/overview-stats`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const data = await response.json();
            const badge = document.getElementById('review-badge');
            if (data.pending_review > 0) {
                badge.textContent = data.pending_review.toString();
                badge.classList.remove('hidden');
            } else {
                badge.classList.add('hidden');
            }
        }
    } catch (err) {
        console.error("Pending badge fetch failed:", err);
    }
}

// ── ORCHESTRATION TERMINAL UTILITIES ──
function writeToConsole(message, type = 'sys') {
    const consoleOut = document.getElementById('console-output');
    const timestamp = new Date().toLocaleTimeString();
    
    let cssClass = 'sys-msg';
    if (type === 'error') cssClass = 'err-msg';
    if (type === 'success') cssClass = 'success-msg';
    
    const p = document.createElement('p');
    p.className = cssClass;
    p.innerHTML = `[${timestamp}] ${message}`;
    consoleOut.appendChild(p);
    
    // Auto-scroll terminal
    consoleOut.scrollTop = consoleOut.scrollHeight;
}

function setPipelineWorking(statusText = 'Orchestrator working') {
    const dot = document.querySelector('#pipeline-status-container .status-dot');
    const text = document.querySelector('#pipeline-status-container .status-text');
    dot.className = 'status-dot working';
    text.textContent = statusText;
}

function setPipelineOnline() {
    const dot = document.querySelector('#pipeline-status-container .status-dot');
    const text = document.querySelector('#pipeline-status-container .status-text');
    dot.className = 'status-dot online';
    text.textContent = 'API Online';
}

async function runIngestionJob() {
    const btn = document.getElementById('run-ingestion-btn');
    btn.disabled = true;
    writeToConsole("Triggering medallion ingestion job (Bronze &rarr; Silver &rarr; Gold flow)...");
    setPipelineWorking('Ingestion Running');
    
    try {
        const response = await fetch(`${API_URL}/pipelines/orchestrate/run`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        
        if (response.ok) {
            const res = await response.json();
            writeToConsole(`Ingestion completed successfully! Run ID: ${res.run_id}`, 'success');
            loadOverviewStats();
            loadSources();
        } else {
            const err = await response.json();
            writeToConsole(`Ingestion failed: ${err.detail || "Server Error"}`, 'error');
        }
    } catch (err) {
        writeToConsole(`Ingestion execution failed: connection lost.`, 'error');
    } finally {
        btn.disabled = false;
        setPipelineOnline();
    }
}

async function runEmbeddingsRefresher() {
    const btn = document.getElementById('run-embeddings-btn');
    btn.disabled = true;
    writeToConsole("Triggering vector index updates (Gemini text-embedding-2)...");
    setPipelineWorking('Embedding Records');
    
    try {
        const response = await fetch(`${API_URL}/pipelines/embeddings/run`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        
        if (response.ok) {
            const res = await response.json();
            writeToConsole(res.message, 'success');
            loadOverviewStats();
        } else {
            const err = await response.json();
            writeToConsole(`Refresh failed: ${err.detail || "Server Error"}`, 'error');
        }
    } catch (err) {
        writeToConsole(`Embedding execution failed: connection lost.`, 'error');
    } finally {
        btn.disabled = false;
        setPipelineOnline();
    }
}

// ── DATA EXPLORER & CHARTS ──
const EXPLORER_COUNTRIES = [
    { code: 'USA', name: 'United States' }, { code: 'GBR', name: 'United Kingdom' },
    { code: 'DEU', name: 'Germany' },        { code: 'FRA', name: 'France' },
    { code: 'JPN', name: 'Japan' },          { code: 'CHN', name: 'China' },
    { code: 'IND', name: 'India' },          { code: 'BRA', name: 'Brazil' },
    { code: 'CAN', name: 'Canada' },         { code: 'AUS', name: 'Australia' },
    { code: 'ITA', name: 'Italy' },          { code: 'KOR', name: 'South Korea' },
    { code: 'ESP', name: 'Spain' },          { code: 'MEX', name: 'Mexico' },
    { code: 'IDN', name: 'Indonesia' },      { code: 'TUR', name: 'Turkey' },
    { code: 'ARG', name: 'Argentina' },      { code: 'ZAF', name: 'South Africa' },
    { code: 'SAU', name: 'Saudi Arabia' },   { code: 'RUS', name: 'Russia' },
];
const EXPLORER_COLORS = ['#00c8ff','#f5a623','#a78bfa','#00e887','#ff3b5c','#f472b6','#38bdf8','#fb923c','#34d399','#fbbf24'];

function loadExplorerSetup() {
    const indSet = document.getElementById('indicator-chip-set');
    const ctySet = document.getElementById('country-chip-set');
    if (!indSet || !ctySet) return;

    // Populate indicator chips from state.indicators
    if (!state.indicatorsLoaded) {
        indSet.innerHTML = '<span class="chip-placeholder">Loading indicators…</span>';
        loadIndicators();
    } else {
        indSet.innerHTML = '';
        if ((state.indicators || []).length === 0) {
            indSet.innerHTML = '<span class="chip-placeholder text-danger">No indicators found in database</span>';
        } else {
            state.indicators.forEach(ind => {
                const chip = document.createElement('button');
                chip.className = 'exp-chip';
                chip.dataset.code = ind.indicator_code;
                chip.title = ind.indicator_name;
                chip.textContent = ind.indicator_code;
                chip.addEventListener('click', () => toggleChip(chip, 'indicator'));
                indSet.appendChild(chip);
            });
        }
    }

    // Populate country chips from static list
    ctySet.innerHTML = '';
    EXPLORER_COUNTRIES.forEach(c => {
        const chip = document.createElement('button');
        chip.className = 'exp-chip';
        chip.dataset.code = c.code;
        chip.title = c.name;
        chip.textContent = c.code;
        chip.addEventListener('click', () => toggleChip(chip, 'country'));
        ctySet.appendChild(chip);
    });
}

function toggleChip(chip, kind) {
    chip.classList.toggle('active');
    if (kind === 'indicator') {
        const code = chip.dataset.code;
        if (chip.classList.contains('active')) {
            if (!state.selectedIndicators.includes(code)) state.selectedIndicators.push(code);
        } else {
            state.selectedIndicators = state.selectedIndicators.filter(c => c !== code);
        }
    } else {
        const code = chip.dataset.code;
        if (chip.classList.contains('active')) {
            if (!state.selectedCountries.includes(code)) state.selectedCountries.push(code);
        } else {
            state.selectedCountries = state.selectedCountries.filter(c => c !== code);
        }
    }
}

async function visualizeExplorer() {
    if (state.selectedIndicators.length === 0) {
        alert('Select at least one indicator.');
        return;
    }

    // Destroy old chart instances
    state.chartInstances.forEach(c => c.destroy());
    state.chartInstances = [];

    const grid = document.getElementById('explorer-charts-grid');
    grid.innerHTML = '';
    const gridClass = state.selectedIndicators.length === 1 ? 'grid-1'
                    : state.selectedIndicators.length === 2 ? 'grid-2' : 'grid-3';
    grid.className = `explorer-charts-grid ${gridClass}`;

    // Show loading placeholders
    state.selectedIndicators.forEach(code => {
        const card = document.createElement('div');
        card.className = 'exp-chart-card';
        card.id = `chart-card-${code}`;
        card.innerHTML = `<div class="exp-chart-loading">Loading ${escHtml(code)}…</div>`;
        grid.appendChild(card);
    });

    // Fetch each indicator (with cache), then render
    await Promise.all(state.selectedIndicators.map(code => fetchIndicatorData(code)));
    state.selectedIndicators.forEach((code, idx) => renderIndicatorChart(code, idx));

    // Also refresh the data table for the first indicator
    renderExplorerTable();
}

async function fetchIndicatorData(indCode) {
    if (state.goldDataCache[indCode]) return;
    try {
        const resp = await fetch(`${API_URL}/gold-data?limit=1000&indicator=${indCode}&year_from=2010&actuals_only=true`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (resp.ok) state.goldDataCache[indCode] = await resp.json();
        else state.goldDataCache[indCode] = [];
    } catch {
        state.goldDataCache[indCode] = [];
    }
}

function renderIndicatorChart(indCode, colorOffset) {
    const card = document.getElementById(`chart-card-${indCode}`);
    if (!card) return;

    const allRecs = state.goldDataCache[indCode] || [];
    const recs = state.selectedCountries.length > 0
        ? allRecs.filter(r => state.selectedCountries.includes(r.country_code))
        : allRecs;

    const indMeta = state.indicators.find(i => i.indicator_code === indCode);
    const indName = indMeta ? indMeta.indicator_name : indCode;
    const unit = recs.length > 0 ? (recs[0].standard_unit || '') : '';

    // Aggregate to annual: group by country then year, compute mean
    const byCountry = {};
    recs.forEach(r => {
        const year = r.period.slice(0, 4);
        if (!byCountry[r.country_code]) byCountry[r.country_code] = {};
        if (!byCountry[r.country_code][year]) byCountry[r.country_code][year] = [];
        byCountry[r.country_code][year].push(r.value);
    });

    const allYears = [...new Set(recs.map(r => r.period.slice(0, 4)))].sort();

    const datasets = Object.keys(byCountry).map((cty, i) => {
        const color = EXPLORER_COLORS[(colorOffset + i) % EXPLORER_COLORS.length];
        const values = allYears.map(yr => {
            const vals = byCountry[cty][yr];
            if (!vals || vals.length === 0) return null;
            return vals.reduce((a, b) => a + b, 0) / vals.length;
        });
        return {
            label: cty,
            data: values,
            borderColor: color,
            backgroundColor: color + '18',
            borderWidth: 2,
            pointBackgroundColor: color,
            pointBorderColor: '#020407',
            pointRadius: 3,
            pointHoverRadius: 5,
            tension: 0.35,
            spanGaps: true,
            fill: Object.keys(byCountry).length === 1,
        };
    });

    card.innerHTML = `
        <div class="exp-chart-header">
            <span class="exp-chart-title">${escHtml(indName)}</span>
            <span class="exp-chart-unit">${escHtml(unit)}</span>
        </div>
        <div class="exp-canvas-wrap"><canvas id="canvas-${indCode}"></canvas></div>
    `;

    if (allYears.length === 0 || datasets.length === 0) {
        card.innerHTML += `<div class="exp-empty-state">No data for selected economies</div>`;
        return;
    }

    const ctx = document.getElementById(`canvas-${indCode}`).getContext('2d');

    // Gradient fill for single-series charts
    if (datasets.length === 1) {
        const color = datasets[0].borderColor;
        const grad = ctx.createLinearGradient(0, 0, 0, 360);
        grad.addColorStop(0, color + '44');
        grad.addColorStop(1, color + '00');
        datasets[0].fill = true;
        datasets[0].backgroundColor = grad;
    }

    const instance = new Chart(ctx, {
        type: 'line',
        data: { labels: allYears, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    display: datasets.length <= 12,
                    position: 'bottom',
                    labels: {
                        boxWidth: 8,
                        padding: 12,
                        color: '#7a8fa6',
                        font: { family: "'JetBrains Mono'", size: 10 }
                    }
                },
                tooltip: {
                    backgroundColor: '#0a0f1a',
                    titleColor: '#00c8ff',
                    bodyColor: '#a0b4c8',
                    borderColor: 'rgba(0,200,255,0.15)',
                    borderWidth: 1,
                    padding: 10,
                    callbacks: {
                        label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString(undefined, {maximumFractionDigits: 2}) : 'N/A'} ${unit}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(0,200,255,0.04)' },
                    ticks: { color: '#4a6070', font: { family: "'JetBrains Mono'", size: 10 } }
                },
                y: {
                    grid: { color: 'rgba(0,200,255,0.04)' },
                    ticks: { color: '#4a6070', font: { family: "'JetBrains Mono'", size: 10 } },
                    title: { display: !!unit, text: unit, color: '#4a6070', font: { family: "'JetBrains Mono'", size: 10 } }
                }
            }
        }
    });
    state.chartInstances.push(instance);
}

function renderExplorerTable() {
    const tableBody = document.getElementById('explorer-table-body');
    const tableCard = document.getElementById('explorer-table-card');
    if (!tableBody) return;

    const allRecs = state.selectedIndicators.flatMap(code => state.goldDataCache[code] || []);
    const recs = state.selectedCountries.length > 0
        ? allRecs.filter(r => state.selectedCountries.includes(r.country_code))
        : allRecs;

    if (recs.length === 0) {
        if (tableCard) tableCard.classList.add('hidden');
        return;
    }
    if (tableCard) tableCard.classList.remove('hidden');

    tableBody.innerHTML = recs.slice(0, 200).map(rec => {
        const forecastTag = rec.is_forecast
            ? '<span class="badge badge-accent">Forecast</span>'
            : '<span class="badge badge-purple">Actual</span>';
        const dqClass = rec.dq_score >= 90 ? 'text-success' : 'text-warning';
        return `<tr>
            <td><strong>${escHtml(rec.indicator_code)}</strong></td>
            <td>${escHtml(rec.country_code)}</td>
            <td><code>${escHtml(rec.period)}</code></td>
            <td><strong>${rec.value.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 2})}</strong></td>
            <td>${escHtml(rec.standard_unit)}</td>
            <td>${forecastTag}</td>
            <td><strong class="${dqClass}">${rec.dq_score.toFixed(1)}%</strong></td>
            <td><a href="${safeHref(rec.source_url)}" target="_blank" class="table-link">${escHtml(rec.source_name)}</a></td>
            <td>${new Date(rec.promoted_at).toLocaleDateString()}</td>
        </tr>`;
    }).join('');
}

function clearExplorerSelection() {
    state.selectedIndicators = [];
    state.selectedCountries = [];
    document.querySelectorAll('.exp-chip.active').forEach(c => c.classList.remove('active'));
    state.chartInstances.forEach(c => c.destroy());
    state.chartInstances = [];
    state.goldDataCache = {};
    const grid = document.getElementById('explorer-charts-grid');
    if (grid) {
        grid.innerHTML = '<div class="exp-empty-state">Select indicators and economies, then click Visualize.</div>';
        grid.className = 'explorer-charts-grid';
    }
    const tableCard = document.getElementById('explorer-table-card');
    if (tableCard) tableCard.classList.add('hidden');
}

// ── REVIEW QUEUE FUNCTIONS ──
async function loadReviewQueue() {
    const tableBody = document.getElementById('review-table-body');
    tableBody.innerHTML = `<tr><td colspan="8" class="loading-cell">Loading items awaiting approval...</td></tr>`;
    
    try {
        const response = await fetch(`${API_URL}/review-queue`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        
        if (response.ok) {
            const queue = await response.json();
            state.reviewQueue = queue;
            
            if (queue.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="8" class="loading-cell">All items reviewed! Queue is empty.</td></tr>`;
                return;
            }
            
            tableBody.innerHTML = queue.map(item => {
                const issueChips = item.failure_reasons.map(reason => `<span class="issue-item">${escHtml(reason)}</span>`).join('');
                return `
                    <tr>
                        <td><strong>${escHtml(item.indicator_code)}</strong></td>
                        <td>${escHtml(item.country_code)}</td>
                        <td><code>${escHtml(item.period)}</code></td>
                        <td><strong>${escHtml(String(item.extracted_value))}</strong></td>
                        <td><strong class="text-warning">${item.dq_score.toFixed(1)}%</strong></td>
                        <td><div class="issue-list">${issueChips}</div></td>
                        <td><a href="${safeHref(item.source_url)}" target="_blank" class="table-link">External Link</a></td>
                        <td class="text-right">
                            <div class="review-actions">
                                <button class="btn btn-primary" onclick="approveReviewItem('${item.queue_id}')">Approve</button>
                                <button class="btn btn-danger" onclick="rejectReviewItem('${item.queue_id}')">Reject</button>
                            </div>
                        </td>
                    </tr>
                `;
            }).join('');
        }
    } catch (err) {
        console.error("Review queue fetch failed:", err);
        tableBody.innerHTML = `<tr><td colspan="8" class="loading-cell text-danger">Failed to load review items.</td></tr>`;
    }
}

async function approveReviewItem(queueId) {
    if (!confirm("Are you sure you want to approve this data point into the Gold layer?")) return;
    
    try {
        const response = await fetch(`${API_URL}/review-queue/${queueId}/approve`, {
            method: 'POST',
            headers: { 
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({})
        });
        if (response.ok) {
            loadReviewQueue();
            updatePendingBadge();
        } else {
            alert("Approve request failed.");
        }
    } catch (err) {
        console.error("Approve item failed:", err);
    }
}

async function rejectReviewItem(queueId) {
    const reason = prompt("Please enter a brief note explaining the reason for rejection:");
    if (reason === null) return;
    if (!reason.trim()) {
        alert("Rejection notes are mandatory!");
        return;
    }
    
    try {
        const response = await fetch(`${API_URL}/review-queue/${queueId}/reject`, {
            method: 'POST',
            headers: { 
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ review_notes: reason })
        });
        if (response.ok) {
            loadReviewQueue();
            updatePendingBadge();
        } else {
            alert("Reject request failed.");
        }
    } catch (err) {
        console.error("Reject item failed:", err);
    }
}

// ── CHATBOT TAB ──
async function setupChatbotTab() {
    // Check if we already have an active session, if not create one
    if (!state.activeSessionId) {
        await createNewChatSession();
    }
}

async function createNewChatSession() {
    try {
        const response = await fetch(`${API_URL}/chat/sessions`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const data = await response.json();
            state.activeSessionId = data.session_id;
            
            // Add session to our local state
            state.sessions.unshift({
                session_id: data.session_id,
                title: `Chat Session ${data.session_id.substring(0, 8)}`
            });
            
            renderSessionsList();
            
            // Reset chat window messages
            const messagesContainer = document.getElementById('chat-messages-container');
            messagesContainer.innerHTML = `
                <div class="chat-message assistant">
                    <div class="msg-avatar"><i data-lucide="bot"></i></div>
                    <div class="msg-content">
                        Hello! I am your Macroeconomic Intelligence RAG Assistant. Ask me questions about GDP, inflation, unemployment, trade balances, and government debt. I will cite verified database records dynamically.
                    </div>
                </div>
            `;
            initLucide();
        }
    } catch (err) {
        console.error("Failed to create chat session:", err);
    }
}

function renderSessionsList() {
    const listEl = document.getElementById('sessions-list');
    listEl.innerHTML = state.sessions.map(s => {
        const activeClass = s.session_id === state.activeSessionId ? 'active' : '';
        return `<div class="session-item ${activeClass}" onclick="selectSession('${s.session_id}')">${s.title}</div>`;
    }).join('');
}

async function selectSession(sessionId) {
    state.activeSessionId = sessionId;
    renderSessionsList();
    
    // Fetch conversation history
    const messagesContainer = document.getElementById('chat-messages-container');
    messagesContainer.innerHTML = `<div class="loading-cell">Loading history...</div>`;
    
    try {
        const response = await fetch(`${API_URL}/chat/sessions/${sessionId}/messages`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const data = await response.json();
            
            if (data.messages.length === 0) {
                messagesContainer.innerHTML = `
                    <div class="chat-message assistant">
                        <div class="msg-avatar"><i data-lucide="bot"></i></div>
                        <div class="msg-content">
                            Conversation started. Ask me any macro questions!
                        </div>
                    </div>
                `;
            } else {
                messagesContainer.innerHTML = '';
                data.messages.forEach(msg => {
                    messagesContainer.appendChild(buildMsgElement(msg.role, msg.content));
                });
            }
            initLucide();
            messagesContainer.scrollTop = messagesContainer.scrollHeight;
        }
    } catch (err) {
        console.error("Failed to load session history:", err);
        messagesContainer.innerHTML = `<div class="loading-cell text-danger">Failed to load history.</div>`;
    }
}

async function sendChatMessage(e) {
    e.preventDefault();
    const inputEl = document.getElementById('chat-message-input');
    const message = inputEl.value.trim();
    if (!message || !state.activeSessionId) return;
    
    // Add user message to UI immediately
    const messagesContainer = document.getElementById('chat-messages-container');
    
    messagesContainer.appendChild(buildMsgElement('user', message));
    initLucide();

    inputEl.value = '';
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    // Typing placeholder — static markup, no untrusted data
    const botMsgDiv = document.createElement('div');
    botMsgDiv.className = 'chat-message assistant';
    botMsgDiv.innerHTML = `
        <div class="msg-avatar"><i data-lucide="bot"></i></div>
        <div class="msg-content">Thinking… <span class="status-dot working" style="display:inline-block;vertical-align:middle;margin-left:6px"></span></div>
    `;
    messagesContainer.appendChild(botMsgDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    try {
        const response = await fetch(`${API_URL}/chat/sessions/${state.activeSessionId}/messages`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message })
        });

        if (response.ok) {
            const data = await response.json();
            const contentEl = botMsgDiv.querySelector('.msg-content');
            contentEl.innerHTML = '';
            renderMsgContent(contentEl, data.response);
        } else {
            botMsgDiv.querySelector('.msg-content').textContent = "I encountered an error querying the RAG vector index. Please try again.";
        }
    } catch (err) {
        console.error("Chat send failed:", err);
        botMsgDiv.querySelector('.msg-content').textContent = "Connection lost. Please check backend.";
    } finally {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }
}

// ── SUMMARIES ENGINE ──
async function loadSummariesTab() {
    loadRecentSummaries();
}

async function loadRecentSummaries() {
    const listEl = document.getElementById('summaries-list');
    listEl.innerHTML = `<div class="loading-cell">Loading reports...</div>`;
    
    try {
        const response = await fetch(`${API_URL}/summaries`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const reports = await response.json();
            
            if (reports.length === 0) {
                listEl.innerHTML = `<div class="loading-cell">No summaries generated yet.</div>`;
                return;
            }
            
            listEl.innerHTML = reports.map(r => `
                <div class="summary-item" onclick="viewSummaryReport('${r.summary_id}')">
                    <div class="summary-item-info">
                        <strong>${r.country_code} - ${r.summary_type.replace(/_/g, ' ')}</strong>
                        <span>Generated by: ${r.model_used} | ${new Date(r.generated_at).toLocaleDateString()}</span>
                    </div>
                    <i data-lucide="chevron-right"></i>
                </div>
            `).join('');
            initLucide();
        }
    } catch (err) {
        console.error("Failed to load summaries list:", err);
        listEl.innerHTML = `<div class="loading-cell text-danger">Failed to load reports.</div>`;
    }
}

async function generateSummaryReport(e) {
    e.preventDefault();
    const country = document.getElementById('sum-country').value;
    const type = document.getElementById('sum-type').value;
    const btn = document.getElementById('gen-summary-btn');
    
    btn.disabled = true;
    btn.querySelector('span').textContent = 'Generating Brief...';
    
    try {
        const response = await fetch(`${API_URL}/summaries/generate`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                country_code: country,
                summary_type: type
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            loadRecentSummaries();
            displayReportCard(data);
        } else {
            alert("Failed to generate summary report.");
        }
    } catch (err) {
        console.error("Summary report generation failed:", err);
    } finally {
        btn.disabled = false;
        btn.querySelector('span').textContent = 'Generate Intelligence Report';
    }
}

async function viewSummaryReport(summaryId) {
    try {
        const response = await fetch(`${API_URL}/summaries`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const list = await response.json();
            // Fetch detailed by listing full reports and filtering by ID
            // In a larger setup, we would request a single resource endpoint, but since list_summaries returns contents,
            // we can retrieve it. However, the list content is truncated in list_summaries (see chat.py line 108).
            // Wait, does chat.py have a GET /summaries/{id} endpoint? Let's check: it doesn't.
            // But we can generate a new one, or pull from full query.
            // Wait! Since list_summaries content is truncated (r.content[:500] in chat.py),
            // let's fetch the detail. Ah! Let's check: actually the summarizer content can be retrieved.
            // Let's create a custom viewer inside app.js. If the content is truncated, we can display what is returned or let them regenerate.
            // Let's see if we can find the full item. Wait! Summaries table is queried via DB. Since there is no single GET,
            // we can fetch the full list if we query. Let's see what is stored in summaries list.
            const match = list.find(r => r.summary_id === summaryId);
            if (match) {
                displayReportCard(match);
            }
        }
    } catch (err) {
        console.error("Failed to load summary details:", err);
    }
}

function displayReportCard(data) {
    const card = document.getElementById('report-view-card');
    document.getElementById('report-title').textContent = `${data.country_code} Ingestion Brief: ${data.summary_type.replace(/_/g, ' ')}`;
    document.getElementById('report-metadata').textContent = `Generated by ${data.model_used} on ${new Date(data.generated_at).toLocaleString()}`;
    const reportHtml = escHtml(data.content).replace(/\n\n/g, '</p><p>').replace(/\n/g, '<br>');
    document.getElementById('report-body').innerHTML = `<p>${reportHtml}</p>`;
    
    card.classList.remove('hidden');
    card.scrollIntoView({ behavior: 'smooth' });
}

// ── RESEARCHER TAB (AUDIT & LINEAGE) ──
async function loadAuditsTab() {
    loadAuditLogs();
}

async function loadResearcherTab() {
    // Researcher starts clean, no initial load needed
}

async function loadAuditLogs() {
    const tableBody = document.getElementById('audit-table-body');
    tableBody.innerHTML = `<tr><td colspan="5" class="loading-cell">Loading audit logs...</td></tr>`;
    
    try {
        const response = await fetch(`${API_URL}/audit-log`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const logs = await response.json();
            
            if (logs.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="5" class="loading-cell">No audit logs found.</td></tr>`;
                return;
            }
            
            tableBody.innerHTML = logs.map(log => `
                <tr>
                    <td><code>${escHtml(new Date(log.timestamp).toLocaleString())}</code></td>
                    <td>${escHtml(log.table_name)}</td>
                    <td><span class="badge ${log.action === 'INSERT' ? 'badge-emerald' : 'badge-purple'}">${escHtml(log.action)}</span></td>
                    <td>${escHtml(log.actor)} (${escHtml(log.actor_role)})</td>
                    <td><code>${escHtml(JSON.stringify(log.new_values || {}))}</code></td>
                </tr>
            `).join('');
        }
    } catch (err) {
        console.error("Failed to load audit logs:", err);
        tableBody.innerHTML = `<tr><td colspan="5" class="loading-cell text-danger">Failed to load audit logs.</td></tr>`;
    }
}

async function traceLineage() {
    const recordId = document.getElementById('lineage-record-id').value.trim();
    const displayEl = document.getElementById('lineage-display');
    const placeholderEl = document.getElementById('lineage-placeholder');
    
    if (!recordId) {
        alert("Please enter a Gold Record UUID");
        return;
    }
    
    placeholderEl.innerHTML = `<div class="loading-cell">Tracing lineage...</div>`;
    displayEl.classList.add('hidden');
    placeholderEl.classList.remove('hidden');
    
    try {
        const response = await fetch(`${API_URL}/lineage/${recordId}`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        
        if (response.ok) {
            const lineage = await response.json();
            
            document.getElementById('lineage-bronze-id').textContent = lineage.bronze_id || 'N/A';
            document.getElementById('lineage-source').textContent = `Source Code: ${lineage.source_code || 'N/A'}`;
            
            document.getElementById('lineage-silver-id').textContent = lineage.silver_id || 'N/A';
            document.getElementById('lineage-dq').textContent = `DQ Score: ${lineage.dq_score ? lineage.dq_score.toFixed(1) + '%' : 'N/A'}`;
            
            document.getElementById('lineage-gold-id').textContent = lineage.gold_id || 'N/A';
            document.getElementById('lineage-value').textContent = `Value: ${lineage.value} ${lineage.standard_unit || ''}`;
            
            placeholderEl.classList.add('hidden');
            displayEl.classList.remove('hidden');
            initLucide();
        } else {
            placeholderEl.innerHTML = `
                <i data-lucide="alert-triangle" class="text-danger"></i>
                <p class="text-danger">Failed to trace lineage. Ensure the Gold Record UUID is correct and belongs to your tenant.</p>
            `;
            initLucide();
        }
    } catch (err) {
        console.error("Lineage trace failed:", err);
        placeholderEl.innerHTML = `<p class="text-danger">Connection error. Please try again.</p>`;
    }
}

// Global loaders helper
function showLoadingState() {
    writeToConsole("Connecting to platform server...");
}

function hideLoadingState() {
    //
}

function showError(msg) {
    writeToConsole(msg, 'error');
}

// ── DYNAMIC WEB CRAWLER ──

async function loadCrawlerTab() {
    const select = document.getElementById('crawler-source');
    select.innerHTML = '<option value="">Loading sources...</option>';
    document.getElementById('crawler-metadata').classList.add('hidden');
    document.getElementById('crawler-articles-list').innerHTML = '<div class="loading-cell">Select a source configuration above and click Discover.</div>';
    
    try {
        const response = await fetch(`${API_URL}/crawler/sources`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const sources = await response.json();
            state.crawlerSources = sources;
            
            if (sources.length === 0) {
                select.innerHTML = '<option value="">No HTML/PDF sources found</option>';
                return;
            }
            
            select.innerHTML = '<option value="">-- Choose a Web Source --</option>' +
                sources.map(s => `<option value="${s.source_code}">${s.source_name} (${s.source_code})</option>`).join('');
        } else {
            select.innerHTML = '<option value="">Failed to load web sources</option>';
        }
    } catch (err) {
        console.error("Failed loading crawler sources:", err);
        select.innerHTML = '<option value="">Error loading sources</option>';
    }
}

function updateCrawlerSourceDetails() {
    const code = document.getElementById('crawler-source').value;
    const metadata = document.getElementById('crawler-metadata');
    
    if (!code) {
        metadata.classList.add('hidden');
        return;
    }
    
    const source = state.crawlerSources.find(s => s.source_code === code);
    if (!source) return;
    
    document.getElementById('crawler-source-url').href = safeHref(source.source_url);
    document.getElementById('crawler-source-url').textContent = source.source_url;
    document.getElementById('crawler-prompt').textContent = source.extraction_prompt || "No custom LLM prompt configured.";
    
    metadata.classList.remove('hidden');
    
    // Auto-populate URL to crawl
    document.getElementById('crawler-url-input').value = source.source_url;
}

async function discoverCrawlerArticles() {
    const code = document.getElementById('crawler-source').value;
    const listEl = document.getElementById('crawler-articles-list');
    
    if (!code) {
        alert("Please select a web source first.");
        return;
    }
    
    const source = state.crawlerSources.find(s => s.source_code === code);
    if (!source) return;
    
    listEl.innerHTML = '<div class="loading-cell">Scanning source index page...</div>';
    
    try {
        const response = await fetch(`${API_URL}/crawler/discover`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                source_url: source.source_url,
                source_code: source.source_code
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            state.discoveredArticles = data.articles;
            
            if (data.articles.length === 0) {
                listEl.innerHTML = '<div class="loading-cell text-muted">No articles discovered. Falling back to curated list.</div>';
                return;
            }
            
            listEl.innerHTML = '';
            data.articles.forEach(art => {
                const div = document.createElement('div');
                div.className = 'summary-item';

                const info = document.createElement('div');
                info.className = 'summary-item-info';

                const title = document.createElement('strong');
                title.textContent = art.title;

                const urlSpan = document.createElement('span');
                urlSpan.style.cssText = 'font-size:10px;color:var(--text-muted)';
                urlSpan.textContent = art.url;

                info.append(title, urlSpan);

                const icon = document.createElement('i');
                icon.setAttribute('data-lucide', 'chevron-right');

                div.append(info, icon);
                div.addEventListener('click', () => selectArticleForCrawl(art.url));
                listEl.appendChild(div);
            });
            initLucide();
        } else {
            listEl.innerHTML = '<div class="loading-cell text-danger">Failed to scan source page.</div>';
        }
    } catch (err) {
        console.error("Discover articles failed:", err);
        listEl.innerHTML = '<div class="loading-cell text-danger">Connection error.</div>';
    }
}

function selectArticleForCrawl(url) {
    document.getElementById('crawler-url-input').value = url;
    document.getElementById('crawler-url-input').scrollIntoView({ behavior: 'smooth' });
}

async function runWebCrawler() {
    const url = document.getElementById('crawler-url-input').value.trim();
    const consoleEl = document.getElementById('crawler-console');
    const outputEl = document.getElementById('crawler-console-output');
    const resultsContainer = document.getElementById('crawler-results-container');
    const tableBody = document.getElementById('crawler-table-body');
    const code = document.getElementById('crawler-source').value;
    
    if (!url) {
        alert("Please enter a URL to crawl.");
        return;
    }
    
    const source = state.crawlerSources.find(s => s.source_code === code);
    const prompt = source ? source.extraction_prompt : null;
    
    consoleEl.classList.remove('hidden');
    resultsContainer.classList.add('hidden');
    outputEl.innerHTML = `<p class="sys-msg">[SYSTEM] Running crawler agent on: ${escHtml(url)}</p>`;
    outputEl.innerHTML += `<p class="sys-msg">[SYSTEM] Invoking LLM text-extraction processor...</p>`;
    
    try {
        const response = await fetch(`${API_URL}/crawler/crawl`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                url: url,
                extraction_prompt: prompt
            })
        });
        
        if (response.ok) {
            const data = await response.json();
            state.extractedRecords = data.extracted;
            
            outputEl.innerHTML += `<p class="success-msg">[SUCCESS] Extracted ${data.extracted.length} indicator records successfully.</p>`;
            outputEl.scrollTop = outputEl.scrollHeight;
            
            if (data.extracted.length === 0) {
                tableBody.innerHTML = `<tr><td colspan="6" class="loading-cell text-warning">Crawler returned 0 records. Try another URL.</td></tr>`;
                resultsContainer.classList.remove('hidden');
                return;
            }
            
            tableBody.innerHTML = data.extracted.map(rec => `
                <tr>
                    <td><strong>${escHtml(rec.indicator_code || 'N/A')}</strong></td>
                    <td>${escHtml(rec.country_code || 'N/A')}</td>
                    <td><code>${escHtml(rec.period || 'N/A')}</code></td>
                    <td><strong>${escHtml(String(rec.raw_value || 'N/A'))}</strong></td>
                    <td>${escHtml(rec.raw_unit || 'N/A')}</td>
                    <td><span class="badge ${rec.is_forecast ? 'badge-accent' : 'badge-purple'}">${rec.is_forecast ? 'Forecast' : 'Actual'}</span></td>
                </tr>
            `).join('');
            
            resultsContainer.classList.remove('hidden');
        } else {
            outputEl.innerHTML += `<p class="err-msg">[ERROR] Crawl failed. Check logs.</p>`;
        }
    } catch (err) {
        console.error("Crawl request failed:", err);
        outputEl.innerHTML += `<p class="err-msg">[ERROR] Network timeout or connection failure.</p>`;
    }
}

async function pushExtractedToPipeline() {
    const code = document.getElementById('crawler-source').value;
    const url = document.getElementById('crawler-url-input').value.trim();
    const outputEl = document.getElementById('crawler-console-output');
    
    if (!code) {
        alert("Please select a web source code to map reputation scores.");
        return;
    }
    
    if (!state.extractedRecords || state.extractedRecords.length === 0) {
        alert("No extracted records to push!");
        return;
    }
    
    outputEl.innerHTML += `<p class="sys-msg">[SYSTEM] Pushing records through Medallion stages (Bronze -> Silver -> Gold)...</p>`;
    outputEl.scrollTop = outputEl.scrollHeight;
    
    const recordsPayload = state.extractedRecords.map(rec => ({
        indicator_code: rec.indicator_code,
        country_code: rec.country_code,
        period: String(rec.period),
        raw_value: String(rec.raw_value),
        raw_unit: rec.raw_unit || null,
        source_url: url,
        source_code: code
    }));
    
    try {
        const response = await fetch(`${API_URL}/crawler/push`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ records: recordsPayload })
        });
        
        if (response.ok) {
            const res = await response.json();
            outputEl.innerHTML += `<p class="success-msg">[SUCCESS] Completed bulk promotion: Promoted: ${res.promoted} | Queued: ${res.queued} | Rejected: ${res.rejected}</p>`;
            loadOverviewStats();
        } else {
            outputEl.innerHTML += `<p class="err-msg">[ERROR] Failed promoting pipeline records.</p>`;
        }
    } catch (err) {
        console.error("Failed pipeline push:", err);
        outputEl.innerHTML += `<p class="err-msg">[ERROR] Connection failed.</p>`;
    }
    outputEl.scrollTop = outputEl.scrollHeight;
}

// ── ANOMALY & ALERT SIGNALS ──

async function loadAnomaliesTab() {
    const listEl = document.getElementById('anomalies-alerts-list');
    listEl.innerHTML = '<div class="loading-cell">Loading active alerts...</div>';
    
    // Fetch cached or in-progress anomalies from backend
    loadDetectedAnomalies(false);
    
    try {
        const response = await fetch(`${API_URL}/anomalies/alerts`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        if (response.ok) {
            const alerts = await response.json();
            state.anomalyAlerts = alerts;
            
            if (alerts.length === 0) {
                listEl.innerHTML = '<div class="loading-cell text-success">🟢 No critical macro signals detected.</div>';
                return;
            }
            
            listEl.innerHTML = alerts.map(a => {
                const color = a.type === 'CRITICAL' ? '🔴' : '🟠';
                return `<div style="padding:10px 14px;border-bottom:1px solid rgba(255,255,255,0.05);font-size:13px;">
                    <strong>${color} ${escHtml(new Date(a.timestamp).toLocaleString())}</strong> — ${escHtml(a.reason)}
                </div>`;
            }).join('');
        } else {
            listEl.innerHTML = '<div class="loading-cell text-danger">Failed to load system alerts.</div>';
        }
    } catch (err) {
        console.error("Alerts load failed:", err);
        listEl.innerHTML = '<div class="loading-cell text-danger">Connection error.</div>';
    }
}

async function loadDetectedAnomalies(force = false) {
    const heatmapEl = document.getElementById('anomaly-heatmap');
    const statusText = document.getElementById('anomalies-status-text');

    if (!heatmapEl.dataset.populated) {
        heatmapEl.innerHTML = '<div class="heatmap-loading">Loading anomalies…</div>';
    }

    try {
        const url = force ? `${API_URL}/anomalies/detect?force=true` : `${API_URL}/anomalies/detect`;
        const response = await fetch(url, { headers: { 'Authorization': `Bearer ${state.token}` } });
        if (response.ok) {
            const anomalies = await response.json();
            state.detectedAnomalies = anomalies;

            const isCalculating = response.headers.get('X-Is-Calculating') === 'true';
            const lastCalc = response.headers.get('X-Last-Calculated');

            if (anomalies.length === 0 && isCalculating) {
                heatmapEl.innerHTML = '<div class="heatmap-loading">Prophet model running — results will appear shortly…</div>';
                delete heatmapEl.dataset.populated;
            } else if (anomalies.length === 0) {
                heatmapEl.innerHTML = '<div class="heatmap-loading text-success">No anomalies detected inside Prophet confidence boundaries.</div>';
                heatmapEl.dataset.populated = '1';
            } else {
                renderAnomalyHeatmap(anomalies, heatmapEl);
                heatmapEl.dataset.populated = '1';
            }

            if (statusText) {
                if (isCalculating) {
                    statusText.textContent = 'Recalculating in background…';
                    statusText.classList.remove('hidden');
                } else {
                    const when = lastCalc ? new Date(lastCalc).toLocaleString() : null;
                    statusText.textContent = when ? `Updated: ${when}` : '';
                    statusText.classList.toggle('hidden', !when);
                }
            }

            if (isCalculating) {
                if (!state.anomalyPollInterval) {
                    state.anomalyPollInterval = setInterval(() => loadDetectedAnomalies(false), 4000);
                }
            } else if (state.anomalyPollInterval) {
                clearInterval(state.anomalyPollInterval);
                state.anomalyPollInterval = null;
            }
        } else {
            heatmapEl.innerHTML = '<div class="heatmap-loading text-danger">Failed to load anomalies.</div>';
        }
    } catch (err) {
        console.error('Anomaly query failed:', err);
        heatmapEl.innerHTML = '<div class="heatmap-loading text-danger">Connection lost.</div>';
    }
}

function renderAnomalyHeatmap(anomalies, container) {
    // Short display names for indicator codes
    const IND_LABELS = {
        CPI_INFLATION:              'CPI Inflation',
        GDP_GROWTH:                 'GDP Growth',
        GDP_CURRENT_USD:            'GDP (USD)',
        GOVT_EXPENDITURE_PCT_GDP:   'Govt Spending',
        EXPORTS_PCT_GDP:            'Exports / GDP',
        IMPORTS_PCT_GDP:            'Imports / GDP',
        UNEMPLOYMENT_RATE:          'Unemployment',
        CURRENT_ACCOUNT_PCT_GDP:    'Current Acct',
        POPULATION:                 'Population',
        POPULATION_GROWTH:          'Pop. Growth',
        FDI_NET_INFLOWS:            'FDI Inflows',
        INTEREST_RATE:              'Interest Rate',
        EXCHANGE_RATE:              'Exchange Rate',
        DEBT_PCT_GDP:               'Debt / GDP',
        GOVT_DEBT_PCT_GDP:          'Govt Debt / GDP',
        TRADE_BALANCE:              'Trade Balance',
    };
    const fmtInd = code => IND_LABELS[code] || code.replace(/_/g, ' ').toLowerCase()
        .replace(/\b(\w)/g, (_, c) => c.toUpperCase());

    // Build lookup: indicator → country → sorted anomaly entries (worst sigma first)
    const lookup = {};
    for (const a of anomalies) {
        if (!lookup[a.indicator_code]) lookup[a.indicator_code] = {};
        if (!lookup[a.indicator_code][a.country_code]) lookup[a.indicator_code][a.country_code] = [];
        lookup[a.indicator_code][a.country_code].push(a);
    }
    for (const ind in lookup) {
        for (const cc in lookup[ind]) {
            lookup[ind][cc].sort((a, b) => Math.abs(b.sigma) - Math.abs(a.sigma));
        }
    }

    // Only include indicators/countries that actually appear in the data
    const indicators = [...new Set(anomalies.map(a => a.indicator_code))].sort();
    const countries  = [...new Set(anomalies.map(a => a.country_code))].sort();

    // Sigma → background color (diverging: amber/red above, cyan/blue below)
    const sigmaColor = sigma => {
        const abs = Math.abs(sigma);
        if (abs < 0.5) return null;
        const t = Math.min(abs / 8, 1);
        const alpha = 0.22 + t * 0.72;
        if (sigma > 0) {
            const g = Math.round(170 * (1 - t * 0.9));
            return `rgba(255, ${g}, 35, ${alpha})`;
        } else {
            const g = Math.round(210 - t * 110);
            return `rgba(20, ${g}, 255, ${alpha})`;
        }
    };

    // Build grid HTML
    const cols = countries.length;
    let html = `<div class="heatmap-wrapper"><div class="heatmap-grid" style="grid-template-columns:150px repeat(${cols},minmax(38px,1fr))">`;

    // Header row
    html += `<div class="heatmap-corner"></div>`;
    html += countries.map(cc => `<div class="heatmap-col-header">${escHtml(cc)}</div>`).join('');

    // Data rows
    for (const ind of indicators) {
        html += `<div class="heatmap-row-header">${escHtml(fmtInd(ind))}</div>`;
        for (const cc of countries) {
            const entries = lookup[ind]?.[cc];
            if (!entries?.length) {
                html += `<div class="heatmap-cell heatmap-cell-empty"></div>`;
                continue;
            }
            const worst = entries[0];
            const sig   = worst.sigma ?? 0;
            const bg    = sigmaColor(sig) || 'rgba(255,255,255,0.04)';
            const sigStr = (sig >= 0 ? '+' : '') + sig.toFixed(1);
            const yr     = worst.date.slice(0, 4);
            const extra  = entries.length > 1 ? entries.length - 1 : 0;
            // Tooltip: all years for this cell
            const tip = entries.map(e => `${e.date.slice(0,4)}: ${e.sigma >= 0 ? '+' : ''}${e.sigma.toFixed(1)}s actual=${e.actual.toFixed(1)}`).join(' | ');
            html += `<div class="heatmap-cell" style="background:${bg}" title="${escHtml(tip)}">
                ${extra ? `<span class="heatmap-more">+${extra}</span>` : ''}
                <span class="heatmap-sigma">${escHtml(sigStr)}σ</span>
                <span class="heatmap-year">${escHtml(yr)}</span>
            </div>`;
        }
    }

    html += `</div>`; // .heatmap-grid

    // Legend
    html += `
    <div class="heatmap-legend">
        <div class="legend-scale">
            <div class="legend-bar" style="background:linear-gradient(to right,rgba(20,210,255,0.25),rgba(20,100,255,0.9))"></div>
            <div class="legend-labels"><span>−8σ</span><span>Below trend</span></div>
        </div>
        <div class="legend-zero">0σ</div>
        <div class="legend-scale">
            <div class="legend-bar" style="background:linear-gradient(to right,rgba(255,170,35,0.3),rgba(255,20,35,0.9))"></div>
            <div class="legend-labels"><span>Above trend</span><span>+8σ</span></div>
        </div>
    </div>`;

    html += `</div>`; // .heatmap-wrapper
    container.innerHTML = html;
}

// ── AUTONOMOUS RESEARCHER ──

async function runAutonomousResearcher(e) {
    e.preventDefault();
    const topic = document.getElementById('research-topic').value.trim();
    const consoleEl = document.getElementById('research-console');
    const outputEl = document.getElementById('research-console-output');
    const viewCard = document.getElementById('research-view-card');
    const btn = document.getElementById('research-btn');
    
    if (!topic) return;
    
    btn.disabled = true;
    consoleEl.classList.remove('hidden');
    viewCard.classList.add('hidden');
    outputEl.innerHTML = `<p class="sys-msg">[SYSTEM] Starting research lead agent for: ${topic}</p>`;
    outputEl.innerHTML += `<p class="sys-msg">[SYSTEM] Searching web index databases (DuckDuckGo)...</p>`;
    
    try {
        const response = await fetch(`${API_URL}/researcher/compile`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${state.token}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ topic: topic })
        });
        
        if (response.ok) {
            const data = await response.json();
            state.compiledReport = data;
            
            outputEl.innerHTML += `<p class="sys-msg">[SYSTEM] Synthesizing investment report with Gemini...</p>`;
            outputEl.innerHTML += `<p class="success-msg">[SUCCESS] Complete. Generated report PDF: ${data.pdf_filename}</p>`;
            outputEl.scrollTop = outputEl.scrollHeight;
            
            document.getElementById('research-title').textContent = data.topic;
            document.getElementById('research-metadata').textContent = `Brain Model: ${data.model} | Compiled: ${new Date(data.generated_at).toLocaleString()}`;
            
            // Escape HTML first, then apply markdown substitutions on safe text only
            let html = escHtml(data.content);
            html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
            html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
            html = html.replace(/^### (.*)$/gm, '<h3>$1</h3>');
            html = html.replace(/^## (.*)$/gm, '<h2>$1</h2>');
            html = html.replace(/^# (.*)$/gm, '<h1>$1</h1>');
            html = html.replace(/^&gt; (.*)$/gm, '<blockquote>$1</blockquote>');
            html = html.replace(/^- (.*)$/gm, '<li>$1</li>');
            html = html.replace(/\n\n/g, '</p><p>');
            html = html.replace(/\n/g, '<br>');
            html = `<p>${html}</p>`;
            document.getElementById('research-body').innerHTML = html;
            
            viewCard.classList.remove('hidden');
            viewCard.scrollIntoView({ behavior: 'smooth' });
        } else {
            outputEl.innerHTML += `<p class="err-msg">[ERROR] Report compiler encountered a failure.</p>`;
        }
    } catch (err) {
        console.error("Researcher failed:", err);
        outputEl.innerHTML += `<p class="err-msg">[ERROR] Connection failed.</p>`;
    } finally {
        btn.disabled = false;
    }
    outputEl.scrollTop = outputEl.scrollHeight;
}

async function downloadCompiledPDF() {
    if (!state.compiledReport || !state.compiledReport.pdf_filename) {
        alert("No compiled PDF available.");
        return;
    }
    
    const filename = state.compiledReport.pdf_filename;
    try {
        const response = await fetch(`${API_URL}/researcher/download-pdf?filename=${encodeURIComponent(filename)}`, {
            headers: { 'Authorization': `Bearer ${state.token}` }
        });
        
        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            a.remove();
        } else {
            alert("Failed to download PDF.");
        }
    } catch (err) {
        console.error("PDF download failed:", err);
        alert("Download connection failed.");
    }
}
