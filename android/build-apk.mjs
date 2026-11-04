// Baut die Bissfest-Android-App als Trusted Web Activity (TWA) mit Bubblewrap.
//
//   cd android && npm install && npm run build
//
// Ergebnis: android/dist/bissfest-<version>.apk (signiert). Die App lädt https://bissfest.facgooder.de –
// Änderungen an der Web-App brauchen daher keine neue APK, nur Name/Icon/Domain/Version.
// Signaturschlüssel liegt bewusst außerhalb des Repos (BISSFEST_SIGNING_DIR, Standard ../../android-signing).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  AndroidSdkTools, Config, ConsoleLog, GradleWrapper, JdkHelper, TwaGenerator, TwaManifest,
} from "@bubblewrap/core";

const VERSION_NAME = "2.5.0";
const VERSION_CODE = 20500; // muss bei jeder neuen APK steigen (2.5.1 -> 20501, 2.6.0 -> 20600 …)
const PACKAGE_ID = "de.facgooder.bissfest";
const WEB_MANIFEST = "https://bissfest.facgooder.de/static/manifest.json";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.join(here, "project");
const distDir = path.join(here, "dist");
const signingDir = process.env.BISSFEST_SIGNING_DIR || path.resolve(here, "../../android-signing");
const keystore = path.join(signingDir, "bissfest-release.keystore");
const password = fs.readFileSync(path.join(signingDir, "passwort.txt"), "utf8").trim();
const jdkPath = process.env.JAVA_HOME || "/usr/lib/jvm/java-17-openjdk-amd64";
const sdkPath = process.env.ANDROID_HOME || path.join(os.homedir(), "Android/Sdk");
const log = new ConsoleLog("bissfest");

// 1) TWA-Beschreibung aus dem Web-Manifest der laufenden App
const twa = await TwaManifest.fromWebManifest(WEB_MANIFEST);
Object.assign(twa, {
  packageId: PACKAGE_ID,
  name: "Bissfest",
  launcherName: "Bissfest",
  appVersionName: VERSION_NAME,
  appVersionCode: VERSION_CODE,
  enableNotifications: true, // Push-Erinnerungen über die App statt über Chrome
  fallbackType: "customtabs",
  minSdkVersion: 24, // Android 7 – Mindestversion der androidbrowserhelper-Bibliothek
  signingKey: { path: keystore, alias: "bissfest" },
  generatorApp: "bissfest/build-apk.mjs",
});

// 2) Android-Projekt frisch erzeugen (wird nicht ins git eingecheckt)
fs.rmSync(projectDir, { recursive: true, force: true });
await new TwaGenerator().createTwaProject(projectDir, twa, log);
await twa.saveToFile(path.join(projectDir, "twa-manifest.json"));

// 2b) Server-Abfrage beim ersten Start einbauen: SetupActivity wird Einstieg der App und öffnet die TWA
// mit der gewählten Adresse. Jeder Bissfest-Server liefert assetlinks.json mit diesem Paket aus.
const javaDir = path.join(projectDir, "app/src/main/java", ...PACKAGE_ID.split("."));
fs.copyFileSync(path.join(here, "overlay/SetupActivity.java"), path.join(javaDir, "SetupActivity.java"));
const patch = (file, pairs) => {
  let text = fs.readFileSync(file, "utf8");
  for (const [from, to] of pairs) {
    if (!text.includes(from)) throw new Error(`Vorlage geändert, nicht gefunden in ${file}: ${from}`);
    text = text.replace(from, to);
  }
  fs.writeFileSync(file, text);
};
patch(path.join(javaDir, "LauncherActivity.java"), [[
  "Uri uri = super.getLaunchingUrl();",
  `Uri uri = super.getLaunchingUrl();

        // Gewählter Bissfest-Server (SetupActivity); direkte Links behalten ihr Ziel
        String server = SetupActivity.serverUrl(this);
        if (server != null && getIntent().getData() == null) {
            return Uri.parse(server + "/");
        }`,
]]);
const launcherFilter = `<intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>`;
patch(path.join(projectDir, "app/src/main/AndroidManifest.xml"), [
  [launcherFilter, ""],
  ['<activity android:name="LauncherActivity"', `<activity android:name=".SetupActivity"
            android:label="@string/launcherName"
            android:theme="@android:style/Theme.Material.Light.NoActionBar"
            android:exported="true">
            ${launcherFilter}
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="bissfest" android:host="setup" />
            </intent-filter>
        </activity>

        <activity android:name="LauncherActivity"`],
  ["<application", '<uses-permission android:name="android.permission.INTERNET" />\n\n    <application'],
]);

// 3) Bauen, ausrichten, signieren
const config = new Config(jdkPath, sdkPath);
// Konstruktor statt AndroidSdkTools.create(): dessen Pfadprüfung kennt nur Bubblewraps eigenes SDK-Layout,
// nicht das von Android Studio (~/Android/Sdk). Benötigt build-tools 36.1.0 (sdkmanager "build-tools;36.1.0").
const sdk = new AndroidSdkTools(process, config, new JdkHelper(process, config), log);
await new GradleWrapper(process, sdk, projectDir).assembleRelease();

const unsigned = path.join(projectDir, "app/build/outputs/apk/release/app-release-unsigned.apk");
const aligned = path.join(projectDir, "app-release-aligned.apk");
const output = path.join(distDir, `bissfest-${VERSION_NAME}.apk`);
fs.mkdirSync(distDir, { recursive: true });
// zipalign/apksigner direkt aufrufen: Bubblewraps Wrapper quotet Pfade mit Leerzeichen nicht ("Abnehm App")
const buildTools = path.join(sdkPath, "build-tools/36.1.0");
const passFile = path.join(signingDir, "passwort.txt");
const env = { ...process.env, JAVA_HOME: jdkPath, PATH: `${jdkPath}/bin:${process.env.PATH}` };
execFileSync(path.join(buildTools, "zipalign"), ["-f", "-p", "4", unsigned, aligned], { stdio: "inherit" });
execFileSync(path.join(buildTools, "apksigner"), ["sign", "--ks", keystore, "--ks-key-alias", "bissfest",
  "--ks-pass", `file:${passFile}`, "--out", output, aligned], { stdio: "inherit", env }); // Schlüssel-PW = Keystore-PW
execFileSync(path.join(buildTools, "apksigner"), ["verify", "--print-certs", output], { stdio: "inherit", env });

// 4) Fingerabdruck für /.well-known/assetlinks.json ausgeben
const certs = execFileSync(path.join(jdkPath, "bin/keytool"),
  ["-list", "-v", "-keystore", keystore, "-alias", "bissfest", "-storepass", password], { encoding: "utf8" });
const sha256 = certs.match(/SHA256:\s*([0-9A-F:]+)/)?.[1];
log.info(`Fertig: ${output}`);
log.info(`Zertifikat SHA-256 (ANDROID_CERT_SHA256): ${sha256}`);
