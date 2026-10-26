/**
 * Zero UI — Live Authority Dashboard
 * ==================================
 * Realtime console built on the backend WebSocket feed:
 *
 *   REST snapshot  → first paint (list + stats + server config)
 *   /ws/dashboard  → emergency.created / .updated / .ended / event.added /
 *                    audio.segment / live_audio.state / stats
 *
 * Selection is by emergency_id everywhere; the detail panel and the audio
 * controller are hard-scoped to the selected emergency so audio can never mix.
 */

(() => {
    // ── State ──
    const state = {
        emergencies: new Map(),        // emergency_id → summary
        selectedId: null,
        detail: null,                  // full detail of the selected emergency
        filter: 'all',
        staleAfter: 90,
        socket: null,
        timersStarted: false,
        serverConfig: {},
        realtime: {},
        soundEnabled: true,
        detailSignature: '',
    };

    // ── DOM ──
    const $ = (id) => document.getElementById(id);
    const dom = {
        loginOverlay: $('loginOverlay'), loginForm: $('loginForm'), loginToken: $('loginToken'),
        loginOperator: $('loginOperator'), loginError: $('loginError'), loginSubmit: $('loginSubmit'),
        logoutBtn: $('logoutBtn'),

        activeCountValue: $('activeCountValue'), cancelledCountValue: $('cancelledCountValue'),
        resolvedCountValue: $('resolvedCountValue'), staleCountValue: $('staleCountValue'),
        connectionBadge: $('connectionBadge'), connectionText: $('connectionText'),

        alarmBanner: $('alarmBanner'), alarmText: $('alarmText'), alarmOpen: $('alarmOpen'),
        alarmDismiss: $('alarmDismiss'),

        emergenciesList: $('emergenciesList'), listMeta: $('listMeta'),

        detailOverlay: $('detailOverlay'), detailStatusBadge: $('detailStatusBadge'),
        detailConnState: $('detailConnState'), detailUser: $('detailUser'), detailTime: $('detailTime'),
        detailEmergencyId: $('detailEmergencyId'), copyEmergencyId: $('copyEmergencyId'),
        detailTrigger: $('detailTrigger'), detailDigipin: $('detailDigipin'),
        detailCoords: $('detailCoords'), detailAccuracy: $('detailAccuracy'),
        detailDuration: $('detailDuration'), detailLastUpdate: $('detailLastUpdate'),
        detailDevice: $('detailDevice'), detailMapsLink: $('detailMapsLink'),
        detailTelemetry: $('detailTelemetry'),
        detailTimeline: $('detailTimeline'), locationHistory: $('locationHistory'),
        locationCount: $('locationCount'),
        segmentList: $('segmentList'), segmentSummary: $('segmentSummary'),
        audioPlayer: $('audioPlayer'), btnListenLive: $('btnListenLive'),
        btnStopLive: $('btnStopLive'), liveAudioState: $('liveAudioState'),
        liveAudioLevel: $('liveAudioLevel'), liveAudioHelp: $('liveAudioHelp'),
        resolveNote: $('resolveNote'), btnDetailResolve: $('btnDetailResolve'),
        detailClose: $('detailClose'), notifSound: $('notifSound'),
    };

    // ══════════════════════════════════════════════════════════════
    // Helpers
    // ══════════════════════════════════════════════════════════════
    const escapeHtml = (value) => {
        const div = document.createElement('div');
        div.textContent = value === null || value === undefined ? '' : String(value);
        return div.innerHTML;
    };

    function timeAgo(iso) {
        if (!iso) return '—';
        const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
        if (seconds < 5) return 'just now';
        if (seconds < 60) return `${seconds}s ago`;
        const minutes = Math.floor(seconds / 60);
        if (minutes < 60) return `${minutes}m ago`;
        const hours = Math.floor(minutes / 60);
        if (hours < 24) return `${hours}h ago`;
        return `${Math.floor(hours / 24)}d ago`;
    }

    const clockTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : '—');
    const fmtDuration = (seconds) => {
        if (seconds === null || seconds === undefined) return '—';
        const total = Math.max(0, Math.round(seconds));
        const mins = Math.floor(total / 60);
        const secs = total % 60;
        return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
    };
    const fmtBytes = (bytes) => {
        if (!bytes) return '0 KB';
        return bytes > 1024 * 1024 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
    };

    /** Local re-derivation of the connection state between server pushes. */
    function connectionState(emergency) {
        if (!emergency) return 'ended';
        if (emergency.status !== 'emergency' && emergency.status !== 'verifying') return 'ended';
        const last = new Date(emergency.last_update_at || emergency.created_at).getTime();
        return (Date.now() - last) / 1000 <= state.staleAfter ? 'live' : 'stale';
    }

    // ══════════════════════════════════════════════════════════════
    // Session / boot
    // ══════════════════════════════════════════════════════════════
    ApiClient.onUnauthorized(() => {
        stopRealtime();
        dom.loginOverlay.classList.add('visible');
        dom.loginError.textContent = 'Session expired — sign in again';
    });

    dom.loginForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        dom.loginError.textContent = '';
        dom.loginSubmit.disabled = true;
        dom.loginSubmit.textContent = 'Connecting…';
        try {
            await ApiClient.login(dom.loginToken.value.trim(), dom.loginOperator.value.trim());
            dom.loginOverlay.classList.remove('visible');
            dom.loginToken.value = '';
            boot();
        } catch (error) {
            dom.loginError.textContent = error.message;
        } finally {
            dom.loginSubmit.disabled = false;
            dom.loginSubmit.textContent = 'Connect to control room';
        }
    });

    dom.logoutBtn.addEventListener('click', () => {
        stopRealtime();
        EmergencyAudio.stopAll();
        ApiClient.clearSession();
        location.reload();
    });

    // Theme toggle (unchanged behaviour, dark by default)
    $('themeToggle').addEventListener('click', () => {
        const html = document.documentElement;
        const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-theme', next);
        $('themeThumb').textContent = next === 'dark' ? '🌙' : '☀️';
        localStorage.setItem('zeroui-dash-theme', next);
    });
    (function restoreTheme() {
        const saved = localStorage.getItem('zeroui-dash-theme');
        if (saved) {
            document.documentElement.setAttribute('data-theme', saved);
            const thumb = $('themeThumb');
            if (thumb) thumb.textContent = saved === 'dark' ? '🌙' : '☀️';
        }
    })();

    // ══════════════════════════════════════════════════════════════
    // Realtime
    // ══════════════════════════════════════════════════════════════
    function setConnectionBadge(mode) {
        const label = { open: 'Live', reconnecting: 'Reconnecting…', closed: 'Offline' }[mode] || mode;
        dom.connectionText.textContent = label;
        dom.connectionBadge.dataset.state = mode;
        dom.connectionBadge.querySelector('.conn-dot').className =
            `conn-dot ${mode === 'open' ? '' : mode}`;
    }

    function stopRealtime() {
        if (state.socket) {
            state.socket.close();
            state.socket = null;
        }
        setConnectionBadge('closed');
    }

    function startRealtime() {
        if (!ApiClient.token) {
            // Calling boot() without a session would only produce a 403
            // handshake loop; wait for the operator to sign in.
            setConnectionBadge('closed');
            return;
        }
        if (state.socket) state.socket.close();
        state.socket = ApiClient.connectSocket({
            onState: (mode) => setConnectionBadge(mode === 'open' ? 'open' : (mode === 'reconnecting' ? 'reconnecting' : 'closed')),
            onMessage: handleRealtimeMessage,
        });
    }

    function handleRealtimeMessage(message) {
        switch (message.type) {
            case 'hello':
                state.serverConfig = message.server_config || {};
                state.staleAfter = state.serverConfig.stale_after_seconds || 90;
                state.realtime = message.realtime || {};
                state.emergencies.clear();
                (message.emergencies || []).forEach((em) => state.emergencies.set(em.emergency_id, em));
                renderList();
                renderCounts();
                maybeRefreshSelectedDetail(true);
                if (state.selectedId) renderDetail();
                break;

            case 'emergency.created':
                state.emergencies.set(message.emergency.emergency_id, message.emergency);
                renderList();
                renderCounts();
                announceEmergency(message.emergency);
                break;

            case 'emergency.updated':
                state.emergencies.set(message.emergency.emergency_id, message.emergency);
                renderList();
                renderCounts();
                if (message.emergency.emergency_id === state.selectedId) {
                    mergeSummaryIntoDetail(message.emergency);
                    renderDetail();
                }
                break;

            case 'emergency.ended':
                state.emergencies.set(message.emergency.emergency_id, message.emergency);
                renderList();
                renderCounts();
                if (message.emergency.emergency_id === state.selectedId) {
                    EmergencyAudio.stopLiveAudio();
                    updateLiveAudioUi('stopped');
                    mergeSummaryIntoDetail(message.emergency);
                    renderDetail();
                }
                break;

            case 'event.added':
                if (message.emergency_id === state.selectedId && state.detail && message.event) {
                    const events = state.detail.events || [];
                    if (!events.some((e) => e.id === message.event.id)) events.push(message.event);
                    state.detail.events = events;
                    renderTimeline();
                }
                break;

            case 'audio.segment':
                if (message.emergency_id === state.selectedId && state.detail && message.segment) {
                    const segments = state.detail.audio_segments || [];
                    if (!segments.some((s) => s.segment_id === message.segment.segment_id)) {
                        segments.push(message.segment);
                        segments.sort((a, b) => a.sequence - b.sequence);
                    }
                    state.detail.audio_segments = segments;
                    renderSegments();
                }
                break;

            case 'live_audio.state':
                if (message.emergency_id === state.selectedId) updateLiveAudioUi();
                break;

            case 'stats':
                state.realtime = message.realtime || {};
                renderCounts();
                break;

            default:
                break;
        }
    }

    function announceEmergency(emergency) {
        playNotification();
        dom.alarmText.textContent = `🚨 ${emergency.user_id} — ${String(emergency.trigger_type).toUpperCase()} emergency`;
        dom.alarmBanner.hidden = false;
        dom.alarmBanner.dataset.emergencyId = emergency.emergency_id;
        if (!state.selectedId) window.showDetail(emergency.emergency_id);
    }

    function playNotification() {
        if (!state.soundEnabled || !dom.notifSound) return;
        try {
            dom.notifSound.currentTime = 0;
            dom.notifSound.play().catch(() => {});
        } catch (_) { /* ignore */ }
    }

    dom.alarmOpen.addEventListener('click', () => {
        const id = dom.alarmBanner.dataset.emergencyId;
        if (id) window.showDetail(id);
    });
    dom.alarmDismiss.addEventListener('click', () => { dom.alarmBanner.hidden = true; });

    // ══════════════════════════════════════════════════════════════
    // List + counts
    // ══════════════════════════════════════════════════════════════
    function list() {
        return Array.from(state.emergencies.values())
            .map((em) => ({ ...em, connection_state: connectionState(em) }))
            .sort((a, b) => {
                const rank = (em) => (em.status === 'emergency' ? 0 : 1);
                if (rank(a) !== rank(b)) return rank(a) - rank(b);
                return new Date(b.created_at) - new Date(a.created_at);
            });
    }

    function filtered() {
        const all = list();
        switch (state.filter) {
            case 'emergency': return all.filter((em) => em.status === 'emergency');
            case 'stale': return all.filter((em) => em.connection_state === 'stale');
            case 'cancelled': return all.filter((em) => em.status === 'cancelled');
            case 'resolved': return all.filter((em) => em.status === 'resolved');
            default: return all;
        }
    }

    function renderCounts() {
        const all = list();
        dom.activeCountValue.textContent = all.filter((em) => em.status === 'emergency').length;
        dom.cancelledCountValue.textContent = all.filter((em) => em.status === 'cancelled').length;
        dom.resolvedCountValue.textContent = all.filter((em) => em.status === 'resolved').length;
        dom.staleCountValue.textContent = all.filter((em) => em.connection_state === 'stale').length;
    }

    function renderList() {
        const items = filtered();
        dom.listMeta.textContent = items.length === 0
            ? 'No emergencies match this filter'
            : `${items.length} shown · ${list().length} total`;

        dom.emergenciesList.innerHTML = '';
        if (items.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'empty-state';
            empty.innerHTML = `
                <div class="empty-icon">📡</div>
                <p class="empty-text">No emergencies to show</p>
                <p class="empty-sub">Listening for device activations</p>`;
            dom.emergenciesList.appendChild(empty);
            return;
        }
        items.forEach((em) => dom.emergenciesList.appendChild(createCard(em)));
    }

    function createCard(em) {
        const card = document.createElement('div');
        card.className = `emergency-card status-${em.status} conn-${em.connection_state}` +
            (em.emergency_id === state.selectedId ? ' active' : '');
        card.dataset.emergencyId = em.emergency_id;
        card.onclick = () => window.showDetail(em.emergency_id);

        const triggerLabel = { fall: '📉 Fall detected', manual: '🔴 Manual SOS', shake: '📳 Shake SOS', test: '🧪 Test' }[em.trigger_type] || em.trigger_type;
        const liveBadge = em.live_audio && em.live_audio.device_streaming
            ? '<span class="card-live-badge">● LIVE AUDIO</span>' : '';
        const audioBadge = em.has_audio
            ? `<span class="card-audio-badge">🔊 ${em.audio_segment_count || 1}</span>` : '';
        const staleBadge = em.connection_state === 'stale'
            ? `<span class="card-stale-badge">⚠ no updates ${Math.round(em.age_seconds || 0)}s</span>` : '';

        card.innerHTML = `
            <div class="card-top">
                <span class="card-user">${escapeHtml(em.user_id)}</span>
                <span class="card-status ${em.status}">
                    <span class="status-dot-sm"></span>${escapeHtml(em.status.toUpperCase())}
                </span>
            </div>
            <div class="card-meta">
                <span class="card-meta-item">${triggerLabel}</span>
                <span class="card-meta-item">📍 ${em.latitude !== null ? Number(em.latitude).toFixed(4) : '—'},
                    ${em.longitude !== null ? Number(em.longitude).toFixed(4) : '—'}</span>
                ${audioBadge}${liveBadge}
            </div>
            <div class="card-bottom">
                <span class="card-digipin">${escapeHtml(em.digipin || 'DigiPIN n/a')}</span>
                <span class="card-time">${timeAgo(em.last_update_at || em.created_at)}</span>
            </div>
            <div class="card-id-row"><code>${escapeHtml(em.emergency_id)}</code>
                <span class="card-conn ${em.connection_state}">${em.connection_state.toUpperCase()}</span></div>
            ${staleBadge}
        `;
        return card;
    }

    // ══════════════════════════════════════════════════════════════
    // Detail panel
    // ══════════════════════════════════════════════════════════════
    async function showDetail(emergencyId) {
        if (state.selectedId === emergencyId && dom.detailOverlay.classList.contains('visible')) return;

        // Hard stop of any audio from the previously selected emergency.
        EmergencyAudio.setSelectedEmergency(emergencyId);
        updateLiveAudioUi('stopped');
        dom.resolveNote.value = '';

        state.selectedId = emergencyId;
        MapModule.setSelected(emergencyId);
        dom.detailOverlay.classList.add('visible');
        renderList();

        try {
            state.detail = await ApiClient.detail(emergencyId);
            state.detailSignature = '';
            renderDetail(true);
            MapModule.updateMarkers(Array.from(state.emergencies.values()));
            if (state.detail.latitude !== null) {
                MapModule.focusOn(state.detail.latitude, state.detail.longitude, 16);
            }
            MapModule.drawTrack(state.detail.locations, state.detail.status);
            await refreshLiveAudioState();
        } catch (error) {
            console.error('Failed to load emergency detail', error);
        }
    }

    function mergeSummaryIntoDetail(summary) {
        if (!state.detail) return;
        state.detail = { ...state.detail, ...summary };
    }

    async function maybeRefreshSelectedDetail(force) {
        if (!state.selectedId) return;
        try {
            const detail = await ApiClient.detail(state.selectedId);
            state.detail = detail;
            renderDetail(force);
            MapModule.drawTrack(detail.locations, detail.status);
        } catch (error) {
            console.warn('Detail refresh failed', error);
        }
    }

    function updateDetailHeader(detail) {
        const conn = connectionState(detail);
        dom.detailStatusBadge.textContent = String(detail.status).toUpperCase();
        dom.detailStatusBadge.className = `detail-status-badge ${detail.status}`;
        dom.detailConnState.textContent = conn.toUpperCase();
        dom.detailConnState.className = `conn-state-badge ${conn}`;
        dom.detailUser.textContent = detail.user_id;
        dom.detailEmergencyId.textContent = detail.emergency_id;
        dom.detailTime.textContent = `Triggered ${timeAgo(detail.created_at)} · ${new Date(detail.created_at).toLocaleString()}`;
        dom.detailTrigger.textContent = detail.trigger_type;
        dom.detailDigipin.textContent = detail.digipin || 'unavailable (outside DIGIPIN coverage)';

        const lat = detail.latitude, lng = detail.longitude;
        dom.detailCoords.textContent = lat !== null && lng !== null
            ? `${Number(lat).toFixed(6)}, ${Number(lng).toFixed(6)}` : '—';
        dom.detailAccuracy.textContent = detail.location_accuracy_m !== null && detail.location_accuracy_m !== undefined
            ? `±${Math.round(detail.location_accuracy_m)} m` : '—';
        dom.detailDuration.textContent = fmtDuration(detail.duration_seconds);
        dom.detailLastUpdate.textContent = detail.last_update_at
            ? `${timeAgo(detail.last_update_at)} (${clockTime(detail.last_update_at)})` : '—';

        const device = detail.device || {};
        dom.detailDevice.textContent = device.device_id
            ? `${device.manufacturer || ''} ${device.model || ''} · Android ${device.os_version || '?'} · app ${device.app_version || '?'}`
            : 'unknown device';
        dom.detailMapsLink.href = `https://www.google.com/maps?q=${lat},${lng}`;

        const active = detail.status === 'emergency' || detail.status === 'verifying';
        dom.btnDetailResolve.style.display = active ? 'block' : 'none';
        dom.btnDetailResolve.disabled = !active;
        dom.resolveNote.style.display = active ? 'block' : 'none';
    }

    function renderDetail(force) {
        const detail = state.detail;
        if (!detail) return;

        const signature = JSON.stringify([
            detail.emergency_id, detail.status, detail.last_update_at, detail.latitude, detail.longitude,
            (detail.events || []).length, (detail.locations || []).length,
            (detail.audio_segments || []).length, JSON.stringify(detail.telemetry || {}),
        ]);
        const changed = signature !== state.detailSignature;

        updateDetailHeader(detail);
        if (force || changed) {
            renderTelemetry();
            renderLocations();
            renderTimeline();
            renderSegments();
        }
        state.detailSignature = signature;
    }

    function renderTelemetry() {
        const detail = state.detail || {};
        const telemetry = detail.telemetry || {};
        const capabilities = telemetry.capabilities || (detail.device && detail.device.capabilities) || {};
        const rows = [];

        const push = (label, value) => {
            if (value === null || value === undefined || value === '') return;
            rows.push(`<div class="telemetry-item"><span class="telemetry-label">${escapeHtml(label)}</span>
                <span class="telemetry-value">${escapeHtml(value)}</span></div>`);
        };

        push('Battery', telemetry.battery_pct !== undefined && telemetry.battery_pct !== null
            ? `${Math.round(telemetry.battery_pct)}%${telemetry.charging ? ' ⚡ charging' : ''}` : null);
        push('Network', telemetry.network_type || telemetry.connectivity);
        push('App state', telemetry.app_state);
        push('Foreground service', telemetry.foreground_service === undefined ? null
            : (telemetry.foreground_service ? 'running' : 'stopped'));
        push('Screen', telemetry.screen_on === undefined ? null : (telemetry.screen_on ? 'on' : 'off'));
        push('Audio session', telemetry.audio_session_active === undefined ? null
            : (telemetry.audio_session_active ? 'capturing' : 'idle'));
        push('Audio segment #', telemetry.audio_segment_sequence);
        push('Motion', telemetry.motion_state);
        push('Accelerometer', telemetry.accelerometer_magnitude !== undefined && telemetry.accelerometer_magnitude !== null
            ? `${Number(telemetry.accelerometer_magnitude).toFixed(2)} m/s²` : null);

        const sensorList = Object.entries(telemetry.sensors || {})
            .map(([name, available]) => `${name}: ${available ? '✓' : '✗'}`).join(' · ');
        if (sensorList) push('Sensors', sensorList);
        const capabilityList = Object.entries(capabilities)
            .map(([name, available]) => `${name}: ${available ? '✓' : '✗'}`).join(' · ');
        if (capabilityList) push('Capabilities', capabilityList);

        const detection = detail.trigger_metadata || {};
        if (detection.impact_magnitude !== undefined) push('Impact', `${Number(detection.impact_magnitude).toFixed(1)} m/s²`);
        if (detection.freefall_ms !== undefined) push('Free-fall', `${detection.freefall_ms} ms`);
        if (detection.inactivity_ms !== undefined) push('Inactivity', `${detection.inactivity_ms} ms`);
        if (detection.countdown_seconds !== undefined && detection.countdown_seconds !== null) {
            push('Countdown', `${detection.countdown_seconds}s expired`);
        }
        if (detail.ended_by) push('Ended by', detail.ended_by);
        if (detail.resolution_note) push('Note', detail.resolution_note);

        dom.detailTelemetry.innerHTML = rows.length
            ? rows.join('')
            : '<span class="telemetry-empty">No telemetry received yet</span>';
    }

    function renderLocations() {
        const detail = state.detail || {};
        const locations = (detail.locations || []).slice().reverse();
        dom.locationCount.textContent = locations.length ? `${locations.length} samples (≈30s interval)` : '';
        if (locations.length === 0) {
            dom.locationHistory.innerHTML = '<span class="telemetry-empty">No location samples yet</span>';
            return;
        }
        dom.locationHistory.innerHTML = locations.slice(0, 60).map((loc) => `
            <div class="tl-entry">
                <div class="tl-dot safe"></div>
                <div class="tl-content">
                    <div class="tl-type">${loc.latitude.toFixed(6)}, ${loc.longitude.toFixed(6)}
                        ${loc.accuracy_m ? `· ±${Math.round(loc.accuracy_m)}m` : ''}</div>
                    <div class="tl-desc">DigiPIN <strong>${escapeHtml(loc.digipin || 'unavailable')}</strong>
                        ${loc.provider ? `· ${escapeHtml(loc.provider)}` : ''}
                        ${loc.is_mock ? '· <span class="mock-flag">mock location</span>' : ''}</div>
                    <div class="tl-time">${clockTime(loc.timestamp)} · ${timeAgo(loc.timestamp)}</div>
                </div>
            </div>`).join('');
    }

    function renderTimeline() {
        const detail = state.detail || {};
        const events = detail.events || [];
        if (events.length === 0) {
            dom.detailTimeline.innerHTML = '<span class="telemetry-empty">No events yet</span>';
            return;
        }
        dom.detailTimeline.innerHTML = events.slice().reverse().map((event) => {
            const type = event.event_type || '';
            let dot = '';
            if (/emergency|sos|fall|no_response|cancel/.test(type)) dot = 'emergency';
            else if (/audio|mic|countdown|verif|location|gps|digipin/.test(type)) dot = 'verifying';
            else if (/resolved|safe|sms/.test(type)) dot = 'safe';
            return `
                <div class="tl-entry">
                    <div class="tl-dot ${dot}"></div>
                    <div class="tl-content">
                        <div class="tl-type">${escapeHtml(type.replace(/_/g, ' '))}
                            ${event.source ? `<span class="tl-source">${escapeHtml(event.source)}</span>` : ''}</div>
                        <div class="tl-desc">${escapeHtml(event.description)}</div>
                        <div class="tl-time">${clockTime(event.timestamp)} · ${timeAgo(event.timestamp)}</div>
                    </div>
                </div>`;
        }).join('');
    }

    function renderSegments() {
        const detail = state.detail || {};
        const segments = detail.audio_segments || [];
        dom.segmentSummary.textContent = segments.length
            ? `${segments.length} segment(s) · ${fmtBytes(detail.audio_total_bytes || segments.reduce((a, s) => a + (s.size_bytes || 0), 0))}`
            : '';

        if (segments.length === 0) {
            dom.segmentList.innerHTML = '<span class="telemetry-empty">No segments uploaded yet</span>';
            return;
        }
        dom.segmentList.innerHTML = segments.map((segment) => `
            <div class="segment-item" data-segment-id="${escapeHtml(segment.segment_id)}">
                <div class="segment-info">
                    <span class="segment-seq">Segment #${segment.sequence}</span>
                    <span class="segment-meta">${segment.duration_seconds ? `${Math.round(segment.duration_seconds)}s · ` : ''}${fmtBytes(segment.size_bytes)}
                        · ${clockTime(segment.started_at || segment.created_at)}</span>
                </div>
                <div class="segment-actions">
                    <button class="segment-play" data-play="${escapeHtml(segment.segment_id)}">▶ Play</button>
                    <a class="segment-download" href="${ApiClient.segmentUrl(detail.emergency_id, segment.segment_id)}"
                       download="${escapeHtml(segment.file_name)}">⤓</a>
                </div>
            </div>`).join('');

        dom.segmentList.querySelectorAll('[data-play]').forEach((button) => {
            button.addEventListener('click', () => {
                if (!state.detail) return;
                EmergencyAudio.playSegment(state.detail.emergency_id, button.dataset.play);
                updateLiveAudioUi('segment');
            });
        });
    }

    // ══════════════════════════════════════════════════════════════
    // Live audio (selected emergency only)
    // ══════════════════════════════════════════════════════════════
    EmergencyAudio.onState((liveState, info) => updateLiveAudioUi(liveState, info));
    EmergencyAudio.onLevel((level) => {
        const width = Math.min(100, Math.round(Math.sqrt(level) * 220));
        dom.liveAudioLevel.style.width = `${width}%`;
    });

    async function refreshLiveAudioState() {
        if (!state.selectedId) return;
        try {
            const info = await ApiClient.liveAudioState(state.selectedId);
            updateLiveAudioUi(info.device_streaming ? 'streaming' : 'idle', info);
        } catch (_) { /* ignore */ }
    }

    function updateLiveAudioUi(liveState, info) {
        const detail = state.detail;
        const streaming = detail && detail.live_audio ? detail.live_audio.device_streaming : false;
        const stateValue = liveState || EmergencyAudio.stats().state;
        const labels = {
            idle: streaming ? 'Device is streaming — press Listen live' : 'Not streaming',
            waiting: 'Connected — waiting for audio frames…',
            streaming: '● LIVE',
            connecting: 'Connecting…',
            stopped: streaming ? 'Device streaming — stopped locally' : 'Not streaming',
            error: 'Live audio error',
            segment: 'Playing stored segment',
        };
        dom.liveAudioState.textContent = labels[stateValue] || stateValue;
        dom.liveAudioState.className = `live-audio-state ${stateValue}`;
        dom.btnStopLive.disabled = !(stateValue === 'streaming' || stateValue === 'waiting' || stateValue === 'connecting');
        if (info && info.format) {
            dom.liveAudioHelp.textContent =
                `Format: ${info.format.encoding} @ ${info.format.sample_rate} Hz · ` +
                `${info.listeners || 0} listener(s) on this emergency.`;
        }
    }

    dom.btnListenLive.addEventListener('click', async () => {
        if (!state.selectedId) return;
        EmergencyAudio.setSelectedEmergency(state.selectedId);
        const ok = EmergencyAudio.startLive(state.selectedId);
        if (ok) updateLiveAudioUi('connecting');
        try { await ApiClient.requestDeviceLiveAudio(state.selectedId, true); } catch (_) { /* best effort */ }
    });
    dom.btnStopLive.addEventListener('click', () => {
        EmergencyAudio.stopLiveAudio();
        updateLiveAudioUi('stopped');
    });

    // ══════════════════════════════════════════════════════════════
    // Actions
    // ══════════════════════════════════════════════════════════════
    dom.btnDetailResolve.addEventListener('click', async () => {
        if (!state.selectedId) return;
        const emergencyId = state.selectedId;
        dom.btnDetailResolve.disabled = true;
        try {
            await ApiClient.resolve(emergencyId, dom.resolveNote.value.trim() || null);
            EmergencyAudio.stopAll();
            await maybeRefreshSelectedDetail(true);
            renderList();
            renderCounts();
        } catch (error) {
            alert(`Could not resolve: ${error.message}`);
        } finally {
            dom.btnDetailResolve.disabled = false;
        }
    });

    dom.detailClose.addEventListener('click', () => {
        EmergencyAudio.stopAll();
        updateLiveAudioUi('stopped');
        state.selectedId = null;
        state.detail = null;
        dom.detailOverlay.classList.remove('visible');
        MapModule.setSelected(null);
        MapModule.clearTrack();
        EmergencyAudio.setSelectedEmergency(null);
        renderList();
    });

    dom.copyEmergencyId.addEventListener('click', async () => {
        if (!state.detail) return;
        try {
            await navigator.clipboard.writeText(state.detail.emergency_id);
            dom.copyEmergencyId.textContent = '✓';
            setTimeout(() => { dom.copyEmergencyId.textContent = '⧉'; }, 1200);
        } catch (_) { /* clipboard unavailable */ }
    });

    document.querySelectorAll('.filter-tab').forEach((tab) => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.filter-tab').forEach((t) => t.classList.remove('active'));
            tab.classList.add('active');
            state.filter = tab.dataset.filter;
            renderList();
        });
    });

    window.addEventListener('resize', () => MapModule.invalidateSize());
    window.showDetail = showDetail;

    // ══════════════════════════════════════════════════════════════
    // Boot
    // ══════════════════════════════════════════════════════════════
    function startTimers() {
        // boot() re-runs on every sign-in; the intervals must exist only once.
        if (state.timersStarted) return;
        state.timersStarted = true;
        // Re-derive staleness + relative times without hammering the server.
        setInterval(() => {
            renderList();
            renderCounts();
            if (state.detail) updateDetailHeader(state.detail);
        }, 5000);

        // Safety net: reconcile the selected detail plus the list every 20 s
        // (covers any missed socket frame; never interrupts audio).
        setInterval(() => {
            if (state.detail) updateDetailHeader(state.detail);
        }, 10000);
        setInterval(() => { maybeRefreshSelectedDetail(false); refreshLiveAudioState(); }, 20000);
        setInterval(async () => {
            try {
                const data = await ApiClient.listEmergencies({ limit: 200 });
                (data.emergencies || []).forEach((em) => state.emergencies.set(em.emergency_id, em));
                renderList();
                renderCounts();
            } catch (_) { /* socket will recover */ }
        }, 30000);
    }

    async function boot() {
        MapModule.init();
        try {
            const snapshot = await ApiClient.snapshot();
            state.serverConfig = snapshot.server_config || {};
            state.staleAfter = state.serverConfig.stale_after_seconds || 90;
            state.realtime = snapshot.realtime || {};
            (snapshot.emergencies || []).forEach((em) => state.emergencies.set(em.emergency_id, em));
            renderList();
            renderCounts();
            if (state.selectedId) await maybeRefreshSelectedDetail(true);
        } catch (error) {
            console.error('Snapshot failed', error);
        }
        startRealtime();
        startTimers();
    }

    if (ApiClient.hasSession()) {
        dom.loginOverlay.classList.remove('visible');
        boot();
    } else {
        dom.loginOverlay.classList.add('visible');
        setConnectionBadge('closed');
        initMapWhenVisible();
    }

    // The map sits behind the sign-in overlay on first visit, so Leaflet would
    // measure a zero-height container. Initialise it once the overlay lifts.
    function initMapWhenVisible() {
        MapModule.init();
        setTimeout(() => MapModule.init(), 0);
    }
})();
