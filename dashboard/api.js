/**
 * Dashboard API client
 * ====================
 * Owns the dashboard session (JWT in sessionStorage), every REST call and the
 * realtime WebSocket with automatic reconnection/backoff.
 *
 * The session token is required for REST *and* for the sockets (passed as
 * `?token=` because browsers cannot set headers on WebSockets or <audio>).
 */

const ApiClient = (() => {
    const TOKEN_KEY = 'zeroui.dashboard.token';
    const OPERATOR_KEY = 'zeroui.dashboard.operator';

    let token = sessionStorage.getItem(TOKEN_KEY) || '';
    let operator = sessionStorage.getItem(OPERATOR_KEY) || 'authority';
    let unauthorizedHandler = null;

    function setSession(newToken, newOperator) {
        token = newToken || '';
        operator = newOperator || 'authority';
        if (token) {
            sessionStorage.setItem(TOKEN_KEY, token);
            sessionStorage.setItem(OPERATOR_KEY, operator);
        }
    }

    function clearSession() {
        token = '';
        sessionStorage.removeItem(TOKEN_KEY);
        sessionStorage.removeItem(OPERATOR_KEY);
    }

    function authHeaders(extra) {
        return Object.assign({ Authorization: `Bearer ${token}` }, extra || {});
    }

    async function request(path, options = {}) {
        const response = await fetch(path, {
            ...options,
            headers: authHeaders(
                options.body instanceof FormData
                    ? options.headers
                    : Object.assign({ 'Content-Type': 'application/json' }, options.headers)
            ),
        });

        if (response.status === 401) {
            clearSession();
            if (unauthorizedHandler) unauthorizedHandler();
            throw new Error('Dashboard session expired — please sign in again');
        }
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const body = await response.json();
                detail = body.detail ? JSON.stringify(body.detail) : detail;
            } catch (_) { /* keep statusText */ }
            throw new Error(detail);
        }
        return response.status === 204 ? null : response.json();
    }

    return {
        get token() { return token; },
        get operator() { return operator; },
        hasSession: () => Boolean(token),
        setSession,
        clearSession,
        onUnauthorized(handler) { unauthorizedHandler = handler; },

        async login(dashboardToken, operatorName) {
            const response = await fetch('/api/auth/dashboard/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token: dashboardToken, operator: operatorName || 'authority' }),
            });
            if (!response.ok) {
                throw new Error(response.status === 401 ? 'Invalid dashboard token' : 'Sign-in failed');
            }
            const body = await response.json();
            setSession(body.session_token, body.operator || operatorName);
            return body;
        },

        snapshot: () => request('/api/dashboard/snapshot'),
        listEmergencies: (params = {}) => {
            const query = new URLSearchParams(params).toString();
            return request(`/api/dashboard/emergencies${query ? `?${query}` : ''}`);
        },
        detail: (emergencyId) => request(`/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}`),
        events: (emergencyId) => request(`/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/events`),
        locations: (emergencyId) => request(`/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/locations`),
        segments: (emergencyId) => request(`/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/audio/segments`),
        liveAudioState: (emergencyId) => request(`/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/live-audio/state`),
        requestDeviceLiveAudio: (emergencyId, enabled) => request(
            `/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/request-live-audio?enabled=${enabled ? 'true' : 'false'}`,
            { method: 'POST' }
        ),
        resolve: (emergencyId, note) => request(
            `/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}/resolve`,
            { method: 'POST', body: JSON.stringify({ note: note || null }) }
        ),
        stats: () => request('/api/dashboard/stats'),

        /** Audio URL scoped to one emergency (token via query parameter). */
        segmentUrl(emergencyId, segmentId) {
            return `/api/dashboard/emergencies/${encodeURIComponent(emergencyId)}` +
                `/audio/segments/${encodeURIComponent(segmentId)}?token=${encodeURIComponent(token)}`;
        },

        /**
         * Realtime control socket. Calls handlers.onMessage for every server
         * message, handlers.onState('open'|'closed'|'reconnecting').
         */
        connectSocket(handlers = {}) {
            let socket = null;
            let closed = false;
            let attempts = 0;
            let pingTimer = null;
            let retryTimer = null;

            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${location.host}/ws/dashboard?token=${encodeURIComponent(token)}`;

            function open() {
                socket = new WebSocket(url);

                socket.onopen = () => {
                    attempts = 0;
                    handlers.onState && handlers.onState('open');
                    pingTimer = setInterval(() => {
                        if (socket && socket.readyState === WebSocket.OPEN) {
                            socket.send(JSON.stringify({ type: 'ping' }));
                        }
                    }, 20000);
                };

                socket.onmessage = (event) => {
                    if (typeof event.data !== 'string') return;
                    try {
                        handlers.onMessage && handlers.onMessage(JSON.parse(event.data));
                    } catch (error) {
                        console.error('Bad realtime payload', error);
                    }
                };

                socket.onclose = () => {
                    clearInterval(pingTimer);
                    if (closed) return;
                    attempts += 1;
                    const delay = Math.min(15000, 500 * 2 ** Math.min(attempts, 5));
                    handlers.onState && handlers.onState('reconnecting');
                    retryTimer = setTimeout(open, delay);
                };

                socket.onerror = () => { /* onclose handles retry */ };
            }

            open();

            return {
                send(message) {
                    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message));
                },
                close() {
                    closed = true;
                    clearInterval(pingTimer);
                    clearTimeout(retryTimer);
                    if (socket) socket.close();
                },
            };
        },
    };
})();
