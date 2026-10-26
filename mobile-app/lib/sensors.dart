import 'dart:async';
import 'dart:math' as math;

import 'package:sensors_plus/sensors_plus.dart';

import 'config.dart';

/// Evidence collected when a fall is detected (sent to the authority).
class FallDetection {
  FallDetection({
    required this.impactMagnitude,
    required this.freefallMs,
    required this.inactivityMs,
    required this.stationaryStdDev,
    DateTime? detectedAt,
  }) : detectedAt = detectedAt ?? DateTime.now();

  final double impactMagnitude;
  final int freefallMs;
  final int inactivityMs;
  final double stationaryStdDev;
  final DateTime detectedAt;

  Map<String, dynamic> toJson() => {
        'detection': 'accelerometer',
        'impact_magnitude': double.parse(impactMagnitude.toStringAsFixed(2)),
        'freefall_ms': freefallMs,
        'inactivity_ms': inactivityMs,
        'stationary_stddev': double.parse(stationaryStdDev.toStringAsFixed(2)),
        'detected_at': detectedAt.toUtc().toIso8601String(),
      };
}

/// Rule-based fall detection:
///
///   phase 1 — free fall   : |a| < freeFallThreshold for >= freeFallMinMs
///   phase 2 — impact      : |a| > impactThreshold within impactWindowMs
///   phase 3 — inactivity  : movement stays below inactivityStdDev for
///                           inactivitySeconds after the impact
///
/// All three phases must be observed, which is what keeps false positives
/// (sitting down, dropping the phone on a bed) manageable. Every threshold is
/// visible in the app's settings screen.
class FallDetector {
  FallDetector({
    required this.settings,
    required this.onFall,
    this.onSample,
    this.onAvailabilityChanged,
  });

  DetectionSettings settings;
  final void Function(FallDetection detection) onFall;
  final void Function(double magnitude)? onSample;
  final void Function(bool available)? onAvailabilityChanged;

  StreamSubscription<AccelerometerEvent>? _subscription;
  Timer? _inactivityWatchdog;

  bool available = false;
  bool running = false;

  double magnitude = 0;

  DateTime? _freeFallStart;
  bool _freeFallDetected = false;
  DateTime? _impactAt;
  double _peakImpact = 0;
  final List<double> _recentSamples = <double>[];
  DateTime? _cooldownUntil;

  static const int _sampleIntervalMs = 50;
  static const int _maxRecentSamples = 200;

  void start() {
    if (running) return;
    running = true;
    try {
      _subscription = accelerometerEventStream(
        samplingPeriod: const Duration(milliseconds: _sampleIntervalMs),
      ).listen(
        _onData,
        onError: (_) => _markUnavailable(),
        cancelOnError: false,
      );
      available = true;
      onAvailabilityChanged?.call(true);
    } catch (_) {
      _markUnavailable();
    }
  }

  void _markUnavailable() {
    available = false;
    onAvailabilityChanged?.call(false);
  }

  void stop() {
    running = false;
    _subscription?.cancel();
    _subscription = null;
    _resetPhases();
  }

  void updateSettings(DetectionSettings value) {
    settings = value;
  }

  void _resetPhases() {
    _freeFallStart = null;
    _freeFallDetected = false;
    _impactAt = null;
    _peakImpact = 0;
    _recentSamples.clear();
    _inactivityWatchdog?.cancel();
    _inactivityWatchdog = null;
  }

  void _onData(AccelerometerEvent event) {
    final value = math.sqrt(event.x * event.x + event.y * event.y + event.z * event.z);
    magnitude = value;
    onSample?.call(value);

    final now = DateTime.now();
    if (!available) {
      available = true;
      onAvailabilityChanged?.call(true);
    }

    if (_cooldownUntil != null && now.isBefore(_cooldownUntil!)) return;

    // ── phase 1: free fall ──
    if (value < settings.freeFallThreshold) {
      _freeFallStart ??= now;
      final elapsed = now.difference(_freeFallStart!).inMilliseconds;
      if (elapsed >= settings.freeFallMinMs) {
        _freeFallDetected = true;
      }
    } else if (_impactAt == null) {
      // Leaving free fall without an impact: normal movement, reset the phase.
      _freeFallStart = null;
      if (_freeFallDetected &&
          now.difference(_lastFreeFallSeen ?? now).inMilliseconds > settings.impactWindowMs) {
        _freeFallDetected = false;
      }
    }
    if (value < settings.freeFallThreshold) _lastFreeFallSeen = now;

    // ── phase 2: impact ──
    if (_impactAt == null) {
      final strongImpact = value > settings.impactThreshold;
      final plausible = _freeFallDetected ||
          value > settings.impactThreshold * 1.8; // e.g. phone in a pocket
      if (strongImpact && plausible) {
        _impactAt = now;
        _peakImpact = value;
        // The stillness window measures motion *after* the impact, so the
        // impact sample itself must not be part of the statistics.
        _recentSamples.clear();
        _startInactivityWatchdog(now);
      }
    } else {
      if (value > _peakImpact) _peakImpact = value;
      _recentSamples.add(value);
      if (_recentSamples.length > _maxRecentSamples) _recentSamples.removeAt(0);
    }
  }

  DateTime? _lastFreeFallSeen;

  void _startInactivityWatchdog(DateTime impactAt) {
    _inactivityWatchdog?.cancel();
    _inactivityWatchdog = Timer(
      Duration(milliseconds: (settings.inactivitySeconds * 1000).round()),
      () => _evaluateInactivity(impactAt),
    );
  }

  void _evaluateInactivity(DateTime impactAt) {
    if (_impactAt == null) return;
    final samples = _recentSamples.where((value) => value < settings.impactThreshold * 1.5).toList();
    if (samples.length < 5) {
      _resetPhases();
      return;
    }
    final mean = samples.reduce((a, b) => a + b) / samples.length;
    final variance =
        samples.map((value) => math.pow(value - mean, 2).toDouble()).reduce((a, b) => a + b) /
            samples.length;
    final stdDev = math.sqrt(variance);

    if (stdDev <= settings.inactivityStdDev) {
      final freefallMs = _freeFallDetected && _freeFallStart != null
          ? impactAt.difference(_freeFallStart!).inMilliseconds
          : 0;
      final detection = FallDetection(
        impactMagnitude: _peakImpact,
        freefallMs: freefallMs,
        inactivityMs: DateTime.now().difference(impactAt).inMilliseconds,
        stationaryStdDev: stdDev,
      );
      // Cooldown prevents a single event from firing repeatedly.
      _cooldownUntil = DateTime.now().add(const Duration(seconds: 12));
      _resetPhases();
      onFall(detection);
    } else {
      _resetPhases();
    }
  }

  /// Development/testing helper: feed a synthetic reading.
  void injectSample(double value) {
    _onData(AccelerometerEvent(0, 0, value, DateTime.now()));
  }

  void dispose() {
    _inactivityWatchdog?.cancel();
    stop();
  }
}
