package dev.jevtest.agent;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.graphics.Rect;
import android.hardware.display.DisplayManager;
import android.os.Bundle;
import android.view.Display;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;

/**
 * jevtest Android agent. Started with
 *   am instrument -w -e port 7912 dev.jevtest.agent/.Agent
 * it keeps one UiAutomation connection open and answers on 127.0.0.1:port:
 *   GET /tree        -> the active window as uiautomator-style XML, plus ime="true|false",
 *                       ime-top="<y where the keyboard starts>" and package="..." on the root element
 *   GET /rotate?to=R -> locks the screen to rotation R (0-3, as /tree reports it) through UiAutomation, which
 *                       works on every Android version (the user_rotation setting doesn't on some phones)
 *                       and puts the device's own rotation state back when the agent stops
 *   GET /pixels?x1=..&y1=..&x2=..&y2=.. -> a fingerprint of the pixels in that part of the screen: a system
 *                       dialog fading or sliding in reports its final bounds in the tree at once, so only
 *                       its pixels show it is still moving
 *   GET /quit        -> stops the agent
 * When it is listening it reports status "ready=1" (visible with `am instrument -r`).
 * Reading the tree this way takes milliseconds instead of the ~2 s that a fresh
 * `uiautomator dump` process needs each time. The agent never waits: jevtest reads the screen when a step
 * checks what it waits for.
 */
public class Agent extends Instrumentation {
    private int port = 7912;

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
                    String body;
                    if (path.equals("/tree")) {
                        body = tree(ui);
                    } else if (path.equals("/pixels")) {
                        body = pixels(ui, (int) param(target, "x1", 0), (int) param(target, "y1", 0),
                                (int) param(target, "x2", 0), (int) param(target, "y2", 0));
                    } else if (path.equals("/rotate")) {
                        body = ui.setRotation((int) param(target, "to", -1)) ? "rotated" : "refused";
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

    private static long param(String target, String name, long fallback) {
        for (String pair : target.substring(target.indexOf('?') + 1).split("&")) {
            if (pair.startsWith(name + "=")) {
                return Long.parseLong(pair.substring(name.length() + 1));
            }
        }
        return fallback;
    }

    /** A fingerprint of the screen's pixels inside the bounds (clipped to the screenshot); "" without one. */
    private static String pixels(UiAutomation ui, int x1, int y1, int x2, int y2) {
        Bitmap shot = ui.takeScreenshot();
        if (shot == null) {
            return "";
        }
        try {
            int left = Math.max(0, Math.min(x1, shot.getWidth()));
            int top = Math.max(0, Math.min(y1, shot.getHeight()));
            int width = Math.max(0, Math.min(x2, shot.getWidth()) - left);
            int height = Math.max(0, Math.min(y2, shot.getHeight()) - top);
            if (width == 0 || height == 0) {
                return "";
            }
            int[] px = new int[width * height];
            shot.getPixels(px, 0, width, left, top, width, height);
            return Integer.toHexString(Arrays.hashCode(px));
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
        List<AccessibilityWindowInfo> windows = ui.getWindows();
        for (AccessibilityWindowInfo w : windows) {
            if (w.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                ime = true;
                Rect r = new Rect();
                w.getBoundsInScreen(r);
                imeTop = r.top;
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
                .append("\" package=\"").append(root == null ? "" : esc(root.getPackageName())).append("\">");
        if (root != null) {
            node(root, 0, sb);
        }
        sb.append("</hierarchy>");
        return sb.toString();
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
