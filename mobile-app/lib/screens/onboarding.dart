import 'package:flutter/material.dart';

import '../emergency_controller.dart';
import '../widgets.dart';

/// First-run setup: explains the detection flow, requests each permission with
/// its reason, enrolls the device and sets the 4-digit safety PIN.
class OnboardingScreen extends StatefulWidget {
  const OnboardingScreen({super.key, required this.controller});

  final EmergencyController controller;

  @override
  State<OnboardingScreen> createState() => _OnboardingScreenState();
}

class _OnboardingScreenState extends State<OnboardingScreen> {
  int _step = 0;
  bool _busy = false;
  String? _error;
  String _userId = 'user_001';
  String _displayName = '';
  String _apiBase = '';
  String _enrollmentToken = '';
  String _firstPin = '';

  @override
  void initState() {
    super.initState();
    _apiBase = widget.controller.store.apiBase;
    _enrollmentToken = widget.controller.store.enrollmentToken;
    _userId = widget.controller.store.userId;
    _displayName = widget.controller.store.displayName;
    widget.controller.permissions.refreshAll().then((_) {
      if (mounted) setState(() {});
    });
  }

  Future<void> _requestPermissions() async {
    setState(() => _busy = true);
    final missing = await widget.controller.permissions.requestAll();
    if (!mounted) return;
    setState(() {
      _busy = false;
      _error = missing.isEmpty
          ? null
          : 'Still missing: ${missing.map((item) => item.title).join(', ')}. '
              'You can grant them later from Settings, but tracking and audio '
              'will not work without them.';
    });
  }

