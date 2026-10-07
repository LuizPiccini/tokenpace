package app.tokenpace.widget;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.widget.RemoteViews;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.ConnectException;
import java.net.HttpURLConnection;
import java.net.NoRouteToHostException;
import java.net.SocketTimeoutException;
import java.net.URL;
import java.net.UnknownHostException;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * Home-screen widget for a tokenpace server. Reads the compact ranking from
 * {server}/api/widget; the server address and optional token come from SetupActivity.
 *
 * Taps open MainActivity, which fetches in the foreground (background network is often
 * blocked on phones, which can leave a widget on "refreshing…" forever); the header only
 * refreshes, a row refreshes and opens the page. RefreshJob refreshes every 15 min in the
 * background when Android allows it. On Android 12+ the rows are a scrollable list;
 * older versions fit rows to the widget's height.
 */
public class UsageWidget extends AppWidgetProvider {
    static final String ACTION_REFRESH = "app.tokenpace.widget.REFRESH";
    static final String PREFS = "tokenpace";
    static final int LIST_PER_GROUP = 8;
    static final int MAX_FIRST_GROUP = 4;   // height-fitted layout (Android < 12)
    static final int MAX_OTHER_GROUP = 2;
    static final long LOGO_MAX_AGE_MS = 24 * 3600 * 1000L;
    static final int LOGO_PX = 64;
    /** Null: by API level (list on 31+). Tests force either layout. */
    static Boolean forceList = null;

    // Layout metrics in dp for the height-fitted layout, mirrored from res/layout (keep in sync).
    static final float ROOT_PADDING_V = 20f;
    static final float ROW_FIXED = 4f + 3f + 6f + 2f;
    static final float SECTION_PADDING = 6f;
    static final float SECTION_LOGO = 12f;
    static final float LINE = 1.35f;

    static boolean useList() {
        return forceList != null ? forceList : Build.VERSION.SDK_INT >= 31;
    }

    @Override
    public void onEnabled(Context context) {
        RefreshJob.schedulePeriodic(context);
    }

