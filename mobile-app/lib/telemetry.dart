import 'dart:io';

import 'package:battery_plus/battery_plus.dart';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:device_info_plus/device_info_plus.dart';
import 'package:geolocator/geolocator.dart';

import 'config.dart';

/// Static device description (sent on emergency creation and shown on the
/// dashboard next to the emergency id).
class DeviceDescription {
  const DeviceDescription({
    required this.manufacturer,
    required this.model,
    required this.osVersion,
    required this.sdkInt,
  });

  final String manufacturer;
  final String model;
  final String osVersion;
  final int sdkInt;

  Map<String, dynamic> toJson() => {
        'manufacturer': manufacturer,
        'model': model,
        'os_version': osVersion,
        'sdk_int': sdkInt,
        'app_version': AppConfig.appVersion,
        'platform': 'android',
      };

  static const DeviceDescription unknown = DeviceDescription(
    manufacturer: 'unknown',
    model: 'unknown',
    osVersion: 'unknown',
    sdkInt: 0,
  );
}

/// Collects the live status the authority dashboard shows for an emergency.
class TelemetryCollector {
  TelemetryCollector();

  final Battery _battery = Battery();
  final Connectivity _connectivity = Connectivity();

  DeviceDescription? _cached;

  Future<DeviceDescription> device() async {
    if (_cached != null) return _cached!;
    if (!Platform.isAndroid) {
      _cached = DeviceDescription.unknown;
      return _cached!;
    }
    try {
      final info = await DeviceInfoPlugin().androidInfo;
      _cached = DeviceDescription(
        manufacturer: info.manufacturer,
        model: info.model,
        osVersion: 'Android ${info.version.release}',
        sdkInt: info.version.sdkInt,
      );
    } catch (_) {
      _cached = DeviceDescription.unknown;
    }
    return _cached!;
  }

  /// Sensor / hardware capabilities actually available on this device.
  ///
  /// Nothing is reported as working unless the platform says so: the values are
  /// the runtime permission/service state plus the hardware feature flags.
  Future<Map<String, bool>> capabilities({
    required bool microphoneGranted,
    required bool accelerometerWorking,
  }) async {
    var gpsEnabled = false;
    var locationGranted = false;
    try {
      gpsEnabled = await Geolocator.isLocationServiceEnabled();
      final permission = await Geolocator.checkPermission();
      locationGranted = permission == LocationPermission.always ||
          permission == LocationPermission.whileInUse;
    } catch (_) {}

    var backgroundLocation = false;
    try {
      backgroundLocation = await Geolocator.checkPermission() == LocationPermission.always;
    } catch (_) {}

    var hasAccelerometer = accelerometerWorking;
    var hasGyroscope = false;
    var hasMagnetometer = false;
    if (Platform.isAndroid) {
      try {
        final info = await DeviceInfoPlugin().androidInfo;
        hasAccelerometer = accelerometerWorking || info.systemFeatures.contains('android.hardware.sensor.accelerometer');
        hasGyroscope = info.systemFeatures.contains('android.hardware.sensor.gyroscope');
        hasMagnetometer = info.systemFeatures.contains('android.hardware.sensor.compass');
      } catch (_) {}
    }

    return {
      'accelerometer': hasAccelerometer,
      'gyroscope': hasGyroscope,
      'magnetometer': hasMagnetometer,
      'gps_service': gpsEnabled,
      'location_permission': locationGranted,
      'background_location': backgroundLocation,
      'microphone': microphoneGranted,
    };
  }

  Future<Map<String, dynamic>> collect({
    required bool foregroundServiceRunning,
    required bool audioActive,
    required int segmentSequence,
    required Map<String, bool> capabilities,
    double? accelerometerMagnitude,
    bool? screenOn,
    String appState = 'foreground',
  }) async {
    int? batteryLevel;
    bool? charging;
    try {
      batteryLevel = await _battery.batteryLevel;
      final state = await _battery.batteryState;
      charging = state == BatteryState.charging || state == BatteryState.full;
    } catch (_) {}

    String? networkType;
    String connectivity = 'unknown';
    try {
      final results = await _connectivity.checkConnectivity();
      final hasWifi = results.contains(ConnectivityResult.wifi) ||
          results.contains(ConnectivityResult.ethernet);
      final hasMobile = results.contains(ConnectivityResult.mobile);
      final hasAny = results.any((result) => result != ConnectivityResult.none);
      networkType = hasWifi ? 'wifi' : (hasMobile ? 'mobile' : (hasAny ? 'other' : 'offline'));
      connectivity = hasAny ? 'online' : 'offline';
    } catch (_) {}

    return {
      'battery_pct': ?batteryLevel,
      'charging': ?charging,
      'network_type': ?networkType,
      'connectivity': connectivity,
      'app_state': appState,
      'foreground_service': foregroundServiceRunning,
      'screen_on': ?screenOn,
      'audio_session_active': audioActive,
      'audio_segment_sequence': segmentSequence,
      if (accelerometerMagnitude != null)
        'accelerometer_magnitude': double.parse(accelerometerMagnitude.toStringAsFixed(2)),
      'sensors': capabilities,
      'collected_at': DateTime.now().toUtc().toIso8601String(),
    };
  }
}