  Future<void> _saveIdentity() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.controller.updateEndpoint(
        apiBase: _apiBase,
        enrollmentToken: _enrollmentToken,
        userId: _userId,
      );
      widget.controller.store.displayName = _displayName;
      await widget.controller.ensureEnrolled();
      if (!mounted) return;
      setState(() {
        _busy = false;
        _step = 3;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = 'Could not reach the backend at $_apiBase — $error';
      });
    }
  }

  Future<void> _savePin(String pin) async {
    if (_firstPin.isEmpty) {
      setState(() {
        _firstPin = pin;
        _error = null;
      });
      return;
    }
    if (_firstPin != pin) {
      setState(() {
        _firstPin = '';
        _error = 'The PINs did not match — try again';
      });
      return;
    }
    setState(() => _busy = true);
    final ok = await widget.controller.setPin(pin);
    if (!ok) {
      if (!mounted) return;
      setState(() {
        _busy = false;
        _error = widget.controller.lastError ?? 'Could not save the PIN';
      });
      return;
    }
    await widget.controller.completeOnboarding();
    if (!mounted) return;
    setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final permissions = widget.controller.permissions.items;

    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Image.asset('assets/zeroui_logo.png', width: 32, height: 32),
                  const SizedBox(width: 10),
                  Text('Zero UI setup', style: theme.textTheme.titleLarge),
                  const Spacer(),
                  Text('${_step + 1}/4', style: theme.textTheme.labelMedium),
                ],
              ),
              const SizedBox(height: 18),
              Expanded(
                child: SingleChildScrollView(
                  child: switch (_step) {
                    0 => _welcome(),
                    1 => _permissionsStep(permissions),
                    2 => _identityStep(),
                    _ => _pinStep(),
                  },
                ),
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 10),
                  child: Text(
                    _error!,
                    style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
                  ),
                ),
              _bottomBar(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _welcome() {
    const points = [
      ('📉', 'Fall / drop detection',
          'The accelerometer watches for a free-fall, an impact and then '
          'stillness — the three phases of a real fall.'),
      ('⏱️', '15-second cancel window',
          'You get 15 seconds to confirm you are safe. Nothing is sent to the '
          'authority dashboard during that window.'),
      ('🔐', 'PIN to cancel',
          'Only the person who knows your 4-digit PIN can cancel, so a bump in '
          'your pocket cannot cancel a real emergency.'),
      ('📡', 'What is shared, and when',
          'Only after the countdown expires: live GPS (every 30 s), DIGIPIN, '
          'device/sensor status and microphone audio (~1-minute segments plus a '
          'live stream while the phone can keep the microphone).'),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'A companion app that calls for help when you cannot.',
          style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600, height: 1.4),
        ),
        const SizedBox(height: 18),
        for (final (icon, title, body) in points)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(icon, style: const TextStyle(fontSize: 20)),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 3),
                      Text(body, style: const TextStyle(fontSize: 12.5, color: Colors.white60, height: 1.45)),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _permissionsStep(List<dynamic> permissions) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Permissions', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        const Text(
          'Android requires each of these to be granted explicitly. The app asks '
          'only for what an emergency actually needs, and explains why.',
          style: TextStyle(fontSize: 12.5, color: Colors.white60, height: 1.45),
        ),
        const SizedBox(height: 16),
        for (final item in permissions)
          Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Icon(
                  item.granted ? Icons.check_circle : Icons.radio_button_unchecked,
                  size: 18,
                  color: item.granted ? const Color(0xFF22C55E) : Colors.white38,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(item.title, style: const TextStyle(fontWeight: FontWeight.w600)),
                      const SizedBox(height: 2),
                      Text(item.why,
                          style: const TextStyle(fontSize: 12, color: Colors.white60, height: 1.4)),
                    ],
                  ),
                ),
              ],
            ),
          ),
        const SizedBox(height: 4),
        const Text(
          'Note: Android turns the "Location: always" request into a trip to '
          'system settings. If it stays denied the app will keep working while '
          'it is open, but not in the background.',
          style: TextStyle(fontSize: 11.5, color: Colors.white38, height: 1.4),
        ),
        const SizedBox(height: 14),
        Row(
          children: [
            FilledButton.icon(
              onPressed: _busy ? null : _requestPermissions,
              icon: const Icon(Icons.lock_open, size: 18),
              label: const Text('Grant permissions'),
            ),
            const SizedBox(width: 10),
            TextButton(
              onPressed: () => widget.controller.permissions.openSettings(),
              child: const Text('Open settings'),
            ),
          ],
        ),
      ],
    );
  }

  Widget _identityStep() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Who and where', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
        const SizedBox(height: 6),
        const Text(
          'The backend identifies this phone by the user id below. Each user can '
          'have exactly one active emergency, and the authority dashboard shows '
          'this id next to the emergency.',
          style: TextStyle(fontSize: 12.5, color: Colors.white60, height: 1.45),
        ),
        const SizedBox(height: 16),
        TextField(
          decoration: const InputDecoration(labelText: 'User id', border: OutlineInputBorder()),
          controller: TextEditingController(text: _userId),
          onChanged: (value) => _userId = value.trim(),
        ),
        const SizedBox(height: 12),
        TextField(
          decoration: const InputDecoration(
            labelText: 'Display name (optional)',
            border: OutlineInputBorder(),
          ),
          controller: TextEditingController(text: _displayName),
          onChanged: (value) => _displayName = value.trim(),
        ),
        const SizedBox(height: 12),
        TextField(
          decoration: const InputDecoration(labelText: 'Backend URL', border: OutlineInputBorder()),
          controller: TextEditingController(text: _apiBase),
          onChanged: (value) => _apiBase = value.trim(),
        ),
        const SizedBox(height: 12),
        TextField(
          decoration: const InputDecoration(
            labelText: 'Enrollment token',
            border: OutlineInputBorder(),
            helperText: 'ZEROUI_ENROLLMENT_TOKEN from backend/.env',
          ),
          controller: TextEditingController(text: _enrollmentToken),
          onChanged: (value) => _enrollmentToken = value.trim(),
        ),
      ],
    );
  }

  Widget _pinStep() {
    return PinPad(
      title: _firstPin.isEmpty ? 'Create your safety PIN' : 'Confirm your PIN',
      subtitle: 'This PIN cancels a false alarm. It is stored hashed on the '
          'device (and on the backend) — never in plain text.',
      busy: _busy,
      error: _error,
      onSubmit: _savePin,
    );
  }

  Widget _bottomBar() {
    if (_step == 3) return const SizedBox.shrink();
    final isLast = _step == 2;
    return Row(
      children: [
        if (_step > 0)
          TextButton(
            onPressed: _busy ? null : () => setState(() => _step -= 1),
            child: const Text('Back'),
          ),
        const Spacer(),
        FilledButton(
          onPressed: _busy
              ? null
              : () async {
                  if (isLast) {
                    await _saveIdentity();
                  } else {
                    setState(() {
                      _step += 1;
                      _error = null;
                    });
                  }
                },
          child: _busy
              ? const SizedBox(height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
              : Text(isLast ? 'Enroll device' : 'Continue'),
        ),
      ],
    );
  }
}
