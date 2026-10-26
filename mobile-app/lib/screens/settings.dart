import 'package:flutter/material.dart';

import '../emergency_controller.dart';
import '../telemetry.dart';
import '../widgets.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.controller});

  final EmergencyController controller;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _apiBase;
  late final TextEditingController _enrollmentToken;
  late final TextEditingController _userId;
  late final TextEditingController _displayName;

  bool _busy = false;
  String _message = '';
  DeviceDescription _device = DeviceDescription.unknown;

  @override
  void initState() {
    super.initState();
    final store = widget.controller.store;
    _apiBase = TextEditingController(text: store.apiBase);
    _enrollmentToken = TextEditingController(text: store.enrollmentToken);
    _userId = TextEditingController(text: store.userId);
    _displayName = TextEditingController(text: store.displayName);
    widget.controller.telemetry.device().then((value) {
      if (mounted) setState(() => _device = value);
    });
    widget.controller.permissions.refreshAll().then((_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _apiBase.dispose();
    _enrollmentToken.dispose();
    _userId.dispose();
    _displayName.dispose();
    super.dispose();
  }

  Future<void> _saveEndpoint() async {
    setState(() {
      _busy = true;
      _message = '';
    });
    await widget.controller.updateEndpoint(
      apiBase: _apiBase.text,
      enrollmentToken: _enrollmentToken.text,
      userId: _userId.text,
    );
    widget.controller.store.displayName = _displayName.text;
    setState(() {
      _busy = false;
      _message = widget.controller.backendReachable
          ? 'Saved. Backend reachable and this device is enrolled.'
          : 'Saved, but the backend could not be reached: ${widget.controller.lastError ?? 'unknown error'}';
    });
  }

  Future<void> _setPin() async {
    // If the backend already holds a PIN for this user, it must be confirmed
    // before a change is accepted (the backend enforces this and would 403).
    final backendHasPin = await widget.controller.backendHasPin();
    if (!mounted) return;

    String? error;
    String? current;
    String first = '';
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF0C0C1A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(22)),
      ),
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) => Padding(
          padding: EdgeInsets.only(
            left: 20,
            right: 20,
            top: 22,
            bottom: MediaQuery.of(context).viewInsets.bottom + 24,
          ),
          child: PinPad(
            title: backendHasPin && current == null
                ? 'Enter current PIN'
                : first.isEmpty
                    ? 'New safety PIN'
                    : 'Confirm new PIN',
            subtitle: backendHasPin && current == null
                ? 'Confirm your existing PIN to change it.'
                : 'Four digits. Used to cancel a false alarm.',
            error: error,
            onSubmit: (pin) async {
              if (backendHasPin && current == null) {
                setSheetState(() => current = pin);
                return;
              }
              if (first.isEmpty) {
                setSheetState(() => first = pin);
                return;
              }
              if (first != pin) {
                setSheetState(() {
                  first = '';
                  error = 'PINs did not match';
                });
                return;
              }
              final ok = await widget.controller
                  .setPin(pin, currentPin: backendHasPin ? current : null);
              if (!context.mounted) return;
              if (ok) {
                Navigator.of(context).pop();
              } else {
                setSheetState(() => error = widget.controller.lastError ?? 'Could not save the PIN');
              }
            },
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final theme = Theme.of(context);
    final detection = controller.store.detection;

    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 32),
        children: [
          Text('BACKEND', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          TextField(
            controller: _apiBase,
            decoration: const InputDecoration(
              labelText: 'Backend URL',
              helperText: 'LAN IP for a real phone, 10.0.2.2 for the emulator',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _enrollmentToken,
            decoration: const InputDecoration(
              labelText: 'Enrollment token',
              helperText: 'ZEROUI_ENROLLMENT_TOKEN from backend/.env',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _userId,
            decoration: const InputDecoration(
              labelText: 'User id',
              helperText: 'Changing this re-enrolls the device (one active '
                  'emergency per user is enforced by the backend)',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            controller: _displayName,
            decoration: const InputDecoration(labelText: 'Display name', border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              FilledButton(
                onPressed: _busy ? null : _saveEndpoint,
                child: _busy
                    ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                    : const Text('Save & re-enroll'),
              ),
              const SizedBox(width: 12),
              Text('Device: ${controller.api.identity?.deviceId.substring(0, 8) ?? '—'}',
                  style: theme.textTheme.bodySmall),
            ],
          ),
          if (_message.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(_message, style: theme.textTheme.bodySmall),
            ),
          const Divider(height: 40),

          Text('SAFETY PIN', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.lock_outline),
            title: Text(controller.store.hasPin ? 'Change safety PIN' : 'Set safety PIN'),
            subtitle: const Text('Required to cancel a false alarm and to stop an active emergency'),
            trailing: const Icon(Icons.chevron_right),
            onTap: _setPin,
          ),
          const Divider(height: 40),

          Text('DETECTION SENSITIVITY', style: theme.textTheme.labelMedium),
          const SizedBox(height: 4),
          Text(
            'Impact threshold ${detection.impactThreshold.toStringAsFixed(1)} m/s² · '
            'free-fall below ${detection.freeFallThreshold.toStringAsFixed(1)} m/s² · '
            'still for ${detection.inactivitySeconds.toStringAsFixed(1)} s',
            style: theme.textTheme.bodySmall,
          ),
          Slider(
            value: detection.impactThreshold,
            min: 12,
            max: 45,
            divisions: 33,
            label: '${detection.impactThreshold.toStringAsFixed(1)} m/s²',
            onChanged: (value) => controller.updateDetectionSettings(
              detection.copyWith(impactThreshold: value),
            ),
          ),
          Slider(
            value: detection.freeFallThreshold,
            min: 1,
            max: 8,
            divisions: 14,
            label: '${detection.freeFallThreshold.toStringAsFixed(1)} m/s²',
            onChanged: (value) => controller.updateDetectionSettings(
              detection.copyWith(freeFallThreshold: value),
            ),
          ),
          Slider(
            value: detection.inactivitySeconds,
            min: 1,
            max: 8,
            divisions: 14,
            label: '${detection.inactivitySeconds.toStringAsFixed(1)} s',
            onChanged: (value) => controller.updateDetectionSettings(
              detection.copyWith(inactivitySeconds: value),
            ),
          ),
          const Divider(height: 40),

          Text('PERMISSIONS', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          for (final item in controller.permissions.items)
            ListTile(
              contentPadding: EdgeInsets.zero,
              dense: true,
              leading: Icon(
                item.granted ? Icons.check_circle : Icons.error_outline,
                color: item.granted ? const Color(0xFF22C55E) : theme.colorScheme.error,
                size: 18,
              ),
              title: Text(item.title, style: const TextStyle(fontSize: 13)),
              subtitle: Text(item.why, style: const TextStyle(fontSize: 11, color: Colors.white38)),
              onTap: () async {
                await item.request();
                if (mounted) setState(() {});
              },
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              OutlinedButton(
                onPressed: () async {
                  await controller.permissions.requestAll();
                  if (mounted) setState(() {});
                },
                child: const Text('Request all'),
              ),
              const SizedBox(width: 12),
              TextButton(
                onPressed: controller.permissions.openSettings,
                child: const Text('System settings'),
              ),
            ],
          ),
          const Divider(height: 40),

          Text('DIAGNOSTICS', style: theme.textTheme.labelMedium),
          const SizedBox(height: 8),
          InfoTile(
            label: 'Device',
            value: '${_device.manufacturer} ${_device.model} · ${_device.osVersion} (API ${_device.sdkInt})',
            icon: Icons.phone_android,
          ),
          const SizedBox(height: 10),
          InfoTile(
            label: 'Backend / realtime',
            value: 'REST ${controller.backendReachable ? 'ok' : 'down'} · '
                'socket ${controller.deviceSocketConnected ? 'connected' : 'disconnected'}',
            icon: Icons.sync_alt,
          ),
          const SizedBox(height: 10),
          InfoTile(
            label: 'Offline queue',
            value: '${controller.queuedItems} item(s) waiting',
            icon: Icons.cloud_off,
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Developer / test mode'),
            subtitle: const Text('Shows the "inject simulated drop" button'),
            value: controller.store.devMode,
            onChanged: (value) {
              controller.store.devMode = value;
              setState(() {});
            },
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              OutlinedButton(
                onPressed: () async {
                  await controller.outbox.flush();
                  await controller.outbox.refreshPending();
                  setState(() => controller.queuedItems = controller.outbox.pending);
                },
                child: const Text('Flush queue'),
              ),
              const SizedBox(width: 12),
              TextButton(
                onPressed: () async {
                  await controller.outbox.clear();
                  setState(() => controller.queuedItems = 0);
                },
                child: const Text('Clear queue'),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
