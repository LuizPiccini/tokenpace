package app.tokenpace.widget;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;
import static org.robolectric.Shadows.shadowOf;

import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.appwidget.AppWidgetHostView;
import android.appwidget.AppWidgetManager;
import android.content.Context;
import android.content.Intent;
import android.os.Looper;
import android.view.View;
import android.widget.Adapter;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.RemoteViews;
import android.widget.TextView;

import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.robolectric.Robolectric;
import org.robolectric.RobolectricTestRunner;
import org.robolectric.RuntimeEnvironment;
import org.robolectric.android.controller.ActivityController;
import org.robolectric.annotation.Config;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;

@RunWith(RobolectricTestRunner.class)
@Config(sdk = 34)
public class UsageWidgetTest {
    static final String FIXTURE_TEMPLATE = "{\"title\":\"AI subscriptions\",\"now\":\"2026-10-06T02:40:00Z\",\"groups\":[{\"id\":\"personal\",\"label\":\"Personal\",\"logo_url\":null,\"items\":[{\"rank\":1,\"name\":\"Claude\",\"window\":\"Week\",\"level\":\"use\",\"verdict\":\"Use first\",\"used_percent\":32,\"elapsed_percent\":75.4,\"need\":2.76,\"free\":false,\"resets_epoch\":\"__R1__\",\"released_epoch\":null,\"note\":null},{\"rank\":2,\"name\":\"Xiaomi MiMo\",\"window\":\"Month\",\"level\":\"lean_use\",\"verdict\":\"Use too\",\"used_percent\":40,\"elapsed_percent\":52,\"need\":1.25,\"free\":false,\"resets_epoch\":\"__R2__\",\"released_epoch\":null,\"note\":\"Entered by hand, 2h00 ago\"},{\"rank\":3,\"name\":\"OpenRouter free\",\"window\":\"Day (UTC)\",\"level\":\"free\",\"verdict\":\"Free reserve\",\"used_percent\":0.1,\"elapsed_percent\":11,\"need\":1.12,\"free\":true,\"resets_epoch\":\"__R3__\",\"released_epoch\":null,\"note\":null},{\"rank\":4,\"name\":\"ChatGPT\",\"window\":\"Week\",\"level\":\"blocked\",\"verdict\":\"Used up now\",\"used_percent\":60,\"elapsed_percent\":46,\"need\":0.74,\"free\":false,\"resets_epoch\":\"__R4__\",\"released_epoch\":\"__REL__\",\"note\":null},{\"rank\":5,\"name\":\"Extra\",\"window\":\"Week\",\"level\":\"save\",\"verdict\":\"Save\",\"used_percent\":90,\"elapsed_percent\":40,\"need\":0.17,\"free\":false,\"resets_epoch\":null,\"released_epoch\":null,\"note\":null}]},{\"id\":\"work\",\"label\":\"Work\",\"logo_url\":null,\"items\":[{\"rank\":1,\"name\":\"Claude\",\"window\":\"Week\",\"level\":\"use\",\"verdict\":\"Use first\",\"used_percent\":9,\"elapsed_percent\":52,\"need\":1.9,\"free\":false,\"resets_epoch\":\"__R1__\",\"released_epoch\":null,\"note\":null},{\"rank\":2,\"name\":\"ChatGPT\",\"window\":\"Week\",\"level\":\"lean_use\",\"verdict\":\"Use too\",\"used_percent\":4,\"elapsed_percent\":46,\"need\":1.78,\"free\":false,\"resets_epoch\":\"__R1__\",\"released_epoch\":null,\"note\":null},{\"rank\":3,\"name\":\"Gemini\",\"window\":\"Week\",\"level\":\"on_pace\",\"verdict\":\"On pace\",\"used_percent\":1,\"elapsed_percent\":2,\"need\":1,\"free\":false,\"resets_epoch\":null,\"released_epoch\":null,\"note\":null}]}]}";
    static final String CLOSED = "http://127.0.0.1:9";

    Context ctx;
    ServerSocket server;

    @Before
    public void setUp() {
        ctx = RuntimeEnvironment.getApplication();
        UsageWidget.forceList = null;
        UsageWidget.prefs(ctx).edit().clear().commit();
    }

    @After
    public void tearDown() {
        if (server != null) {
            try {
                server.close();
            } catch (Exception ignored) {
                // test teardown
            }
        }
    }

