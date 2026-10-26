/**
 * Emergency mode (browser simulator)
 * ==================================
 * Speaks the same protocol as the Android app:
 *
 *   POST /api/emergencies/start                     (device token)
 *   WS   /ws/device/{emergency_id}?token=…          location / telemetry / events
 *   POST /api/emergencies/{id}/audio/segments       ~1-minute audio segments
 *   POST /api/emergencies/{id}/status               resolved / cancelled (+PIN)
 *
 * No coordinates are invented: if the browser cannot provide a position, the
 * simulator refuses to activate and reports it (the Android app behaves the
 * same way).
 */

const EmergencyModule = (() => {
    const DEFAULT_API_BASE = '';
    const LOCATION_UPDATE_INTERVAL = 30000; // 30 seconds
    const TELEMETRY_INTERVAL = 15000;
    const HEARTBEAT_INTERVAL = 25000;

    let emergencyId = null;
    let currentLat = null;
    let currentLng = null;
    let digipin = null;
    let socket = null;
    let locationTimer = null;
    let telemetryTimer = null;
    let heartbeatTimer = null;
    let onEndedCallback = null;
    let serverConfig = { countdown_seconds: 15, location_interval_seconds: 30, audio_segment_seconds: 60 };

    async function loadServerConfig() {
        try {
            serverConfig = await SimDevice.config();
        } catch (_) { /* keep defaults */ }
        return serverConfig;
    }

    // ── GPS ──
    function getPosition() {
        return new Promise((resolve, reject) => {
            if (!navigator.geolocation) {
                reject(new Error('This browser has no Geolocation API'));
                return;
            }
            navigator.geolocation.getCurrentPosition(
                (position) => resolve({
                    latitude: position.coords.latitude,
                    longitude: position.coords.longitude,
                    accuracy: position.coords.accuracy,
                    altitude: position.coords.altitude,
                    speed: position.coords.speed,
                    heading: position.coords.heading,
                    timestamp: new Date(position.timestamp || Date.now()).toISOString(),
                    provider: 'browser-geolocation',
                    is_mock: false,
                }),
                (error) => reject(new Error(`Location unavailable: ${error.message}`)),
                { enableHighAccuracy: true, timeout: 12000, maximumAge: 5000 }
            );
        });
    }

    const mapsLink = (lat, lng) => `https://www.google.com/maps?q=${lat},${lng}`;

    // ── realtime channel ──
    function connectSocket() {
        if (!emergencyId) return;
        const base = SimDevice.apiBase() || location.origin;
        const wsUrl = base.replace(/^http/, 'ws');
        const endpoint = `${wsUrl}/ws/device/${emergencyId}?token=${encodeURIComponent(SimDevice.token || '')}`;

        socket = new WebSocket(endpoint);
        socket.onmessage = (event) => {
            if (typeof event.data !== 'string') return;
            let message;
            try { message = JSON.parse(event.data); } catch (_) { return; }
            if (message.type === 'ready') {
                log('🔌 Realtime channel to the backend opened', 'success');
            } else if (message.type === 'ack' && message.kind === 'location' && message.digipin) {
                digipin = message.digipin;
                const display = document.getElementById('digipinDisplay');
                if (display) display.textContent = digipin;
            } else if (message.type === 'emergency.ended') {
                log(`🏁 The authority ended this emergency (${message.status})`, 'success');
                if (typeof onEndedCallback === 'function') onEndedCallback(message.status);
            }
        };
        socket.onclose = () => {
            log('🔌 Realtime channel closed', 'warn');
        };
        socket.onerror = () => {
            log('⚠️ Realtime channel error — REST fallback keeps working', 'warn');
        };
    }

    function send(message) {
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify(message));
            return true;
        }
        return false;
    }

    function log(message, type = '') {
        if (typeof window.simLog === 'function') window.simLog(message, type);
    }

    // ── periodic updates ──
    async function pushLocation() {
        if (!emergencyId) return;
        try {
            const sample = await getPosition();
            currentLat = sample.latitude;
            currentLng = sample.longitude;
            if (!send({ type: 'location', data: sample })) {
                const response = await SimDevice.request(`/api/emergencies/${emergencyId}/locations`, {
                    method: 'POST',
                    body: JSON.stringify(sample),
                });
                if (response && response.digipin) {
                    digipin = response.digipin;
                    const display = document.getElementById('digipinDisplay');
                    if (display) display.textContent = digipin;
                }
            }
            const coords = document.getElementById('gpsCoords');
            if (coords) coords.textContent = `${sample.latitude.toFixed(6)}, ${sample.longitude.toFixed(6)}`;
        } catch (error) {
            log(`⚠️ ${error.message}`, 'warn');
        }
    }

    async function pushTelemetry() {
        if (!emergencyId) return;
        const telemetry = {
            battery_pct: null,
            network_type: navigator.onLine ? 'online' : 'offline',
            connectivity: navigator.onLine ? 'online' : 'offline',
            app_state: document.visibilityState === 'visible' ? 'foreground' : 'background',
            foreground_service: false,
            screen_on: document.visibilityState === 'visible',
            audio_session_active: AudioModule.isActive(),
            audio_segment_sequence: AudioModule.segmentCount(),
            motion_state: 'simulated',
            accelerometer_magnitude: SensorModule.getCurrentAccel(),
            sensors: {
                accelerometer: 'simulated',
                gyroscope: false,
                gps_service: 'geolocation' in navigator,
                microphone: AudioModule.isActive(),
                browser_simulator: true,
            },
            collected_at: new Date().toISOString(),
        };
        delete telemetry.battery_pct;
        if (!send({ type: 'telemetry', data: telemetry })) {
            try {
                await SimDevice.request(`/api/emergencies/${emergencyId}/telemetry`, {
                    method: 'POST',
                    body: JSON.stringify(telemetry),
                });
            } catch (_) { /* retried on the next tick */ }
        }
    }

    return {
        loadServerConfig,
        getConfig: () => serverConfig,

        /**
         * Activate an emergency (called after the countdown expires).
         * @returns {Object} {id, lat, lng, digipin, mapsLink, sms_sent, sms_total}
         */
        async activate(userId, triggerType, eventLogs, options = {}) {
            onEndedCallback = options.onEnded || null;

            let sample;
            try {
                sample = await getPosition();
            } catch (error) {
                throw new Error(
                    `${error.message}. Allow location access for this page — the simulator ` +
                    'never invents coordinates.'
                );
            }

            await SimDevice.ensure(userId);
            currentLat = sample.latitude;
            currentLng = sample.longitude;

            const response = await SimDevice.request('/api/emergencies/start', {
                method: 'POST',
                body: JSON.stringify({
                    trigger_type: triggerType,
                    latitude: sample.latitude,
                    longitude: sample.longitude,
                    accuracy_m: sample.accuracy,
                    location_provider: sample.provider,
                    location_timestamp: sample.timestamp,
                    countdown_seconds: serverConfig.countdown_seconds,
                    trigger_metadata: options.metadata || {},
                    capabilities: {
                        accelerometer: 'simulated',
                        gps: 'geolocation' in navigator,
                        microphone: AudioModule.isActive(),
                    },
                    device: {
                        platform: 'web-simulator',
                        model: navigator.userAgent.split(' ')[0] || 'browser',
                        os_version: navigator.platform || 'web',
                        app_version: '2.0.0-sim',
                    },
                    client_emergency_id: `sim-${SimDevice.userId}-${Date.now()}`,
                    event_logs: eventLogs || [],
                }),
            });

            const emergency = response.emergency;
            emergencyId = emergency.emergency_id;
            digipin = emergency.digipin || null;

            connectSocket();
            locationTimer = setInterval(pushLocation, serverConfig.location_interval_seconds * 1000);
            telemetryTimer = setInterval(pushTelemetry, TELEMETRY_INTERVAL);
            heartbeatTimer = setInterval(() => send({ type: 'heartbeat' }), HEARTBEAT_INTERVAL);
            pushTelemetry();

            return {
                id: emergencyId,
                lat: sample.latitude,
                lng: sample.longitude,
                digipin: digipin || 'outside DIGIPIN coverage',
                mapsLink: mapsLink(sample.latitude, sample.longitude),
                sms_sent: response.sms_sent || 0,
                sms_total: response.sms_total || 0,
                digipin_available: response.digipin_available !== false,
            };
        },

        /** Stop an active emergency. */
        async deactivate(options = {}) {
            const status = options.status || 'resolved';
            [locationTimer, telemetryTimer, heartbeatTimer].forEach(clearInterval);
            locationTimer = telemetryTimer = heartbeatTimer = null;

            const blob = await AudioModule.stopRecording();
            if (blob && emergencyId) {
                await AudioModule.uploadAudio(emergencyId, blob);
            }

            const closingId = emergencyId;
            if (closingId) {
                try {
                    await SimDevice.request(`/api/emergencies/${closingId}/status`, {
                        method: 'POST',
                        body: JSON.stringify({
                            status,
                            pin_verified: !!options.pin,
                            ...(options.pin ? { pin: options.pin } : {}),
                            ...(options.reason ? { reason: options.reason } : {}),
                            ...(options.note ? { note: options.note } : {}),
                        }),
                    });
                } catch (error) {
                    log(`⚠️ Could not close the emergency: ${error.message}`, 'warn');
                }
            }

            if (socket) {
                try { socket.close(); } catch (_) {}
                socket = null;
            }
            emergencyId = null;
            return closingId;
        },

        async sendEvent(eventType, description, payload = {}) {
            if (!emergencyId) return;
            const message = { type: 'event', data: { event_type: eventType, description, payload } };
            if (send(message)) return;
            try {
                await SimDevice.request(`/api/emergencies/${emergencyId}/events`, {
                    method: 'POST',
                    body: JSON.stringify({ event_type: eventType, description, payload }),
                });
            } catch (_) { /* the timeline is best effort offline */ }
        },

        isSocketOpen: () => !!(socket && socket.readyState === WebSocket.OPEN),
        getEmergencyId: () => emergencyId,
        getDigipin: () => digipin,
        getPosition: () => ({ lat: currentLat, lng: currentLng }),
        defaults: { apiBase: DEFAULT_API_BASE, locationInterval: LOCATION_UPDATE_INTERVAL },
    };
})();
