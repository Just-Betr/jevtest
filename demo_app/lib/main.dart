import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:webview_flutter/webview_flutter.dart';

// jevtest demo app: a small app that exercises every action the harness
// supports (tap, type, clear, double tap, long press, scroll, swipe, back,
// toggles, dialogs, navigation). Password for the login screen is "hunter22".

void main() => runApp(const DemoApp());

class DemoApp extends StatelessWidget {
  const DemoApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Jev Demo',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorSchemeSeed: Colors.indigo),
      home: const LoginPage(),
    );
  }
}

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  String? _error;

  void _signIn() {
    final email = _email.text.trim();
    if (!email.contains('@') || _password.text != 'hunter22') {
      setState(() => _error = 'Invalid email or password');
      return;
    }
    setState(() => _error = null);
    Navigator.of(context).pushReplacement(
      MaterialPageRoute(builder: (_) => HomePage(email: email)),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Sign in')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _email,
              keyboardType: TextInputType.emailAddress,
              decoration: const InputDecoration(labelText: 'Email'),
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Password'),
            ),
            const SizedBox(height: 24),
            FilledButton(onPressed: _signIn, child: const Text('Sign in')),
            if (_error != null) ...[
              const SizedBox(height: 16),
              Text(_error!, style: const TextStyle(color: Colors.red)),
            ],
          ],
        ),
      ),
    );
  }
}

class HomePage extends StatefulWidget {
  const HomePage({super.key, required this.email});
  final String email;

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  int _taps = 0;
  int _doubleTaps = 0;
  bool _notifications = false;
  String _data = 'No data yet';

  Future<void> _loadData() async {  // like a network call: the result arrives a second later
    setState(() => _data = 'Loading...');
    await Future<void>.delayed(const Duration(seconds: 1));
    if (mounted) setState(() => _data = 'Data loaded');
  }

  void _showHeldDialog() {
    showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('You held it'),
        content: const Text('Long press detected.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Home')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          Text('Welcome, ${widget.email}',
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 24),
          Text('Taps: $_taps'),
          FilledButton(
            onPressed: () => setState(() => _taps++),
            child: const Text('Add one'),
          ),
          const SizedBox(height: 16),
          Text('Double taps: $_doubleTaps'),
          GestureDetector(
            onDoubleTap: () => setState(() => _doubleTaps++),
            child: Semantics(
              button: true,
              label: 'Double tap me',
              child: Container(
                height: 56,
                alignment: Alignment.center,
                color: Colors.indigo.shade50,
                child: const ExcludeSemantics(child: Text('Double tap me')),
              ),
            ),
          ),
          const SizedBox(height: 16),
          GestureDetector(
            onLongPress: _showHeldDialog,
            child: Semantics(
              button: true,
              label: 'Press and hold me',
              child: Container(
                height: 56,
                alignment: Alignment.center,
                color: Colors.amber.shade100,
                child: const ExcludeSemantics(child: Text('Press and hold me')),
              ),
            ),
          ),
          const SizedBox(height: 16),
          SwitchListTile(
            title: const Text('Notifications'),
            value: _notifications,
            onChanged: (v) => setState(() => _notifications = v),
          ),
          Text(_notifications ? 'Notifications are on' : 'Notifications are off'),
          const SizedBox(height: 16),
          OutlinedButton(onPressed: _loadData, child: const Text('Load data')),
          Text(_data),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const ItemsPage()),
            ),
            child: const Text('Open list'),
          ),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const WebPage()),
            ),
            child: const Text('Open web page'),
          ),
          const SizedBox(height: 16),
          OutlinedButton(
            onPressed: () => const MethodChannel('jevtest/native').invokeMethod('open'),
            child: const Text('Open native screen'),
          ),
          const SizedBox(height: 16),
          TextButton(
            onPressed: () => Navigator.of(context).pushReplacement(
              MaterialPageRoute(builder: (_) => const LoginPage()),
            ),
            child: const Text('Log out'),
          ),
        ],
      ),
    );
  }
}

class ItemsPage extends StatefulWidget {
  const ItemsPage({super.key});

  @override
  State<ItemsPage> createState() => _ItemsPageState();
}

class _ItemsPageState extends State<ItemsPage> {
  final _items = List<String>.generate(40, (i) => 'Item ${i + 1}');

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Items')),
      body: ListView.builder(
        itemCount: _items.length,
        itemBuilder: (context, index) {
          final item = _items[index];
          return Dismissible(
            key: ValueKey(item),
            onDismissed: (_) {
              setState(() => _items.removeAt(index));
              ScaffoldMessenger.of(context)
                  .showSnackBar(SnackBar(content: Text('$item deleted')));
            },
            background: Container(color: Colors.red),
            child: ListTile(
              title: Text(item),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => DetailPage(item: item)),
              ),
            ),
          );
        },
      ),
    );
  }
}