    static String fixture() {
        long now = System.currentTimeMillis() / 1000L;
        return FIXTURE_TEMPLATE
            .replace("\"__R1__\"", String.valueOf(now + 41 * 3600 + 120))
            .replace("\"__R2__\"", String.valueOf(now + 17 * 86400 + 120))
            .replace("\"__R3__\"", String.valueOf(now + 21 * 3600 + 120))
            .replace("\"__R4__\"", String.valueOf(now + 3 * 86400 + 120))
            .replace("\"__REL__\"", String.valueOf(now + 2 * 3600 + 10 * 60 + 30));
    }

    void server(String url) {
        UsageWidget.prefs(ctx).edit().putString("server_url", url).commit();
    }

    void store(String json) {
        server(CLOSED);
        UsageWidget.prefs(ctx).edit().putString("json", json)
            .putLong("fetched", System.currentTimeMillis()).commit();
    }

    View render(String note, boolean refreshing, int heightDp) {
        RemoteViews rv = UsageWidget.build(ctx, note, refreshing, heightDp);
        // Collection adapters attach only under an AppWidgetHostView, as on a real home screen.
        return rv.apply(ctx, new AppWidgetHostView(ctx));
    }

    static Items items(View root) {
        ListView list = root.findViewById(R.id.list);
        return new Items(list, list.getAdapter());
    }

    static final class Items {
        final ListView view;
        final Adapter adapter;

        Items(ListView view, Adapter adapter) {
            this.view = view;
            this.adapter = adapter;
        }

        int count() {
            return adapter == null ? 0 : adapter.getCount();
        }

        View get(int i) {
            return adapter.getView(i, null, view);
        }
    }

    static String text(View row, int id) {
        return ((TextView) row.findViewById(id)).getText().toString();
    }

    static int color(View row) {
        return ((TextView) row.findViewById(R.id.row_verdict)).getCurrentTextColor();
    }

    static String status(View root) {
        TextView status = root.findViewById(R.id.status);
        return status.getVisibility() == View.VISIBLE ? status.getText().toString() : null;
    }

    @Test
    public void rendersGroupsAsScrollableList() {
        store(fixture());
        View root = render(null, false, 0);
        assertEquals(View.VISIBLE, root.findViewById(R.id.list).getVisibility());
        assertEquals(View.GONE, root.findViewById(R.id.rows).getVisibility());
        assertEquals("AI subscriptions", text(root, R.id.title));
        Items list = items(root);
        // Personal header + 5 rows, Work header + 3 rows: nothing hidden, the list scrolls.
        assertEquals(10, list.count());

        View personal = list.get(0);
        assertEquals("Personal", text(personal, R.id.section_text));
        assertEquals(View.GONE, personal.findViewById(R.id.section_logo).getVisibility());

        View first = list.get(1);
        assertEquals("1. Claude · Week", text(first, R.id.row_name));
        assertEquals("Use first", text(first, R.id.row_verdict));
        ProgressBar bar = first.findViewById(R.id.row_bar);
        assertEquals(32, bar.getProgress());
        assertEquals(75, bar.getSecondaryProgress());
        assertEquals(View.VISIBLE, bar.getVisibility());
        assertEquals(View.GONE, first.findViewById(R.id.row_bar_ahead).getVisibility());
        assertEquals("used 32% · period 75% · 2.8× · resets 1d 17h", text(first, R.id.row_detail));
        assertEquals(0xFF4FB8A6, color(first));

        View second = list.get(2);
        assertEquals("Use too", text(second, R.id.row_verdict));
        assertTrue(text(second, R.id.row_detail).endsWith("· entered by hand, 2h00 ago"));

        View free = list.get(3);
        assertEquals("used 0% · period 11% · resets 21h02", text(free, R.id.row_detail));
        assertEquals(0xFF7894C5, color(free));

        assertEquals("used up · frees in 2h10", text(list.get(4), R.id.row_detail));
        // Ahead of pace (90% used, 40% elapsed): the amber bar replaces the teal one.
        View save = list.get(5);
        assertEquals(View.GONE, save.findViewById(R.id.row_bar).getVisibility());
        ProgressBar aheadBar = save.findViewById(R.id.row_bar_ahead);
        assertEquals(View.VISIBLE, aheadBar.getVisibility());
        assertEquals(40, aheadBar.getProgress());
        assertEquals(90, aheadBar.getSecondaryProgress());
        assertEquals("Work", text(list.get(6), R.id.section_text));
        assertEquals("3. Gemini · Week", text(list.get(9), R.id.row_name));
        assertNull(status(root));
        String header = text(root, R.id.updated);
        assertTrue(header, header.matches("\\d\\d:\\d\\d  ↻"));
    }

