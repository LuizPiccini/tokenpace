package app.tokenpace.widget;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;

/**
 * Invisible screen behind every tap on the widget. Fetching while an activity is in the
 * foreground is not subject to the phone's background network limits. The header
 * refreshes; a row also opens the full page. Without a server it opens the setup screen.
 */
public class MainActivity extends Activity {
    static final String EXTRA_OPEN_PAGE = "open_page";
    static final long MAX_WAIT_MS = 7000;

    private boolean done;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        final Context app = getApplicationContext();
        if (UsageWidget.serverUrl(app) == null) {
            startActivity(new Intent(app, SetupActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            done = true;
            finish();
            return;
        }
        final boolean openPage = getIntent().getBooleanExtra(EXTRA_OPEN_PAGE, false);
        try {
            RefreshJob.schedulePeriodic(app);
        } catch (RuntimeException ignored) {
            // the foreground fetch below still runs
        }
        UsageWidget.renderAll(app, null, true);
        final Handler main = new Handler(Looper.getMainLooper());
        final Runnable finish = new Runnable() {
            @Override
            public void run() {
                close(openPage);
            }
        };
        main.postDelayed(finish, MAX_WAIT_MS);   // never hold the screen longer than this
        new Thread(new Runnable() {
            @Override
            public void run() {
                UsageWidget.refreshBlocking(app);
                main.post(finish);
            }
        }, "tokenpace-tap").start();
    }

    private void close(boolean openPage) {
        if (done) {
            return;
        }
        done = true;
        String page = UsageWidget.serverUrl(getApplicationContext());
        if (openPage && page != null) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(page + "/"))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            } catch (Exception ignored) {
                // no browser: the widget still refreshed
            }
        }
        finish();
        overridePendingTransition(0, 0);
    }
}
