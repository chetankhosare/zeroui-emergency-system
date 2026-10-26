import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import 'config.dart';
import 'models.dart';

class ApiException implements Exception {
  ApiException(this.statusCode, this.message);

  final int? statusCode;
  final String message;

  bool get isClientError => statusCode != null && statusCode! >= 400 && statusCode! < 500;

  @override
  String toString() => message;
}

/// REST + WebSocket client for the Zero UI backend.
///
/// Every call carries the device's private `X-Device-Token`, so the backend can
/// scope all data to this user. Requests are retried on transient failures and
/// the caller can fall back to the [Outbox] when the network is unavailable.
class ApiClient {
  ApiClient({required SessionStore store, http.Client? httpClient})
      : _store = store,
        _http = httpClient ?? http.Client();

  final SessionStore _store;
  final http.Client _http;

  DeviceIdentity? identity;
  ServerConfig serverConfig = const ServerConfig();

  String get baseUrl => _store.apiBase;

  Map<String, String> _headers({bool json = true}) => {
        if (json) 'Content-Type': 'application/json',
        if (identity != null) 'X-Device-Token': identity!.deviceToken,
      };

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<Map<String, dynamic>> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool auth = true,
    int retries = 2,
  }) async {
    Object? lastError;
    for (var attempt = 0; attempt <= retries; attempt++) {
      try {
        final request = http.Request(method, _uri(path));
        request.headers.addAll(_headers());
        if (body != null) request.body = jsonEncode(body);

        if (auth && identity == null) {
          throw ApiException(401, 'Device is not enrolled yet');
        }

        final response = await _http.send(request).timeout(const Duration(seconds: 20));
        final text = await response.stream.bytesToString();
        if (response.statusCode >= 200 && response.statusCode < 300) {
          if (text.isEmpty) return <String, dynamic>{};
          final decoded = jsonDecode(text);
          return decoded is Map<String, dynamic> ? decoded : {'data': decoded};
        }
        throw ApiException(response.statusCode, _extractDetail(text, response.statusCode));
      } on ApiException {
        rethrow;
      } catch (error) {
        lastError = error;
        if (attempt < retries) {
          await Future<void>.delayed(Duration(milliseconds: 400 * (attempt + 1) * 2));
        }
      }
    }
    throw ApiException(null, 'Network error: $lastError');
  }

  static String _extractDetail(String body, int status) {
    try {
      final decoded = jsonDecode(body);
      if (decoded is Map && decoded['detail'] != null) {
        final detail = decoded['detail'];
        return detail is String ? detail : jsonEncode(detail);
      }
    } catch (_) {}
    return 'HTTP $status';
  }

  // ── configuration & enrolment ──────────────────────────────────────

  Future<ServerConfig> fetchConfig() async {
    final json = await _send('GET', '/api/config', auth: false);
    serverConfig = ServerConfig.fromJson(json);
    return serverConfig;
  }

  Future<DeviceIdentity> enroll({
    required String userId,
    String displayName = '',
    required Map<String, dynamic> capabilities,
    required Map<String, dynamic> device,
  }) async {
    final response = await _http.post(
      _uri('/api/devices/register'),
      headers: {
        'Content-Type': 'application/json',
        'X-Enrollment-Token': _store.enrollmentToken,
      },
      body: jsonEncode({
        'user_id': userId,
        'display_name': displayName,
        'platform': 'android',
        'manufacturer': device['manufacturer'] ?? '',
        'model': device['model'] ?? '',
        'os_version': device['os_version'] ?? '',
        'app_version': AppConfig.appVersion,
        'capabilities': capabilities,
      }),
    );
    if (response.statusCode != 200) {
      throw ApiException(response.statusCode, _extractDetail(response.body, response.statusCode));
    }
    final json = jsonDecode(response.body) as Map<String, dynamic>;
    final result = DeviceIdentity(
      deviceId: json['device_id'] as String,
      deviceToken: json['device_token'] as String,
      userId: json['user_id'] as String,
    );
    identity = result;
    await _store.saveDevice(result.deviceId, result.deviceToken);
    return result;
  }

  Future<Map<String, dynamic>> deviceInfo() => _send('GET', '/api/devices/me');

  // ── emergency lifecycle ────────────────────────────────────────────

  Future<Map<String, dynamic>> startEmergency({
    required String triggerType,
    required LocationSample location,
    required Map<String, dynamic> triggerMetadata,
    required Map<String, dynamic> capabilities,
    required Map<String, dynamic> device,
    required int countdownSeconds,
    required String clientEmergencyId,
    required List<AppEvent> events,
  }) {
    return _send('POST', '/api/emergencies/start', body: {
      'trigger_type': triggerType,
      'latitude': location.latitude,
      'longitude': location.longitude,
      if (location.accuracy != null) 'accuracy_m': location.accuracy,
      if (location.altitude != null) 'altitude_m': location.altitude,
      'location_provider': location.provider,
      'location_timestamp': location.timestamp,
      'countdown_seconds': countdownSeconds,
      'trigger_metadata': triggerMetadata,
      'capabilities': capabilities,
      'device': device,
      'client_emergency_id': clientEmergencyId,
      'event_logs': events.map((e) => e.toJson()).toList(),
    });
  }

  Future<EmergencyRef?> activeEmergency() async {
    final json = await _send('GET', '/api/emergencies/active');
    final emergency = json['emergency'];
    if (json['has_active_emergency'] == true && emergency is Map) {
      return EmergencyRef.fromJson(emergency.cast<String, dynamic>());
    }
    return null;
  }

  Future<EmergencyRef> emergencyDetail(String emergencyId) async {
    final json = await _send('GET', '/api/emergencies/$emergencyId');
    return EmergencyRef.fromJson(json);
  }

  /// Returns the server-generated DigiPIN (or null when unavailable).
  Future<String?> postLocation(String emergencyId, LocationSample sample) async {
    final json = await _send('POST', '/api/emergencies/$emergencyId/locations',
        body: sample.toJson());
    return json['digipin'] as String?;
  }

  Future<void> postTelemetry(String emergencyId, Map<String, dynamic> telemetry) =>
      _send('POST', '/api/emergencies/$emergencyId/telemetry', body: telemetry);

  Future<void> postEvent(String emergencyId, AppEvent event) =>
      _send('POST', '/api/emergencies/$emergencyId/events', body: event.toJson());

  Future<Map<String, dynamic>> uploadSegment({
    required String emergencyId,
    required int sequence,
    required Uint8List wav,
    required double durationSeconds,
    String? startedAt,
    String? endedAt,
  }) async {
    final request = http.MultipartRequest(
      'POST',
      _uri('/api/emergencies/$emergencyId/audio/segments'),
    );
    request.headers.addAll(_headers(json: false));
    request.fields['sequence'] = '$sequence';
    request.fields['duration_seconds'] = durationSeconds.toStringAsFixed(1);
    request.fields['sample_rate'] = '${AppConfig.sampleRate}';
    request.fields['channels'] = '${AppConfig.channels}';
    request.fields['codec'] = 'pcm_s16le';
    if (startedAt != null) request.fields['started_at'] = startedAt;
    if (endedAt != null) request.fields['ended_at'] = endedAt;
    request.files.add(http.MultipartFile.fromBytes(
      'file',
      wav,
      filename: 'segment_${sequence.toString().padLeft(4, '0')}.wav',
    ));

    final streamed = await request.send().timeout(const Duration(seconds: 90));
    final response = await http.Response.fromStream(streamed);
    if (response.statusCode != 200) {
      throw ApiException(response.statusCode, _extractDetail(response.body, response.statusCode));
    }
    return jsonDecode(response.body) as Map<String, dynamic>;
  }

  Future<void> endEmergency(
    String emergencyId,
    String status, {
    String? pin,
    bool pinVerified = false,
    String? reason,
    String? note,
  }) {
    return _send('POST', '/api/emergencies/$emergencyId/status', body: {
      'status': status,
      'pin': ?pin,
      'pin_verified': pinVerified,
      'reason': ?reason,
      'note': ?note,
    });
  }

  Future<List<Map<String, dynamic>>> listSegments(String emergencyId) async {
    final json = await _send('GET', '/api/emergencies/$emergencyId/audio/segments');
    return (json['segments'] as List? ?? const []).cast<Map<String, dynamic>>();
  }

  // ── PIN / contacts ─────────────────────────────────────────────────

  Future<bool> setPin(String userId, String pin, {String? currentPin}) async {
    // Propagate ApiException so callers can show the backend's real reason
    // (e.g. 403 "Current PIN required to change an existing PIN").
    await _send('POST', '/api/pin/set', body: {
      'user_id': userId,
      'pin': pin,
      'current_pin': ?currentPin,
    });
    return true;
  }

  /// Whether the backend already holds a PIN for this user.
  Future<bool> hasBackendPin(String userId) async {
    try {
      final json = await _send('GET', '/api/pin/status/${Uri.encodeComponent(userId)}');
      return json['has_pin'] == true;
    } on ApiException {
      return false;
    }
  }

  Future<bool> verifyPin(String userId, String pin) async {
    try {
      await _send('POST', '/api/pin/verify', body: {'user_id': userId, 'pin': pin});
      return true;
    } on ApiException {
      return false;
    }
  }

  Future<List<Map<String, dynamic>>> contacts(String userId) async {
    final json = await _send('GET', '/api/contacts/$userId');
    return (json['contacts'] as List? ?? const []).cast<Map<String, dynamic>>();
  }

  Future<void> addContact(String userId, String name, String phone, String relationship) =>
      _send('POST', '/api/contacts',
          body: {'user_id': userId, 'name': name, 'phone_number': phone, 'relationship': relationship});

  Uri deviceSocketUri(String emergencyId) {
    final base = Uri.parse(baseUrl);
    final scheme = base.scheme == 'https' ? 'wss' : 'ws';
    return Uri(
      scheme: scheme,
      host: base.host,
      port: base.hasPort ? base.port : (base.scheme == 'https' ? 443 : 80),
      path: '/ws/device/$emergencyId',
      queryParameters: {'token': identity?.deviceToken ?? ''},
    );
  }

  /// Raw audio frames go straight to the backend socket (no base64 overhead).
  Uri liveAudioUri(String emergencyId) {
    final base = Uri.parse(baseUrl);
    final scheme = base.scheme == 'https' ? 'wss' : 'ws';
    return Uri(
      scheme: scheme,
      host: base.host,
      port: base.hasPort ? base.port : (base.scheme == 'https' ? 443 : 80),
      path: '/ws/live-audio/$emergencyId',
      queryParameters: {'token': identity?.deviceToken ?? ''},
    );
  }

  void close() => _http.close();
}

