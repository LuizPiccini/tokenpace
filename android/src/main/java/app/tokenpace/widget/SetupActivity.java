package app.tokenpace.widget;

import android.app.Activity;
import android.appwidget.AppWidgetManager;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

/**
 * Server address and optional token. Opens from the launcher and as the widget's
 * configuration screen; "Save and test" stores the settings and fetches once.
 */
public class SetupActivity extends Activity {
    private int widgetId = AppWidgetManager.INVALID_APPWIDGET_ID;
    private EditText url;
    private EditText token;
    private TextView status;
    private Button save;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_setup);
        Bundle extras = getIntent().getExtras();
        if (extras != null) {
            widgetId = extras.getInt(AppWidgetManager.EXTRA_APPWIDGET_ID, AppWidgetManager.INVALID_APPWIDGET_ID);
        }
        // Backing out of the configuration screen cancels adding the widget.
        setResult(RESULT_CANCELED, result());
        url = findViewById(R.id.setup_url);
        token = findViewById(R.id.setup_token);
        status = findViewById(R.id.setup_status);
        save = findViewById(R.id.setup_save);
        Context app = getApplicationContext();
        String current = UsageWidget.serverUrl(app);
        if (current != null) {
            url.setText(current);
            setResult(RESULT_OK, result());
        }
        String currentToken = UsageWidget.token(app);
        if (currentToken != null) {
            token.setText(currentToken);
        }
        save.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                saveAndTest();
            }
        });
        findViewById(R.id.setup_close).setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
    }

    private Intent result() {
        return new Intent().putExtra(AppWidgetManager.EXTRA_APPWIDGET_ID, widgetId);
    }

    void saveAndTest() {
        final String normalized = UsageWidget.normalizeUrl(url.getText().toString());
        if (normalized == null) {
            status.setText("Enter the server address, for example http://my-server:8787");
            return;
        }
        url.setText(normalized);
        final Context app = getApplicationContext();
        UsageWidget.prefs(app).edit()
            .putString("server_url", normalized)
            .putString("token", token.getText().toString().trim())
            .remove("json").remove("last_error").commit();
        setResult(RESULT_OK, result());
        status.setText("Testing…");
        save.setEnabled(false);
        new Thread(new Runnable() {
            @Override
            public void run() {
                final boolean ok = UsageWidget.refreshBlocking(app);
                final String error = UsageWidget.prefs(app).getString("last_error", "");
                runOnUiThread(new Runnable() {
                    @Override
                    public void run() {
                        save.setEnabled(true);
                        if (ok) {
                            status.setText("Connected. The widget is ready.");
                            RefreshJob.schedulePeriodic(app);
                            finish();
                        } else {
                            status.setText("Saved, but the test failed: " + error);
                        }
                    }
                });
            }
        }, "tokenpace-setup").start();
    }
}
