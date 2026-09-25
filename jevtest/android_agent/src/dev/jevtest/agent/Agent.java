package dev.jevtest.agent;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.view.Display;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.concurrent.TimeoutException;

/**
 * jevtest Android agent. Started with
 *   am instrument -w -e port 7912 dev.jevtest.agent/.Agent
 * it keeps one UiAutomation connection open and answers on 127.0.0.1:port:
 *   GET /tree        -> the active window as uiautomator-style XML, plus
 *                       ime="true|false" and package="..." on the root element
 *   GET /idle?ms=N   -> returns once the UI has had no accessibility events for 150 ms (max N ms)
 *   GET /change?ms=N -> returns as soon as the screen content changes (max N ms)
 *   GET /quit        -> stops the agent
 * When it is listening it reports status "ready=1" (visible with `am instrument -r`).
 * Reading the tree this way takes milliseconds instead of the ~2 s that a fresh
 * `uiautomator dump` process needs each time.
 */
public class Agent extends Instrumentation {
    private static final long QUIET_MS = 150;
    private int port = 7912;
    private final Object changed = new Object();
    private long changes = 0;

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
        ui.setOnAccessibilityEventListener(event -> {
            int type = event.getEventType();
            if (type == AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED
                    || type == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                    || type == AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
                synchronized (changed) {
                    changes++;
                    changed.notifyAll();
                }
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
                    // Request line: "GET /path?ms=N HTTP/1.1"
                    String[] parts = String.valueOf(in.readLine()).split(" ");
                    String target = parts.length > 1 ? parts[1] : "";
                    int q = target.indexOf("?ms=");
                    String path = q < 0 ? target : target.substring(0, q);
                    long ms = q < 0 ? 0 : Long.parseLong(target.substring(q + 4));
                    String body;
                    if (path.equals("/tree")) {
                        body = tree(ui);
                    } else if (path.equals("/idle")) {
                        body = idle(ui, ms);
                    } else if (path.equals("/change")) {
                        body = change(ms);
                    } else if (path.equals("/quit")) {
                        reply(client, "bye");
                        break;
                    } else {
                        body = "unknown " + path;
                    }
                    reply(client, body);
                } catch (Exception e) {
                    // One bad request must not stop the agent.
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

    private static String idle(UiAutomation ui, long ms) {
        try {
            ui.waitForIdle(QUIET_MS, ms);
            return "idle";
        } catch (TimeoutException e) {
            return "busy";
        }
    }

    private String change(long ms) throws InterruptedException {
        long deadline = System.currentTimeMillis() + ms;
        synchronized (changed) {
            long start = changes;
            while (changes == start) {
                long left = deadline - System.currentTimeMillis();
                if (left <= 0) {
                    return "unchanged";
                }
                changed.wait(left);
            }
        }
        return "changed";
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
        List<AccessibilityWindowInfo> windows = ui.getWindows();
        for (AccessibilityWindowInfo w : windows) {
            if (w.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                ime = true;
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
                .append("\" package=\"").append(root == null ? "" : esc(root.getPackageName())).append("\">");
        if (root != null) {
            node(root, 0, sb);
        }
        sb.append("</hierarchy>");
        return sb.toString();
    }

    private int rotation() {
        WindowManager wm = (WindowManager) getContext().getSystemService(android.content.Context.WINDOW_SERVICE);
        Display display = wm.getDefaultDisplay();
        return display.getRotation();
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
                .append(" checked=\"").append(n.isChecked()).append('"')
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
