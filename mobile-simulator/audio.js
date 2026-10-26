/**
 * Audio capture (browser simulator)
 * =================================
 * Uses MediaRecorder and cuts the stream into ~1-minute segments that are
 * uploaded to `POST /api/emergencies/{id}/audio/segments` (device token).
 *
 * Difference from the Android app, on purpose and documented:
 *  * the browser cannot produce raw PCM frames that the dashboard's live
 *    player understands, so the simulator only uploads segments;
 *  * live streaming is a real-device feature (`record` PCM stream → WebSocket).
 * Segments start only after the emergency is activated, exactly like on the
 * phone.
 */

const AudioModule = (() => {
    let mediaRecorder = null;
    let stream = null;
    let isRecording = false;
    let emergencyId = null;
    let segmentSeconds = 60;
    let sequence = 0;
    let uploadedSegments = 0;
    let pendingChunks = [];

    function log(message, type = '') {
        if (typeof window.simLog === 'function') window.simLog(message, type);
    }

    async function requestMic() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            return true;
        } catch (error) {
            console.warn('Microphone unavailable:', error.message);
            return false;
        }
    }

    function stopStream() {
        if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
        }
    }

    async function uploadSegment(blob, durationSeconds) {
        if (!blob || blob.size === 0 || !emergencyId) return false;
        if (typeof SimDevice.request !== 'function') return false;

        sequence += 1;
        const form = new FormData();
        form.append('file', blob, `segment_${String(sequence).padStart(4, '0')}.webm`);
        form.append('sequence', String(sequence));
        form.append('duration_seconds', (durationSeconds || segmentSeconds).toFixed(1));
        form.append('codec', 'opus');
        try {
            await SimDevice.request(`/api/emergencies/${emergencyId}/audio/segments`, {
                method: 'POST',
                body: form,
            });
            uploadedSegments += 1;
            log(`🎧 Audio segment #${sequence} uploaded (${Math.round(blob.size / 1024)} KB)`, 'success');
            return true;
        } catch (error) {
            log(`⚠️ Segment #${sequence} failed: ${error.message}`, 'warn');
            return false;
        }
    }

    return {
        configure(options = {}) {
            if (options.segmentSeconds) segmentSeconds = options.segmentSeconds;
        },

        /**
         * Start capturing. `id` scopes the segments to one emergency, so audio
         * can never be attributed to the wrong one.
         */
        async startRecording(id) {
            if (isRecording) return true;
            if (id) emergencyId = id;

            const hasAccess = await requestMic();
            if (!hasAccess) return false;

            pendingChunks = [];
            mediaRecorder = new MediaRecorder(stream, {
                mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                    ? 'audio/webm;codecs=opus'
                    : 'audio/webm',
            });

            mediaRecorder.ondataavailable = async (event) => {
                if (!event.data || event.data.size === 0) return;
                pendingChunks.push(event.data);
                // Timeslice = one full segment: upload it as its own ~1-minute file.
                const blob = new Blob(pendingChunks, { type: 'audio/webm' });
                pendingChunks = [];
                await uploadSegment(blob, segmentSeconds);
            };

            mediaRecorder.start(segmentSeconds * 1000);
            isRecording = true;
            return true;
        },

        /** Stop capturing. Returns the trailing partial segment (if any). */
        stopRecording() {
            return new Promise((resolve) => {
                if (!mediaRecorder || !isRecording) {
                    resolve(null);
                    return;
                }
                mediaRecorder.onstop = () => {
                    const blob = pendingChunks.length > 0
                        ? new Blob(pendingChunks, { type: 'audio/webm' })
                        : null;
                    pendingChunks = [];
                    isRecording = false;
                    stopStream();
                    resolve(blob);
                };
                mediaRecorder.stop();
            });
        },

        uploadAudio: (id, blob) => uploadSegment(blob, segmentSeconds),

        setEmergency(id) { emergencyId = id; },

        isActive: () => isRecording,
        segmentCount: () => uploadedSegments,
        currentSequence: () => sequence,
    };
})();