    @Test
    public void olderAndroidFitsRowsToHeight() {
        UsageWidget.forceList = false;
        store(fixture());
        // Font scale 1: chrome 38.9 dp, row 47.4 dp, header 19.5 dp. 200 dp: first header + 2 rows.
        View root = render(null, false, 200);
        assertEquals(View.GONE, root.findViewById(R.id.list).getVisibility());
        assertEquals(3, ((LinearLayout) root.findViewById(R.id.rows)).getChildCount());
        // Hidden: 2 of the first group's 4 and both of Work's 2.
        assertTrue(text(root, R.id.updated).startsWith("+4 · "));
        // 400 dp: header + 4 rows + Work header + 2 rows.
        assertEquals(8, ((LinearLayout) render(null, false, 400).findViewById(R.id.rows)).getChildCount());
        // 60 dp: no room for a header; one row is always shown.
        assertEquals(1, ((LinearLayout) render(null, false, 60).findViewById(R.id.rows)).getChildCount());
    }

    @Test
    public void refreshingShowsInHeader() {
        store(fixture());
        View root = render(null, true, 0);
        assertNull(status(root));
        assertEquals(10, items(root).count());
        assertEquals("refreshing…  ↻", text(root, R.id.updated));
    }

    @Test
    public void asksForServerFirst() {
        View root = render(null, false, 0);
        assertEquals(0, items(root).count());
        assertEquals("open the Token Pace app to set the server", status(root));
        assertFalse(UsageWidget.refreshBlocking(ctx));
    }

    @Test
    public void showsHintWithoutData() {
        server(CLOSED);
        assertEquals("no data yet · tap ↻", status(render(null, false, 0)));
    }

    @Test
    public void failedRefreshSaysWhyAndKeepsRanking() {
        store(fixture());
        AppWidgetManager manager = AppWidgetManager.getInstance(ctx);
        int id = shadowOf(manager).createWidget(UsageWidget.class, R.layout.widget);
        assertEquals("refreshing…  ↻", text(shadowOf(manager).getViewFor(id), R.id.updated));
        assertFalse(UsageWidget.refreshBlocking(ctx));
        String status = status(shadowOf(manager).getViewFor(id));
        assertTrue(status, status.matches("failed at \\d\\d:\\d\\d: no connection to the server"));
        assertEquals(10, items(render(null, false, 0)).count());
    }

    @Test
    public void newerApkIsAnnounced() {
        store(fixture().replaceFirst("\\{", "{\"apk\": {\"version_code\": 99, \"version\": \"9.9\"}, "));
        assertEquals("new version 9.9 on the page", status(render(null, false, 0)));
        store(fixture().replaceFirst("\\{", "{\"apk\": {\"version_code\": 1, \"version\": \"0.1\"}, "));
        assertNull(status(render(null, false, 0)));
    }

    @Test
    public void refreshesFromServerWithToken() throws Exception {
        final String body = fixture();
        // A minimal HTTP server: 200 with the fixture only for the right bearer token.
        server = new ServerSocket(0, 5, InetAddress.getByName("127.0.0.1"));
        final ServerSocket listening = server;
        Thread responder = new Thread(() -> {
            while (!listening.isClosed()) {
                try (Socket s = listening.accept()) {
                    BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8));
                    boolean ok = false;
                    for (String line = in.readLine(); line != null && !line.isEmpty(); line = in.readLine()) {
                        ok |= line.equalsIgnoreCase("Authorization: Bearer s3cret");
                    }
                    byte[] out = (ok ? body : "{\"error\":\"token required\"}").getBytes(StandardCharsets.UTF_8);
                    OutputStream os = s.getOutputStream();
                    os.write(("HTTP/1.1 " + (ok ? "200 OK" : "401 Unauthorized") + "\r\nContent-Type: application/json\r\n"
                        + "Content-Length: " + out.length + "\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.UTF_8));
                    os.write(out);
                    os.flush();
                } catch (Exception ignored) {
                    // socket closed at teardown
                }
            }
        });
        responder.setDaemon(true);
        responder.start();
        server("http://127.0.0.1:" + server.getLocalPort());
        assertFalse(UsageWidget.refreshBlocking(ctx));
        assertEquals("the server wants a token (open the app)", UsageWidget.prefs(ctx).getString("last_error", ""));
        UsageWidget.prefs(ctx).edit().putString("token", "s3cret").commit();
        assertTrue(UsageWidget.refreshBlocking(ctx));
        assertEquals(10, items(render(null, false, 0)).count());
    }

