import 'dart:async';

import 'package:flutter/services.dart';

/// Dart side of the native **passive fall-monitoring** service
/// (`in.zeroui/monitoring` → [FallMonitoringService] on Android).
///
/// When the app is minimised or the screen locks, Android freezes scheduled
/// Dart isolates, so the Dart sensor subscription can silently stop. This
/// native service runs the same three-phase rule (free fall → impact →
/// stillness) on a dedicated sensor thread, keeps the process alive and wakes
/// Dart with a `fallDetected` event so the real countdown / PIN / backend
/// state machine starts even from a locked screen.
class MonitoringServiceBridge {
  static const MethodChannel _channel = MethodChannel('in.zeroui/monitoring');
  static const MethodChannel _events = MethodChannel('in.zeroui/monitoring/events');

  static bool _initialized = false;
  static bool running = false;

  /// Wire the event channel once; callbacks receive native fall reports and
  /// SOS-gesture triggers (both funnel into the shared emergency machine).
  static void ensureEventHandlers({
    void Function(Map<String, dynamic>)? onFallDetected,
    void Function(Map<String, dynamic>)? onSosTriggered,
  }) {
    if (_initialized) return;
    _initialized = true;
    _events.setMethodCallHandler((call) async {
      final args = (call.arguments as Map?)?.cast<Object?, Object?>() ?? const {};
      switch (call.method) {
        case 'fallDetected':
          onFallDetected?.call(args.map((k, v) => MapEntry('$k', v)));
          break;
        case 'sosTriggered':
          onSosTriggered?.call(args.map((k, v) => MapEntry('$k', v)));
          break;
        case 'monitorState':
          final args = (call.arguments as Map?)?.cast<Object?, Object?>() ?? const {};
          running = '${args['state'] ?? ''}' != 'stopped';
          break;
      }
      return null;
    });
  }

  Future<bool> start({required Map<String, dynamic> settings}) async {
    try {
      final ok = await _channel.invokeMethod<bool>('start', settings);
      running = ok ?? false;
      return running;
    } on PlatformException catch (error) {
      throw ForegroundServiceException('Could not start monitoring: ${error.message ?? error.code}');
    } on MissingPluginException {
      // Not Android (tests, desktop): treat as "not available", never a crash.
      return false;
    }
  }

  Future<void> stop({bool cancel = false}) async {
    try {
      await _channel.invokeMethod<bool>('stop', {'cancel': cancel});
    } catch (_) {}
    running = false;
  }

  Future<void> updateSettings(Map<String, dynamic> settings) async {
    try {
      await _channel.invokeMethod<bool>('setSettings', settings);
    } catch (_) {}
  }

  Future<bool> isRunning() async {
    try {
      final result = await _channel.invokeMethod<Map<Object?, Object?>>('isRunning');
      running = '${result?['running'] ?? false}' == 'true' || result?['running'] == true;
    } catch (_) {
      running = false;
    }
    return running;
  }

  /// Dart consumed the pending fall: clear the native pending-fall state.
  Future<void> clearPendingFall() async {
    try {
      await _channel.invokeMethod<bool>('clearPendingFall');
    } catch (_) {}
  }
}

/// Dart side of the native emergency foreground service
/// (`in.zeroui/foreground` → [EmergencyForegroundService] on Android).
///
/// Why a foreground service: Android throttles and eventually kills background
/// processes, which would silently end location updates and audio capture.
/// While the service runs the process stays alive and the user always sees an
/// ongoing notification (Android requires one).
class ForegroundServiceBridge {
  static const MethodChannel _channel = MethodChannel('in.zeroui/foreground');

  /// Cached so the UI can show the real state even before the first call.
  static bool running = false;

  Future<bool> start({required String title, required String text}) async {
    try {
      final result = await _channel.invokeMethod<bool>('start', {
        'title': title,
        'text': text,
      });
      running = result ?? false;
      return running;
    } on PlatformException catch (error) {
      throw ForegroundServiceException(
        error.message ?? 'Could not start the emergency service',
      );
    } on MissingPluginException {
      return false;
    }
  }

  Future<void> update({required String title, required String text}) async {
    if (!running) return;
    try {
      await _channel.invokeMethod<bool>('update', {'title': title, 'text': text});
    } catch (_) {
      // Notification updates are cosmetic; never let them break the session.
    }
  }

  Future<void> stop() async {
    try {
      await _channel.invokeMethod<bool>('stop');
    } catch (_) {}
    running = false;
  }

  Future<bool> isRunning() async {
    try {
      running = await _channel.invokeMethod<bool>('isRunning') ?? false;
    } catch (_) {
      running = false;
    }
    return running;
  }
}

class ForegroundServiceException implements Exception {
  ForegroundServiceException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// A single permission the app asks for, with the reason shown to the user.
class PermissionItem {
  PermissionItem({
    required this.key,
    required this.title,
    required this.why,
    this.required = true,
  });

  final String key;
  final String title;
  final String why;
  final bool required;

