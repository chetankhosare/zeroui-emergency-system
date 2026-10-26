import 'dart:async';
import 'dart:typed_data';

import 'package:record/record.dart';

import 'config.dart';

/// Build a 16-bit PCM WAV container around raw PCM samples.
///
/// Writing the container locally (instead of asking the platform for a file)
/// means one single capture stream feeds both the live WebSocket and the
/// ~1-minute segments, so the microphone is opened exactly once.
Uint8List pcmToWav(
  Uint8List pcm, {
  int sampleRate = AppConfig.sampleRate,
  int channels = AppConfig.channels,
}) {
  const bitsPerSample = 16;
  final byteRate = sampleRate * channels * bitsPerSample ~/ 8;
  final blockAlign = channels * bitsPerSample ~/ 8;
  final dataLength = pcm.length;

  final header = ByteData(44);
  void writeString(int offset, String value) {
    for (var i = 0; i < value.length; i++) {
      header.setUint8(offset + i, value.codeUnitAt(i));
    }
  }

  writeString(0, 'RIFF');
  header.setUint32(4, 36 + dataLength, Endian.little);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  header.setUint32(16, 16, Endian.little); // PCM chunk size
  header.setUint16(20, 1, Endian.little); // format = PCM
  header.setUint16(22, channels, Endian.little);
  header.setUint32(24, sampleRate, Endian.little);
  header.setUint32(28, byteRate, Endian.little);
  header.setUint16(32, blockAlign, Endian.little);
  header.setUint16(34, bitsPerSample, Endian.little);
  writeString(36, 'data');
  header.setUint32(40, dataLength, Endian.little);

  final wav = Uint8List(44 + dataLength);
  wav.setRange(0, 44, header.buffer.asUint8List());
  wav.setRange(44, 44 + dataLength, pcm);
  return wav;
}

/// Duration in seconds of a raw PCM16 mono buffer.
double pcmDurationSeconds(Uint8List pcm, {int sampleRate = AppConfig.sampleRate}) =>
    pcm.length / (sampleRate * AppConfig.channels * AppConfig.bytesPerSample);

/// One finished audio segment, ready to be uploaded.
class AudioSegment {
  AudioSegment({
    required this.sequence,
    required this.wav,
    required this.durationSeconds,
    required this.startedAt,
    required this.endedAt,
  });

  final int sequence;
  final Uint8List wav;
  final double durationSeconds;
  final DateTime startedAt;
  final DateTime endedAt;
}

/// Capture from the microphone, streaming PCM to the backend and cutting the
/// stream into ~1-minute WAV segments.
///
/// Android limitations that shape this design (also documented in the README):
///  * the microphone can only be opened by one recorder at a time, so a single
///    PCM stream is split instead of running two recorders;
///  * audio capture continues only while the app's process is alive — the
///    native foreground service keeps that process alive in the background;
///  * if the OS revokes the microphone (call in progress, permission revoked)
///    capture stops and the app reports it instead of pretending to record.
class AudioCaptureService {
  AudioCaptureService({this.segmentSeconds = AppConfig.audioSegmentSeconds});

  final int segmentSeconds;

  final AudioRecorder _recorder = AudioRecorder();
  StreamSubscription<Uint8List>? _subscription;

  bool _active = false;
  bool get isActive => _active;

  int _sequence = 0;
  int get sequence => _sequence;

  final BytesBuilder _buffer = BytesBuilder(copy: false);
  int _bufferedBytes = 0;
  DateTime? _segmentStartedAt;

  String? lastError;

  void Function(Uint8List frame)? onLiveFrame;
  void Function(AudioSegment segment)? onSegment;

  int get _bytesPerSecond => AppConfig.sampleRate * AppConfig.channels * AppConfig.bytesPerSample;

  Future<bool> hasPermission() async {
    try {
      return await _recorder.hasPermission();
    } catch (error) {
      lastError = 'Microphone permission check failed: $error';
      return false;
    }
  }

  Future<bool> start() async {
    if (_active) return true;

    if (!await hasPermission()) {
      lastError = 'Microphone permission not granted';
      return false;
    }

    try {
      final stream = await _recorder.startStream(
        const RecordConfig(
          encoder: AudioEncoder.pcm16bits,
          sampleRate: AppConfig.sampleRate,
          numChannels: AppConfig.channels,
        ),
      );
      _active = true;
      _bufferedBytes = 0;
      _buffer.clear();
      _segmentStartedAt = DateTime.now();

      _subscription = stream.listen(
        _onChunk,
        onError: (error) {
          lastError = 'Audio capture error: $error';
          _active = false;
        },
        onDone: () {
          _active = false;
        },
        cancelOnError: false,
      );
      return true;
    } catch (error) {
      lastError = 'Could not start audio capture: $error';
      _active = false;
      return false;
    }
  }

  void _onChunk(Uint8List chunk) {
    if (chunk.isEmpty) return;

    // 1. live stream (best effort, low latency)
    onLiveFrame?.call(chunk);

    // 2. accumulate for the current ~60 s segment
    _buffer.add(chunk);
    _bufferedBytes += chunk.length;

    if (_bufferedBytes >= _bytesPerSecond * segmentSeconds) {
      _flushSegment();
    }
  }

  void _flushSegment() {
    if (_bufferedBytes == 0) return;
    final pcm = _buffer.takeBytes();
    final startedAt = _segmentStartedAt ?? DateTime.now();
    final endedAt = DateTime.now();
    _bufferedBytes = 0;
    _segmentStartedAt = endedAt;
    _sequence += 1;

    onSegment?.call(AudioSegment(
      sequence: _sequence,
      wav: pcmToWav(pcm),
      durationSeconds: pcmDurationSeconds(pcm),
      startedAt: startedAt,
      endedAt: endedAt,
    ));
  }

  /// Stop capture. Any buffered audio (>= 1 s) is emitted as a final segment.
  Future<void> stop({bool flush = true}) async {
    await _subscription?.cancel();
    _subscription = null;

    if (_active) {
      try {
        await _recorder.stop();
      } catch (_) {}
    }
    _active = false;

    if (flush && _bufferedBytes > _bytesPerSecond) {
      _flushSegment();
    }
    _buffer.clear();
    _bufferedBytes = 0;
  }

  Future<void> dispose() async {
    await stop(flush: false);
    try {
      await _recorder.dispose();
    } catch (_) {}
  }
}
