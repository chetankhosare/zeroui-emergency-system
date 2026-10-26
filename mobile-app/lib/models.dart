import 'package:flutter/foundation.dart';

String nowIso() => DateTime.now().toUtc().toIso8601String();

/// Server capabilities/behaviour, fetched from `GET /api/config`.
class ServerConfig {
  const ServerConfig({
    this.countdownSeconds = 15,
    this.locationIntervalSeconds = 30,
    this.audioSegmentSeconds = 60,
    this.staleAfterSeconds = 90,
    this.digipinAvailable = true,
    this.digipinCoverage,
  });

  final int countdownSeconds;
  final int locationIntervalSeconds;
  final int audioSegmentSeconds;
  final int staleAfterSeconds;
  final bool digipinAvailable;
  final Map<String, dynamic>? digipinCoverage;

  static ServerConfig fromJson(Map<String, dynamic> json) {
    final digipin = (json['digipin'] as Map?)?.cast<String, dynamic>() ?? const {};
    return ServerConfig(
      countdownSeconds: (json['countdown_seconds'] as num?)?.toInt() ?? 15,
      locationIntervalSeconds: (json['location_interval_seconds'] as num?)?.toInt() ?? 30,
      audioSegmentSeconds: (json['audio_segment_seconds'] as num?)?.toInt() ?? 60,
      staleAfterSeconds: (json['stale_after_seconds'] as num?)?.toInt() ?? 90,
      digipinAvailable: (digipin['library_available'] as bool?) ?? true,
      digipinCoverage: (digipin['coverage'] as Map?)?.cast<String, dynamic>(),
    );
  }
}

/// The device's private identity, issued once by the backend.
class DeviceIdentity {
  const DeviceIdentity({
    required this.deviceId,
    required this.deviceToken,
    required this.userId,
  });

  final String deviceId;
  final String deviceToken;
  final String userId;
}

/// The server's view of the user's active emergency.
class EmergencyRef {
  const EmergencyRef({
    required this.emergencyId,
    required this.status,
    required this.triggerType,
    this.digipin,
    this.digipinAvailable = true,
    this.latitude,
    this.longitude,
    this.createdAt,
    this.lastUpdateAt,
  });

  final String emergencyId;
  final String status;
  final String triggerType;
  final String? digipin;
  final bool digipinAvailable;
  final double? latitude;
  final double? longitude;
  final String? createdAt;
  final String? lastUpdateAt;

  bool get isActive => status == 'emergency' || status == 'verifying';

  static EmergencyRef fromJson(Map<String, dynamic> json) => EmergencyRef(
        emergencyId: json['emergency_id'] as String? ?? '',
        status: json['status'] as String? ?? 'unknown',
        triggerType: json['trigger_type'] as String? ?? 'unknown',
        digipin: json['digipin'] as String?,
        digipinAvailable: (json['digipin_available'] as bool?) ?? true,
        latitude: (json['latitude'] as num?)?.toDouble(),
        longitude: (json['longitude'] as num?)?.toDouble(),
        createdAt: json['created_at'] as String?,
        lastUpdateAt: json['last_update_at'] as String?,
      );
}

/// A single GPS fix (30 s cadence while an emergency is active).
class LocationSample {
  const LocationSample({
    required this.latitude,
    required this.longitude,
    required this.timestamp,
    this.accuracy,
    this.altitude,
    this.speed,
    this.heading,
    this.provider = 'unknown',
    this.isMock = false,
    this.digipin,
  });

  final double latitude;
  final double longitude;
  final String timestamp;
  final double? accuracy;
  final double? altitude;
  final double? speed;
  final double? heading;
  final String provider;
  final bool isMock;

  /// Filled in by the backend response (never computed on the device).
  final String? digipin;

  LocationSample withDigipin(String? value) => LocationSample(
        latitude: latitude,
        longitude: longitude,
        timestamp: timestamp,
        accuracy: accuracy,
        altitude: altitude,
        speed: speed,
        heading: heading,
        provider: provider,
        isMock: isMock,
        digipin: value,
      );

  Map<String, dynamic> toJson() => {
        'latitude': latitude,
        'longitude': longitude,
        'timestamp': timestamp,
        if (accuracy != null) 'accuracy_m': accuracy,
        if (altitude != null) 'altitude_m': altitude,
        if (speed != null) 'speed_mps': speed,
        if (heading != null) 'heading_deg': heading,
        'provider': provider,
        'is_mock': isMock,
      };

  String get readable =>
      '${latitude.toStringAsFixed(6)}, ${longitude.toStringAsFixed(6)}';
}

/// Timeline entry (device-local trail + everything sent to the backend).
class AppEvent {
  AppEvent({
    required this.type,
    required this.description,
    this.payload = const {},
    String? timestamp,
  }) : timestamp = timestamp ?? nowIso();

  final String type;
  final String description;
  final Map<String, dynamic> payload;
  final String timestamp;

  Map<String, dynamic> toJson() => {
        'event_type': type.startsWith('local_') ? type.replaceFirst('local_', '') : type,
        'description': description,
        'timestamp': timestamp,
        'payload': payload,
      };
}

/// Everything the emergency needs to remember between UI rebuilds.
@immutable
class EmergencySession {
  const EmergencySession({
    required this.emergencyId,
    required this.triggerType,
    required this.startedAt,
    this.digipin,
    this.digipinAvailable = true,
    this.location,
    this.segmentsUploaded = 0,
    this.liveAudioActive = false,
    this.realtimeConnected = false,
    this.queued = 0,
    this.lastSyncAt,
    this.resolved = false,
    this.status = 'emergency',
  });

  final String emergencyId;
  final String triggerType;
  final DateTime startedAt;
  final String? digipin;
  final bool digipinAvailable;
  final LocationSample? location;
  final int segmentsUploaded;
  final bool liveAudioActive;
  final bool realtimeConnected;
  final int queued;
  final DateTime? lastSyncAt;
  final bool resolved;
  final String status;

  Duration get elapsed => DateTime.now().difference(startedAt);

  EmergencySession copyWith({
    String? digipin,
    bool? digipinAvailable,
    LocationSample? location,
    int? segmentsUploaded,
    bool? liveAudioActive,
    bool? realtimeConnected,
    int? queued,
    DateTime? lastSyncAt,
    bool? resolved,
    String? status,
  }) {
    return EmergencySession(
      emergencyId: emergencyId,
      triggerType: triggerType,
      startedAt: startedAt,
      digipin: digipin ?? this.digipin,
      digipinAvailable: digipinAvailable ?? this.digipinAvailable,
      location: location ?? this.location,
      segmentsUploaded: segmentsUploaded ?? this.segmentsUploaded,
      liveAudioActive: liveAudioActive ?? this.liveAudioActive,
      realtimeConnected: realtimeConnected ?? this.realtimeConnected,
      queued: queued ?? this.queued,
      lastSyncAt: lastSyncAt ?? this.lastSyncAt,
      resolved: resolved ?? this.resolved,
      status: status ?? this.status,
    );
  }
}