/// ---------------------------------------------------------------------------
/// Offline outbox
/// ---------------------------------------------------------------------------
/// Emergency data must not be lost when the phone loses connectivity.
/// Locations, telemetry and events are queued as JSON; audio segments are
/// written to the app's documents directory and uploaded on reconnect.
class Outbox {
  Outbox({required this.store, required this.api, required this.directory});

  final SessionStore store;
  final ApiClient api;
  final Directory directory;

  int _pending = 0;
  int get pending => _pending;
  bool flushing = false;

  Future<void> refreshPending() async {
    _pending = store.outbox.length + await _countFiles();
  }

  Future<int> _countFiles() async {
    if (!await directory.exists()) return 0;
    final files = await directory
        .list()
        .where((entity) => entity is File && entity.path.endsWith('.wav'))
        .length;
    return files;
  }

  /// Queue a small payload (location / telemetry / event / end).
  Future<void> enqueuePayload({
    required String type,
    required String emergencyId,
    required Map<String, dynamic> body,
  }) async {
    final items = store.outbox;
    items.add({
      'id': '${DateTime.now().microsecondsSinceEpoch}-${items.length}',
      'type': type,
      'emergency_id': emergencyId,
      'body': body,
      'attempts': 0,
    });
    await store.saveOutbox(items);
    await refreshPending();
  }

