package de.facgooder.bissfest;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ShortcutInfo;
import android.content.pm.ShortcutManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.Icon;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Collections;
import java.util.Locale;

/**
 * Einstieg der App: Beim ersten Start wird die Adresse des Bissfest-Servers abgefragt und geprüft,
 * danach geht es direkt in die Trusted Web Activity (LauncherActivity). Mit bissfest://setup
 * (Knopf in den App-Einstellungen, App-Shortcut) lässt sich der Server später wechseln.
 */
public class SetupActivity extends Activity {
    private static final String PREFS = "bissfest";
    private static final String KEY_SERVER = "server_url";
    private static final String DEFAULT_SERVER = "https://bissfest.facgooder.de";

    private EditText input;
    private TextView message;
    private Button connect;

    static String serverUrl(Context context) {
        return context.getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_SERVER, null);
    }

    /** Material-You-Farben als #-Teil der Startadresse (Hell, dann Dunkel), vor Android 12 leer. */
    static String systemColorsFragment(Context context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) {
            return "";
        }
        int[] tones = {
                android.R.color.system_accent1_600, android.R.color.system_accent1_100, android.R.color.system_accent1_0,
                android.R.color.system_accent1_200, android.R.color.system_accent1_700, android.R.color.system_accent1_800,
        };
        StringBuilder colors = new StringBuilder("#syscolors=");
        for (int i = 0; i < tones.length; i++) {
            colors.append(i == 0 ? "" : ".")
                    .append(String.format(Locale.ROOT, "%06x", context.getColor(tones[i]) & 0xFFFFFF));
        }
        return colors.toString();
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        String saved = serverUrl(this);
        boolean change = getIntent() != null && "bissfest".equals(getIntent().getScheme());
        if (saved != null && !change) {
            openApp();
            return;
        }
        buildUi(saved != null ? saved : DEFAULT_SERVER);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void buildUi(String server) {
        int green = Color.parseColor("#2E8B5F");
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        box.setPadding(dp(28), dp(56), dp(28), dp(28));

        ImageView logo = new ImageView(this);
        logo.setImageResource(getApplicationInfo().icon);
        box.addView(logo, new LinearLayout.LayoutParams(dp(88), dp(88)));

        TextView title = new TextView(this);
        title.setText("Bissfest");
        title.setTextSize(28);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(Color.parseColor("#17543A"));
        title.setPadding(0, dp(12), 0, dp(4));
        box.addView(title);

        TextView hint = new TextView(this);
        hint.setText("Mit welchem Bissfest-Server möchtest du dich verbinden? Der offizielle Server ist bereits eingetragen.");
        hint.setTextColor(Color.parseColor("#4F4F4A"));
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(0, dp(8), 0, dp(20));
        box.addView(hint);

        input = new EditText(this);
        input.setText(server);
        input.setSingleLine(true);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setImeOptions(EditorInfo.IME_ACTION_GO);
        input.setOnEditorActionListener((v, actionId, event) -> {
            check();
            return true;
        });
        box.addView(input, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        message = new TextView(this);
        message.setTextColor(Color.parseColor("#C94A2E"));
        message.setGravity(Gravity.CENTER);
        message.setPadding(0, dp(10), 0, dp(10));
        box.addView(message);

        connect = new Button(this);
        connect.setText("Verbinden");
        connect.setAllCaps(false);
        connect.setTextColor(Color.WHITE);
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(green);
        shape.setCornerRadius(dp(12));
        connect.setBackground(shape);
        connect.setOnClickListener((View v) -> check());
        box.addView(connect, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(52)));

        ScrollView root = new ScrollView(this);
        root.setFillViewport(true);
        root.setBackgroundColor(Color.parseColor("#FBF7EF"));
        root.addView(box);
        setContentView(root);
    }

    /** Eingabe vereinheitlichen; null, wenn es keine gültige HTTPS-Adresse ist. */
    private static String normalize(String raw) {
        String url = raw.trim();
        while (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        if (url.isEmpty() || url.toLowerCase().startsWith("http://")) {
            return null;
        }
        if (!url.toLowerCase().startsWith("https://")) {
            url = "https://" + url;
        }
        Uri uri = Uri.parse(url);
        return uri.getHost() == null || uri.getHost().isEmpty() ? null : url;
    }

    private void check() {
        final String url = normalize(input.getText().toString());
        if (url == null) {
            message.setText("Bitte eine HTTPS-Adresse eingeben, z. B. https://bissfest.example.de");
            return;
        }
        message.setText("");
        connect.setEnabled(false);
        connect.setText("Prüfe Server …");
        new Thread(() -> {
            String version = fetchVersion(url);
            runOnUiThread(() -> {
                connect.setEnabled(true);
                connect.setText("Verbinden");
                if (version == null) {
                    message.setText("Unter dieser Adresse wurde kein Bissfest-Server gefunden.");
                    return;
                }
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_SERVER, url).apply();
                openApp();
            });
        }).start();
    }

    /** Fragt /api/config ab; liefert die Server-Version oder null, wenn dort kein Bissfest läuft. */
    private static String fetchVersion(String server) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(server + "/api/config").openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            if (conn.getResponseCode() != 200) {
                return null;
            }
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), "UTF-8"))) {
                String line;
                while ((line = reader.readLine()) != null) {
                    body.append(line);
                }
            }
            return new JSONObject(body.toString()).optString("version", null);
        } catch (Exception e) {
            return null;
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private void addShortcut() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.N_MR1) {
            return;
        }
        ShortcutManager manager = getSystemService(ShortcutManager.class);
        if (manager == null) {
            return;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW, Uri.parse("bissfest://setup"), this, SetupActivity.class);
        manager.setDynamicShortcuts(Collections.singletonList(new ShortcutInfo.Builder(this, "server")
                .setShortLabel("Server wechseln")
                .setIcon(Icon.createWithResource(this, getApplicationInfo().icon))
                .setIntent(intent)
                .build()));
    }

    private void openApp() {
        addShortcut();
        Intent intent = new Intent(this, LauncherActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK);
        startActivity(intent);
        finish();
    }
}
