import 'dart:convert';
import 'dart:math';

import 'package:crypto/crypto.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// ---------------------------------------------------------------------------
/// Build-time configuration (override with --dart-define at build time).
///
///   flutter build apk --dart-define=ZEROUI_API_BASE=http://192.168.1.5:8000 \
///                     --dart-define=ZEROUI_ENROLLMENT_TOKEN=...
///
/// The emulator reaches the host machine at 10.0.2.2, which is the default.
/// Everything here can also be changed at runtime in the in-app Settings screen.
/// ---------------------------------------------------------------------------
class AppConfig {
  static const String apiBase = String.fromEnvironment(
    'ZEROUI_API_BASE',
    defaultValue: 'http://10.0.2.2:8000',
  );

  static const String enrollmentToken = String.fromEnvironment(
    'ZEROUI_ENROLLMENT_TOKEN',
    defaultValue: 'zeroui-dev-enroll-token',
  );

  /// Server-provided defaults, used until `/api/config` answers.
  static const int countdownSeconds = 15;
  static const int locationIntervalSeconds = 30;
  static const int audioSegmentSeconds = 60;
  static const int staleAfterSeconds = 90;

  /// Audio format used for live streaming and segments (PCM16 mono).
  static const int sampleRate = 16000;
  static const int channels = 1;
  static const int bytesPerSample = 2;

  static const String appVersion = '2.0.0';
}

/// Accelerometer thresholds for the fall detector (user tunable).
class DetectionSettings {
  const DetectionSettings({
    this.freeFallThreshold = 4.0,
    this.freeFallMinMs = 120,
    this.impactThreshold = 22.0,
    this.impactWindowMs = 1500,
    this.inactivitySeconds = 2.5,
    this.inactivityStdDev = 1.6,
  });

  final double freeFallThreshold;
  final int freeFallMinMs;
  final double impactThreshold;
  final int impactWindowMs;
  final double inactivitySeconds;
  final double inactivityStdDev;

  DetectionSettings copyWith({
    double? freeFallThreshold,
    int? freeFallMinMs,
    double? impactThreshold,
    int? impactWindowMs,
    double? inactivitySeconds,
    double? inactivityStdDev,
  }) {
    return DetectionSettings(
      freeFallThreshold: freeFallThreshold ?? this.freeFallThreshold,
      freeFallMinMs: freeFallMinMs ?? this.freeFallMinMs,
      impactThreshold: impactThreshold ?? this.impactThreshold,
      impactWindowMs: impactWindowMs ?? this.impactWindowMs,
      inactivitySeconds: inactivitySeconds ?? this.inactivitySeconds,
      inactivityStdDev: inactivityStdDev ?? this.inactivityStdDev,
    );
  }

  Map<String, dynamic> toJson() => {
        'freeFallThreshold': freeFallThreshold,
        'freeFallMinMs': freeFallMinMs,
        'impactThreshold': impactThreshold,
        'impactWindowMs': impactWindowMs,
        'inactivitySeconds': inactivitySeconds,
        'inactivityStdDev': inactivityStdDev,
      };

  static DetectionSettings fromJson(Map<String, dynamic> json) => DetectionSettings(
        freeFallThreshold: (json['freeFallThreshold'] as num?)?.toDouble() ?? 4.0,
        freeFallMinMs: (json['freeFallMinMs'] as num?)?.toInt() ?? 120,
        impactThreshold: (json['impactThreshold'] as num?)?.toDouble() ?? 22.0,
        impactWindowMs: (json['impactWindowMs'] as num?)?.toInt() ?? 1500,
        inactivitySeconds: (json['inactivitySeconds'] as num?)?.toDouble() ?? 2.5,
        inactivityStdDev: (json['inactivityStdDev'] as num?)?.toDouble() ?? 1.6,
      );
}

/// ---------------------------------------------------------------------------
/// Session store — everything that must survive a process kill.
/// ---------------------------------------------------------------------------
class SessionStore {
  SessionStore._(this._prefs);

  final SharedPreferences _prefs;
  static SessionStore? _instance;

  static Future<SessionStore> instance() async {
    _instance ??= SessionStore._(await SharedPreferences.getInstance());
    return _instance!;
  }

  // ── endpoint / identity ──
  String get apiBase => _prefs.getString('zeroui.apiBase') ?? AppConfig.apiBase;
  set apiBase(String value) => _prefs.setString('zeroui.apiBase', _normaliseBase(value));

  String get enrollmentToken =>
      _prefs.getString('zeroui.enrollToken') ?? AppConfig.enrollmentToken;
  set enrollmentToken(String value) => _prefs.setString('zeroui.enrollToken', value.trim());

  String get userId => _prefs.getString('zeroui.userId') ?? 'user_001';
  set userId(String value) => _prefs.setString('zeroui.userId', value.trim());

  String get displayName => _prefs.getString('zeroui.displayName') ?? '';
  set displayName(String value) => _prefs.setString('zeroui.displayName', value.trim());

  String? get deviceId => _prefs.getString('zeroui.deviceId');
  String? get deviceToken => _prefs.getString('zeroui.deviceToken');