  /// Queue a finished ~1-minute audio segment.
  Future<void> enqueueSegment({
    required String emergencyId,
    required int sequence,
    required Uint8List wav,
    required double durationSeconds,
    String? startedAt,
    String? endedAt,
  }) async {
    if (!await directory.exists()) {
      await directory.create(recursive: true);
    }
    final file = File('${directory.path}/segment_${emergencyId}_$sequence.wav');
    await file.writeAsBytes(wav, flush: true);

    final items = store.outbox;
    items.add({
      'id': '${DateTime.now().microsecondsSinceEpoch}-seg',
      'type': 'segment',
      'emergency_id': emergencyId,
      'sequence': sequence,
      'duration_seconds': durationSeconds,
      'started_at': startedAt,
      'ended_at': endedAt,
      'file': file.path,
      'attempts': 0,
    });
    await store.saveOutbox(items);
    await refreshPending();
  }

  /// Try to deliver everything, in order. Stops at the first network failure.
  Future<int> flush() async {
    if (flushing) return _pending;
    flushing = true;
    try {
      var items = store.outbox;
      final remaining = <Map<String, dynamic>>[];
      var networkDown = false;

      for (final item in items) {
        if (networkDown) {
          remaining.add(item);
          continue;
        }
        try {
          await _deliver(item);
        } on ApiException catch (error) {
          if (error.isClientError) {
            // 4xx: the payload can never succeed (e.g. emergency already ended)
            await _discard(item);
          } else {
            // 5xx: keep it for the next attempt
            remaining.add(_bump(item));
          }
        } catch (_) {
          networkDown = true;
          remaining.add(_bump(item));
        }
      }

      await store.saveOutbox(remaining);
      await refreshPending();
      return remaining.length;
    } finally {
      flushing = false;
    }
  }

