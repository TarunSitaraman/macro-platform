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

// Renders assistant message content safely using markdown parser.
function renderMsgContent(el, text) {
    renderMarkdownToElement(el, text);
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

    // Sidebar collapse toggle
    document.getElementById('sidebar-collapse-btn').addEventListener('click', () => {
        const sidebar = document.getElementById('sidebar');
        const collapsed = sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0');
    });
    if (localStorage.getItem('sidebarCollapsed') === '1') {
        document.getElementById('sidebar').classList.add('collapsed');
    }
    
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
    document.getElementById('anomaly-filter-clear').addEventListener('click', () => {
        _lv.activeIndicator = null;
        if (state.detectedAnomalies) renderLinkedViews(state.detectedAnomalies);
    });

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

// ── ORCHESTRATION STATUS UTILITIES ──
function writeToConsole(message, type = 'sys') {
    const statusEl = document.getElementById('orchestrator-status');
    const resultEl = document.getElementById('orchestrator-result');
    if (!statusEl || !resultEl) return;

    if (type === 'success' || type === 'error') {
        statusEl.classList.add('hidden');
        resultEl.classList.remove('hidden');
        resultEl.className = `orchestrator-result ${type === 'error' ? 'orch-error' : 'orch-success'}`;
        const icon = type === 'error' ? 'x-circle' : 'check-circle';
        resultEl.innerHTML = `<i data-lucide="${icon}"></i><span>${message}</span>`;
        if (window.lucide) window.lucide.createIcons();
    } else {
        statusEl.classList.remove('hidden');
        resultEl.classList.add('hidden');
        const textEl = document.getElementById('orchestrator-status-text');
        if (textEl) textEl.textContent = message.replace(/&rarr;/g, '→').replace(/<[^>]+>/g, '');
        if (window.lucide) window.lucide.createIcons();
    }
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
            pointBorderColor: '#0d0d12',
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
            <div style="display:flex;align-items:center;gap:8px">
                <span class="exp-chart-unit">${escHtml(unit)}</span>
                <button class="exp-expand-btn" title="Expand chart">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                </button>
            </div>
        </div>
        <div class="exp-canvas-wrap"><canvas id="canvas-${escHtml(indCode)}"></canvas></div>
    `;
    // Bind expand handler via closure — never embed dynamic values in onclick attributes
    card.querySelector('.exp-expand-btn').addEventListener('click', () => openChartModal(indCode, indName, unit));

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
                        boxWidth: 10,
                        padding: 14,
                        color: '#c4cfd9',
                        font: { family: "'Plus Jakarta Sans'", size: 12 }
                    }
                },
                tooltip: {
                    backgroundColor: '#FAF5EC',
                    titleColor: '#1C1510',
                    bodyColor: '#3a2e24',
                    borderColor: 'rgba(196,98,58,0.25)',
                    borderWidth: 1,
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString(undefined, {maximumFractionDigits: 2}) : 'N/A'} ${unit}`
                    }
                }
            },
            scales: {
                x: {
                    grid: { color: 'rgba(0,0,0,0.06)' },
                    ticks: { color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 11 } }
                },
                y: {
                    grid: { color: 'rgba(0,0,0,0.06)' },
                    ticks: { color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 11 } },
                    title: { display: !!unit, text: unit, color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 11 } }
                }
            }
        }
    });
    state.chartInstances.push(instance);
}

