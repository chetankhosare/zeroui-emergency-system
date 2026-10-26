/**
 * Zero UI Emergency App — Main State Machine
 * ============================================
 * Orchestrates the three modes: PASSIVE → VERIFYING → EMERGENCY
 * 
 * State transitions:
 *   PASSIVE  →  Fall detected  →  VERIFYING
 *   PASSIVE  →  Triple press   →  EMERGENCY (instant)
 *   VERIFYING → User responds  →  PASSIVE
 *   VERIFYING → No response    →  EMERGENCY
 *   EMERGENCY → Resolved       →  PASSIVE
 */

(() => {
    // ── Constants ──
    // 15 s is the reference value; refreshed from GET /api/config on boot.
    let VERIFY_COUNTDOWN = 15; // seconds
    const CIRCUMFERENCE = 2 * Math.PI * 52; // SVG circle circumference

    // ── State ──
    let currentState = 'PASSIVE';
    let countdownTimer = null;
    let countdownRemaining = VERIFY_COUNTDOWN;
    let eventLog = []; // Accumulated events for this session
    let pinEntryBuffer = ''; // Current PIN input
    let hasPinConfigured = false; // Whether user has a PIN set in backend

    // ── DOM Refs ──
    const views = {
        passive: document.getElementById('passiveView'),
        verify: document.getElementById('verifyView'),
        emergency: document.getElementById('emergencyView')
    };

    const countdownCircle = document.getElementById('countdownCircle');
    const countdownNumber = document.getElementById('countdownNumber');
    const gpsCoords = document.getElementById('gpsCoords');
    const digipinDisplay = document.getElementById('digipinDisplay');
    const mapsLink = document.getElementById('mapsLink');
    const pinOverlay = document.getElementById('pinOverlay');
    const pinDots = document.querySelectorAll('.pin-dot');
    const pinError = document.getElementById('pinError');
    const eventTimeline = document.getElementById('eventTimeline');
    const systemLog = document.getElementById('systemLog');

    // Control buttons
    const btnSimulateFall = document.getElementById('btnSimulateFall');
    const btnTriplePress = document.getElementById('btnTriplePress');
    const btnRespondOk = document.getElementById('btnRespondOk');
    const btnResolve = document.getElementById('btnResolve');

    // ── Initialization ──
    function init() {
        // Set up countdown circle
        if (countdownCircle) {
            countdownCircle.style.strokeDasharray = CIRCUMFERENCE;
            countdownCircle.style.strokeDashoffset = 0;
        }

        // Start sensor monitoring
        SensorModule.start(onFallDetected);

        // Update button states
        updateButtons();

        // Check if current user has a PIN configured
        checkPinStatus();

        // Enroll this browser as a device and align the timings with the backend.
        (async () => {
            try {
                const config = await EmergencyModule.loadServerConfig();
                if (config && config.countdown_seconds) {
                    VERIFY_COUNTDOWN = config.countdown_seconds;
                }
                AudioModule.configure({ segmentSeconds: (config && config.audio_segment_seconds) || 60 });
                await SimDevice.ensure(document.getElementById('userId').value || 'user_001');
                log(`🔌 Enrolled device ${SimDevice.deviceId} · countdown ${VERIFY_COUNTDOWN}s`, 'success');
            } catch (error) {
                log(`⚠️ Device enrollment failed: ${error.message}`, 'error');
            }
        })();

        log('🟢 System initialized — Passive mode active', 'success');
    }

    // ── Check PIN status from backend ──
    async function checkPinStatus() {
        const userId = document.getElementById('userId').value || 'user_001';
        const currentPinRow = document.getElementById('currentPinRow');
        const pinHint = document.getElementById('pinHint');
        try {
            await SimDevice.ensure(userId);
            const data = await SimDevice.pinStatus(userId);
            hasPinConfigured = data.has_pin;
            if (hasPinConfigured) {
                log('🔐 Safety PIN is configured for this user', 'success');
                // Show current PIN field for changing
                if (currentPinRow) currentPinRow.style.display = 'flex';
                if (pinHint) pinHint.textContent = 'Enter current PIN to change it';
            } else {
                log('⚠️ No safety PIN set — please configure one in the control panel', 'warn');
                if (currentPinRow) currentPinRow.style.display = 'none';
                if (pinHint) pinHint.textContent = 'Set a unique 4-digit PIN for the current user';
            }
        } catch (err) {
            log('⚠️ Could not check PIN status — backend may be offline', 'warn');
            hasPinConfigured = false;
            if (currentPinRow) currentPinRow.style.display = 'none';
        }
    }

    // Re-check PIN status and contacts when user ID changes
    const userIdInput = document.getElementById('userId');
    if (userIdInput) {
        userIdInput.addEventListener('change', () => {
            checkPinStatus();
            loadContacts();
        });
    }

    // ── State Transition ──
    function switchView(state) {
        // Hide all views
        Object.values(views).forEach(v => v.classList.remove('active'));

        switch (state) {
            case 'PASSIVE':
                views.passive.classList.add('active');
                break;
            case 'VERIFYING':
                views.verify.classList.add('active');
                break;
            case 'EMERGENCY':
                views.emergency.classList.add('active');
                break;
        }

        currentState = state;
        updateButtons();
    }

    function updateButtons() {
        btnSimulateFall.disabled = currentState !== 'PASSIVE';
        btnTriplePress.disabled = currentState !== 'PASSIVE';
        btnRespondOk.disabled = currentState !== 'VERIFYING';
        btnResolve.disabled = currentState !== 'EMERGENCY';
    }

    // ── Event Logging ──
    function addEvent(type, description, dotClass = '') {
        const timestamp = new Date().toISOString();
        eventLog.push({ event_type: type, description, timestamp });
        
        // Add to phone timeline
        if (eventTimeline) {
            const entry = document.createElement('div');
            entry.className = 'timeline-entry';
            entry.innerHTML = `
                <div class="timeline-dot ${dotClass}"></div>
                <div class="timeline-text">${description}</div>
                <div class="timeline-time">${new Date().toLocaleTimeString()}</div>
            `;
            eventTimeline.prepend(entry);
        }
    }

    function log(message, type = '') {
        if (!systemLog) return;
        const entry = document.createElement('div');
        entry.className = `log-entry ${type}`;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        systemLog.prepend(entry);

        // Keep log manageable
        while (systemLog.children.length > 50) {
            systemLog.removeChild(systemLog.lastChild);
        }
    }

    // Share the console with the other simulator modules.
    window.simLog = log;

    // ── Fall Detection Callback ──
    function onFallDetected() {
        if (currentState !== 'PASSIVE') return;

        log('⚠️ Fall detected — initiating verification', 'warn');
        addEvent('fall_detected', 'Fall detected — sudden impact followed by inactivity', 'emergency');
        addEvent('no_movement', 'No movement detected for 2.5 seconds', 'verify');

        enterVerifying();
    }

    // ── VERIFYING Mode ──
    async function enterVerifying() {
        addEvent('verification_started', 'Verification started — "Are you okay?"', 'verify');
        log('🎤 Microphone activated temporarily for verification', 'warn');

        // Start audio recording NOW — captures the verification phase
        const micStarted = await AudioModule.startRecording();
        if (micStarted) {
            addEvent('mic_activated', 'Microphone recording started for verification', 'verify');
            log('🎤 Recording started — capturing verification audio', 'warn');
        } else {
            log('⚠️ Microphone unavailable — continuing without audio', 'warn');
        }

        switchView('VERIFYING');
        countdownRemaining = VERIFY_COUNTDOWN;
        updateCountdown();

        countdownTimer = setInterval(() => {
            countdownRemaining--;
            updateCountdown();

            if (countdownRemaining <= 0) {
                clearInterval(countdownTimer);
                countdownTimer = null;
                
                addEvent('no_response', 'No response from user — escalating to emergency', 'emergency');
                log('🚨 No response — activating Emergency Mode', 'error');
                
                enterEmergency('fall');
            }
        }, 1000);
    }

    function updateCountdown() {
        if (countdownNumber) {
            countdownNumber.textContent = countdownRemaining;
        }
        if (countdownCircle) {
            const progress = (1 - countdownRemaining / VERIFY_COUNTDOWN) * CIRCUMFERENCE;
            countdownCircle.style.strokeDashoffset = progress;
        }
    }

    // ── EMERGENCY Mode ──
    async function enterEmergency(triggerType) {
        addEvent('emergency_triggered', `Emergency triggered via ${triggerType === 'manual' ? 'manual panic button' : 'fall detection + no response'}`, 'emergency');
        
        switchView('EMERGENCY');
        log('🆘 Emergency Mode ACTIVE — sending SOS', 'error');

        const userId = document.getElementById('userId').value || 'user_001';

        // Activate the emergency module. It never invents a position, so a
        // browser without a location fix fails loudly instead of faking GPS.
        let info;
        try {
            info = await EmergencyModule.activate(userId, triggerType, eventLog, {
                metadata: { trigger: triggerType, simulator: true },
                onEnded: (status) => {
                    log(`🏁 Emergency ${status} by the authority dashboard`, 'success');
                    EmergencyModule.deactivate({ status: 'resolved' }).then(() => {
                        SensorModule.reset();
                        eventLog = [];
                        switchView('PASSIVE');
                    });
                },
            });
            AudioModule.setEmergency(info.id);
        } catch (error) {
            log(`❌ ${error.message}`, 'error');
            addEvent('activation_failed', error.message, 'emergency');
            switchView('PASSIVE');
            return;
        }

        // Update UI
        if (gpsCoords) {
            gpsCoords.textContent = `${info.lat.toFixed(6)}, ${info.lng.toFixed(6)}`;
        }
        if (digipinDisplay) {
            digipinDisplay.textContent = info.digipin;
        }
        if (mapsLink) {
            mapsLink.href = info.mapsLink;
        }

        addEvent('sos_sent', `SOS alert sent to backend (ID: ${info.id || 'pending'})`, 'emergency');
        addEvent('gps_acquired', `GPS location: ${info.lat.toFixed(6)}, ${info.lng.toFixed(6)}`, 'safe');
        addEvent('digipin_generated', `DigiPIN: ${info.digipin}`, 'safe');
        addEvent('audio_started', 'Audio recording started', 'verify');
        addEvent('location_tracking', 'Live location updates started (every 30s)', 'safe');

        log(`📍 GPS: ${info.lat.toFixed(6)}, ${info.lng.toFixed(6)}`, 'error');
        log(`🔢 DigiPIN: ${info.digipin}`, 'error');
        log(`🗺️ Maps: ${info.mapsLink}`, 'error');

        // SMS notification status
        if (info.sms_total > 0) {
            addEvent('sms_sent', `SMS alerts sent to ${info.sms_sent}/${info.sms_total} emergency contacts`, info.sms_sent > 0 ? 'safe' : 'emergency');
            log(`📱 SMS alerts: ${info.sms_sent}/${info.sms_total} contacts notified`, info.sms_sent > 0 ? 'success' : 'error');
        } else {
            log('⚠️ No emergency contacts configured — no SMS sent', 'warn');
        }
    }

    // ── Global Functions (called from HTML onclick) ──

    // Simulate a fall event
    window.simulateFall = function() {
        if (currentState !== 'PASSIVE') return;
        log('📉 Simulating fall event...', 'warn');
        SensorModule.simulateFall();
    };

    // Simulate triple power button press
    window.simulateTriplePress = function() {
        if (currentState !== 'PASSIVE') return;
        log('🔴 Triple power press detected — IMMEDIATE emergency', 'error');
        addEvent('manual_trigger', 'Power button pressed 3 times — manual panic trigger', 'emergency');
        eventLog = [{ event_type: 'manual_trigger', description: 'Power button pressed 3 times — manual panic trigger', timestamp: new Date().toISOString() }];
        enterEmergency('manual');
    };

    // Show PIN pad overlay when "I'm Okay" is pressed
    window.respondOk = function() {
        if (currentState !== 'VERIFYING') return;
        if (!hasPinConfigured) {
            log('❌ Cannot dismiss — no safety PIN configured! Set one first.', 'error');
            addEvent('no_pin', 'Attempted to dismiss but no PIN is configured', 'emergency');
            return;
        }
        showPinOverlay();
    };

    // PIN Overlay management
    function showPinOverlay() {
        pinEntryBuffer = '';
        updatePinDots();
        if (pinError) pinError.style.display = 'none';
        if (pinOverlay) pinOverlay.classList.add('active');
        log('🔐 PIN entry requested — verify identity', 'warn');
    }

    function hidePinOverlay() {
        pinEntryBuffer = '';
        updatePinDots();
        if (pinOverlay) pinOverlay.classList.remove('active');
    }

    function updatePinDots() {
        pinDots.forEach((dot, i) => {
            dot.classList.toggle('filled', i < pinEntryBuffer.length);
        });
    }

    // Called from PIN pad buttons in HTML
    window.pinInput = async function(digit) {
        if (pinEntryBuffer.length >= 4) return;
        pinEntryBuffer += digit;
        updatePinDots();

        if (pinEntryBuffer.length === 4) {
            // Verify PIN against backend
            const userId = document.getElementById('userId').value || 'user_001';
            let verified = false;
            try {
                await SimDevice.verifyPin(userId, pinEntryBuffer);
                verified = true;
            } catch (error) {
                log(`ℹ️ PIN rejected: ${error.message}`, 'warn');
            }
            {
                if (verified) {
                    // Correct PIN — dismiss emergency
                    hidePinOverlay();
                    confirmOk();
                } else {
                    // Wrong PIN — shake and reset
                    log('❌ Wrong PIN entered — server rejected', 'error');
                    addEvent('wrong_pin', 'Wrong safety PIN entered — identity not verified', 'emergency');
                    if (pinError) pinError.style.display = 'block';
                    const pinPad = document.querySelector('.pin-dots-row');
                    if (pinPad) {
                        pinPad.classList.add('shake');
                        setTimeout(() => pinPad.classList.remove('shake'), 500);
                    }
                    setTimeout(() => {
                        pinEntryBuffer = '';
                        updatePinDots();
                        if (pinError) pinError.style.display = 'none';
                    }, 1200);
                }
            }
        }
    };

    window.pinBackspace = function() {
        pinEntryBuffer = pinEntryBuffer.slice(0, -1);
        updatePinDots();
    };

    window.pinCancel = function() {
        hidePinOverlay();
    };

    // Actual OK confirmation after PIN is verified
    async function confirmOk() {
        if (currentState !== 'VERIFYING') return;

        if (countdownTimer) {
            clearInterval(countdownTimer);
            countdownTimer = null;
        }

        // Stop and discard verification recording — user is safe
        if (AudioModule.isActive()) {
            await AudioModule.stopRecording(); // discard the blob
            log('🎤 Recording stopped — user is safe', 'success');
        }

        addEvent('pin_verified', 'Safety PIN verified — identity confirmed', 'safe');
        addEvent('user_responded', 'User responded — "I\'m okay"', 'safe');
        log('🔐 PIN verified — identity confirmed', 'success');
        log('✅ User responded — returning to passive mode', 'success');

        SensorModule.reset();
        eventLog = [];
        switchView('PASSIVE');
    }

    // Update PIN via backend API
    window.updatePin = async function() {
        const input = document.getElementById('pinInput');
        const currentPinInput = document.getElementById('currentPinInput');
        if (!input) return;
        const newPin = input.value.trim();
        if (newPin.length !== 4 || !/^\d{4}$/.test(newPin)) {
            log('⚠️ PIN must be exactly 4 digits', 'warn');
            return;
        }

        const userId = document.getElementById('userId').value || 'user_001';
        const payload = { user_id: userId, pin: newPin };

        // If user already has a PIN, require current PIN to change it
        if (hasPinConfigured) {
            const currentPin = currentPinInput ? currentPinInput.value.trim() : '';
            if (currentPin.length !== 4 || !/^\d{4}$/.test(currentPin)) {
                log('⚠️ Enter your current 4-digit PIN to change it', 'warn');
                return;
            }
            payload.current_pin = currentPin;
        }

        try {
            await SimDevice.ensure(userId);
            await SimDevice.setPin(userId, newPin, hasPinConfigured ? payload.current_pin : undefined);
            hasPinConfigured = true;
            log(`🔐 Safety PIN updated for user "${userId}"`, 'success');
            input.value = '';
            if (currentPinInput) currentPinInput.value = '';
            // Show current PIN row for future changes
            const currentPinRow = document.getElementById('currentPinRow');
            if (currentPinRow) currentPinRow.style.display = 'flex';
            const pinHint = document.getElementById('pinHint');
            if (pinHint) pinHint.textContent = 'Enter current PIN to change it';
        } catch (error) {
            if (error.status === 403) {
                log('❌ Current PIN is incorrect — cannot change PIN', 'error');
            } else {
                log(`⚠️ Could not set PIN: ${error.message}`, 'warn');
            }
        }
    };

    // Resolve an active emergency
    window.resolveEmergency = async function() {
        if (currentState !== 'EMERGENCY') return;

        log('🏁 Resolving emergency...', 'success');
        addEvent('emergency_resolved', 'Emergency resolved by user', 'safe');

        await EmergencyModule.deactivate({
            status: 'resolved',
            note: 'Ended by the user in the simulator',
        });
        SensorModule.reset();
        eventLog = [];

        // Clear timeline
        if (eventTimeline) eventTimeline.innerHTML = '';

        log('✅ Emergency resolved — returning to passive mode', 'success');
        switchView('PASSIVE');
    };

    // ── Emergency Contacts ──

    async function loadContacts() {
        const userId = document.getElementById('userId').value || 'user_001';
        try {
            await SimDevice.ensure(userId);
            const data = await SimDevice.contacts(userId);
            renderContacts(data.contacts || []);
            if (data.count > 0) {
                log(`📱 ${data.count} emergency contact(s) loaded`, 'success');
            }
        } catch (err) {
            log('⚠️ Could not load contacts — backend may be offline', 'warn');
        }
    }

    function renderContacts(contacts) {
        const list = document.getElementById('contactsList');
        if (!list) return;

        if (contacts.length === 0) {
            list.innerHTML = '<div class="no-contacts">No emergency contacts added yet</div>';
            return;
        }

        list.innerHTML = contacts.map(c => `
            <div class="contact-card" id="contact-${c.id}">
                <div class="contact-info">
                    <div class="contact-name">${c.name}</div>
                    <div class="contact-phone">${c.phone_number}</div>
                    ${c.relationship ? `<div class="contact-relation">${c.relationship}</div>` : ''}
                </div>
                <button class="contact-delete" onclick="deleteContact(${c.id})" title="Remove contact">✕</button>
            </div>
        `).join('');
    }

    window.addContact = async function() {
        const nameEl = document.getElementById('contactName');
        const phoneEl = document.getElementById('contactPhone');
        const relationEl = document.getElementById('contactRelation');

        const name = nameEl?.value.trim();
        const phone = phoneEl?.value.trim();
        const relationship = relationEl?.value.trim() || '';

        if (!name) { log('⚠️ Contact name is required', 'warn'); return; }
        if (!phone || phone.length < 10) { log('⚠️ Valid phone number required (e.g. +919876543210)', 'warn'); return; }

        const userId = document.getElementById('userId').value || 'user_001';
        try {
            await SimDevice.ensure(userId);
            await SimDevice.addContact(userId, name, phone, relationship);
            log(`📱 Contact "${name}" added — they'll be notified in emergencies`, 'success');
            nameEl.value = '';
            phoneEl.value = '';
            relationEl.value = '';
            loadContacts();
        } catch (error) {
            log(`❌ Could not add contact: ${error.message}`, 'error');
        }
    };

    window.deleteContact = async function(contactId) {
        const userId = document.getElementById('userId').value || 'user_001';
        try {
            await SimDevice.removeContact(contactId, userId);
            log('📱 Contact removed', 'success');
            const card = document.getElementById(`contact-${contactId}`);
            if (card) {
                card.style.animation = 'fadeSlideIn 0.3s ease reverse';
                setTimeout(() => loadContacts(), 300);
            } else {
                loadContacts();
            }
        } catch (error) {
            log(`❌ Could not remove contact: ${error.message}`, 'error');
        }
    };

    window.testSms = async function() {
        const userId = document.getElementById('userId').value || 'user_001';
        log('📤 Sending test SMS to all contacts...', 'warn');
        try {
            const data = await SimDevice.testSms(userId);
            log(`📤 Test SMS: ${data.sent}/${data.total} delivered`, data.sent > 0 ? 'success' : 'error');
        } catch (error) {
            log(`⚠️ Could not send test SMS: ${error.message}`, 'warn');
        }
    };

    // ── Boot ──
    init();
    loadContacts();
})();
