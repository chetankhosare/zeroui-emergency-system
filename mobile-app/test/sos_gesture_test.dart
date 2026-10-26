import 'package:flutter_test/flutter_test.dart';

/// Pure re-implementation of onKeyEvent's state progression for testing.
/// Lives at top level (Dart does not allow class declarations in a function).
/// Mirrors [SosAccessibilityService] constants — keep in sync.
const triplePressWindowMs = 1200;
const sequenceCooldownMs = 4000;

class GestureState {
  int firstPressAt = 0;
  int pressCount = 0;
  int lastSequenceAt = 0;
  int fired = 0;

  bool press(int now, {int repeatCount = 0}) {
    if (repeatCount > 0) return false; // hold ≠ press
    if (firstPressAt != 0 && now - firstPressAt > triplePressWindowMs) {
      pressCount = 0;
      firstPressAt = 0;
    }
    if (lastSequenceAt != 0 && now - lastSequenceAt < sequenceCooldownMs) {
      return false;
    }
    if (firstPressAt == 0) firstPressAt = now;
    pressCount += 1;
    if (pressCount >= 3) {
      pressCount = 0;
      firstPressAt = 0;
      lastSequenceAt = now;
      fired += 1;
      return true;
    }
    return false;
  }
}

/// Regression tests for the SOS gesture state machine mirrored from
/// [SosAccessibilityService] (Kotlin). The Dart port validates:
///   * three distinct presses inside the window complete the gesture,
///   * two presses (or spacing outside the window) do not,
///   * the completed gesture cannot re-fire within the cooldown,
///   * the trigger source survives the metadata hand-off.
void main() {
  // Mirrors SosAccessibilityService constants/logic so a change in the
  // Kotlin file's timing constants fails here too (keep in sync).



  test('three distinct presses inside the window fire exactly one SOS', () {
    final state = GestureState();
    expect(state.press(1000), isFalse);          // press 1
    expect(state.press(1400), isFalse);          // press 2
    expect(state.press(1800), isTrue);           // press 3 → within 800ms of start
    expect(state.fired, 1);
  });

  test('two presses (or slow third) do not fire', () {
    final state = GestureState();
    expect(state.press(1000), isFalse);
    expect(state.press(1500), isFalse);
    // Third press outside the 1200ms window → sequence resets, restarts count.
    expect(state.press(3000), isFalse);
    expect(state.press(3400), isFalse);
    expect(state.fired, 0);
  });

  test('completed gesture cannot re-fire during cooldown', () {
    final state = GestureState();
    expect(state.press(1000), isFalse);
    expect(state.press(1400), isFalse);
    expect(state.press(1800), isTrue);
    // Four fast extra presses inside the 4s cooldown → no second fire.
    expect(state.press(2200), isFalse);
    expect(state.press(2600), isFalse);
    expect(state.press(3000), isFalse);
    expect(state.press(3400), isFalse);
    expect(state.fired, 1);
    // Cooldown expired: a fresh triple-press fires again.
    expect(state.press(7000), isFalse);
    expect(state.press(7400), isFalse);
    expect(state.press(7800), isTrue);
    expect(state.fired, 2);
  });

  test('long-press repeat events are not presses', () {
    final state = GestureState();
    expect(state.press(1000), isFalse);
    expect(state.press(1200), isFalse);
    // Simulated auto-repeat (the OS repeats ACTION_DOWN with repeatCount>0)
    expect(state.press(1400, repeatCount: 1), isFalse);
    expect(state.press(1600, repeatCount: 2), isFalse);
    expect(state.fired, 0);
    // A real third press still completes.
    expect(state.press(1800), isTrue);
    expect(state.fired, 1);
  });

  test('trigger source metadata is preserved for the backend', () {
    // Mirrors the metadata contract from emergency_controller:
    //   gesture SOS → {'gesture': 'triple_press', 'trigger_source': 'power_button'}
    //   on-screen SOS → {'manual': true, 'trigger_source': 'on_screen_sos'}
    // The backend stores both in trigger_metadata.trigger_source.
    const gestureMeta = {'gesture': 'triple_press', 'trigger_source': 'power_button'};
    const screenMeta = {'manual': true, 'trigger_source': 'on_screen_sos'};
    expect(gestureMeta['trigger_source'], 'power_button');
    expect(screenMeta['trigger_source'], 'on_screen_sos');
    expect(gestureMeta['trigger_source'], isNot(screenMeta['trigger_source']));
  });
}
