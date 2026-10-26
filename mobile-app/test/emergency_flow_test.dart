import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:zeroui_app/audio.dart';
import 'package:zeroui_app/config.dart';
import 'package:zeroui_app/models.dart';
import 'package:zeroui_app/sensors.dart';

/// The fall detector is the trigger for the whole emergency flow, so its
/// three-phase rule is tested directly (free fall → impact → stillness).
void main() {
  group('FallDetector', () {
    test('fires on free fall + impact + inactivity', () async {
      final settings = const DetectionSettings(
        freeFallThreshold: 4.0,
        freeFallMinMs: 100,
        impactThreshold: 22.0,
        inactivitySeconds: 0.3,
        inactivityStdDev: 1.6,
      );
      final detections = <FallDetection>[];
      final detector = FallDetector(
        settings: settings,
        onFall: detections.add,
      );

      detector.injectSample(9.8);
      for (var i = 0; i < 6; i++) {
        detector.injectSample(2.0); // free fall
        await Future<void>.delayed(const Duration(milliseconds: 25));
      }
      detector.injectSample(31.5); // impact
      for (var i = 0; i < 12; i++) {
        detector.injectSample(0.35); // phone lying still
        await Future<void>.delayed(const Duration(milliseconds: 25));
      }
      // wait for the inactivity watchdog
      await Future<void>.delayed(const Duration(milliseconds: 700));

      expect(detections, hasLength(1));
      expect(detections.single.impactMagnitude, greaterThan(22));
      detector.dispose();
    });

    test('a single bump does not trigger an emergency', () async {
      final detections = <FallDetection>[];
      final detector = FallDetector(
        settings: const DetectionSettings(inactivitySeconds: 0.3),
        onFall: detections.add,
      );
      for (var i = 0; i < 10; i++) {
        detector.injectSample(9.8 + (i.isEven ? 2.5 : -2.5)); // walking-ish noise
        await Future<void>.delayed(const Duration(milliseconds: 15));
      }
      await Future<void>.delayed(const Duration(milliseconds: 500));
      expect(detections, isEmpty);
      detector.dispose();
    });

    test('a phone carried in a pocket (no free fall) does not trigger', () async {
      final detections = <FallDetection>[];
      final detector = FallDetector(
        settings: const DetectionSettings(inactivitySeconds: 0.3),
        onFall: detections.add,
      );
      // Walking with the phone in a pocket: gravity + mild swings, no free fall.
      // A strong single spike is plausible (impact threshold × ~1.8) only when
      // there was no preceding stillness — the detector must require inactivity.
      detector.injectSample(9.8);
      for (var i = 0; i < 6; i++) {
        detector.injectSample(9.8 + (i.isEven ? 3.0 : -3.0));
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
      detector.injectSample(42.0); // hard single bump while moving
      for (var i = 0; i < 10; i++) {
        detector.injectSample(8.0 + (i.isEven ? 2.0 : -2.0)); // immediately moving again
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
      await Future<void>.delayed(const Duration(milliseconds: 600));
      expect(detections, isEmpty);
      detector.dispose();
    });
  });

  group('WAV container', () {
    test('writes a valid 44-byte PCM header', () {
      final pcm = Uint8List.fromList(List<int>.generate(3200, (index) => index % 128));
      final wav = pcmToWav(pcm);
      expect(wav.length, 44 + pcm.length);
      expect(String.fromCharCodes(wav.sublist(0, 4)), 'RIFF');
      expect(String.fromCharCodes(wav.sublist(8, 12)), 'WAVE');
      expect(String.fromCharCodes(wav.sublist(36, 40)), 'data');
      final header = ByteData.sublistView(wav, 0, 44);
      expect(header.getUint32(24, Endian.little), AppConfig.sampleRate);
      expect(header.getUint16(22, Endian.little), AppConfig.channels);
      expect(header.getUint32(40, Endian.little), pcm.length);
    });

    test('duration matches the sample rate', () {
      final oneSecond = Uint8List(AppConfig.sampleRate * 2);
      expect(pcmDurationSeconds(oneSecond), closeTo(1.0, 0.001));
    });
  });

  group('Models', () {
    test('location payload uses the backend field names', () {
      final sample = LocationSample(
        latitude: 19.8762,
        longitude: 75.3433,
        timestamp: '2026-01-01T00:00:00.000Z',
        accuracy: 8.5,
        provider: 'gps',
      );
      final json = sample.toJson();
      expect(json['latitude'], 19.8762);
      expect(json['accuracy_m'], 8.5);
      expect(json.containsKey('digipin'), isFalse); // generated server-side only
    });

    test('app events strip the local_ prefix', () {
      final event = AppEvent(type: 'local_pending', description: 'queued');
      expect(event.toJson()['event_type'], 'pending');
      expect(AppEvent(type: 'sos_sent', description: 'sent').toJson()['event_type'], 'sos_sent');
    });

    test('server config parses the emergency parameters', () {
      final config = ServerConfig.fromJson({
        'countdown_seconds': 15,
        'location_interval_seconds': 30,
        'audio_segment_seconds': 60,
        'stale_after_seconds': 90,
        'digipin': {'library_available': true},
      });
      expect(config.countdownSeconds, 15);
      expect(config.locationIntervalSeconds, 30);
      expect(config.audioSegmentSeconds, 60);
    });
  });
}
