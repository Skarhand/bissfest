package de.facgooder.bissfest;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.view.View;
import android.widget.RemoteViews;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Startbildschirm-Widgets für Wasser, Kaffee und Tee, angelehnt an die Dashboard-Karten der Home-Assistant-Integration.
 * Sie sprechen die API /api/v1 mit einem eigenen Token an, den die Web-App über bissfest://widget übergibt.
 */
public abstract class BissfestWidget extends AppWidgetProvider {
    private static final String PREFS = "bissfest";
    private static final String KEY_TOKEN = "widget_token";
    private static final String KEY_STATUS = "widget_status";
    private static final String ACTION_ENTRY = "de.facgooder.bissfest.WIDGET_ENTRY";
    private static final String EXTRA_ENTRY = "entry";
    private static final int CAFFEINE_GUIDE_MG = 400; // wie der Koffein-Ring der Home-Assistant-Karte
    private static final int[] BUTTONS = {R.id.widget_button1, R.id.widget_button2, R.id.widget_button3};
    private static final List<Class<? extends BissfestWidget>> TYPES = new ArrayList<>();

    static {
        TYPES.add(Calories.class);
        TYPES.add(Water.class);
        TYPES.add(Coffee.class);
        TYPES.add(Tea.class);
    }

    public static class Calories extends BissfestWidget {
    }

    public static class Water extends BissfestWidget {
    }

    public static class Coffee extends BissfestWidget {
    }

    public static class Tea extends BissfestWidget {
    }

    private static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** Speichert den Token aus der Web-App und lädt die Widgets neu. */
    static void connect(Context context, String token) {
        prefs(context).edit().putString(KEY_TOKEN, token).remove(KEY_STATUS).apply();
        updateAll(context);
    }

    /** Der Token gehört zu genau einem Server: Beim Serverwechsel wird er verworfen. */
    static void disconnect(Context context) {
        prefs(context).edit().remove(KEY_TOKEN).remove(KEY_STATUS).apply();
        updateAll(context);
    }

