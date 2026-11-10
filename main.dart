import 'package:flutter/material.dart';
import 'package:webview_flutter/webview_flutter.dart';

const appUrl = String.fromEnvironment(
  'APP_URL',
  defaultValue: 'http://127.0.0.1:8000/',
);

void main() {
  runApp(const CutLostAndFoundApp());
}

class CutLostAndFoundApp extends StatelessWidget {
  const CutLostAndFoundApp({super.key});

  @override
  Widget build(BuildContext context) {
    const blue = Color(0xFF1687BB);
    return MaterialApp(
      title: 'CUT Lost & Found',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: blue,
          primary: blue,
          surface: const Color(0xFFF4F9FC),
        ),
        scaffoldBackgroundColor: const Color(0xFFF4F9FC),
        useMaterial3: true,
      ),
      home: const CutWebApp(),
    );
  }
}

class CutWebApp extends StatefulWidget {
  const CutWebApp({super.key});

  @override
  State<CutWebApp> createState() => _CutWebAppState();
}

class _CutWebAppState extends State<CutWebApp> {
  late final WebViewController _controller;
  bool _isLoading = true;
  bool _hasError = false;
  bool _canGoBack = false;

  @override
  void initState() {
    super.initState();
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(const Color(0xFFF4F9FC))
      ..setNavigationDelegate(
        NavigationDelegate(
          onPageStarted: (_) {
            if (!mounted) return;
            setState(() {
              _isLoading = true;
              _hasError = false;
            });
          },
          onPageFinished: (_) async {
            final canGoBack = await _controller.canGoBack();
            if (!mounted) return;
            setState(() {
              _isLoading = false;
              _canGoBack = canGoBack;
            });
          },
          onWebResourceError: (error) {
            if (error.isForMainFrame != false && mounted) {
              setState(() {
                _isLoading = false;
                _hasError = true;
              });
            }
          },
        ),
      )
      ..loadRequest(Uri.parse(appUrl));
  }

  Future<void> _handleBack(bool didPop) async {
    if (!didPop && _canGoBack) {
      await _controller.goBack();
    }
  }

  Future<void> _retry() async {
    setState(() {
      _isLoading = true;
      _hasError = false;
    });
    await _controller.loadRequest(Uri.parse(appUrl));
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: !_canGoBack,
      onPopInvokedWithResult: (didPop, _) => _handleBack(didPop),
      child: Scaffold(
        body: SafeArea(
          child: Stack(
            children: [
              WebViewWidget(controller: _controller),
              if (_isLoading && !_hasError)
                const Align(
                  alignment: Alignment.topCenter,
                  child: LinearProgressIndicator(
                    minHeight: 2,
                    color: Color(0xFF1687BB),
                    backgroundColor: Color(0xFFE4B54D),
                  ),
                ),
              if (_hasError)
                ColoredBox(
                  color: const Color(0xFFF4F9FC),
                  child: Center(
                    child: Padding(
                      padding: const EdgeInsets.all(28),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(
                            Icons.wifi_off_rounded,
                            color: Color(0xFF1687BB),
                            size: 44,
                          ),
                          const SizedBox(height: 16),
                          const Text(
                            'Cannot reach CUT Lost & Found',
                            textAlign: TextAlign.center,
                            style: TextStyle(
                              color: Color(0xFF1C3444),
                              fontSize: 19,
                              fontWeight: FontWeight.w700,
                            ),
                          ),
                          const SizedBox(height: 8),
                          const Text(
                            'Check that the Python server is running on your computer and USB port forwarding is set up.',
                            textAlign: TextAlign.center,
                            style: TextStyle(color: Color(0xFF647984)),
                          ),
                          const SizedBox(height: 20),
                          FilledButton.icon(
                            onPressed: _retry,
                            icon: const Icon(Icons.refresh),
                            label: const Text('Try again'),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