    @Override
    public void onDisabled(Context context) {
        RefreshJob.cancelAll(context);
    }

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        RefreshJob.schedulePeriodic(context);
        requestRefresh(context);
    }

    @Override
    public void onAppWidgetOptionsChanged(Context context, AppWidgetManager manager, int id, Bundle options) {
        renderAll(context, null, false);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        super.onReceive(context, intent);
        if (ACTION_REFRESH.equals(intent.getAction())) {
            requestRefresh(context);
        }
    }

    // ------------------------------------------------------------ settings

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static String serverUrl(Context context) {
        String url = prefs(context).getString("server_url", null);
        return url == null || url.isEmpty() ? null : url;
    }

    static String token(Context context) {
        String token = prefs(context).getString("token", null);
        return token == null || token.isEmpty() ? null : token;
    }

    /** "my-server:8787/" -> "http://my-server:8787"; null when it is not a usable URL. */
    static String normalizeUrl(String raw) {
        if (raw == null) {
            return null;
        }
        String url = raw.trim();
        if (url.isEmpty()) {
            return null;
        }
        String lower = url.toLowerCase(Locale.ROOT);
        if (lower.startsWith("https://")) {
            url = "https://" + url.substring(8);
        } else if (lower.startsWith("http://")) {
            url = "http://" + url.substring(7);
        } else if (lower.contains("://")) {
            return null;   // only http and https
        } else {
            url = "http://" + url;
        }
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        try {
            URL parsed = new URL(url);
            if (parsed.getHost() == null || parsed.getHost().isEmpty() || parsed.getUserInfo() != null
                    || parsed.getQuery() != null || parsed.getRef() != null) {
                return null;   // a plain base address: /api/widget is appended to it
            }
        } catch (Exception e) {
            return null;
        }
        return url;
    }

    // ------------------------------------------------------------ fetching

    /** Background path (system update, reboot): queue a job and show "refreshing…". */
    static void requestRefresh(Context context) {
        Context app = context.getApplicationContext();
        renderAll(app, null, true);
        try {
            RefreshJob.scheduleNow(app);
        } catch (RuntimeException e) {
            recordFailure(app, "could not schedule the refresh (" + e.getClass().getSimpleName() + ")");
            renderAll(app, null, false);
        }
    }

    /** Fetch and render; runs on MainActivity's, SetupActivity's or RefreshJob's worker thread. */
    static boolean refreshBlocking(Context app) {
        String base = serverUrl(app);
        if (base == null) {
            renderAll(app, null, false);
            return false;
        }
        String error;
        try {
            String body = fetchText(base + "/api/widget", userAgent(app), token(app));
            JSONObject root = new JSONObject(body);
            root.getJSONArray("groups");
            cacheLogos(app, base, root);
            long now = System.currentTimeMillis();
            prefs(app).edit().putString("json", body).putLong("fetched", now)
                .putLong("last_attempt", now).remove("last_error").commit();
            renderAll(app, null, false);
            return true;
        } catch (Exception e) {
            error = describe(e);
        } catch (Throwable t) {
            error = "internal error (" + t.getClass().getSimpleName() + ")";
        }
        recordFailure(app, error);
        renderAll(app, null, false);
        return false;
    }

    static String describe(Exception e) {
        if (e instanceof SocketTimeoutException) {
            return "the server did not answer in time";
        }
        if (e instanceof ConnectException || e instanceof NoRouteToHostException || e instanceof UnknownHostException) {
            return "no connection to the server";
        }
        if (e instanceof JSONException) {
            return "invalid answer from the server";
        }
        String msg = e.getMessage();
        if ("HTTP 401".equals(msg)) {
            return "the server wants a token (open the app)";
        }
        return e.getClass().getSimpleName() + (msg == null ? "" : ": " + (msg.length() > 60 ? msg.substring(0, 60) : msg));
    }

    static void recordFailure(Context context, String why) {
        prefs(context).edit().putLong("last_attempt", System.currentTimeMillis()).putString("last_error", why).commit();
    }

    static long versionCode(Context context) {
        try {
            PackageInfo info = context.getPackageManager().getPackageInfo(context.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? info.getLongVersionCode() : info.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    static String userAgent(Context context) {
        String version = "?";
        try {
            version = context.getPackageManager().getPackageInfo(context.getPackageName(), 0).versionName;
        } catch (Exception ignored) {
            // keep "?"
        }
        return "tokenpace-widget/" + version + " (Android " + Build.VERSION.SDK_INT + ")";
    }

    static String fetchText(String address, String userAgent, String token) throws Exception {
        return new String(fetchBytes(address, userAgent, token, 262144), "UTF-8");
    }

    static byte[] fetchBytes(String address, String userAgent, String token, int limit) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(address).openConnection();
        conn.setConnectTimeout(4000);
        conn.setReadTimeout(6000);
        conn.setRequestProperty("User-Agent", userAgent);
        if (token != null) {
            conn.setRequestProperty("Authorization", "Bearer " + token);
        }
        try {
            if (conn.getResponseCode() != 200) {
                throw new IllegalStateException("HTTP " + conn.getResponseCode());
            }
            InputStream in = conn.getInputStream();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[4096];
            int n;
            while ((n = in.read(buf)) > 0) {
                out.write(buf, 0, n);
                if (out.size() > limit) {
                    throw new IllegalStateException("too large");
                }
            }
            return out.toByteArray();
        } finally {
            conn.disconnect();
        }
    }

    static File logoFile(Context context, String groupId) {
        return new File(context.getFilesDir(), "logo-" + groupId.replaceAll("[^A-Za-z0-9_-]", "") + ".png");
    }

    /** Group logos for the section headers: PNG, JPEG or WebP (SVG cannot be decoded here). */
    static void cacheLogos(Context context, String base, JSONObject root) {
        JSONArray groups = root.optJSONArray("groups");
        for (int i = 0; groups != null && i < groups.length(); i++) {
            JSONObject g = groups.optJSONObject(i);
            String logo = g == null || g.isNull("logo_url") ? null : g.optString("logo_url", null);
            if (logo == null || logo.isEmpty()) {
                continue;
            }
            File file = logoFile(context, g.optString("id"));
            if (file.exists() && System.currentTimeMillis() - file.lastModified() < LOGO_MAX_AGE_MS) {
                continue;
            }
            try {
                byte[] bytes = fetchBytes(base + "/" + logo.replaceFirst("^/", ""), userAgent(context), token(context), 524288);
                Bitmap small = decodeLogo(bytes);
                if (small == null) {
                    continue;
                }
                try (FileOutputStream out = new FileOutputStream(file)) {
                    small.compress(Bitmap.CompressFormat.PNG, 100, out);
                }
            } catch (Exception ignored) {
                // no logo: the header shows the label alone
            }
        }
    }

    /** Decode at most ~4x the target size, then fit inside LOGO_PX x LOGO_PX (any aspect ratio). */
    static Bitmap decodeLogo(byte[] bytes) {
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) {
            return null;
        }
        BitmapFactory.Options opts = new BitmapFactory.Options();
        opts.inSampleSize = 1;
        while (Math.max(bounds.outWidth, bounds.outHeight) / (opts.inSampleSize * 2) >= LOGO_PX * 4) {
            opts.inSampleSize *= 2;
        }
        Bitmap bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length, opts);
        if (bitmap == null) {
            return null;
        }
        float scale = Math.min(1f, (float) LOGO_PX / Math.max(bitmap.getWidth(), bitmap.getHeight()));
        int w = Math.max(1, Math.round(bitmap.getWidth() * scale));
        int h = Math.max(1, Math.round(bitmap.getHeight() * scale));
        return Bitmap.createScaledBitmap(bitmap, w, h, true);
    }

    // ------------------------------------------------------------ rendering

    static void renderAll(Context context, String note, boolean refreshing) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        int[] ids = manager.getAppWidgetIds(new ComponentName(context, UsageWidget.class));
        if (ids == null) {
            return;
        }
        for (int id : ids) {
            Bundle options = manager.getAppWidgetOptions(id);
            int heightDp = options == null ? 0 : options.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0);
            manager.updateAppWidget(id, build(context, note, refreshing, heightDp));
        }
    }

    static float lineDp(Context context, float sp) {
        return sp * context.getResources().getConfiguration().fontScale * LINE;
    }

    static float rowDp(Context context) {
        return ROW_FIXED + lineDp(context, 13) + lineDp(context, 11);
    }

    static float sectionDp(Context context) {
        return SECTION_PADDING + Math.max(SECTION_LOGO, lineDp(context, 10));
    }

    static float chromeDp(Context context, boolean statusVisible) {
        return ROOT_PADDING_V + lineDp(context, 14) + (statusVisible ? 2f + lineDp(context, 11) : 0f);
    }

    static String clock(long ms) {
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date(ms));
    }

    /** The status line: setup hint, an explicit note, the last failure, old data, or a newer APK. */
    static String status(Context context, String note, boolean refreshing, JSONObject root, long fetched) {
        if (note != null) {
            return note;
        }
        if (serverUrl(context) == null) {
            return "open the Token Pace app to set the server";
        }
        SharedPreferences p = prefs(context);
        String error = p.getString("last_error", null);
        long attempt = p.getLong("last_attempt", 0);
        long now = System.currentTimeMillis();
        String status = null;
        if (error != null && attempt >= fetched && !refreshing) {
            status = "failed at " + clock(attempt) + ": " + error;
        } else if (root == null && !refreshing && p.getString("json", null) == null) {
            status = "no data yet · tap ↻";
        } else if (root != null && fetched > 0 && now - fetched > 2 * 3600 * 1000L) {
            status = "data from " + duration((now - fetched) / 1000L) + " ago";
        }
        JSONObject apk = root == null ? null : root.optJSONObject("apk");
        if (apk != null && apk.optLong("version_code", 0) > versionCode(context)) {
            String update = "new version " + apk.optString("version", "") + " on the page";
            status = status == null ? update : status + " · " + update;
        }
        return status;
    }

    static RemoteViews build(Context context, String note, boolean refreshing, int heightDp) {
        RemoteViews v = new RemoteViews(context.getPackageName(), R.layout.widget);
        SharedPreferences p = prefs(context);
        String json = p.getString("json", null);
        long fetched = p.getLong("fetched", 0);
        long now = System.currentTimeMillis() / 1000L;
        JSONObject root = null;
        JSONArray groups = null;
        String invalid = null;
        if (json != null) {
            try {
                root = new JSONObject(json);
                groups = root.getJSONArray("groups");
            } catch (Exception e) {
                root = null;
                invalid = "invalid answer from the server";
            }
        }
        String status = invalid != null && note == null ? invalid : status(context, note, refreshing, root, fetched);
        int hidden = 0;
        boolean list = useList();
        v.setViewVisibility(R.id.list, list ? View.VISIBLE : View.GONE);
        v.setViewVisibility(R.id.rows, list ? View.GONE : View.VISIBLE);
        v.removeAllViews(R.id.rows);
        v.setTextViewText(R.id.title, root == null ? context.getString(R.string.title)
            : root.optString("title", context.getString(R.string.title)));
        if (groups != null) {
            if (list) {
                fillList(context, v, groups, now);
            } else {
                hidden = fillFitted(context, v, groups, now, heightDp, status != null);
            }
            if (status == null && countItems(groups) == 0) {
                status = "no readings to rank";
            }
        } else if (list) {
            fillList(context, v, new JSONArray(), now);
        }
        if (status != null) {
            v.setTextViewText(R.id.status, status);
            v.setViewVisibility(R.id.status, View.VISIBLE);
        } else {
            v.setViewVisibility(R.id.status, View.GONE);
        }
        StringBuilder header = new StringBuilder();
        if (refreshing) {
            header.append("refreshing…  ");
        } else {
            if (hidden > 0) {
                header.append('+').append(hidden).append(" · ");
            }
            if (fetched > 0) {
                header.append(clock(fetched)).append("  ");
            }
        }
        header.append('↻');
        v.setTextViewText(R.id.updated, header.toString());

        // Header: refresh in the foreground. Rows: refresh, then open the page.
        Intent refresh = new Intent(context, MainActivity.class).setAction(ACTION_REFRESH)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        v.setOnClickPendingIntent(R.id.header, PendingIntent.getActivity(context, 1, refresh,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        Intent open = new Intent(context, MainActivity.class).putExtra(MainActivity.EXTRA_OPEN_PAGE, true)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        v.setOnClickPendingIntent(R.id.rows, PendingIntent.getActivity(context, 2, open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        return v;
    }

    static int countItems(JSONArray groups) {
        int n = 0;
        for (int i = 0; i < groups.length(); i++) {
            JSONObject g = groups.optJSONObject(i);
            JSONArray items = g == null ? null : g.optJSONArray("items");
            n += items == null ? 0 : items.length();
        }
        return n;
    }

    /** Android 12+: every group's header and rows in a scrollable list. */
    static void fillList(Context context, RemoteViews v, JSONArray groups, long now) {
        RemoteViews.RemoteCollectionItems.Builder items = new RemoteViews.RemoteCollectionItems.Builder()
            .setViewTypeCount(2);
        long id = 1;
        for (int i = 0; i < groups.length(); i++) {
            JSONObject g = groups.optJSONObject(i);
            if (g == null) {
                continue;
            }
            List<RemoteViews> rows = rows(context, g.optJSONArray("items"), LIST_PER_GROUP, now, true);
            if (rows.isEmpty()) {
                continue;
            }
            RemoteViews label = section(context, g);
            label.setOnClickFillInIntent(R.id.section, openPageFillIn());
            items.addItem(id++, label);
            for (RemoteViews row : rows) {
                items.addItem(id++, row);
            }
        }
        v.setRemoteAdapter(R.id.list, items.build());
        Intent template = new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        v.setPendingIntentTemplate(R.id.list, PendingIntent.getActivity(context, 3, template,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_MUTABLE));
    }

    static Intent openPageFillIn() {
        return new Intent().putExtra(MainActivity.EXTRA_OPEN_PAGE, true);
    }

    /** Android < 12: as many rows as the widget's height fits; returns how many were left out. */
    static int fillFitted(Context context, RemoteViews v, JSONArray groups, long now, int heightDp,
                          boolean statusVisible) {
        float budget = heightDp > 0 ? heightDp - chromeDp(context, statusVisible) : Float.MAX_VALUE;
        float row = rowDp(context);
        float section = sectionDp(context);
        int hidden = 0;
        boolean firstShown = false;
        for (int i = 0; i < groups.length(); i++) {
            JSONObject g = groups.optJSONObject(i);
            JSONArray items = g == null ? null : g.optJSONArray("items");
            int wanted = Math.min(firstShown ? MAX_OTHER_GROUP : MAX_FIRST_GROUP, items == null ? 0 : items.length());
            if (wanted == 0) {
                continue;
            }
            int fit;
            if (!firstShown) {
                // The first group always shows at least one row; its header only when it fits too.
                boolean header = budget >= section + row;
                if (header) {
                    budget -= section;
                    v.addView(R.id.rows, section(context, g));
                }
                fit = Math.max(1, fit(budget, row, wanted));
                firstShown = true;
            } else {
                fit = budget >= section + row ? fit(budget - section, row, wanted) : 0;
                if (fit > 0) {
                    budget -= section;
                    v.addView(R.id.rows, section(context, g));
                }
            }
            for (RemoteViews r : rows(context, items, fit, now, false)) {
                v.addView(R.id.rows, r);
            }
            budget -= fit * row;
            hidden += wanted - fit;
        }
        return hidden;
    }

    static RemoteViews section(Context context, JSONObject group) {
        RemoteViews label = new RemoteViews(context.getPackageName(), R.layout.widget_section);
        String text = group.optString("label", group.optString("id"));
        label.setTextViewText(R.id.section_text, text);
        label.setContentDescription(R.id.section, text);
        File logo = logoFile(context, group.optString("id"));
        Bitmap bitmap = logo.exists() ? BitmapFactory.decodeFile(logo.getPath()) : null;
        if (bitmap != null) {
            label.setImageViewBitmap(R.id.section_logo, bitmap);
            label.setViewVisibility(R.id.section_logo, View.VISIBLE);
        } else {
            label.setViewVisibility(R.id.section_logo, View.GONE);
        }
        return label;
    }

    static int fit(float budget, float row, int wanted) {
        if (budget == Float.MAX_VALUE) {
            return wanted;
        }
        return (int) Math.max(0, Math.min(wanted, Math.floor(budget / row)));
    }

    static List<RemoteViews> rows(Context context, JSONArray rows, int count, long now, boolean fillIn) {
        List<RemoteViews> out = new ArrayList<>();
        if (rows == null) {
            return out;
        }
        for (int i = 0; i < Math.min(count, rows.length()); i++) {
            JSONObject r = rows.optJSONObject(i);
            if (r == null) {
                continue;
            }
            RemoteViews row = new RemoteViews(context.getPackageName(), R.layout.widget_row);
            row.setTextViewText(R.id.row_name, r.optInt("rank", i + 1) + ". " + r.optString("name")
                + " · " + r.optString("window"));
            String level = r.optString("level");
            row.setTextViewText(R.id.row_verdict, r.optString("verdict"));
            row.setTextColor(R.id.row_verdict, levelColor(level));
            int used = (int) Math.round(r.optDouble("used_percent", 0));
            int elapsed = (int) Math.round(r.optDouble("elapsed_percent", 0));
            // One bar, two colours. Behind pace: blue = used, teal up to the time elapsed (slack).
            // Ahead: blue up to the time elapsed, amber from there to the used share. The stock
            // ProgressBar draws its secondary layer under the primary, so each case needs its own bar.
            // A row without a time share has nothing to compare: blue fill only, no band.
            boolean hasElapsed = !r.isNull("elapsed_percent");
            boolean ahead = hasElapsed && used > elapsed;
            int bar = ahead ? R.id.row_bar_ahead : R.id.row_bar;
            row.setViewVisibility(R.id.row_bar, ahead ? View.GONE : View.VISIBLE);
            row.setViewVisibility(R.id.row_bar_ahead, ahead ? View.VISIBLE : View.GONE);
            row.setProgressBar(bar, 100, clamp(ahead ? elapsed : used), false);
            row.setInt(bar, "setSecondaryProgress", clamp(ahead ? used : hasElapsed ? elapsed : 0));
            StringBuilder detail = new StringBuilder();
            if ("blocked".equals(level) && !r.isNull("released_epoch")) {
                detail.append("used up · frees in ").append(duration(r.optLong("released_epoch") - now));
            } else {
                detail.append("used ").append(used).append('%');
                if (hasElapsed) {
                    detail.append(" · period ").append(elapsed).append('%');
                }
                if (!r.optBoolean("free", false) && r.has("need")) {
                    detail.append(" · ").append(times(r.optDouble("need", 0)));
                }
                if (!r.isNull("resets_epoch")) {
                    detail.append(" · resets ").append(duration(r.optLong("resets_epoch") - now));
                }
            }
            if (!r.isNull("note")) {
                detail.append(" · ").append(r.optString("note").toLowerCase(Locale.ROOT));
            }
            row.setTextViewText(R.id.row_detail, detail.toString());
            if (fillIn) {
                row.setOnClickFillInIntent(R.id.row_root, openPageFillIn());
            }
            out.add(row);
        }
        return out;
    }

    static String times(double need) {
        if (need >= 99) {
            return "99×+";   // the server caps the needed pace at 99
        }
        return String.format(Locale.ROOT, "%.1f", need) + "×";
    }

    static int clamp(int v) {
        return Math.max(0, Math.min(100, v));
    }

    static int levelColor(String level) {
        if ("use".equals(level)) {
            return Color.parseColor("#4FB8A6");
        }
        if ("lean_use".equals(level)) {
            return Color.parseColor("#8FCBC0");
        }
        if ("free".equals(level)) {
            return Color.parseColor("#7894C5");
        }
        if ("save".equals(level)) {
            return Color.parseColor("#F5A623");
        }
        if ("blocked".equals(level)) {
            return Color.parseColor("#F87171");
        }
        return Color.parseColor("#A8B8D9");
    }

    static String duration(long seconds) {
        if (seconds <= 0) {
            return "now";
        }
        long minutes = seconds / 60;
        long days = minutes / 1440;
        long hours = (minutes % 1440) / 60;
        long mins = minutes % 60;
        if (days > 0) {
            return days + "d " + hours + "h";
        }
        if (hours > 0) {
            return hours + "h" + (mins < 10 ? "0" : "") + mins;
        }
        return mins + " min";
    }
}
