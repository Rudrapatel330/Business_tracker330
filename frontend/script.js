/**
 * Business Tracker — Frontend Logic
 * Handles search form, progress polling, results table, sorting, filtering, CSV export.
 */

(() => {
    'use strict';

    // ===== Configuration =====
    // ----------------------------------------------------------------------
    // DEPLOYMENT INSTRUCTIONS:
    // If hosting this frontend on Vercel and the backend on Hugging Face, 
    // change the API_BASE below to your Hugging Face Space URL.
    // Example: const API_BASE = 'https://username-spacename.hf.space/api';
    // ----------------------------------------------------------------------
    const API_BASE = window.API_BASE_URL || 'https://rudragamerz-business-tracker-api.hf.space/api';
    const POLL_INTERVAL = 1500; // ms

    // ===== DOM Elements =====
    const $ = (sel) => document.querySelector(sel);
    const searchForm = $('#search-form');
    const businessTypeInput = $('#business-type');
    const locationInput = $('#location');
    const btnScrape = $('#btn-scrape');
    const btnContent = btnScrape.querySelector('.btn-content');
    const btnLoader = btnScrape.querySelector('.btn-loader');

    const progressSection = $('#progress-section');
    const statFound = $('#stat-found');
    const statStatus = $('#stat-status');
    const statElapsed = $('#stat-elapsed');
    const progressFill = $('#progress-fill');
    const progressPercent = $('#progress-percent');
    const statusBadge = $('#status-badge');
    const btnCancel = $('#btn-cancel');

    const resultsSection = $('#results-section');
    const resultsTbody = $('#results-tbody');
    const resultsCount = $('#results-count');
    const tableSearch = $('#table-search');
    const emptyState = $('#empty-state');
    const tableContainer = $('#table-container');
    const btnDownload = $('#btn-download');
    const btnNewSearch = $('#btn-new-search');

    const errorToast = $('#error-toast');
    const toastMessage = $('#toast-message');
    const toastClose = $('#toast-close');
    const successToast = $('#success-toast');
    const successToastMessage = $('#success-toast-message');
    const successToastClose = $('#success-toast-close');

    // ===== State =====
    let currentJobId = null;
    let pollTimer = null;
    let elapsedTimer = null;
    let elapsedSeconds = 0;
    let allResults = [];
    let sortColumn = null;
    let sortAsc = true;

    // ===== Helpers =====
    function showToast(type, message, duration = 5000) {
        const toast = type === 'error' ? errorToast : successToast;
        const msgEl = type === 'error' ? toastMessage : successToastMessage;
        msgEl.textContent = message;
        toast.classList.remove('hidden');
        clearTimeout(toast._hideTimer);
        toast._hideTimer = setTimeout(() => toast.classList.add('hidden'), duration);
    }

    function hideToast(type) {
        const toast = type === 'error' ? errorToast : successToast;
        toast.classList.add('hidden');
    }

    function setLoading(loading) {
        if (loading) {
            btnContent.hidden = true;
            btnLoader.hidden = false;
            btnScrape.disabled = true;
        } else {
            btnContent.hidden = false;
            btnLoader.hidden = true;
            btnScrape.disabled = false;
        }
    }

    function showSection(section) {
        [progressSection, resultsSection].forEach(s => s.classList.add('hidden'));
        if (section) section.classList.remove('hidden');
    }

    function formatElapsed(secs) {
        if (secs < 60) return `${secs}s`;
        const m = Math.floor(secs / 60);
        const s = secs % 60;
        return `${m}m ${s}s`;
    }

    function escapeHtml(str) {
        if (!str) return '—';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function starsHtml(rating) {
        if (!rating) return '—';
        const r = parseFloat(rating);
        const full = Math.floor(r);
        const half = r - full >= 0.5;
        let s = '';
        for (let i = 0; i < full; i++) s += '★';
        if (half) s += '½';
        return `<span class="stars">${s}</span>${r.toFixed(1)}`;
    }

    // ===== Start Elapsed Timer =====
    function startElapsedTimer() {
        elapsedSeconds = 0;
        statElapsed.textContent = '0s';
        clearInterval(elapsedTimer);
        elapsedTimer = setInterval(() => {
            elapsedSeconds++;
            statElapsed.textContent = formatElapsed(elapsedSeconds);
        }, 1000);
    }

    function stopElapsedTimer() {
        clearInterval(elapsedTimer);
    }

    // ===== API Calls =====
    async function startScrape(businessType, location) {
        const res = await fetch(`${API_BASE}/scrape`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ business_type: businessType, location })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `Server error ${res.status}`);
        }
        return res.json();
    }

    async function fetchStatus(jobId) {
        const res = await fetch(`${API_BASE}/status/${jobId}`);
        if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
        return res.json();
    }

    function getDownloadUrl(jobId) {
        return `${API_BASE}/download/${jobId}`;
    }

    // ===== Poll for Progress =====
    function startPolling(jobId) {
        clearInterval(pollTimer);
        pollTimer = setInterval(async () => {
            try {
                const data = await fetchStatus(jobId);
                updateProgress(data);

                if (data.status === 'done' || data.status === 'completed') {
                    stopPolling();
                    stopElapsedTimer();
                    onScrapeComplete(data);
                } else if (data.status === 'error' || data.status === 'failed') {
                    stopPolling();
                    stopElapsedTimer();
                    onScrapeError(data);
                }
            } catch (err) {
                console.error('Poll error:', err);
                // Don't stop polling on transient errors
            }
        }, POLL_INTERVAL);
    }

    function stopPolling() {
        clearInterval(pollTimer);
        pollTimer = null;
    }

    function updateProgress(data) {
        const count = data.results?.length || 0;
        const current = data.progress || 0;
        const total = data.total || 0;

        statFound.textContent = count;

        // Build a meaningful status message from the data
        if (data.status === 'pending') {
            statStatus.textContent = 'Waiting to start...';
        } else if (data.status === 'running') {
            if (total > 0 && current > 0) {
                statStatus.textContent = `Extracting business ${current} of ${total}...`;
            } else if (total > 0) {
                statStatus.textContent = `Found ${total} listings, preparing to extract...`;
            } else {
                statStatus.textContent = 'Searching and scrolling results...';
            }
        }

        // Calculate progress percentage
        let pct = 0;
        if (total > 0 && current > 0) {
            pct = Math.round((current / total) * 100);
        } else if (data.status === 'running') {
            pct = 10; // Show some activity
        }
        progressFill.style.width = `${Math.min(pct, 100)}%`;
        progressPercent.textContent = `${Math.min(pct, 100)}%`;
    }

    function onScrapeComplete(data) {
        statusBadge.textContent = 'Done';
        statusBadge.className = 'status-badge done';
        statStatus.textContent = 'Completed';
        progressFill.style.width = '100%';
        progressPercent.textContent = '100%';

        allResults = data.results || [];
        showToast('success', `Scraping complete! Found ${allResults.length} businesses.`);

        // Show results after a brief delay for UX
        setTimeout(() => {
            showSection(resultsSection);
            renderResults(allResults);
            setLoading(false);
        }, 800);
    }

    function onScrapeError(data) {
        statusBadge.textContent = 'Error';
        statusBadge.className = 'status-badge error';
        statStatus.textContent = data.error || 'Scraping failed';

        showToast('error', data.error || 'Scraping failed. Please try again.');
        setLoading(false);
    }

    // ===== Render Results Table =====
    function renderResults(results) {
        resultsTbody.innerHTML = '';
        resultsCount.textContent = `${results.length} business${results.length !== 1 ? 'es' : ''}`;

        if (results.length === 0) {
            emptyState.classList.remove('hidden');
            tableContainer.classList.add('hidden');
            return;
        }

        emptyState.classList.add('hidden');
        tableContainer.classList.remove('hidden');

        results.forEach((biz, i) => {
            const tr = document.createElement('tr');
            tr.style.animationDelay = `${i * 30}ms`;

            let websiteHref = biz.website || '';
            if (websiteHref && !/^https?:\/\//i.test(websiteHref)) {
                websiteHref = 'http://' + websiteHref;
            }

            const websiteCell = biz.website
                ? `<a href="${escapeHtml(websiteHref)}" target="_blank" rel="noopener">${truncate(biz.website, 30)}</a>`
                : '—';

            tr.innerHTML = `
                <td>${i + 1}</td>
                <td class="td-name">${escapeHtml(biz.name)}</td>
                <td class="td-phone">${escapeHtml(biz.phone)}</td>
                <td>${escapeHtml(biz.address)}</td>
                <td class="td-website">${websiteCell}</td>
                <td class="td-rating">${starsHtml(biz.rating)}</td>
                <td class="td-reviews">${biz.reviews ? Number(biz.reviews).toLocaleString() : '—'}</td>
                <td>${escapeHtml(biz.category)}</td>
                <td class="td-profile">
                    ${biz.profile_link ? `<a href="${escapeHtml(biz.profile_link)}" target="_blank" rel="noopener noreferrer">View Profile &nearr;</a>` : '—'}
                </td>
            `;
            resultsTbody.appendChild(tr);
        });
    }

    function truncate(str, len) {
        if (!str) return '';
        // Remove protocol for display
        let clean = str.replace(/^https?:\/\/(www\.)?/, '');
        if (clean.length > len) clean = clean.substring(0, len) + '…';
        return escapeHtml(clean);
    }

    // ===== Sorting =====
    function sortResults(column) {
        if (sortColumn === column) {
            sortAsc = !sortAsc;
        } else {
            sortColumn = column;
            sortAsc = true;
        }

        // Update header UI
        document.querySelectorAll('.results-table th').forEach(th => {
            th.classList.remove('sorted-asc', 'sorted-desc');
        });
        const activeHeader = document.querySelector(`.results-table th[data-column="${column}"]`);
        if (activeHeader) {
            activeHeader.classList.add(sortAsc ? 'sorted-asc' : 'sorted-desc');
        }

        const filtered = getFilteredResults();
        filtered.sort((a, b) => {
            let va = a[column] ?? '';
            let vb = b[column] ?? '';

            // Numeric columns
            if (column === 'rating' || column === 'reviews') {
                va = parseFloat(va) || 0;
                vb = parseFloat(vb) || 0;
                return sortAsc ? va - vb : vb - va;
            }

            // String columns
            va = String(va).toLowerCase();
            vb = String(vb).toLowerCase();
            if (va < vb) return sortAsc ? -1 : 1;
            if (va > vb) return sortAsc ? 1 : -1;
            return 0;
        });

        renderResults(filtered);
    }

    // ===== Filtering =====
    function getFilteredResults() {
        const query = tableSearch.value.toLowerCase().trim();
        if (!query) return [...allResults];
        return allResults.filter(biz => {
            return Object.values(biz).some(val =>
                val && String(val).toLowerCase().includes(query)
            );
        });
    }

    function applyFilter() {
        const filtered = getFilteredResults();
        if (sortColumn) {
            filtered.sort((a, b) => {
                let va = a[sortColumn] ?? '';
                let vb = b[sortColumn] ?? '';
                if (sortColumn === 'rating' || sortColumn === 'reviews') {
                    va = parseFloat(va) || 0;
                    vb = parseFloat(vb) || 0;
                    return sortAsc ? va - vb : vb - va;
                }
                va = String(va).toLowerCase();
                vb = String(vb).toLowerCase();
                if (va < vb) return sortAsc ? -1 : 1;
                if (va > vb) return sortAsc ? 1 : -1;
                return 0;
            });
        }
        renderResults(filtered);
    }

    // ===== CSV Export (client-side fallback) =====
    function exportCSV() {
        if (currentJobId) {
            // Try server-side download first
            window.open(getDownloadUrl(currentJobId), '_blank');
            return;
        }
        // Client-side fallback
        clientSideCSV();
    }

    function clientSideCSV() {
        if (allResults.length === 0) {
            showToast('error', 'No results to export.');
            return;
        }
        const headers = ['Name', 'Phone', 'Address', 'Website', 'Rating', 'Reviews', 'Category'];
        const keys = ['name', 'phone', 'address', 'website', 'rating', 'reviews', 'category'];
        const csvRows = [headers.join(',')];

        allResults.forEach(biz => {
            const row = keys.map(k => {
                let val = biz[k] || '';
                val = String(val).replace(/"/g, '""');
                return `"${val}"`;
            });
            csvRows.push(row.join(','));
        });

        const blob = new Blob([csvRows.join('\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `business_tracker_results.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        showToast('success', 'CSV downloaded!');
    }

    // ===== Reset UI =====
    function resetForNewSearch() {
        currentJobId = null;
        allResults = [];
        sortColumn = null;
        sortAsc = true;
        showSection(null);
        setLoading(false);
        businessTypeInput.value = '';
        locationInput.value = '';
        tableSearch.value = '';
        resultsTbody.innerHTML = '';
        statusBadge.textContent = 'Running';
        statusBadge.className = 'status-badge';
        progressFill.style.width = '0%';
        progressPercent.textContent = '0%';
        statFound.textContent = '0';
        statStatus.textContent = 'Initializing...';
        statElapsed.textContent = '0s';
        businessTypeInput.focus();
    }

    // ===== Event Listeners =====

    // Form submit
    searchForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const businessType = businessTypeInput.value.trim();
        const location = locationInput.value.trim();

        if (!businessType || !location) {
            showToast('error', 'Please fill in both fields.');
            return;
        }

        setLoading(true);
        showSection(progressSection);
        statusBadge.textContent = 'Running';
        statusBadge.className = 'status-badge';
        progressFill.style.width = '0%';
        progressPercent.textContent = '0%';
        statFound.textContent = '0';
        statStatus.textContent = 'Starting browser...';
        startElapsedTimer();

        try {
            const data = await startScrape(businessType, location);
            currentJobId = data.job_id;
            statStatus.textContent = 'Browser launched, navigating...';
            startPolling(currentJobId);
        } catch (err) {
            console.error('Start scrape error:', err);
            showToast('error', err.message || 'Failed to start scraping. Is the server running?');
            setLoading(false);
            stopElapsedTimer();
            showSection(null);
        }
    });

    // Cancel
    btnCancel.addEventListener('click', () => {
        stopPolling();
        stopElapsedTimer();
        showSection(null);
        setLoading(false);
        showToast('error', 'Scraping cancelled.');
    });

    // Download CSV
    btnDownload.addEventListener('click', exportCSV);

    // New search
    btnNewSearch.addEventListener('click', resetForNewSearch);

    // Sort columns
    document.querySelectorAll('.results-table th.sortable').forEach(th => {
        th.addEventListener('click', () => {
            const column = th.dataset.column;
            if (column) sortResults(column);
        });
    });

    // Filter
    let filterDebounce = null;
    tableSearch.addEventListener('input', () => {
        clearTimeout(filterDebounce);
        filterDebounce = setTimeout(applyFilter, 200);
    });

    // Toast close
    toastClose.addEventListener('click', () => hideToast('error'));
    successToastClose.addEventListener('click', () => hideToast('success'));

    // ===== Keyboard shortcut: Ctrl/Cmd + Enter to submit =====
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            if (!btnScrape.disabled) searchForm.requestSubmit();
        }
    });

    // Focus first input on load
    businessTypeInput.focus();

})();