    /** Stößt die Aktualisierung aller Widgets an, die auf dem Startbildschirm liegen. */
    static void updateAll(Context context) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        for (Class<? extends BissfestWidget> type : TYPES) {
            int[] ids = manager.getAppWidgetIds(new ComponentName(context, type));
            if (ids.length > 0) {
                context.sendBroadcast(new Intent(AppWidgetManager.ACTION_APPWIDGET_UPDATE, null, context, type)
                        .putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids));
            }
        }
    }

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        runAsync(context, null);
    }

    @Override
    public void onReceive(Context context, Intent intent) {
        if (ACTION_ENTRY.equals(intent.getAction())) {
            runAsync(context, intent.getStringExtra(EXTRA_ENTRY));
            return;
        }
        super.onReceive(context, intent);
    }

    /** Netzwerk darf nicht im Hauptthread laufen, goAsync hält den Empfänger bis zum Ende am Leben. */
    private void runAsync(Context context, String entry) {
        Context app = context.getApplicationContext();
        PendingResult result = goAsync();
        new Thread(() -> {
            try {
                refresh(app, entry);
            } finally {
                result.finish();
            }
        }).start();
    }

    private static void refresh(Context context, String entry) {
        String token = prefs(context).getString(KEY_TOKEN, null);
        String server = SetupActivity.serverUrl(context);
        if (token == null || server == null) {
            renderAll(context, null, "Bitte in Bissfest verbinden: Einstellungen → Extras → Widgets");
            return;
        }
        try {
            JSONObject status = entry == null ? request(server, token, "GET", "/api/v1/status", null)
                    : sendEntry(server, token, entry);
            prefs(context).edit().putString(KEY_STATUS, status.toString()).apply();
            renderAll(context, status, null);
        } catch (Exception e) {
            boolean denied = e instanceof SecurityException;
            renderAll(context, denied ? null : cachedStatus(context),
                    denied ? "Verbindung abgelaufen, bitte in Bissfest neu verbinden" : "Keine Verbindung");
        }
    }

    private static JSONObject cachedStatus(Context context) {
        try {
            String raw = prefs(context).getString(KEY_STATUS, null);
            return raw == null ? null : new JSONObject(raw);
        } catch (Exception e) {
            return null;
        }
    }

    /** entry ist "water:200", "coffee:mug" usw. und stammt nur aus den eigenen Knöpfen. */
    private static JSONObject sendEntry(String server, String token, String entry) throws Exception {
        String[] parts = entry.split(":", 2);
        JSONObject body = new JSONObject();
        if ("water".equals(parts[0])) {
            body.put("ml", Integer.parseInt(parts[1]));
            return request(server, token, "POST", "/api/v1/water", body.toString());
        }
        body.put("kind", parts[0]).put("size", parts[1]);
        return request(server, token, "POST", "/api/v1/drink", body.toString());
    }

    private static JSONObject request(String server, String token, String method, String path, String body) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(server + path).openConnection();
        try {
            conn.setConnectTimeout(4000);
            conn.setReadTimeout(4000);
            conn.setRequestMethod(method);
            conn.setRequestProperty("Authorization", "Bearer " + token);
            if (body != null) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json");
                try (OutputStream out = conn.getOutputStream()) {
                    out.write(body.getBytes(StandardCharsets.UTF_8));
                }
            }
            int code = conn.getResponseCode();
            if (code == 401) {
                throw new SecurityException("Token ungültig");
            }
            if (code != 200) {
                throw new IllegalStateException("HTTP " + code);
            }
            try (InputStream in = conn.getInputStream()) {
                ByteArrayOutputStream data = new ByteArrayOutputStream();
                byte[] buffer = new byte[8192];
                for (int n; (n = in.read(buffer)) > 0; ) {
                    data.write(buffer, 0, n);
                }
                return new JSONObject(new String(data.toByteArray(), StandardCharsets.UTF_8));
            }
        } finally {
            conn.disconnect();
        }
    }

    private static void renderAll(Context context, JSONObject status, String message) {
        AppWidgetManager manager = AppWidgetManager.getInstance(context);
        for (Class<? extends BissfestWidget> type : TYPES) {
            int[] ids = manager.getAppWidgetIds(new ComponentName(context, type));
            if (ids.length == 0) {
                continue;
            }
            manager.updateAppWidget(ids, render(context, type, status, message));
        }
    }

    private static String kindOf(Class<? extends BissfestWidget> type) {
        return type == Calories.class ? "calories" : type == Water.class ? "water" : type == Coffee.class ? "coffee" : "tea";
    }

    private static RemoteViews render(Context context, Class<? extends BissfestWidget> type, JSONObject status, String message) {
        String kind = kindOf(type);
        RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.widget_bissfest);
        views.setOnClickPendingIntent(R.id.widget_root, PendingIntent.getActivity(context, 0,
                new Intent(context, SetupActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));

        String[][] buttons;
        if ("calories".equals(kind)) {
            views.setTextViewText(R.id.widget_title, "🔥 Kalorien");
            buttons = new String[0][];
        } else if ("water".equals(kind)) {
            views.setTextViewText(R.id.widget_title, "💧 Wasser");
            buttons = new String[][]{{"+200 ml", "water:200"}, {"+500 ml", "water:500"}};
        } else if ("coffee".equals(kind)) {
            views.setTextViewText(R.id.widget_title, "☕ Kaffee");
            buttons = new String[][]{{"Tasse", "coffee:cup"}, {"Pott", "coffee:mug"}, {"Espresso", "coffee:espresso"}};
        } else {
            views.setTextViewText(R.id.widget_title, "🍵 Tee");
            buttons = new String[][]{{"Tasse", "tea:cup"}, {"Pott", "tea:mug"}};
        }

        JSONObject drink = status == null ? null : status.optJSONObject(kind);
        boolean hasSwitch = "coffee".equals(kind) || "tea".equals(kind);
        boolean disabled = drink != null && hasSwitch && !drink.optBoolean("enabled");
        boolean showButtons = status != null && !disabled;
        for (int i = 0; i < BUTTONS.length; i++) {
            boolean used = showButtons && i < buttons.length;
            views.setViewVisibility(BUTTONS[i], used ? View.VISIBLE : View.GONE);
            if (used) {
                views.setTextViewText(BUTTONS[i], buttons[i][0]);
                Intent intent = new Intent(context, type).setAction(ACTION_ENTRY).putExtra(EXTRA_ENTRY, buttons[i][1]);
                views.setOnClickPendingIntent(BUTTONS[i], PendingIntent.getBroadcast(context, i, intent,
                        PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
            }
        }

        String value = "";
        String sub = message == null ? "" : message;
        int percent = -1;
        if (disabled) {
            sub = ("coffee".equals(kind) ? "Kaffee" : "Tee") + " ist in Bissfest nicht eingeschaltet";
        } else if ("calories".equals(kind) && drink != null) {
            int eaten = drink.optInt("eaten", 0);
            int remaining = drink.optInt("remaining", 0);
            int budget = eaten + remaining; // Ziel plus mit Sport verdiente Kalorien
            value = String.format(Locale.GERMANY, "%,d kcal", Math.abs(remaining));
            percent = budget > 0 ? Math.round(eaten * 100f / budget) : 100;
            sub = message != null ? message : String.format(Locale.GERMANY, "%s, %,d von %,d gegessen",
                    remaining < 0 ? "zu viel" : "übrig", eaten, budget);
        } else if ("water".equals(kind) && status != null) {
            JSONObject water = status.optJSONObject("water");
            if (water != null) {
                double ml = water.optDouble("ml", 0);
                double goal = water.optDouble("goal_ml", 0);
                value = String.format(Locale.GERMANY, "%.1f / %.1f l", ml / 1000, goal / 1000);
                percent = water.optInt("percent", 0);
                sub = message != null ? message : percent + " % vom Tagesziel";
            }
        } else if (drink != null) {
            int mg = drink.optInt("caffeine_mg", 0);
            value = mg + " mg";
            if ("coffee".equals(kind)) {
                percent = Math.round(mg * 100f / CAFFEINE_GUIDE_MG);
            }
            sub = message != null ? message : drinkSummary(drink);
        }

        views.setTextViewText(R.id.widget_value, value);
        views.setTextViewText(R.id.widget_sub, sub);
        views.setViewVisibility(R.id.widget_bar, percent >= 0 ? View.VISIBLE : View.GONE);
        views.setProgressBar(R.id.widget_bar, 100, Math.min(Math.max(percent, 0), 100), false);
        return views;
    }

    private static String drinkSummary(JSONObject drink) {
        List<String> parts = new ArrayList<>();
        addCount(parts, drink.optInt("cups", 0), "Tasse", "Tassen");
        addCount(parts, drink.optInt("mugs", 0), "Pott", "Pötte");
        addCount(parts, drink.optInt("espressos", 0), "Espresso", "Espressi");
        return parts.isEmpty() ? "Heute noch nichts" : String.join(" · ", parts);
    }

    private static void addCount(List<String> parts, int count, String one, String many) {
        if (count > 0) {
            parts.add(count + " " + (count == 1 ? one : many));
        }
    }
}