  /// One of: granted | denied | restricted | notRequired.
  String status = 'denied';

  /// True once a system dialog has been shown for this permission.
  bool requested = false;

  bool get granted => status == 'granted' || status == 'notRequired';

  /// Android will no longer show a dialog: the user must use system settings.
  bool get needsSettings => !granted && requested;

  void applyStatus(String? value) {
    status = value ?? 'denied';
  }

  Future<void> request() async {
    await PermissionService.instance.requestKeys([key]);
    requested = true;
  }
}

/// Runtime permissions, backed by the native [PermissionBridge] channel.
///
/// `permission_handler` is deliberately not used: its Android plugin (14.x)
/// requires a newer Android Gradle Plugin than this project builds with, and
/// this way the exact grant state (including "must be changed in settings") is
/// reported honestly to the setup screens.
class PermissionService {
  PermissionService() {
    items = [
      PermissionItem(
        key: 'notifications',
        title: 'Notifications',
        why: 'Shows the ongoing "emergency active" notification that Android '
            'requires before the app can keep tracking in the background.',
      ),
      PermissionItem(
        key: 'location_when_in_use',
        title: 'Location (while using the app)',
        why: 'Sends live GPS coordinates and the derived DIGIPIN while an '
            'emergency is active.',
      ),
      PermissionItem(
        key: 'location_always',
        title: 'Location (always)',
        why: 'Keeps sending position updates when the phone is in a pocket, the '
            'screen is off, or the app is in the background. Android 11+ only '
            'allows this from system settings.',
      ),
      PermissionItem(
        key: 'microphone',
        title: 'Microphone',
        why: 'Captures emergency audio — ~1-minute segments plus the live '
            'stream — and only after an emergency has actually been triggered.',
      ),
      PermissionItem(
        key: 'battery_optimisation',
        title: 'Battery optimisation exemption',
        why: 'Without the exemption Android may freeze the app mid-emergency '
            'and stop location and audio updates.',
        required: false,
      ),
    ];
    _instance = this;
  }

  static const MethodChannel channel = MethodChannel('in.zeroui/permissions');
  static PermissionService? _instance;

  static PermissionService get instance {
    _instance ??= PermissionService();
    return _instance!;
  }

  late final List<PermissionItem> items;

  PermissionItem item(String key) => items.firstWhere((entry) => entry.key == key);

  List<String> get _keys => items.map((entry) => entry.key).toList();

  Future<Map<String, String>> _status(List<String> keys) async {
    try {
      final result = await channel.invokeMethod<Map<Object?, Object?>>(
        'status',
        {'permissions': keys},
      );
      return {
        for (final entry in (result ?? const {}).entries)
          '${entry.key}': '${entry.value}',
      };
    } on PlatformException {
      return {};
    } on MissingPluginException {
      return {};
    }
  }

  Future<void> _apply(List<String> keys) async {
    final statuses = await _status(keys);
    for (final entry in statuses.entries) {
      item(entry.key).applyStatus(entry.value);
    }
  }

  Future<List<PermissionItem>> refreshAll() async {
    await _apply(_keys);
    return items;
  }

  /// Ask for one or more permissions (sequential dialogs, Android-safe order).
  Future<void> requestKeys(List<String> keys) async {
    final runtime = keys.where((key) => key != 'battery_optimisation').toList();
    if (runtime.isNotEmpty) {
      try {
        await channel.invokeMethod<Map<Object?, Object?>>('request', {'permissions': runtime});
      } catch (_) {}
    }
    if (keys.contains('battery_optimisation')) {
      try {
        await channel.invokeMethod<bool>('requestIgnoreBatteryOptimizations');
      } catch (_) {}
    }
    await _apply(keys);
  }

  /// Full setup flow: notifications + foreground location + microphone first,
  /// then background location (Android 11+ routes this through settings), then
  /// the battery exemption.
  Future<List<PermissionItem>> requestAll() async {
    await refreshAll();

    final first = ['notifications', 'location_when_in_use', 'microphone']
        .where((key) => !item(key).granted)
        .toList();
    if (first.isNotEmpty) {
      await requestKeys(first);
      for (final key in first) {
        item(key).requested = true;
      }
    }

    if (!item('location_always').granted) {
      await requestKeys(['location_always']);
      item('location_always').requested = true;
    }

    if (!item('battery_optimisation').granted) {
      await requestKeys(['battery_optimisation']);
      item('battery_optimisation').requested = true;
    }

    await refreshAll();
    return missing;
  }

  Future<bool> isGranted(String key) async {
    await _apply([key]);
    return item(key).granted;
  }

  Future<void> openSettings() async {
    try {
      await channel.invokeMethod<bool>('openSettings');
    } catch (_) {}
  }

  /// Everything required for a functional emergency session.
  bool get ready => items.where((item) => item.required).every((item) => item.granted);

  List<PermissionItem> get missing =>
      items.where((item) => item.required && !item.granted).toList();

  Map<String, String> statusMap() => {for (final entry in items) entry.key: entry.status};
}
