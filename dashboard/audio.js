/**
 * Emergency audio controller
 * ==========================
 * Guarantees that audio from only ONE emergency can ever be audible:
 *
 *  * a single <audio> element is reused for stored segments,
 *  * a single Web Audio graph is used for the live PCM stream,
 *  * every public method first asserts the caller's emergency_id matches the
 *    currently selected emergency — otherwise playback is stopped, never mixed,
 *  * switching emergencies, closing the detail panel or resolving an emergency
 *    all stop everything.
 *
 * Live audio arrives as raw PCM frames (`pcm_s16le`, mono, 8/16/48 kHz) over
 * `/ws/live-audio/{emergency_id}` and is scheduled into an AudioContext for
 * gapless playback. Segment playback uses the per-emergency HTTP URL.
 */

const EmergencyAudio = (() => {
    let selectedEmergencyId = null;
    let liveEmergencyId = null;

    const player = document.getElementById('audioPlayer');

    let socket = null;
    let audioContext = null;
    let gainNode = null;
    let nextStartTime = 0;
    let sampleRate = 16000;
    let liveState = 'idle';       // idle | connecting | streaming | stopped
    let level = 0;
    let bytesReceived = 0;
    let levelTimer = null;

    const hooks = { onState: null, onLevel: null };

    // ── helpers ──────────────────────────────────────────────────────
    function ensureContext() {
        if (!audioContext) {
            const Ctor = window.AudioContext || window.webkitAudioContext;
            audioContext = new Ctor();
            gainNode = audioContext.createGain();
            gainNode.gain.value = 0.9;
            gainNode.connect(audioContext.destination);
        }
        if (audioContext.state === 'suspended') audioContext.resume();
        return audioContext;
    }

    function setState(state, detail) {
        liveState = state;
        if (hooks.onState) hooks.onState(state, detail || {});
    }

    function stopSegmentPlayback() {
        if (!player) return;
        player.pause();
        player.removeAttribute('src');
        try { player.load(); } catch (_) { /* ignore */ }
    }

    /** Stop the live stream and discard anything still scheduled. */
    function stopLive() {
        if (socket) {
            try { socket.close(); } catch (_) { /* ignore */ }
            socket = null;
        }
        if (levelTimer) {
            clearInterval(levelTimer);
            levelTimer = null;
        }
        if (audioContext) {
            // tear the graph down completely so no queued buffer can resume
            try { gainNode && gainNode.disconnect(); } catch (_) { /* ignore */ }
            try { audioContext.close(); } catch (_) { /* ignore */ }
            audioContext = null;
            gainNode = null;
        }
        nextStartTime = 0;
        bytesReceived = 0;
        level = 0;
        liveEmergencyId = null;
        if (hooks.onLevel) hooks.onLevel(0);
        setState('stopped');
    }

    function enqueuePcm(arrayBuffer) {
        if (!audioContext || !gainNode) return;
        const samples = new Int16Array(arrayBuffer);
        if (samples.length === 0) return;

        const floats = new Float32Array(samples.length);
        let sumSquares = 0;
        for (let i = 0; i < samples.length; i += 1) {
            const value = samples[i] / 32768;
            floats[i] = value;
            sumSquares += value * value;
        }
        level = samples.length ? Math.sqrt(sumSquares / samples.length) : 0;

        const buffer = audioContext.createBuffer(1, floats.length, sampleRate);
        buffer.copyToChannel(floats, 0);

        const source = audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(gainNode);

        const now = audioContext.currentTime;
        if (nextStartTime < now + 0.05) nextStartTime = now + 0.05;
        source.start(nextStartTime);
        nextStartTime += floats.length / sampleRate;
        bytesReceived += arrayBuffer.byteLength;
    }

    return {
        onState(handler) { hooks.onState = handler; },
        onLevel(handler) { hooks.onLevel = handler; },

        /** Called by the dashboard whenever the operator selects a card. */
        setSelectedEmergency(emergencyId) {
            if (selectedEmergencyId === emergencyId) return;
            this.stopAll();
            selectedEmergencyId = emergencyId;
        },

        getSelectedEmergency: () => selectedEmergencyId,

        /** Play a stored ~1-minute segment belonging to the selected emergency. */
        playSegment(emergencyId, segmentId) {
            if (emergencyId !== selectedEmergencyId) {
                console.warn('Refusing to play audio for an emergency that is not selected');
                return false;
            }
            this.stopLive();
            stopSegmentPlayback();
            player.src = ApiClient.segmentUrl(emergencyId, segmentId);
            player.play().catch((error) => console.warn('Playback blocked:', error.message));
            return true;
        },

        /** Open the live stream for the selected emergency only. */
        startLive(emergencyId) {
            if (emergencyId !== selectedEmergencyId) return false;
            if (socket || liveEmergencyId === emergencyId) return true;

            this.stopAll();
            selectedEmergencyId = emergencyId;
            liveEmergencyId = emergencyId;
            setState('connecting');

            const context = ensureContext();
            sampleRate = 16000;

            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const url = `${protocol}//${location.host}/ws/live-audio/${encodeURIComponent(emergencyId)}` +
                `?token=${encodeURIComponent(ApiClient.token)}`;

            socket = new WebSocket(url);
            socket.binaryType = 'arraybuffer';

            socket.onmessage = (event) => {
                if (liveEmergencyId !== selectedEmergencyId) return;   // selection changed
                if (typeof event.data === 'string') {
                    try {
                        const message = JSON.parse(event.data);
                        if (message.type === 'live_audio.state') {
                            if (message.format && message.format.sample_rate) {
                                sampleRate = Number(message.format.sample_rate) || 16000;
                            }
                            setState(message.streaming ? 'streaming' : 'waiting', message);
                        }
                    } catch (_) { /* ignore */ }
                    return;
                }
                enqueuePcm(event.data);
                setState('streaming');
            };

            socket.onopen = () => {
                setState('waiting');
                socket.send(JSON.stringify({ type: 'state' }));
            };
            socket.onclose = () => {
                socket = null;
                if (liveEmergencyId === selectedEmergencyId) setState('stopped');
            };
            socket.onerror = () => setState('error');

            levelTimer = setInterval(() => {
                const value = level;
                level *= 0.6;                       // decay between frames
                if (hooks.onLevel) hooks.onLevel(value);
            }, 120);

            void context;
            return true;
        },

        stopSegmentPlayback,
        stopLiveAudio: stopLive,

        /** Stop everything (segment + live). */
        stopAll() {
            stopSegmentPlayback();
            stopLive();
        },

        stats: () => ({ state: liveState, bytes: bytesReceived, sampleRate }),
    };
})();
