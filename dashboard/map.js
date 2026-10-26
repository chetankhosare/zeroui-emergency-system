/**
 * Leaflet map module
 * ==================
 * Status-aware markers, selection highlight and the location track of the
 * selected emergency (drawn as a polyline with a separate "latest" marker).
 */

const MapModule = (() => {
    const DEFAULT_CENTER = [19.8762, 75.3433];   // Chhatrapati Sambhajinagar
    const DEFAULT_ZOOM = 12;

    let map = null;
    let markerLayer = null;
    let trackLayer = null;
    let markers = {};
    let selectedId = null;
    let hasAutoFocused = false;

    function iconClass(emergency) {
        if (emergency.connection_state === 'stale' && emergency.status === 'emergency') return 'marker-stale';
        if (emergency.status === 'emergency') return 'marker-emergency';
        if (emergency.status === 'verifying') return 'marker-verifying';
        if (emergency.status === 'cancelled') return 'marker-cancelled';
        return 'marker-resolved';
    }

    function createMarkerIcon(emergency, selected) {
        return L.divIcon({
            className: `${iconClass(emergency)}${selected ? ' marker-selected' : ''}`,
            iconSize: [26, 26],
            iconAnchor: [13, 13],
            popupAnchor: [0, -16],
        });
    }

    function escapeHtml(value) {
        const div = document.createElement('div');
        div.textContent = value === null || value === undefined ? '' : String(value);
        return div.innerHTML;
    }

    function popupContent(emergency) {
        const mapsUrl = `https://www.google.com/maps?q=${emergency.latitude},${emergency.longitude}`;
        return `
            <div class="popup-content">
                <div class="popup-user">${escapeHtml(emergency.user_id)}</div>
                <div class="popup-row">Status: <strong>${escapeHtml(emergency.status.toUpperCase())}</strong>
                    · ${escapeHtml(emergency.connection_state.toUpperCase())}</div>
                <div class="popup-row">Trigger: ${escapeHtml(emergency.trigger_type)}</div>
                <div class="popup-row">DigiPIN: <span class="popup-digipin">${escapeHtml(emergency.digipin || 'unavailable')}</span></div>
                <div class="popup-row">Coords: ${Number(emergency.latitude).toFixed(6)}, ${Number(emergency.longitude).toFixed(6)}</div>
                <div class="popup-row">ID: <code>${escapeHtml(emergency.emergency_id)}</code></div>
                <div class="popup-row"><a href="${mapsUrl}" target="_blank" style="color:#5dade2;">Open in Google Maps ↗</a></div>
            </div>
        `;
    }

    return {
        init() {
            // Idempotent: boot() runs again after every sign-in, and Leaflet
            // throws "Map container is already initialized" on a second init.
            if (map) {
                map.invalidateSize();
                return map;
            }
            map = L.map('mapContainer', {
                center: DEFAULT_CENTER,
                zoom: DEFAULT_ZOOM,
                zoomControl: true,
                attributionControl: true,
            });
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '© OpenStreetMap contributors',
                maxZoom: 19,
            }).addTo(map);
            markerLayer = L.layerGroup().addTo(map);
            trackLayer = L.layerGroup().addTo(map);
            return map;
        },

        updateMarkers(emergencies) {
            const alive = new Set();
            emergencies.forEach((emergency) => {
                if (emergency.latitude === null || emergency.longitude === null) return;
                alive.add(emergency.emergency_id);
                const selected = emergency.emergency_id === selectedId;
                const icon = createMarkerIcon(emergency, selected);
                const latlng = [emergency.latitude, emergency.longitude];

                if (markers[emergency.emergency_id]) {
                    markers[emergency.emergency_id].setLatLng(latlng);
                    markers[emergency.emergency_id].setIcon(icon);
                    markers[emergency.emergency_id].setPopupContent(popupContent(emergency));
                } else {
                    const marker = L.marker(latlng, { icon })
                        .addTo(markerLayer)
                        .bindPopup(popupContent(emergency));
                    marker.on('click', () => {
                        if (typeof window.showDetail === 'function') {
                            window.showDetail(emergency.emergency_id);
                        }
                    });
                    markers[emergency.emergency_id] = marker;
                }
            });

            Object.keys(markers).forEach((id) => {
                if (!alive.has(id)) {
                    markerLayer.removeLayer(markers[id]);
                    delete markers[id];
                }
            });
        },

        setSelected(emergencyId) {
            selectedId = emergencyId;
        },

        /** Draw the location track of the selected emergency only. */
        drawTrack(locations, status) {
            trackLayer.clearLayers();
            if (!locations || locations.length === 0) return;
            const points = locations
                .filter((l) => l.latitude !== null && l.longitude !== null)
                .map((l) => [l.latitude, l.longitude]);
            if (points.length === 0) return;

            const color = status === 'emergency' ? '#ef4444'
                : (status === 'cancelled' ? '#eab308' : '#22c55e');

            if (points.length > 1) {
                L.polyline(points, { color, weight: 3, opacity: 0.85, dashArray: '6 6' })
                    .addTo(trackLayer);
            }
            L.circleMarker(points[0], {
                radius: 5, color, weight: 2, fillColor: color, fillOpacity: 0.5,
            }).bindTooltip('First known position').addTo(trackLayer);
            L.circleMarker(points[points.length - 1], {
                radius: 7, color: '#ffffff', weight: 2, fillColor: color, fillOpacity: 0.95,
            }).bindTooltip('Latest position').addTo(trackLayer);
        },

        clearTrack() {
            if (trackLayer) trackLayer.clearLayers();
        },

        focusOn(lat, lng, zoom = 16) {
            if (map) map.flyTo([lat, lng], zoom, { duration: 0.8 });
        },

        fitToTrack(locations) {
            if (!map || !locations || locations.length === 0) return;
            const bounds = L.latLngBounds(locations.map((l) => [l.latitude, l.longitude]));
            map.fitBounds(bounds.pad(0.25), { maxZoom: 17 });
        },

        /** Auto-centre once on the newest active emergency, then stay put. */
        autoFocus(emergencies) {
            if (hasAutoFocused) return;
            const active = emergencies.find((e) => e.status === 'emergency');
            if (active && active.latitude !== null) {
                hasAutoFocused = true;
                this.focusOn(active.latitude, active.longitude, 15);
            }
        },

        invalidateSize() {
            if (map) setTimeout(() => map.invalidateSize(), 100);
        },
    };
})();
