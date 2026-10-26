import 'package:flutter/material.dart';

import '../emergency_controller.dart';
import '../widgets.dart';

/// The 15-second cancellation window.
///
/// Nothing about this event has been sent to the backend yet: only if the timer
/// reaches zero does the app create an emergency. Cancelling therefore needs a
/// PIN but produces **no** authority-visible record.
class CountdownScreen extends StatelessWidget {
  const CountdownScreen({super.key, required this.controller});

  final EmergencyController controller;

  Future<void> _openPinSheet(BuildContext context) async {
    final theme = Theme.of(context);
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
              title: 'Confirm you are safe',
              subtitle: 'Enter your 4-digit PIN to cancel the alarm.',
              error: error,
              busy: busy,
              onSubmit: (pin) async {
                setSheetState(() {
                  busy = true;
                  error = null;
                });
                final ok = await controller.confirmSafeWithPin(pin);
                if (!context.mounted) return;
                if (ok) {
                  Navigator.of(context).pop();
                } else {
                  setSheetState(() {
                    busy = false;
                    error = 'Incorrect PIN — the alarm is still running';
                  });
                }
              },
            ),
          );
        },
      ),
    );
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(controller.notice ?? 'Alarm cancelled'),
          backgroundColor: theme.colorScheme.surface,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final total = controller.countdownTotal.inSeconds.clamp(1, 600);
    final remaining = controller.countdownRemaining.clamp(0, total);
    final progress = 1 - (remaining / total);

    return Scaffold(
      backgroundColor: const Color(0xFF14090B),
      body: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(22),
          child: Column(
            children: [
              const StatusChip(
                label: 'POSSIBLE FALL DETECTED',
                color: Color(0xFFEF4444),
                icon: Icons.warning_amber_rounded,
                pulse: true,
              ),
              const SizedBox(height: 10),
              Text(
                'Sending help in',
                style: theme.textTheme.titleMedium?.copyWith(color: Colors.white70),
              ),
              Expanded(
                child: Center(
                  child: SizedBox(
                    width: 230,
                    height: 230,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        SizedBox(
                          width: 230,
                          height: 230,
                          child: CircularProgressIndicator(
                            value: progress,
                            strokeWidth: 12,
                            backgroundColor: Colors.white12,
                            valueColor: const AlwaysStoppedAnimation(Color(0xFFEF4444)),
                          ),
                        ),
                        Text(
                          '$remaining',
                          style: const TextStyle(
                            fontSize: 72,
                            fontWeight: FontWeight.w800,
                          ),
                        ),
                        Positioned(
                          bottom: 40,
                          child: Text(
                            'seconds',
                            style: theme.textTheme.bodySmall?.copyWith(color: Colors.white54),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
              if (controller.lastDetectionSummary != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 16),
                  child: Text(
                    controller.lastDetectionSummary!,
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 12, color: Colors.white54),
                  ),
                ),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: const Color(0xFF22C55E),
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(vertical: 18),
                  ),
                  onPressed: () => _openPinSheet(context),
                  icon: const Icon(Icons.check_circle_outline),
                  label: const Text(
                    "I'M SAFE — CANCEL",
                    style: TextStyle(fontWeight: FontWeight.w800, letterSpacing: 0.5),
                  ),
                ),
              ),
              const SizedBox(height: 12),
              const Text(
                'Nothing has been shared with the authority dashboard yet.',
                style: TextStyle(fontSize: 11.5, color: Colors.white38),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