  Future<void> saveDevice(String deviceId, String deviceToken) async {
    await _prefs.setString('zeroui.deviceId', deviceId);
    await _prefs.setString('zeroui.deviceToken', deviceToken);
  }

  Future<void> clearDevice() async {
    await _prefs.remove('zeroui.deviceId');
    await _prefs.remove('zeroui.deviceToken');
  }

  // ── onboarding ──
  bool get onboardingComplete => _prefs.getBool('zeroui.onboardingDone') ?? false;
  set onboardingComplete(bool value) => _prefs.setBool('zeroui.onboardingDone', value);

  bool get devMode => _prefs.getBool('zeroui.devMode') ?? false;
  set devMode(bool value) => _prefs.setBool('zeroui.devMode', value);

  // ── safety PIN (hash kept locally so cancelling works offline) ──
  bool get hasPin => (_prefs.getString('zeroui.pinHash') ?? '').isNotEmpty;

  Future<void> savePinHash(String pin) async {
    await _prefs.setString('zeroui.pinHash', _hashPin(pin));
  }

  /// Clears the local PIN (used when the backend rejects a first-time save,
  /// so the app doesn't keep a PIN the backend doesn't know about).
  Future<void> clearPin() async {
    await _prefs.remove('zeroui.pinHash');
  }

  bool verifyPin(String pin) {
    final stored = _prefs.getString('zeroui.pinHash') ?? '';
    if (stored.isEmpty) return false;
    return stored == _hashPin(pin);
  }

  static String _hashPin(String pin) {
    // Local convenience hash; the backend stores its own salted hash.
    return sha256.convert(utf8.encode('zeroui-local:$pin')).toString();
  }

  // ── detection settings ──
  DetectionSettings get detection {
    final raw = _prefs.getString('zeroui.detection');
    if (raw == null) return const DetectionSettings();
    try {
      return DetectionSettings.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      return const DetectionSettings();
    }
  }

  Future<void> saveDetection(DetectionSettings settings) =>
      _prefs.setString('zeroui.detection', jsonEncode(settings.toJson()));

  // ── active emergency (crash / process-death recovery) ──
  String? get activeEmergencyId => _prefs.getString('zeroui.activeEmergencyId');
  String? get activeEmergencyStartedAt => _prefs.getString('zeroui.activeEmergencyStartedAt');

  /// Countdown deadline for the fall-triggered cancel window. Persisted so a
  /// process death during the 15-second window still resolves correctly:
  /// restore the countdown if time is left, or confirm the emergency if not.
  String? get countdownDeadline => _prefs.getString('zeroui.countdownDeadline');
  Future<void> saveCountdownDeadline(DateTime? deadline) async {
    if (deadline == null) {
      await _prefs.remove('zeroui.countdownDeadline');
    } else {
      await _prefs.setString('zeroui.countdownDeadline', deadline.toIso8601String());
    }
  }

  Future<void> saveActiveEmergency(String? emergencyId, String? startedAt) async {
    if (emergencyId == null) {
      await _prefs.remove('zeroui.activeEmergencyId');
      await _prefs.remove('zeroui.activeEmergencyStartedAt');
      await _prefs.remove('zeroui.activeTrail');
      return;
    }
    await _prefs.setString('zeroui.activeEmergencyId', emergencyId);
    if (startedAt != null) {
      await _prefs.setString('zeroui.activeEmergencyStartedAt', startedAt);
    }
  }

  /// Local detection trail collected before the emergency became active.
  List<Map<String, dynamic>> get trail {
    final raw = _prefs.getString('zeroui.activeTrail');
    if (raw == null) return [];
    try {
      return (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  Future<void> saveTrail(List<Map<String, dynamic>> entries) =>
      _prefs.setString('zeroui.activeTrail', jsonEncode(entries));

  // ── last report shown on the home screen ──
  String? get lastReport => _prefs.getString('zeroui.lastReport');
  set lastReport(String? value) {
    if (value == null) {
      _prefs.remove('zeroui.lastReport');
    } else {
      _prefs.setString('zeroui.lastReport', value);
    }
  }

  /// Unsent payloads (offline queue metadata lives next to the segment files).
  List<Map<String, dynamic>> get outbox {
    final raw = _prefs.getString('zeroui.outbox');
    if (raw == null) return [];
    try {
      return (jsonDecode(raw) as List).cast<Map<String, dynamic>>();
    } catch (_) {
      return [];
    }
  }

  Future<void> saveOutbox(List<Map<String, dynamic>> items) =>
      _prefs.setString('zeroui.outbox', jsonEncode(items));

  int get nextQueueIndex => _prefs.getInt('zeroui.queueIndex') ?? 0;
  Future<void> setNextQueueIndex(int value) => _prefs.setInt('zeroui.queueIndex', value);

  static String _normaliseBase(String value) {
    var base = value.trim();
    if (base.isEmpty) return AppConfig.apiBase;
    if (!base.startsWith('http://') && !base.startsWith('https://')) {
      base = 'http://$base';
    }
    while (base.endsWith('/')) {
      base = base.substring(0, base.length - 1);
    }
    return base;
  }

  static String newLocalId() {
    final random = Random();
    final bytes = List<int>.generate(8, (_) => random.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