function openChartModal(indCode, indName, unit) {
    // Rebuild chart config from cached data
    const allRecs = state.goldDataCache[indCode] || [];
    const recs = state.selectedCountries.length > 0
        ? allRecs.filter(r => state.selectedCountries.includes(r.country_code))
        : allRecs;

    const byCountry = {};
    recs.forEach(r => {
        const year = r.period.slice(0, 4);
        if (!byCountry[r.country_code]) byCountry[r.country_code] = {};
        if (!byCountry[r.country_code][year]) byCountry[r.country_code][year] = [];
        byCountry[r.country_code][year].push(r.value);
    });
    const allYears = [...new Set(recs.map(r => r.period.slice(0, 4)))].sort();
    const datasets = Object.keys(byCountry).map((cty, i) => {
        const color = EXPLORER_COLORS[i % EXPLORER_COLORS.length];
        const values = allYears.map(yr => {
            const vals = byCountry[cty][yr];
            return vals && vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
        });
        return { label: cty, data: values, borderColor: color, backgroundColor: color + '18',
                 borderWidth: 2.5, pointBackgroundColor: color, pointBorderColor: '#0d0d12',
                 pointRadius: 4, pointHoverRadius: 6, tension: 0.35, spanGaps: true };
    });

    // Create modal DOM
    const backdrop = document.createElement('div');
    backdrop.className = 'chart-modal-backdrop';
    backdrop.innerHTML = `
        <div class="chart-modal">
            <div class="chart-modal-header">
                <span class="chart-modal-title">${escHtml(indName)}</span>
                <button class="chart-modal-close" title="Close">✕</button>
            </div>
            <div class="chart-modal-body">
                <canvas id="modal-canvas"></canvas>
            </div>
        </div>
    `;
    document.body.appendChild(backdrop);

    const close = () => { if (modalChart) modalChart.destroy(); backdrop.remove(); };
    backdrop.querySelector('.chart-modal-close').addEventListener('click', close);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) close(); });
    document.addEventListener('keydown', function esc(e) { if (e.key === 'Escape') { close(); document.removeEventListener('keydown', esc); } });

    const ctx = document.getElementById('modal-canvas').getContext('2d');
    const modalChart = new Chart(ctx, {
        type: 'line',
        data: { labels: allYears, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: { display: true, position: 'bottom', labels: { boxWidth: 10, padding: 16, color: '#6a5a48', font: { family: 'Inter', size: 12 } } },
                tooltip: { backgroundColor: '#FAF5EC', titleColor: '#1C1510', bodyColor: '#3a2e24', borderColor: 'rgba(196,98,58,0.25)', borderWidth: 1, padding: 12, cornerRadius: 8,
                    callbacks: { label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y != null ? ctx.parsed.y.toLocaleString(undefined, {maximumFractionDigits: 2}) : 'N/A'} ${unit}` } }
            },
            scales: {
                x: { grid: { color: 'rgba(0,0,0,0.06)' }, ticks: { color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 12 } } },
                y: { grid: { color: 'rgba(0,0,0,0.06)' }, ticks: { color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 12 } },
                     title: { display: !!unit, text: unit, color: '#6a5a48', font: { family: "'Plus Jakarta Sans'", size: 12 } } }
            }
        }
    });
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

function renderMarkdownToElement(element, content) {
    if (!element) return;
    
    let rawContent = content || '';
    
    // ── PRE-PROCESS: Extract mermaid blocks BEFORE marked.js parsing ──
    // LLMs sometimes output 3 or 4 backticks. We normalize all mermaid fenced
    // blocks into placeholders, then restore them after marked parsing so they
    // don't get mangled by the markdown parser.
    const mermaidStore = [];
    // Match ```mermaid or ````mermaid (3-4+ backticks) blocks
    rawContent = rawContent.replace(/`{3,}\s*mermaid\s*\n([\s\S]*?)\n`{3,}/gi, (_match, code) => {
        const idx = mermaidStore.length;
        mermaidStore.push(code.trim());
        return `\n<div class="mermaid-placeholder" data-mermaid-idx="${idx}"></div>\n`;
    });
    
    // Parse markdown to HTML using marked.js
    let rawHtml = marked.parse(rawContent);
    
    element.innerHTML = rawHtml;
    
    // Restore mermaid blocks from placeholders
    element.querySelectorAll('.mermaid-placeholder').forEach(ph => {
        const idx = parseInt(ph.getAttribute('data-mermaid-idx'), 10);
        if (idx >= 0 && idx < mermaidStore.length) {
            const div = document.createElement('div');
            div.className = 'mermaid';
            div.id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
            div.textContent = sanitizeMermaidCode(mermaidStore[idx]);
            ph.replaceWith(div);
        }
    });
    
    // Also catch any mermaid code blocks that marked.js DID parse normally
    element.querySelectorAll('pre code.language-mermaid').forEach(block => {
        const div = document.createElement('div');
        div.className = 'mermaid';
        div.id = 'mermaid-' + Math.random().toString(36).substr(2, 9);
        div.textContent = sanitizeMermaidCode(block.textContent);
        block.parentElement.replaceWith(div);
    });
    
    // Safely process [text](Source: XYZ) which marked parsed as <a href="Source: XYZ">text</a>
    element.querySelectorAll('a').forEach(a => {
        let href = a.getAttribute('href') || '';
        if (href.trim().toLowerCase().startsWith('source:')) {
            let sourceText = href.trim().substring(7).trim();
            let text = a.innerHTML;
            let span = document.createElement('span');
            span.className = 'source-tooltip-container';
            span.innerHTML = `${text}<span class="source-tooltip">${sourceText}</span>`;
            a.replaceWith(span);
        }
    });

    // Safely process standalone [Source: XYZ] in text nodes (excluding code/pre)
    const walk = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, null, false);
    let node;
    const textNodes = [];
    while ((node = walk.nextNode())) {
        if (node.parentNode && !node.parentNode.closest('pre, code, .mermaid')) {
            if (/\[Source:\s*([^\]]+)\]/i.test(node.nodeValue)) {
                textNodes.push(node);
            }
        }
    }
    
    textNodes.forEach(textNode => {
        const regex = /\[Source:\s*([^\]]+)\]/gi;
        const fragment = document.createDocumentFragment();
        let lastIndex = 0;
        let match;
        const text = textNode.nodeValue;
        
        while ((match = regex.exec(text)) !== null) {
            if (match.index > lastIndex) {
                fragment.appendChild(document.createTextNode(text.substring(lastIndex, match.index)));
            }
            const span = document.createElement('span');
            span.className = 'source-tooltip-container source-icon-only';
            span.innerHTML = `<i data-lucide="info" style="width:14px;height:14px;color:var(--text-muted);vertical-align:middle;margin-left:4px;cursor:help;"></i><span class="source-tooltip">${match[1]}</span>`;
            fragment.appendChild(span);
            lastIndex = regex.lastIndex;
        }
        if (lastIndex < text.length) {
            fragment.appendChild(document.createTextNode(text.substring(lastIndex)));
        }
        textNode.parentNode.replaceChild(fragment, textNode);
    });

    // Add classes to tables
    const tables = element.querySelectorAll('table');
    tables.forEach(t => t.classList.add('data-table'));

    // Process blockquotes to detect and style GitHub alerts
    const blockquotes = element.querySelectorAll('blockquote');
    blockquotes.forEach(bq => {
        const firstP = bq.querySelector('p');
        if (firstP) {
            const htmlContent = firstP.innerHTML.trim();
            const alertMatch = htmlContent.match(/^\[!(IMPORTANT|WARNING|NOTE|TIP|CAUTION)\]\s*(?:<br>)?([\s\S]*)$/i);
            if (alertMatch) {
                const type = alertMatch[1].toUpperCase();
                const remainder = alertMatch[2].trim();
                
                bq.className = `github-alert alert-${type.toLowerCase()}`;
                
                let iconName = 'info';
                if (type === 'IMPORTANT') iconName = 'alert-circle';
                else if (type === 'WARNING') iconName = 'alert-triangle';
                else if (type === 'CAUTION') iconName = 'alert-octagon';
                else if (type === 'TIP') iconName = 'lightbulb';
                
                if (remainder) {
                    firstP.innerHTML = remainder;
                } else {
                    firstP.remove();
                }
                
                const innerHtml = bq.innerHTML;
                bq.innerHTML = `
                    <div class="alert-header">
                        <i data-lucide="${iconName}"></i>
                        <span>${type}</span>
                    </div>
                    <div class="alert-content">
                        ${innerHtml}
                    </div>
                `;
            }
        }
    });

    // ── RENDER MERMAID DIAGRAMS ──
    // Render each diagram individually so one bad diagram doesn't break others
    if (window.mermaid) {
        mermaid.initialize({
            theme: 'dark',
            securityLevel: 'loose',
            fontFamily: "'Plus Jakarta Sans', sans-serif",
        });
        
        const mermaidNodes = element.querySelectorAll('.mermaid');
        if (mermaidNodes.length > 0) {
            renderMermaidDiagrams(mermaidNodes);
        }
    }
    
    // Initialize Lucide icons dynamically added
    initLucide();
}

