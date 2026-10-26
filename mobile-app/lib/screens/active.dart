import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../emergency_controller.dart';
import '../widgets.dart';

/// Live emergency screen: state, timer, emergency id, location, DIGIPIN, audio
/// status, upload queue and the local event timeline — plus the two ways out
/// (resolve, or cancel with the PIN).
class ActiveEmergencyScreen extends StatefulWidget {
  const ActiveEmergencyScreen({super.key, required this.controller});

  final EmergencyController controller;

  @override
  State<ActiveEmergencyScreen> createState() => _ActiveEmergencyScreenState();
}

class _ActiveEmergencyScreenState extends State<ActiveEmergencyScreen> {
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  Future<void> _confirmResolve() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('End this emergency?'),
        content: const Text(
          'The authority dashboard will mark this emergency as resolved. '
          'Audio and location tracking stop immediately.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Keep active')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('End emergency')),
        ],
      ),
    );
    if (confirmed == true) {
      await widget.controller.resolveEmergency(note: 'Ended from the phone');
    }
  }

  Future<void> _openCancelSheet() async {
    await showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: const Color(0xFF0C0C1A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(22)),
      ),
      builder: (sheetContext) => StatefulBuilder(
        builder: (context, setSheetState) {
          String? error;
          bool busy = false;
          return Padding(
            padding: EdgeInsets.only(
              left: 20,
              right: 20,
              top: 22,
              bottom: MediaQuery.of(context).viewInsets.bottom + 24,
            ),
            child: PinPad(
              title: 'Cancel this emergency',
              subtitle: 'The PIN proves the person who started it is safe. '
                  'The dashboard will show it as cancelled, not resolved.',
              error: error,
              busy: busy,
              onSubmit: (pin) async {
                setSheetState(() {
                  busy = true;
                  error = null;
                });
                final ok = await widget.controller.cancelActiveEmergency(pin);
                if (!context.mounted) return;
                if (ok) {
                  Navigator.of(context).pop();
                } else {
                  setSheetState(() {
                    busy = false;
                    error = 'Incorrect PIN — the emergency stays active';
                  });
                }
              },
            ),
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final session = controller.session;
    final theme = Theme.of(context);
    if (session == null) return const SizedBox.shrink();

    if (session.resolved) {
      return _RecoveryView(controller: controller, session: session);
    }

    final elapsed = session.elapsed;
    final elapsedText = '${elapsed.inMinutes.toString().padLeft(2, '0')}:'
        '${(elapsed.inSeconds % 60).toString().padLeft(2, '0')}';

    return Scaffold(
      backgroundColor: const Color(0xFF120608),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 28),
          children: [
            const Center(
              child: StatusChip(
                label: 'EMERGENCY ACTIVE',
                color: Color(0xFFEF4444),
                icon: Icons.emergency,
                pulse: true,
              ),
            ),
            const SizedBox(height: 16),

            // ── identity + timer ──
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xFFEF4444).withValues(alpha: 0.10),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFFEF4444).withValues(alpha: 0.35)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text('ACTIVE FOR', style: theme.textTheme.labelSmall),
                      const Spacer(),
                      Text(
                        elapsedText,
                        style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  Text('EMERGENCY ID', style: theme.textTheme.labelSmall),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Expanded(
                        child: SelectableText(
                          session.emergencyId,
                          maxLines: 1,
                          style: const TextStyle(fontFamily: 'monospace', fontSize: 12.5),
                        ),
                      ),
                      IconButton(
                        tooltip: 'Copy',
                        iconSize: 18,
                        onPressed: () async {
                          await Clipboard.setData(ClipboardData(text: session.emergencyId));
                          if (context.mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              const SnackBar(content: Text('Emergency id copied')),
                            );
                          }
                        },
                        icon: const Icon(Icons.copy),
                      ),
                    ],
                  ),
                  Text(
                    'Trigger: ${session.triggerType} · '
                    '${session.realtimeConnected ? 'realtime channel connected' : 'reconnecting'}',
                    style: const TextStyle(fontSize: 11.5, color: Colors.white54),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 18),

            Text('LOCATION', style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.8,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                InfoTile(
                  label: 'Coordinates',
                  value: session.location?.readable ?? 'waiting for GPS',
                  icon: Icons.my_location,
                  monospace: true,
                ),
                InfoTile(
                  label: 'DIGIPIN',
                  value: session.digipin ??
                      (session.digipinAvailable ? 'pending' : 'outside India coverage'),
                  icon: Icons.pin_drop,
                  monospace: true,
                ),
                InfoTile(
                  label: 'Accuracy',
                  value: session.location?.accuracy == null
                      ? '—'
                      : '±${session.location!.accuracy!.round()} m',
                  icon: Icons.straighten,
                ),
                InfoTile(
                  label: 'Updates sent',
                  value: session.lastSyncAt == null
                      ? 'starting…'
                      : 'every ${controller.serverConfig.locationIntervalSeconds}s',
                  icon: Icons.sync,
                ),
              ],
            ),
            const SizedBox(height: 18),

            Text('AUDIO', style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.8,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                InfoTile(
                  label: 'Microphone',
                  value: controller.recording ? 'capturing' : 'stopped',
                  icon: Icons.mic,
                  valueColor: controller.recording ? null : theme.colorScheme.error,
                ),
                InfoTile(
                  label: 'Segments stored',
                  value: '${session.segmentsUploaded} (~60 s each)',
                  icon: Icons.library_music,
                ),
              ],
            ),
            const SizedBox(height: 10),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              value: controller.liveAudioEnabled,
              onChanged: controller.setLiveAudioEnabled,
              title: const Text('Stream live audio to the dashboard'),
              subtitle: const Text(
                'Segments are always recorded; live streaming sends the same '
                'microphone stream in near real-time. Stops if Android suspends '
                'the microphone or the app is killed.',
                style: TextStyle(fontSize: 11.5, color: Colors.white38, height: 1.4),
              ),
            ),
            const SizedBox(height: 14),

            Text('TRANSFER', style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            GridView.count(
              crossAxisCount: 2,
              childAspectRatio: 1.8,
              crossAxisSpacing: 10,
              mainAxisSpacing: 10,
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              children: [
                InfoTile(
                  label: 'Foreground service',
                  value: controller.recording ? 'running' : 'starting',
                  icon: Icons.notifications_active_outlined,
                ),
                InfoTile(
                  label: 'Queued offline',
                  value: '${controller.queuedItems} item(s)',
                  icon: Icons.cloud_off_outlined,
                  valueColor: controller.queuedItems > 0 ? const Color(0xFFF59E0B) : null,
                ),
              ],
            ),
            if (controller.lastError != null) ...[
              const SizedBox(height: 12),
              Text(
                controller.lastError!,
                style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
              ),
            ],
            const SizedBox(height: 20),

            Text('EVENT TIMELINE', style: theme.textTheme.labelMedium),
            const SizedBox(height: 10),
            EventTimeline(
              events: controller.events.map((event) => event.toJson()).toList(),
            ),
            const SizedBox(height: 22),

            SizedBox(
              width: double.infinity,
              child: FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: const Color(0xFF22C55E),
                  foregroundColor: Colors.black,
                  padding: const EdgeInsets.symmetric(vertical: 16),
                ),
                onPressed: _confirmResolve,
                icon: const Icon(Icons.check_circle_outline),
                label: const Text('END EMERGENCY (RESOLVED)'),
              ),
            ),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: _openCancelSheet,
                icon: const Icon(Icons.lock_outline),
                label: const Text('Cancel false alarm (PIN required)'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Shown after the emergency ends (from either side).
class _RecoveryView extends StatelessWidget {
  const _RecoveryView({required this.controller, required this.session});

  final EmergencyController controller;
  final dynamic session;

  @override
  Widget build(BuildContext context) {
    final cancelled = session.status == 'cancelled';
    final colour = cancelled ? const Color(0xFFF59E0B) : const Color(0xFF22C55E);

    return Scaffold(
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(20),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: StatusChip(
                  label: cancelled ? 'EMERGENCY CANCELLED' : 'EMERGENCY RESOLVED',
                  color: colour,
                  icon: cancelled ? Icons.cancel_outlined : Icons.verified_outlined,
                ),
              ),
              const SizedBox(height: 20),
              Text(
                cancelled
                    ? 'You confirmed you were safe. The authority dashboard shows '
                        'this event as cancelled.'
                    : 'This emergency is closed. Location and audio tracking have stopped.',
                style: const TextStyle(fontSize: 13, color: Colors.white70, height: 1.5),
              ),
              const SizedBox(height: 20),
              InfoTile(
                label: 'Emergency id',
                value: session.emergencyId,
                icon: Icons.tag,
                monospace: true,
              ),
              const SizedBox(height: 10),
              InfoTile(
                label: 'Duration',
                value: '${session.elapsed.inMinutes} min ${session.elapsed.inSeconds % 60} s',
                icon: Icons.timer_outlined,
              ),
              const SizedBox(height: 10),
              InfoTile(
                label: 'Audio segments stored',
                value: '${session.segmentsUploaded}',
                icon: Icons.library_music,
              ),
              const SizedBox(height: 10),
              InfoTile(
                label: 'Final position',
                value: session.location?.readable ?? 'unknown',
                icon: Icons.place_outlined,
                monospace: true,
              ),
              const SizedBox(height: 10),
              InfoTile(
                label: 'DIGIPIN',
                value: session.digipin ?? 'not available',
                icon: Icons.pin_drop_outlined,
                monospace: true,
              ),
              const Spacer(),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: controller.dismissRecovery,
                  child: const Text('Back to monitoring'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