class DetailPage extends StatelessWidget {
  const DetailPage({super.key, required this.item});
  final String item;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(item)),
      body: Center(child: Text('Details for $item')),
    );
  }
}

// An in-app web app: a form (text, password, dropdown, radio, checkbox), navigation between
// pages, a long page that needs scrolling, and a JavaScript alert shown as an app dialog.
const _html = """
<!doctype html>
<html>
<head><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: -apple-system, Roboto, sans-serif; padding: 16px; }
  input[type=text], input[type=password], select, button { font-size: 18px; padding: 10px; width: 100%; box-sizing: border-box; margin: 8px 0; }
  label { font-size: 18px; display: block; margin: 8px 0; }
  .page { display: none; } .page.on { display: block; }
  .tall { height: 1400px; }
</style></head>
<body>
  <div id="form" class="page on">
    <h1>Web Greeter</h1>
    <label for="name">Your name</label>
    <input id="name" type="text" placeholder="Your name" value="">
    <button onclick="greet()">Say hello</button>
    <p id="out">Nobody greeted yet</p>

    <label for="pw">Web password</label>
    <input id="pw" type="password" placeholder="Web password">

    <label for="country">Country</label>
    <select id="country">
      <option value="">Choose a country</option>
      <option>Canada</option>
      <option>Mexico</option>
      <option>United States</option>
    </select>

    <p>Plan</p>
    <label><input type="radio" name="plan" value="Free" checked> Free plan</label>
    <label><input type="radio" name="plan" value="Pro"> Pro plan</label>
    <label><input id="agree" type="checkbox" onchange="agreed()"> I agree to the terms</label>
    <p id="terms">Terms not accepted</p>
    <button onclick="submitForm()">Submit form</button>
    <p id="summary">Form not submitted</p>

    <p><a href="#" onclick="more(); return false;">Show more</a></p>
    <p id="more"></p>
    <button onclick="alert('Hello from the web page')">Show web alert</button>
    <p><a href="#" onclick="go('settings'); return false;">Open settings page</a></p>
    <div class="tall"></div>
    <p>End of the page</p>
    <button onclick="window.scrollTo(0, 0)">Back to top</button>
  </div>

  <div id="settings" class="page">
    <h1>Web settings</h1>
    <p>This is the second page of the web app.</p>
    <a href="#" onclick="go('form'); return false;">Back to the form</a>
  </div>
  <script>
    function greet() {
      var n = document.getElementById('name').value.trim();
      document.getElementById('out').textContent = n ? 'Hello, ' + n + '!' : 'Please enter a name';
    }
    function agreed() {
      document.getElementById('terms').textContent =
        document.getElementById('agree').checked ? 'Terms accepted' : 'Terms not accepted';
    }
    function more() { document.getElementById('more').textContent = 'Here is more content from the web page.'; }
    function submitForm() {
      var c = document.getElementById('country').value || 'no country';
      var plan = document.querySelector('input[name=plan]:checked').value;
      var pw = document.getElementById('pw').value ? 'with a password' : 'without a password';
      document.getElementById('summary').textContent = 'Submitted: ' + c + ', ' + plan + ' plan, ' + pw;
    }
    function show(page) {
      document.querySelectorAll('.page').forEach(function (p) { p.classList.toggle('on', p.id === page); });
      window.scrollTo(0, 0);
    }
    function go(page) { history.pushState({page: page}, '', '#' + page); show(page); }
    window.onpopstate = function (e) { show(e.state && e.state.page ? e.state.page : 'form'); };
  </script>
</body>
</html>
""";

class WebPage extends StatefulWidget {
  const WebPage({super.key});

  @override
  State<WebPage> createState() => _WebPageState();
}

class _WebPageState extends State<WebPage> {
  late final WebViewController _controller = WebViewController()
    ..setJavaScriptMode(JavaScriptMode.unrestricted)
    ..setOnJavaScriptAlertDialog((request) => showDialog<void>(  // a web alert() as an app dialog
          context: context,
          builder: (context) => AlertDialog(
            content: Text(request.message),
            actions: [TextButton(onPressed: () => Navigator.of(context).pop(), child: const Text('OK'))],
          ),
        ))
    ..loadHtmlString(_html);

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Web page')),
      body: WebViewWidget(controller: _controller),
    );
  }
}