/**
 * Sanitize LLM-generated mermaid code to fix common syntax issues.
 */
function sanitizeMermaidCode(code) {
    let sanitized = code;
    
    // Remove [Source: ...] citations that LLMs sometimes insert
    sanitized = sanitized.replace(/\[Source:\s*[^\]]*\]/gi, '');
    
    // Remove empty lines that might break the graph definition
    sanitized = sanitized.replace(/\n{3,}/g, '\n\n');
    
    // Trim trailing whitespace from each line
    sanitized = sanitized.split('\n').map(l => l.trimEnd()).join('\n');
    
    return sanitized.trim();
}

/**
 * Render each mermaid diagram individually with error handling.
 * If a diagram fails, show a styled fallback instead of the raw mermaid error.
 */
async function renderMermaidDiagrams(nodes) {
    for (const node of nodes) {
        const code = node.textContent;
        try {
            const { svg } = await mermaid.render(node.id + '-svg', code);
            node.innerHTML = svg;
        } catch (e) {
            console.warn('Mermaid diagram failed to render:', e.message || e);
            // Show a graceful fallback with the raw code
            node.innerHTML = '';
            node.classList.add('mermaid-error');
            const header = document.createElement('div');
            header.className = 'mermaid-error-header';
            header.innerHTML = '<i data-lucide="alert-triangle" style="width:14px;height:14px;"></i> Diagram could not be rendered';
            const pre = document.createElement('pre');
            pre.textContent = code;
            node.appendChild(header);
            node.appendChild(pre);
        }
    }
}

