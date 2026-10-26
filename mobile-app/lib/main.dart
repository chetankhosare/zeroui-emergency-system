import 'package:flutter/material.dart';

import 'config.dart';
import 'emergency_controller.dart';
import 'foreground.dart';
import 'screens/active.dart';
import 'screens/countdown.dart';
import 'screens/home.dart';
import 'screens/onboarding.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final store = await SessionStore.instance();
  final permissions = PermissionService();
  final controller = EmergencyController(store: store, permissions: permissions);
  WidgetsBinding.instance.addObserver(controller);
  runApp(ZeroUiApp(controller: controller));
  // Initialise after the first frame so the splash is visible while the app
  // restores identity, recovers an in-flight emergency and starts sensors.
  WidgetsBinding.instance.addPostFrameCallback((_) => controller.initialize());
}

class ZeroUiApp extends StatelessWidget {
  const ZeroUiApp({super.key, required this.controller});

  final EmergencyController controller;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Zero UI',
      debugShowCheckedModeBanner: false,
      theme: _theme(),
      home: ListenableBuilder(
        listenable: controller,
        builder: (context, _) {
          switch (controller.phase) {
            case AppPhase.booting:
              return const _Splash();
            case AppPhase.onboarding:
              return OnboardingScreen(controller: controller);
            case AppPhase.passive:
              return HomeScreen(controller: controller);
            case AppPhase.countdown:
              return CountdownScreen(controller: controller);
            case AppPhase.active:
            case AppPhase.recovered:
              return ActiveEmergencyScreen(controller: controller);
          }
        },
      ),
    );
  }

  ThemeData _theme() {
    const background = Color(0xFF060610);
    const surface = Color(0xFF0C0C1A);
    const accent = Color(0xFFEF4444);
    final scheme = ColorScheme.fromSeed(
      seedColor: accent,
      brightness: Brightness.dark,
      surface: surface,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: background,
      appBarTheme: const AppBarTheme(
        backgroundColor: background,
        elevation: 0,
        centerTitle: false,
      ),
      cardTheme: CardThemeData(
        color: Colors.white.withValues(alpha: 0.035),
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: BorderSide(color: Colors.white.withValues(alpha: 0.07)),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
      inputDecorationTheme: const InputDecorationTheme(
        border: OutlineInputBorder(),
        isDense: true,
      ),
      textTheme: const TextTheme(
        labelMedium: TextStyle(
          fontSize: 11,
          letterSpacing: 1.1,
          fontWeight: FontWeight.w700,
          color: Colors.white54,
        ),
        labelSmall: TextStyle(
          fontSize: 10,
          letterSpacing: 1.0,
          fontWeight: FontWeight.w700,
          color: Colors.white38,
        ),
      ),
    );
  }
}

class _Splash extends StatelessWidget {
  const _Splash();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            // App logo (bundled asset, pre-decoded at a small size).
            Image.asset('assets/zeroui_logo.png', width: 96, height: 96),
            const SizedBox(height: 14),
            const Text('Zero UI', style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
            const SizedBox(height: 6),
            const Text('Preparing emergency monitoring…',
                style: TextStyle(fontSize: 12.5, color: Colors.white54)),
            const SizedBox(height: 22),
            const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2)),
          ],
        ),
      ),
    );
  }
}