  Map<String, dynamic> _bump(Map<String, dynamic> item) {
    final attempts = (item['attempts'] as num?)?.toInt() ?? 0;
    return {...item, 'attempts': attempts + 1};
  }

  Future<void> _deliver(Map<String, dynamic> item) async {
    final type = item['type'] as String;
    final emergencyId = item['emergency_id'] as String;
    final body = (item['body'] as Map?)?.cast<String, dynamic>() ?? const {};

    switch (type) {
      case 'location':
        await api.postLocation(emergencyId, LocationSample(
          latitude: (body['latitude'] as num).toDouble(),
          longitude: (body['longitude'] as num).toDouble(),
          timestamp: body['timestamp'] as String? ?? nowIso(),
          accuracy: (body['accuracy_m'] as num?)?.toDouble(),
          provider: body['provider'] as String? ?? 'outbox',
        ));
        break;
      case 'telemetry':
        await api.postTelemetry(emergencyId, body);
        break;
      case 'event':
        await api._send('POST', '/api/emergencies/$emergencyId/events', body: body);
        break;
      case 'end':
        await api.endEmergency(
          emergencyId,
          body['status'] as String? ?? 'resolved',
          reason: body['reason'] as String?,
          note: body['note'] as String?,
        );
        break;
      case 'segment':
        final file = File(item['file'] as String);
        if (!await file.exists()) break;
        await api.uploadSegment(
          emergencyId: emergencyId,
          sequence: (item['sequence'] as num).toInt(),
          wav: await file.readAsBytes(),
          durationSeconds: (item['duration_seconds'] as num?)?.toDouble() ?? 60,
          startedAt: item['started_at'] as String?,
          endedAt: item['ended_at'] as String?,
        );
        await file.delete();
        break;
      default:
        break;
    }
    await _discard(item);
  }

  Future<void> _discard(Map<String, dynamic> item) async {
    final items = store.outbox;
    items.removeWhere((candidate) => candidate['id'] == item['id']);
    await store.saveOutbox(items);
    if (item['type'] == 'segment' && item['file'] != null) {
      final file = File(item['file'] as String);
      if (await file.exists()) await file.delete();
    }
  }

  Future<void> clear() async {
    await store.saveOutbox([]);
    if (await directory.exists()) {
      await directory.delete(recursive: true);
    }
    await refreshPending();
  }
}
