import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 4-digit safety PIN pad. Submits automatically once four digits are entered.
class PinPad extends StatefulWidget {
  const PinPad({
    super.key,
    required this.title,
    required this.subtitle,
    required this.onSubmit,
    this.error,
    this.busy = false,
    this.actionLabel = 'Confirm',
  });

  final String title;
  final String subtitle;
  final Future<void> Function(String pin) onSubmit;
  final String? error;
  final bool busy;
  final String actionLabel;

  @override
  State<PinPad> createState() => _PinPadState();
}

class _PinPadState extends State<PinPad> {
  String _entry = '';

  void _press(String digit) {
    if (widget.busy || _entry.length >= 4) return;
    HapticFeedback.selectionClick();
    setState(() => _entry += digit);
    if (_entry.length == 4) {
      final pin = _entry;
      Future<void>.delayed(const Duration(milliseconds: 120), () async {
        await widget.onSubmit(pin);
        if (mounted) setState(() => _entry = '');
      });
    }
  }

  void _backspace() {
    if (widget.busy || _entry.isEmpty) return;
    HapticFeedback.selectionClick();
    setState(() => _entry = _entry.substring(0, _entry.length - 1));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(widget.title, style: theme.textTheme.titleLarge, textAlign: TextAlign.center),
        const SizedBox(height: 6),
        Text(
          widget.subtitle,
          style: theme.textTheme.bodySmall,
          textAlign: TextAlign.center,
        ),
        const SizedBox(height: 20),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: List.generate(4, (index) {
            final filled = index < _entry.length;
            return Container(
              margin: const EdgeInsets.symmetric(horizontal: 8),
              width: 16,
              height: 16,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: filled ? theme.colorScheme.primary : Colors.transparent,
                border: Border.all(
                  color: filled ? theme.colorScheme.primary : Colors.white24,
                  width: 2,
                ),
              ),
            );
          }),
        ),
        const SizedBox(height: 16),
        if (widget.error != null)
          Text(
            widget.error!,
            style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
            textAlign: TextAlign.center,
          ),
        const SizedBox(height: 8),
        SizedBox(
          width: 260,
          child: Column(
            children: [
              for (final row in const [
                ['1', '2', '3'],
                ['4', '5', '6'],
                ['7', '8', '9'],
              ])
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    for (final digit in row) _Key(label: digit, onTap: () => _press(digit)),
                  ],
                ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  const SizedBox(width: 68),
                  _Key(label: '0', onTap: () => _press('0')),
                  _Key(label: '⌫', onTap: _backspace),
                ],
              ),
            ],
          ),
        ),
        if (widget.busy) ...[
          const SizedBox(height: 16),
          const SizedBox(
            height: 18,
            width: 18,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ],
      ],
    );
  }
}

class _Key extends StatelessWidget {
  const _Key({required this.label, required this.onTap});

  final String label;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(6),
      child: InkWell(
        borderRadius: BorderRadius.circular(40),
        onTap: onTap,
        child: Container(
          width: 68,
          height: 68,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: Colors.white.withValues(alpha: 0.06),
            border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
          ),
          child: Text(
            label,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w600),
          ),
        ),
      ),
    );
  }
}

/// Small label/value tile used across the home and emergency screens.
class InfoTile extends StatelessWidget {
  const InfoTile({
    super.key,
    required this.label,
    required this.value,
    this.icon,
    this.valueColor,
    this.monospace = false,
  });

  final String label;
  final String value;
  final IconData? icon;
  final Color? valueColor;
  final bool monospace;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withValues(alpha: 0.07)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (icon != null) ...[
                Icon(icon, size: 13, color: theme.colorScheme.primary),
                const SizedBox(width: 5),
              ],
              Expanded(
                child: Text(
                  label.toUpperCase(),
                  style: theme.textTheme.labelSmall?.copyWith(
                    letterSpacing: 0.8,
                    color: Colors.white54,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            value,
            style: theme.textTheme.bodyMedium?.copyWith(
              fontWeight: FontWeight.w600,
              color: valueColor,
              fontFamily: monospace ? 'monospace' : null,
            ),
          ),
        ],
      ),
    );
  }
}

/// Pill showing a status word with a matching colour.
class StatusChip extends StatelessWidget {
  const StatusChip({
    super.key,
    required this.label,
    required this.color,
    this.icon,
    this.pulse = false,
  });

  final String label;
  final Color color;
  final IconData? icon;
  final bool pulse;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(30),
        border: Border.all(color: color.withValues(alpha: 0.45)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: color),
            const SizedBox(width: 6),
          ],
          Text(
            label,
            style: TextStyle(
              color: color,
              fontWeight: FontWeight.w700,
              fontSize: 12,
              letterSpacing: 0.4,
            ),
          ),
          if (pulse) ...[
            const SizedBox(width: 8),
            Container(
              width: 8,
              height: 8,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: color,
                boxShadow: [BoxShadow(color: color, blurRadius: 8)],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// Vertical event timeline used on the emergency screen.
class EventTimeline extends StatelessWidget {
  const EventTimeline({super.key, required this.events, this.maxEntries = 60});

  final List<Map<String, dynamic>> events;
  final int maxEntries;

  @override
  Widget build(BuildContext context) {
    if (events.isEmpty) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 12),
        child: Text('No events yet', style: TextStyle(color: Colors.white38, fontSize: 12)),
      );
    }
    final visible = events.reversed.take(maxEntries).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final event in visible)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  margin: const EdgeInsets.only(top: 5),
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _eventColor(event['event_type'] as String? ?? ''),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        (event['description'] as String? ?? '').trim(),
                        style: const TextStyle(fontSize: 13, height: 1.35),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${event['event_type']} · ${_clock(event['timestamp'] as String?)}',
                        style: const TextStyle(fontSize: 10.5, color: Colors.white38),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  static String _clock(String? iso) {
    if (iso == null) return '';
    final parsed = DateTime.tryParse(iso);
    if (parsed == null) return '';
    final local = parsed.toLocal();
    return '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}:'
        '${local.second.toString().padLeft(2, '0')}';
  }

  static Color _eventColor(String type) {
    if (RegExp(r'emergency|sos|fall|cancel|no_response').hasMatch(type)) {
      return const Color(0xFFEF4444);
    }
    if (RegExp(r'audio|mic|countdown|location|gps|digipin|live').hasMatch(type)) {
      return const Color(0xFFF59E0B);
    }
    if (RegExp(r'resolved|safe|sms|connected').hasMatch(type)) {
      return const Color(0xFF22C55E);
    }
    return Colors.white38;
  }
}
