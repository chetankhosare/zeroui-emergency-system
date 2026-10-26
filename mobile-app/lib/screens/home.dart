import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../emergency_controller.dart';
import '../widgets.dart';
import 'settings.dart';

/// Passive monitoring screen: live sensor readout, backend/device state and the
/// manual SOS triggers. Clearly separates "monitoring" from "emergency".
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.controller});

  final EmergencyController controller;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  bool _locationOn = false;
  String _permissionSummary = '';

  @override
  void initState() {
    super.initState();
    _refreshStatus();
  }

  Future<void> _refreshStatus() async {
    var serviceOn = false;
    String summary = '';
    try {
      serviceOn = await Geolocator.isLocationServiceEnabled();
      final permission = await Geolocator.checkPermission();
      summary = switch (permission) {
        LocationPermission.always => 'granted (always)',
        LocationPermission.whileInUse => 'granted (while in use only)',
        LocationPermission.denied => 'denied',
        LocationPermission.deniedForever => 'blocked in settings',
        _ => permission.name,
      };
    } catch (_) {}
    if (mounted) {
      setState(() {
        _locationOn = serviceOn;
        _permissionSummary = summary;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final theme = Theme.of(context);
    final statusColor = controller.phase == AppPhase.passive
        ? const Color(0xFF22C55E)
        : const Color(0xFFF59E0B);

    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Image.asset('assets/zeroui_logo.png', width: 30, height: 30),
            const SizedBox(width: 10),
            const Text('Zero UI'),
          ],
        ),
        actions: [
          IconButton(
            tooltip: 'Refresh status',
            onPressed: _refreshStatus,
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: 'Settings',
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => SettingsScreen(controller: controller)),
            ),
            icon: const Icon(Icons.settings_outlined),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refreshStatus,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 28),
          children: [
            Center(
              child: StatusChip(
                label: 'MONITORING FOR FALLS',
                color: statusColor,
                icon: Icons.shield_outlined,
                pulse: true,
              ),
            ),
            const SizedBox(height: 16),

            // ── SOS ──
            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFFEF4444),
                  padding: const EdgeInsets.symmetric(vertical: 20),
                ),
                onPressed: controller.triggerManualSos,
                icon: const Icon(Icons.sos, size: 26),
                label: const Text(
                  'SEND SOS NOW',
                  style: TextStyle(fontWeight: FontWeight.w800, letterSpacing: 0.6),
                ),
              ),
            ),
            const SizedBox(height: 8),
            const Text(
              'SOS also starts the 15-second countdown, so a mistaken press can be '
              'cancelled with your PIN before anything is sent.',
              style: TextStyle(fontSize: 11.5, color: Colors.white38, height: 1.4),
            ),
            const SizedBox(height: 20),

            // ── Sensors ──
            Text('SENSORS', style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.85,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                InfoTile(
                  label: 'Accelerometer',
                  value: controller.accelerometerAvailable
                      ? '${controller.accelerometerMagnitude.toStringAsFixed(2)} m/s²'
                      : 'unavailable',
                  icon: Icons.speed,
                  valueColor: controller.accelerometerAvailable ? null : theme.colorScheme.error,
                ),
                InfoTile(
                  label: 'Motion state',
                  value: controller.accelerometerMagnitude > 2.5 ? 'moving' : 'still',
                  icon: Icons.directions_walk,
                ),
                InfoTile(
                  label: 'GPS service',
                  value: _locationOn ? 'enabled' : 'off',
                  icon: Icons.gps_fixed,
                  valueColor: _locationOn ? null : theme.colorScheme.error,
                ),
                InfoTile(
                  label: 'Location permission',
                  value: _permissionSummary.isEmpty ? 'unknown' : _permissionSummary,
                  icon: Icons.place_outlined,
                ),
              ],
            ),
            const SizedBox(height: 20),

            // ── Connectivity / identity ──
            Text('CONNECTION', style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.85,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                InfoTile(
                  label: 'Backend',
                  value: controller.backendReachable ? 'reachable' : 'unreachable',
                  icon: Icons.cloud_outlined,
                  valueColor: controller.backendReachable ? null : theme.colorScheme.error,
                ),
                InfoTile(
                  label: 'Queued items',
                  value: '${controller.queuedItems}',
                  icon: Icons.outbox_outlined,
                ),
                InfoTile(
                  label: 'Device id',
                  value: controller.api.identity?.deviceId.substring(0, 8) ?? 'not enrolled',
                  icon: Icons.smartphone,
                  monospace: true,
                ),
                InfoTile(
                  label: 'Server',
                  value: controller.store.apiBase,
                  icon: Icons.dns_outlined,
                ),
              ],
            ),
            const SizedBox(height: 20),

            if (controller.lastDigipin != null) ...[
              Text('LAST LOCATION', style: theme.textTheme.labelMedium),
              const SizedBox(height: 8),
              InfoTile(
                label: 'DIGIPIN (India Post)',
                value: controller.lastDigipin!,
                icon: Icons.pin_drop_outlined,
                monospace: true,
              ),
              const SizedBox(height: 20),
            ],

            if (controller.store.devMode) ...[
              Text('DEVELOPER / TEST', style: theme.textTheme.labelMedium),
              const SizedBox(height: 8),
              OutlinedButton.icon(
                onPressed: controller.simulateDrop,
                icon: const Icon(Icons.science_outlined),
                label: const Text('Inject simulated drop (runs the real detector)'),
              ),
              const SizedBox(height: 6),
              const Text(
                'Injects free-fall → impact → stillness into the production fall '
                'detector, so the 15-second countdown starts exactly as it would '
                'in a real drop.',
                style: TextStyle(fontSize: 11, color: Colors.white38, height: 1.4),
              ),
              const SizedBox(height: 20),
            ],

            if (controller.store.lastReport != null)
              InfoTile(
                label: 'Last emergency',
                value: controller.store.lastReport!,
                icon: Icons.history,
              ),

            if (controller.lastError != null) ...[
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.error.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  controller.lastError!,
                  style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
