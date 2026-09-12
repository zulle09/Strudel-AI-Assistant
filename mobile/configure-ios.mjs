/* Apply the iOS-specific settings Capacitor's template does not include.
 *
 * Run this once, after `npx cap add ios`, and again if you ever delete and
 * regenerate the ios/ directory. It is idempotent: each edit checks for its
 * own marker first, so running it twice changes nothing.
 *
 * Three things need doing, and none of them are optional:
 *
 * 1. NSMicrophoneUsageDescription. Without it iOS kills the app the instant
 *    getUserMedia is called - not a denied permission, a crash.
 *
 * 2. The audio session category. A WKWebView defaults to a session that the
 *    ring/silent switch mutes, so Strudel plays nothing on a phone that is on
 *    silent, which is most phones. It also defaults to routing playAndRecord
 *    output to the earpiece, so after the first recording the music would come
 *    out of the phone-call speaker.
 *
 * 3. Local networking. The backend runs on your Mac over plain http, and App
 *    Transport Security blocks that by default.
 *
 * If an anchor cannot be found - because a future Capacitor template differs
 * from the one this was written against - the script prints the snippet to
 * apply by hand rather than corrupting the file.
 */

import { readFile, writeFile, access } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const APP_DIR = resolve(here, "ios", "App", "App");
const PLIST = resolve(APP_DIR, "Info.plist");
const DELEGATE = resolve(APP_DIR, "AppDelegate.swift");

const PLIST_KEYS = `	<key>NSMicrophoneUsageDescription</key>
	<string>Strudel Assistant records the melody you hum so it can transcribe it into a pattern.</string>
	<key>NSLocalNetworkUsageDescription</key>
	<string>Strudel Assistant talks to the assistant backend running on your computer.</string>
	<key>NSAppTransportSecurity</key>
	<dict>
		<!-- Development only. Lets the app reach a backend on your Mac over
		     plain http. Remove this before shipping anything, and serve the
		     backend over https instead. -->
		<key>NSAllowsLocalNetworking</key>
		<true/>
	</dict>
`;

const SESSION_SNIPPET = `        // Web Audio in a WKWebView follows the app's audio session. Without
        // this, Strudel is silent whenever the ring/silent switch is on, and
        // once the microphone has been used the output moves to the earpiece.
        // .playAndRecord covers both halves of this app; .defaultToSpeaker
        // keeps the music on the loudspeaker after a recording.
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(
                .playAndRecord,
                mode: .default,
                options: [.defaultToSpeaker, .allowBluetooth, .mixWithOthers]
            )
            try session.setActive(true)
        } catch {
            print("[StrudelAssistant] audio session setup failed: \\(error)")
        }
`;

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

function manual(label, snippet, file) {
  console.error(`\n[configure-ios] Could not patch ${file} automatically.`);
  console.error(`[configure-ios] Add this ${label} by hand:\n`);
  console.error(snippet);
  return false;
}

async function patchPlist() {
  let xml = await readFile(PLIST, "utf8");
  if (xml.includes("NSMicrophoneUsageDescription")) {
    console.log("[configure-ios] Info.plist already configured.");
    return true;
  }
  // The template's Info.plist ends with the dict close followed by the plist
  // close; inserting before it keeps the keys inside the root dictionary.
  const anchor = "</dict>\n</plist>";
  if (!xml.includes(anchor)) return manual("keys", PLIST_KEYS, "Info.plist");
  xml = xml.replace(anchor, PLIST_KEYS + anchor);
  await writeFile(PLIST, xml);
  console.log("[configure-ios] Info.plist: added microphone and networking keys.");
  return true;
}

async function patchDelegate() {
  let swift = await readFile(DELEGATE, "utf8");
  if (swift.includes("AVAudioSession.sharedInstance()")) {
    console.log("[configure-ios] AppDelegate.swift already configured.");
    return true;
  }

  const importAnchor = "import Capacitor";
  const bodyAnchor = "// Override point for customization after application launch.";
  if (!swift.includes(importAnchor) || !swift.includes(bodyAnchor)) {
    return manual("audio session setup", SESSION_SNIPPET, "AppDelegate.swift");
  }

  swift = swift.replace(importAnchor, `${importAnchor}\nimport AVFoundation`);
  swift = swift.replace(bodyAnchor, `${bodyAnchor}\n${SESSION_SNIPPET}`);
  await writeFile(DELEGATE, swift);
  console.log("[configure-ios] AppDelegate.swift: configured the audio session.");
  return true;
}

if (!(await exists(APP_DIR))) {
  console.error(
    `[configure-ios] ${APP_DIR} does not exist.\n` +
      "[configure-ios] Run `npx cap add ios` first (needs Xcode and CocoaPods)."
  );
  process.exit(1);
}

const ok = (await patchPlist()) && (await patchDelegate());
process.exit(ok ? 0 : 1);