function displayReportCard(data) {
    const card = document.getElementById('report-view-card');
    document.getElementById('report-title').textContent = `${data.country_code} Ingestion Brief: ${data.summary_type.replace(/_/g, ' ')}`;
    document.getElementById('report-metadata').textContent = `Generated by ${data.model_used} on ${new Date(data.generated_at).toLocaleString()}`;
    
    // Render Markdown beautifully
    renderMarkdownToElement(document.getElementById('report-body'), data.content);
    
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
    // Placeholder for loading state initialization
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
                return `<div style="padding:10px 14px;border-bottom:1px solid rgba(0,0,0,0.06);font-size:13px;">
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
    const stripEl   = document.getElementById('anomaly-strip-panel');
    const rankedEl  = document.getElementById('anomaly-ranked-panel');
    const statusText = document.getElementById('anomalies-status-text');

    if (stripEl && !stripEl.dataset.populated) {
        stripEl.innerHTML = '<div class="strip-loading">Loading anomalies…</div>';
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
                if (stripEl) stripEl.innerHTML = '<div class="strip-loading">Prophet model running — results will appear shortly…</div>';
                if (stripEl) delete stripEl.dataset.populated;
            } else if (anomalies.length === 0) {
                if (stripEl) { stripEl.innerHTML = '<div class="strip-loading text-success">No anomalies detected inside Prophet confidence boundaries.</div>'; stripEl.dataset.populated = '1'; }
            } else {
                renderLinkedViews(anomalies);
                updateSidebarTicker(anomalies);
                if (stripEl) stripEl.dataset.populated = '1';
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
            if (stripEl) stripEl.innerHTML = '<div class="strip-loading text-danger">Failed to load anomalies.</div>';
        }
    } catch (err) {
        console.error('Anomaly query failed:', err);
        if (document.getElementById('anomaly-strip-panel')) {
            document.getElementById('anomaly-strip-panel').innerHTML = '<div class="strip-loading text-danger">Connection lost.</div>';
        }
    }
}

// ── Linked Views state ──
const _lv = { activeIndicator: null };

function renderLinkedViews(anomalies) {
    const IND_LABELS = {
        CPI_INFLATION:              'CPI Inflation',
        GDP_GROWTH:                 'GDP Growth',
        GDP_CURRENT_USD:            'GDP (USD)',
        GOVT_EXPENDITURE_PCT_GDP:   'Govt Spending',
        EXPORTS_PCT_GDP:            'Exports / GDP',
        IMPORTS_PCT_GDP:            'Imports / GDP',
        UNEMPLOYMENT_RATE:          'Unemployment',
        CURRENT_ACCOUNT_PCT_GDP:    'Current Account',
        POPULATION:                 'Population',
        POPULATION_GROWTH:          'Pop Growth',
        FDI_NET_INFLOWS:            'FDI Inflows',
        INTEREST_RATE:              'Interest Rate',
        EXCHANGE_RATE:              'Exchange Rate',
        DEBT_PCT_GDP:               'Debt / GDP',
        GOVT_DEBT_PCT_GDP:          'Govt Debt',
        TRADE_BALANCE:              'Trade Balance',
    };
    const fmtInd = code => IND_LABELS[code] || code.replace(/_/g, ' ').replace(/\b(\w)/g, (_, c) => c.toUpperCase());

    // Build lookup: indicator → country → worst-sigma entry
    const lookup = {};
    for (const a of anomalies) {
        const ind = a.indicator_code, cc = a.country_code;
        if (!lookup[ind]) lookup[ind] = {};
        if (!lookup[ind][cc] || Math.abs(a.sigma) > Math.abs(lookup[ind][cc].sigma)) lookup[ind][cc] = a;
    }

    // Sort indicators by coverage
    const indicators = Object.keys(lookup).sort((a, b) => Object.keys(lookup[b]).length - Object.keys(lookup[a]).length);

    // σ axis range: -7 to +7
    const AXIS_MIN = -7, AXIS_MAX = 7;
    const pct = s => ((Math.max(AXIS_MIN, Math.min(AXIS_MAX, s)) - AXIS_MIN) / (AXIS_MAX - AXIS_MIN) * 100).toFixed(1);
    const dotClass = s => Math.abs(s) < 0.5 ? 'dot-neu' : s > 0 ? 'dot-hi' : 'dot-lo';

    // Build strip panel
    const stripEl = document.getElementById('anomaly-strip-panel');
    if (!stripEl) return;
    let sHtml = '';
    for (const ind of indicators) {
        const isActive = _lv.activeIndicator === ind;
        sHtml += `<div class="strip-row${isActive ? ' strip-active' : ''}" data-ind="${escHtml(ind)}">
            <div class="strip-label" title="${escHtml(fmtInd(ind))}">${escHtml(fmtInd(ind))}</div>
            <div class="strip-axis">`;

        for (const [cc, entry] of Object.entries(lookup[ind])) {
            const sig = entry.sigma ?? 0;
            const sigStr = (sig >= 0 ? '+' : '') + sig.toFixed(1);
            const yr = (entry.date || '').slice(0, 4);
            const tip = `${cc}: ${sigStr}σ (${yr})`;
            sHtml += `<div class="strip-dot ${escHtml(dotClass(sig))}"
                style="left:${pct(sig)}%"
                title="${escHtml(tip)}"
                data-ind="${escHtml(ind)}"
                data-cc="${escHtml(cc)}"></div>`;
        }

        sHtml += `<div class="strip-axis-labels"><span>−7σ</span><span>0</span><span>+7σ</span></div>
            </div>
        </div>`;
    }
    stripEl.innerHTML = sHtml;

    // Attach dot click handlers
    stripEl.querySelectorAll('.strip-dot').forEach(dot => {
        dot.addEventListener('click', e => {
            e.stopPropagation();
            const ind = dot.dataset.ind;
            _lv.activeIndicator = _lv.activeIndicator === ind ? null : ind;
            renderLinkedViews(anomalies);
        });
    });

    // Strip row click (entire row selects that indicator)
    stripEl.querySelectorAll('.strip-row').forEach(row => {
        row.addEventListener('click', () => {
            const ind = row.dataset.ind;
            _lv.activeIndicator = _lv.activeIndicator === ind ? null : ind;
            renderLinkedViews(anomalies);
        });
    });

    // Filter bar
    const filterBar = document.getElementById('anomaly-filter-bar');
    const filterLabel = document.getElementById('anomaly-filter-label');
    if (filterBar && filterLabel) {
        if (_lv.activeIndicator) {
            filterLabel.textContent = `Filtered: ${fmtInd(_lv.activeIndicator)}`;
            filterBar.classList.remove('hidden');
        } else {
            filterBar.classList.add('hidden');
        }
    }

    // Ranked table — build rows from filtered anomalies
    const rankedEl = document.getElementById('anomaly-ranked-panel');
    if (!rankedEl) return;

    let rows = [];
    for (const ind of indicators) {
        if (_lv.activeIndicator && ind !== _lv.activeIndicator) continue;
        for (const [cc, entry] of Object.entries(lookup[ind])) {
            rows.push({ ind, cc, entry, absSigma: Math.abs(entry.sigma ?? 0) });
        }
    }
    rows.sort((a, b) => b.absSigma - a.absSigma);

    const MAX_SIGMA = 7;
    let tHtml = `<table class="ranked-table">
        <thead><tr>
            <th>Country</th>
            <th>Indicator</th>
            <th>σ</th>
            <th class="ranked-bar-cell">Deviation</th>
            <th>Year</th>
        </tr></thead><tbody>`;

    for (const { ind, cc, entry } of rows) {
        const sig = entry.sigma ?? 0;
        const sigStr = (sig >= 0 ? '+' : '') + sig.toFixed(2);
        const yr = (entry.date || '').slice(0, 4);
        const barPct = Math.min(Math.abs(sig) / MAX_SIGMA * 50, 50).toFixed(1);
        const dir = sig >= 0 ? 'hi' : 'lo';
        tHtml += `<tr>
            <td><span class="ranked-country">${escHtml(cc)}</span></td>
            <td><span class="ranked-indicator">${escHtml(fmtInd(ind))}</span></td>
            <td><span class="ranked-sigma ${dir}">${escHtml(sigStr)}σ</span></td>
            <td class="ranked-bar-cell">
                <div class="ranked-bar-wrap">
                    <div class="ranked-bar-fill ${dir}" style="width:${barPct}%"></div>
                </div>
            </td>
            <td><span class="ranked-year">${escHtml(yr)}</span></td>
        </tr>`;
    }
    tHtml += '</tbody></table>';
    rankedEl.innerHTML = tHtml;
}

// Keep old name as alias for any remaining callers
function renderAnomalyHeatmap(anomalies) { renderLinkedViews(anomalies); }

function updateSidebarTicker(anomalies) {
    const el = document.getElementById('ticker-content');
    if (!el || !anomalies || anomalies.length === 0) return;
    const top = [...anomalies].sort((a, b) => Math.abs(b.sigma) - Math.abs(a.sigma)).slice(0, 4);
    el.innerHTML = top.map(a => {
        const sig = (a.sigma >= 0 ? '+' : '') + a.sigma.toFixed(1);
        const cls = a.sigma > 0 ? 'color:#C4623A' : 'color:#5A7A38';
        return `<span style="${cls};font-weight:700">${escHtml(sig)}σ</span> <span style="color:#5a4a38">${escHtml(a.country_code)}·${escHtml(a.indicator_code.slice(0,8))}</span>`;
    }).join('<br>');
}

// ── AUTONOMOUS RESEARCHER ──

async function runAutonomousResearcher(e) {
    e.preventDefault();
    const topic = document.getElementById('research-topic').value.trim();
    const viewCard = document.getElementById('research-view-card');
    const chartsCard = document.getElementById('research-charts-card');
    const btn = document.getElementById('research-btn');

    if (!topic) return;

    btn.disabled = true;
    btn.innerHTML = '<i data-lucide="loader-2"></i><span>Researching…</span>';
    if (window.lucide) window.lucide.createIcons();
    viewCard.classList.add('hidden');
    chartsCard.classList.add('hidden');

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

            document.getElementById('research-title').textContent = data.topic;
            document.getElementById('research-metadata').textContent = `${data.model} · ${new Date(data.generated_at).toLocaleString()}`;

            // Render Markdown beautifully
            renderMarkdownToElement(document.getElementById('research-body'), data.content);

            viewCard.classList.remove('hidden');
            viewCard.scrollIntoView({ behavior: 'smooth' });

            // Load supporting data charts
            loadResearchCharts(['GDP_GROWTH', 'CPI_INFLATION']);
        } else {
            alert('Report compilation failed. Please try again.');
        }
    } catch (err) {
        console.error("Researcher failed:", err);
        alert('Connection failed. Please check the API server.');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i data-lucide="send"></i><span>Start Research</span>';
        if (window.lucide) window.lucide.createIcons();
    }
}

async function loadResearchCharts(indicatorCodes) {
    const chartsCard = document.getElementById('research-charts-card');
    const grid = document.getElementById('research-charts-grid');
    if (!chartsCard || !grid) return;

    const G7 = ['USA', 'GBR', 'DEU', 'FRA', 'JPN', 'CAN', 'ITA'];
    const COLORS = ['#C4623A','#5A7A38','#C4823A','#8B6B4A','#A04020','#7A9E5A','#9A6A2A'];
    const IND_LABELS = { GDP_GROWTH: 'GDP Growth Rate (%)', CPI_INFLATION: 'CPI Inflation (%)' };

    chartsCard.classList.remove('hidden');
    grid.innerHTML = indicatorCodes.map(c => `
        <div class="research-chart-card glass-panel-nested">
            <div class="research-chart-title">${IND_LABELS[c] || c}</div>
            <div class="research-chart-wrap"><canvas id="rc-canvas-${c}"></canvas></div>
        </div>`).join('');

    for (const indCode of indicatorCodes) {
        try {
            const resp = await fetch(`${API_URL}/gold-data?limit=500&indicator=${indCode}&year_from=2015&actuals_only=true`, {
                headers: { 'Authorization': `Bearer ${state.token}` }
            });
            if (!resp.ok) continue;
            const records = await resp.json();

            // Group by country
            const byCountry = {};
            for (const r of records) {
                if (!G7.includes(r.country_code)) continue;
                if (!byCountry[r.country_code]) byCountry[r.country_code] = [];
                byCountry[r.country_code].push(r);
            }

            // Build sorted year labels
            const allYears = [...new Set(records.map(r => r.period))].sort();
            const datasets = G7
                .filter(cc => byCountry[cc]?.length)
                .map((cc, i) => {
                    const pts = byCountry[cc].sort((a, b) => a.period.localeCompare(b.period));
                    const dataMap = Object.fromEntries(pts.map(p => [p.period, p.standardised_value ?? p.raw_value]));
                    return {
                        label: cc,
                        data: allYears.map(yr => dataMap[yr] ?? null),
                        borderColor: COLORS[i % COLORS.length],
                        backgroundColor: COLORS[i % COLORS.length] + '18',
                        borderWidth: 1.5,
                        pointRadius: 2,
                        tension: 0.3,
                        spanGaps: true,
                    };
                });

            const canvas = document.getElementById(`rc-canvas-${indCode}`);
            if (!canvas) continue;
            new Chart(canvas, {
                type: 'line',
                data: { labels: allYears, datasets },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: { labels: { color: '#c4cfd9', font: { family: "'Plus Jakarta Sans'", size: 11 }, boxWidth: 12, padding: 12 } },
                        tooltip: { backgroundColor: '#1a1a28', titleColor: '#a5b4fc', bodyColor: '#c4cfd9' },
                    },
                    scales: {
                        x: { ticks: { color: '#6a5a48', font: { size: 10 } }, grid: { color: 'rgba(0,0,0,0.06)' } },
                        y: { ticks: { color: '#6a5a48', font: { size: 10 } }, grid: { color: 'rgba(0,0,0,0.06)' } },
                    },
                },
            });
        } catch (err) {
            console.error('Research chart load failed for', indCode, err);
        }
    }
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
