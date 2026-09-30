package dev.jevtest.agent;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Display;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * jevtest Android agent. Started with
 *   am instrument -w -e port 7912 dev.jevtest.agent/.Agent
 * it keeps one UiAutomation connection open and answers on 127.0.0.1:port:
 *   GET /tree        -> the active window as uiautomator-style XML, plus ime="true|false",
 *                       ime-top="<y where the keyboard starts>", bars="l,t,r,b;..." (the system's windows
 *                       over the app, like the status bar) and package="..." on the root element, and
 *                       position="<0 to 1>" on a node with a range (a slider: where its thumb is)
 *   GET /rotate?to=R -> locks the screen to rotation R (0-3, as /tree reports it) through UiAutomation, which
 *                       works on every Android version (the user_rotation setting doesn't on some phones)
 *                       and puts the device's own rotation state back when the agent stops
 *   GET /pixels?rects=x1,y1,x2,y2;x1,y1,x2,y2;... -> a fingerprint of the pixels in those parts of the
 *                       screen, from one screenshot: a system dialog fading or sliding in reports bounds in
 *                       the tree that don't move with it, so only its pixels show it is still moving
 *   GET /insert?text=T&fields=C1,C2 -> inserts T (any text) at the cursor of the field with input focus, as
 *                       typing would; answers "inserted", or why not. For what `adb shell input text` can't type.
 *                       A field is a node whose class's short name is one of the C (jevtest's own list, so the
 *                       agent and jevtest's screen reading agree on what a text field is).
 * Parameters are URL-encoded. A request the agent can't do answers "error: " and why.
 *   GET /quit        -> stops the agent
 * When it is listening it reports status "ready=1" (visible with `am instrument -r`).
 * Reading the tree this way takes milliseconds instead of the ~2 s that a fresh
 * `uiautomator dump` process needs each time. The agent never waits: jevtest reads the screen when a step
 * checks what it waits for.
 */
public class Agent extends Instrumentation {
    private int port = 7912;
    /** The last toast's text, and when it was shown (uptime milliseconds): a toast isn't in any window the tree
     *  reads, and Android says only that it was shown (one accessibility event), not when it goes. */
    private volatile String toastText;
    private volatile long toastShownAt;
    /** How long after a toast is shown to look for its window: the longest measured was on screen 4.2 s. */
    private static final long TOAST_LOOK_MS = 6000;
    private static final Pattern TOAST_WINDOW = Pattern.compile(
            "Window\\{\\w+ u\\d+ Toast\\}:(.*?)(?=\\n  Window #|\\z)", Pattern.DOTALL);
    private static final Pattern FRAME = Pattern.compile(" frame=\\[(-?\\d+),(-?\\d+)\\]\\[(-?\\d+),(-?\\d+)\\]");

    @Override
    public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        if (arguments != null && arguments.getString("port") != null) {
            port = Integer.parseInt(arguments.getString("port"));
        }
        start();
    }

    @Override
    public void onStart() {
        UiAutomation ui = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
        AccessibilityServiceInfo info = ui.getServiceInfo();
        info.flags |= AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS
                | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS;
        ui.setServiceInfo(info);
        ui.setOnAccessibilityEventListener(e -> {
            if (e.getEventType() == AccessibilityEvent.TYPE_NOTIFICATION_STATE_CHANGED
                    && "android.widget.Toast".contentEquals(String.valueOf(e.getClassName()))) {
                StringBuilder text = new StringBuilder();
                for (CharSequence part : e.getText()) {
                    text.append(text.length() == 0 ? "" : " ").append(part);
                }
                toastText = text.toString();
                toastShownAt = SystemClock.uptimeMillis();
            }
        });
        try (ServerSocket server = new ServerSocket(port, 8, InetAddress.getByName("127.0.0.1"))) {
            Bundle ready = new Bundle();
            ready.putString("ready", "1");
            sendStatus(0, ready);
            while (true) {
                try (Socket client = server.accept()) {
                    BufferedReader in = new BufferedReader(
                            new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
                    // Request line: "GET /path?to=R HTTP/1.1"
                    String[] parts = String.valueOf(in.readLine()).split(" ");
                    String target = parts.length > 1 ? parts[1] : "";
                    int q = target.indexOf('?');
                    String path = q < 0 ? target : target.substring(0, q);
                    Map<String, String> params = query(q < 0 ? "" : target.substring(q + 1));
                    if (path.equals("/quit")) {
                        reply(client, "bye");
                        break;
                    }
                    String body;
                    try {
                        body = answer(ui, path, params);
                    } catch (Exception e) {
                        body = "error: " + e;  // one bad request must not stop the agent: it says why instead
                    }
                    reply(client, body);
                } catch (Exception e) {
                    // The client went away mid-request: nothing to answer.
                }
            }
        } catch (Exception e) {
            Bundle result = new Bundle();
            result.putString("error", String.valueOf(e));
            finish(1, result);
            return;
        }
        finish(0, new Bundle());
    }

    private String answer(UiAutomation ui, String path, Map<String, String> params) throws Exception {
        switch (path) {
            case "/tree":
                return tree(ui);
            case "/pixels":
                return pixels(ui, required(params, "rects"));
            case "/insert":
                Set<String> fields = new HashSet<>(Arrays.asList(required(params, "fields").split(",")));
                return insert(ui, required(params, "text"), fields);
            case "/touch_mode":
                // a key sent with `input keyevent` is a hardware key: it takes the device out of touch mode, where a
                // launch focuses the first field and brings up the keyboard (measured on Android 17; `input text`
                // leaves touch mode as it is)
                setInTouchMode(true);
                return "touch mode";
            case "/rotate":
                return ui.setRotation(Integer.parseInt(required(params, "to"))) ? "rotated" : "refused";
            default:
                return "error: unknown " + path;
        }
    }

    /** The query's parameters, each value URL-decoded. */
    private static Map<String, String> query(String query) throws Exception {
        Map<String, String> params = new HashMap<>();
        for (String pair : query.split("&")) {
            int eq = pair.indexOf('=');
            if (eq > 0) {
                params.put(pair.substring(0, eq), URLDecoder.decode(pair.substring(eq + 1), "UTF-8"));
            }
        }
        return params;
    }

    private static String required(Map<String, String> params, String name) {
        String value = params.get(name);
        if (value == null) {
            throw new IllegalArgumentException("missing parameter " + name);
        }
        return value;
    }

    /**
     * Insert text at the cursor of the field with input focus, as typing would: the field's text becomes what's
     * before the cursor (or selection), the text, and what's after, and the cursor goes after the text.
     */
    private static String insert(UiAutomation ui, String typed, Set<String> fields) {
        AccessibilityNodeInfo root = ui.getRootInActiveWindow();
        AccessibilityNodeInfo field = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if ((field == null || !isField(field, fields)) && root != null) {
            // In a web page, findFocus can answer with the WebView, which holds the input focus, rather than the
            // focused <input> inside it (measured: WebView 146); the <input> itself is marked focused.
            field = focusedField(root, fields);
        }
        if (field == null || !isField(field, fields)) {
            return "no text field has input focus";
        }
        CharSequence shown = field.getText();
        boolean hint = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && field.isShowingHintText();
        String now = shown == null || hint ? "" : shown.toString();
        if (field.isPassword() && !now.isEmpty()) {
            return "a password field hides its text, so text can only be put into it while it's empty";
        }
        int start = Math.min(field.getTextSelectionStart(), field.getTextSelectionEnd());
        int end = Math.max(field.getTextSelectionStart(), field.getTextSelectionEnd());
        if (start < 0 || end > now.length()) {
            start = now.length();
            end = now.length();
        }
        Bundle text = new Bundle();
        text.putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                now.substring(0, start) + typed + now.substring(end));
        if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, text)) {
            return "the field refused the text";
        }
        Bundle cursor = new Bundle();
        cursor.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, start + typed.length());
        cursor.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, start + typed.length());
        field.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, cursor);
        return "inserted";
    }

    /** Whether the node is a text field: its class's short name is one of `fields`, as jevtest decides it. */
    private static boolean isField(AccessibilityNodeInfo node, Set<String> fields) {
        String cls = String.valueOf(node.getClassName());
        return fields.contains(cls.substring(cls.lastIndexOf('.') + 1));
    }

    /** The focused text field under `node`, found by walking the tree; null if none. */
    private static AccessibilityNodeInfo focusedField(AccessibilityNodeInfo node, Set<String> fields) {
        if (node.isFocused() && isField(node, fields)) {
            return node;
        }
        for (int i = 0; i < node.getChildCount(); i++) {
            AccessibilityNodeInfo child = node.getChild(i);
            AccessibilityNodeInfo found = child == null ? null : focusedField(child, fields);
            if (found != null) {
                return found;
            }
        }
        return null;
    }

    /**
     * A fingerprint of the screen's pixels inside each "x1,y1,x2,y2" rectangle (";"-separated, clipped to the
     * screenshot), all from one screenshot; "" without one.
     */
    private static String pixels(UiAutomation ui, String rects) {
        Bitmap shot = ui.takeScreenshot();
        if (shot == null) {
            return "";
        }
        try {
            int hash = 1;
            for (String rect : rects.split(";")) {
                String[] v = rect.split(",");
                if (v.length != 4) {
                    throw new IllegalArgumentException("not x1,y1,x2,y2: " + rect);
                }
                int left = Math.max(0, Math.min(Integer.parseInt(v[0]), shot.getWidth()));
                int top = Math.max(0, Math.min(Integer.parseInt(v[1]), shot.getHeight()));
                int width = Math.max(0, Math.min(Integer.parseInt(v[2]), shot.getWidth()) - left);
                int height = Math.max(0, Math.min(Integer.parseInt(v[3]), shot.getHeight()) - top);
                int[] px = new int[width * height];
                if (px.length > 0) {
                    shot.getPixels(px, 0, width, left, top, width, height);
                }
                hash = 31 * hash + Arrays.hashCode(px);
            }
            return Integer.toHexString(hash);
        } finally {
            shot.recycle();
        }
    }

    private static void reply(Socket client, String body) throws Exception {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        OutputStream out = client.getOutputStream();
        out.write(("HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: "
                + bytes.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        out.write(bytes);
        out.flush();
    }

    private String tree(UiAutomation ui) {
        boolean ime = false;
        int imeTop = 0;  // where the keyboard starts: gestures on the page must stay above it
        StringBuilder bars = new StringBuilder();  // the system's windows over the app: a touch there isn't the app's
        List<AccessibilityWindowInfo> windows = ui.getWindows();
        for (AccessibilityWindowInfo w : windows) {
            Rect r = new Rect();
            w.getBoundsInScreen(r);
            if (w.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                ime = true;
                imeTop = r.top;
            } else if (w.getType() == AccessibilityWindowInfo.TYPE_SYSTEM) {
                bars.append(bars.length() == 0 ? "" : ";").append(r.left).append(',').append(r.top).append(',')
                        .append(r.right).append(',').append(r.bottom);
            }
        }
        // The accessibility cache can miss a WebView's change events; never serve a stale tree.
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            ui.clearCache();
        }
        AccessibilityNodeInfo root = ui.getRootInActiveWindow();
        StringBuilder sb = new StringBuilder();
        sb.append("<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>");
        sb.append("<hierarchy rotation=\"").append(rotation()).append("\" ime=\"").append(ime)
                .append("\" ime-top=\"").append(imeTop)
                .append("\" bars=\"").append(bars)
                .append("\" package=\"").append(root == null ? "" : esc(root.getPackageName())).append("\">");
        if (root != null) {
            node(root, 0, sb);
        }
        toast(ui, sb);
        sb.append("</hierarchy>");
        return sb.toString();
    }

    /**
     * The toast on screen, as a node: its text from its accessibility event, its place from the window manager, and
     * only while its window is visible (measured: a short toast 2.6 s, a long one 4.2 s after it was shown).
     */
    private void toast(UiAutomation ui, StringBuilder sb) {
        String text = toastText;
        if (text == null || SystemClock.uptimeMillis() - toastShownAt > TOAST_LOOK_MS) {
            return;
        }
        Matcher window = TOAST_WINDOW.matcher(shell(ui, "dumpsys window windows"));
        while (window.find()) {
            String block = window.group(1);
            Matcher frame = FRAME.matcher(block);
            if (block.contains("isVisible=true") && frame.find()) {
                sb.append("<node index=\"0\" text=\"").append(esc(text)).append("\" class=\"android.widget.Toast\"")
                        .append(" package=\"android\" content-desc=\"\" bounds=\"[").append(frame.group(1)).append(',')
                        .append(frame.group(2)).append("][").append(frame.group(3)).append(',').append(frame.group(4))
                        .append("]\"></node>");
                return;
            }
        }
    }

    /** What a shell command prints, run as the shell user through UiAutomation. */
    private static String shell(UiAutomation ui, String command) {
        StringBuilder out = new StringBuilder();
        try (BufferedReader in = new BufferedReader(new InputStreamReader(
                new ParcelFileDescriptor.AutoCloseInputStream(ui.executeShellCommand(command)),
                StandardCharsets.UTF_8))) {
            String line;
            while ((line = in.readLine()) != null) {
                out.append(line).append('\n');
            }
        } catch (Exception e) {
            return "";  // no toast is reported, rather than one that may not be there
        }
        return out.toString();
    }

    private int rotation() {
        DisplayManager displays =
                (DisplayManager) getContext().getSystemService(android.content.Context.DISPLAY_SERVICE);
        return displays.getDisplay(Display.DEFAULT_DISPLAY).getRotation();
    }

    /** Whether a node is checked: getChecked() from Android 16 (API 36), isChecked() before it. */
    private static boolean checked(AccessibilityNodeInfo n) {
        if (android.os.Build.VERSION.SDK_INT >= 36) {
            return n.getChecked() == AccessibilityNodeInfo.CHECKED_STATE_TRUE;
        }
        return checkedBeforeAndroid16(n);
    }

    @SuppressWarnings("deprecation") // isChecked() is the only way to ask before API 36, where it's deprecated
    private static boolean checkedBeforeAndroid16(AccessibilityNodeInfo n) {
        return n.isChecked();
    }

    private static void node(AccessibilityNodeInfo n, int index, StringBuilder sb) {
        if (android.os.Build.VERSION.SDK_INT < 34) {
            n.refresh();  // no clearCache() before Android 14
        }
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        sb.append("<node index=\"").append(index).append('"')
                .append(" text=\"").append(esc(n.getText())).append('"')
                .append(" resource-id=\"").append(esc(n.getViewIdResourceName())).append('"')
                .append(" class=\"").append(esc(n.getClassName())).append('"')
                .append(" package=\"").append(esc(n.getPackageName())).append('"')
                .append(" content-desc=\"").append(esc(n.getContentDescription())).append('"')
                .append(" checkable=\"").append(n.isCheckable()).append('"')
                .append(" checked=\"").append(checked(n)).append('"')
                .append(" clickable=\"").append(n.isClickable()).append('"')
                .append(" enabled=\"").append(n.isEnabled()).append('"')
                .append(" focusable=\"").append(n.isFocusable()).append('"')
                .append(" focused=\"").append(n.isFocused()).append('"')
                .append(" scrollable=\"").append(n.isScrollable()).append('"')
                .append(" long-clickable=\"").append(n.isLongClickable()).append('"')
                .append(" password=\"").append(n.isPassword()).append('"')
                .append(" selected=\"").append(n.isSelected()).append('"')
                .append(" bounds=\"[").append(r.left).append(',').append(r.top).append("][")
                .append(r.right).append(',').append(r.bottom).append("]\"");
        if (android.os.Build.VERSION.SDK_INT >= 26) {
            sb.append(" hint=\"").append(esc(n.getHintText())).append('"');
        }
        AccessibilityNodeInfo.RangeInfo range = n.getRangeInfo();
        if (range != null && range.getMax() > range.getMin()) {  // a slider's or progress bar's value, 0 to 1
            float at = (range.getCurrent() - range.getMin()) / (range.getMax() - range.getMin());
            sb.append(" position=\"").append(Math.max(0f, Math.min(1f, at))).append('"');
        }
        sb.append('>');
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo child = n.getChild(i);
            if (child != null) {
                node(child, i, sb);
            }
        }
        sb.append("</node>");
    }

    private static String esc(CharSequence s) {
        if (s == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '&': out.append("&amp;"); break;
                case '<': out.append("&lt;"); break;
                case '>': out.append("&gt;"); break;
                case '"': out.append("&quot;"); break;
                case '\n': out.append("&#10;"); break;
                default:
                    if (c < 0x20 && c != '\t') {
                        out.append(' ');
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}
