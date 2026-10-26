/**
 * Simulator device identity
 * =========================
 * The browser simulator plays the role of a real phone, so it must enroll like
 * one: a shared enrollment token is exchanged once for a private device token,
 * and every later call carries `X-Device-Token`. The backend then scopes the
 * data to this user, exactly as it does for the Android app.
 */

const SimDevice = (() => {
    const STORE_KEY = 'zeroui-sim-device';

    let state = (() => {
        try {
            return JSON.parse(localStorage.getItem(STORE_KEY) || 'null') || {};
        } catch (_) {
            return {};
        }
    })();

    function persist() {
        localStorage.setItem(STORE_KEY, JSON.stringify(state));
    }

    /** Backend base URL: empty = same origin as the simulator. */
    function apiBase() {
        const input = document.getElementById('simApiBase');
        const value = (input ? input.value : '').trim();
        return value.replace(/\/+$/, '');
    }

    function enrollmentToken() {
        const input = document.getElementById('simEnrollToken');
        const value = (input ? input.value : '').trim();
        return value || state.enrollmentToken || 'zeroui-dev-enroll-token';
    }

    function url(path) {
        return `${apiBase()}${path}`;
    }

    function headers(json = true) {
        return Object.assign(
            json ? { 'Content-Type': 'application/json' } : {},
            state.token ? { 'X-Device-Token': state.token } : {}
        );
    }

    async function request(path, options = {}) {
        const response = await fetch(url(path), {
            ...options,
            headers: Object.assign(headers(!(options.body instanceof FormData)), options.headers || {}),
        });
        if (!response.ok) {
            let detail = response.statusText;
            try {
                const body = await response.json();
                detail = body.detail ? JSON.stringify(body.detail) : detail;
            } catch (_) { /* keep statusText */ }
            const error = new Error(detail);
            error.status = response.status;
            throw error;
        }
        if (response.status === 204) return null;
        return response.json();
    }

    return {
        get deviceId() { return state.deviceId || null; },
        get token() { return state.token || null; },
        get userId() { return state.userId || null; },
        get actor() { return this.userId || 'unknown'; },
        apiBase,
        url,
        headers,
        request,

        async config() {
            const response = await fetch(url('/api/config'));
            return response.json();
        },

        /** Enroll (or re-enroll when the user id changed). */
        async ensure(userId, displayName = '') {
            if (state.userId === userId && state.token) return state;

            const capabilities = {
                accelerometer: true,
                gyroscope: false,
                magnetometer: false,
                gps_service: 'geolocation' in navigator,
                location_permission: false,
                background_location: false,
                microphone: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
                simulated_device: true,
            };
            if ('geolocation' in navigator) {
                try {
                    capabilities.location_permission =
                        (await navigator.permissions.query({ name: 'geolocation' })).state === 'granted';
                } catch (_) { /* permissions API unavailable */ }
            }

            const response = await fetch(url('/api/devices/register'), {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Enrollment-Token': enrollmentToken(),
                },
                body: JSON.stringify({
                    user_id: userId,
                    display_name: displayName || `${userId} (browser simulator)`,
                    platform: 'web-simulator',
                    manufacturer: 'Browser',
                    model: `${navigator.platform || 'web'} · ${navigator.userAgent.split(' ')[0]}`,
                    os_version: navigator.userAgent,
                    app_version: '2.0.0-sim',
                    capabilities,
                }),
            });
            if (!response.ok) {
                const text = await response.text();
                throw new Error(`Enrollment failed (${response.status}): ${text}`);
            }
            const body = await response.json();
            state = {
                userId,
                deviceId: body.device_id,
                token: body.device_token,
                enrollmentToken: enrollmentToken(),
            };
            persist();
            return state;
        },

        async forget() {
            state = {};
            localStorage.removeItem(STORE_KEY);
        },

        // ── PIN ──
        setPin: (userId, pin, currentPin) => request('/api/pin/set', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId, pin, ...(currentPin ? { current_pin: currentPin } : {}) }),
        }),
        verifyPin: (userId, pin) => request('/api/pin/verify', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId, pin }),
        }),
        pinStatus: (userId) => request(`/api/pin/status/${encodeURIComponent(userId)}`),

        // ── contacts ──
        contacts: (userId) => request(`/api/contacts/${encodeURIComponent(userId)}`),
        addContact: (userId, name, phone, relationship) => request('/api/contacts', {
            method: 'POST',
            body: JSON.stringify({ user_id: userId, name, phone_number: phone, relationship }),
        }),
        removeContact: (contactId, userId) => request(
            `/api/contacts/${contactId}?user_id=${encodeURIComponent(userId)}`,
            { method: 'DELETE' }
        ),
        testSms: (userId) => request(`/api/contacts/test-sms?user_id=${encodeURIComponent(userId)}`, {
            method: 'POST',
        }),
    };
})();