    @Test
    public void normalizesServerAddress() {
        assertEquals("http://my-server:8787", UsageWidget.normalizeUrl("  my-server:8787/ "));
        assertEquals("https://usage.example.com", UsageWidget.normalizeUrl("https://usage.example.com//"));
        assertNull(UsageWidget.normalizeUrl(""));
        assertNull(UsageWidget.normalizeUrl("http://"));
        assertEquals("https://Usage.Example.com", UsageWidget.normalizeUrl("HTTPS://Usage.Example.com/"));
        assertNull(UsageWidget.normalizeUrl("ftp://usage.example.com"));
        assertNull(UsageWidget.normalizeUrl("http://host:8787/#token=x"));
        assertNull(UsageWidget.normalizeUrl("http://user:pw@host:8787"));
        assertEquals("http://host/tokenpace", UsageWidget.normalizeUrl("host/tokenpace/"));
    }

    @Test
    public void setupSavesAndTests() throws Exception {
        ActivityController<SetupActivity> controller = Robolectric.buildActivity(SetupActivity.class).create();
        SetupActivity activity = controller.get();
        ((EditText) activity.findViewById(R.id.setup_url)).setText("127.0.0.1:9/");
        activity.findViewById(R.id.setup_save).performClick();
        assertEquals("http://127.0.0.1:9", UsageWidget.serverUrl(ctx));
        long deadline = System.currentTimeMillis() + 10000;
        String status = "";
        while (!status.startsWith("Saved") && System.currentTimeMillis() < deadline) {
            Thread.sleep(50);
            shadowOf(Looper.getMainLooper()).idle();
            status = ((TextView) activity.findViewById(R.id.setup_status)).getText().toString();
        }
        assertEquals("Saved, but the test failed: no connection to the server", status);
    }

    @Test
    public void addingWidgetAndBroadcastScheduleJobs() {
        JobScheduler js = (JobScheduler) ctx.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        js.cancelAll();
        shadowOf(AppWidgetManager.getInstance(ctx)).createWidget(UsageWidget.class, R.layout.widget);
        JobInfo now = js.getPendingJob(RefreshJob.JOB_NOW);
        JobInfo periodic = js.getPendingJob(RefreshJob.JOB_PERIODIC);
        assertTrue("immediate job queued", now != null && now.isExpedited());
        assertTrue("periodic job queued", periodic != null && periodic.isPeriodic() && periodic.isPersisted());
        assertEquals(RefreshJob.PERIOD_MS, periodic.getIntervalMillis());
        js.cancel(RefreshJob.JOB_NOW);
        new UsageWidget().onReceive(ctx, new Intent(UsageWidget.ACTION_REFRESH));
        assertTrue(js.getPendingJob(RefreshJob.JOB_NOW) != null);
    }

    MainActivity tap(Intent intent) throws Exception {
        ActivityController<MainActivity> controller = Robolectric.buildActivity(MainActivity.class, intent).create();
        long deadline = System.currentTimeMillis() + 10000;
        while (UsageWidget.prefs(ctx).getString("last_error", null) == null && System.currentTimeMillis() < deadline) {
            Thread.sleep(50);
        }
        // The worker posts finish() to the main thread after recording the error; idle until it lands.
        long finishBy = System.currentTimeMillis() + 3000;
        do {
            Thread.sleep(50);
            shadowOf(Looper.getMainLooper()).idle();
        } while (!controller.get().isFinishing() && System.currentTimeMillis() < finishBy);
        return controller.get();
    }

    @Test
    public void headerTapRefreshesInForegroundAndCloses() throws Exception {
        server(CLOSED);
        MainActivity activity = tap(new Intent(ctx, MainActivity.class).setAction(UsageWidget.ACTION_REFRESH));
        assertTrue(activity.isFinishing());
        assertTrue(UsageWidget.prefs(ctx).getString("last_error", "").startsWith("no connection"));
        assertNull("header tap does not open the page", shadowOf(activity).getNextStartedActivity());
    }

    @Test
    public void rowTapRefreshesThenOpensPage() throws Exception {
        server(CLOSED);
        MainActivity activity = tap(new Intent(ctx, MainActivity.class).putExtra(MainActivity.EXTRA_OPEN_PAGE, true));
        assertTrue(activity.isFinishing());
        Intent opened = shadowOf(activity).getNextStartedActivity();
        assertEquals(Intent.ACTION_VIEW, opened.getAction());
        assertEquals(CLOSED + "/", opened.getDataString());
    }

    @Test
    public void tapWithoutServerOpensSetup() {
        MainActivity activity = Robolectric.buildActivity(MainActivity.class,
            new Intent(ctx, MainActivity.class).setAction(UsageWidget.ACTION_REFRESH)).create().get();
        assertTrue(activity.isFinishing());
        Intent opened = shadowOf(activity).getNextStartedActivity();
        assertEquals(SetupActivity.class.getName(), opened.getComponent().getClassName());
    }
}
