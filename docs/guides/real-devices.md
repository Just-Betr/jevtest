# Real phones

Emulators and simulators are convenient; a real phone is the real thing. jevtest drives both the same way: only `device:` changes.

## Android phone

1. On the phone: **Settings > About phone**, tap **Build number** seven times, then **Settings > System > Developer options > USB debugging** on.
2. Plug it in and tap **Allow** on the phone.
3. Find its name: `adb devices` lists the serial; its model name (`Pixel 4a`) works too.
4. `device: { android: Pixel 4a }`, and keep the phone unlocked while tests run.

A locked or sleeping phone fails the test with a message to unlock it.

## iPhone

Apple only runs code on an iPhone when it is signed by a developer account the phone trusts, so each person does this once:

1. **Xcode > Settings > Accounts > + > Apple Account.** A free Apple ID works; a paid developer account avoids profiles that expire after 7 days.
2. **On the iPhone:** Settings > Privacy & Security > **Developer Mode** on (the phone restarts), then Settings > Developer > **Enable UI Automation**.
3. **Plug it in**, tap **Trust**, and keep it unlocked while tests run.
4. **Build your app for the device, signed with your team.** An `.app` or an `.ipa` containing one. Use Release, or any build that runs without a debugger attached.
5. In the test file:

```yaml
app:
  ios: build/Release-iphoneos/MyApp.app
device:
  ios: My iPhone                # Settings > General > About > Name, or its UDID
```

jevtest signs its own agent with **the same team that signed your app**, read from the app's provisioning profile, so there's nothing to configure. That team must be signed into Xcode; if it isn't, the error says which team to add.

**First run:** jevtest builds its agent, signs it with your team and registers the phone with that team (about a minute). Later runs reuse it. jevtest reaches the phone through the USB connection Xcode already keeps to it, so nothing else needs installing.

**The phone may ask for its passcode.** When jevtest's agent starts, iOS can show an XCTest prompt asking for the passcode to allow UI automation. Enter it on the phone; the run waits for it. Apple doesn't let this be skipped, so be near the phone when a run starts.

**A touch on a permission prompt takes a minute?** Restart the iPhone. The agent log in `~/.cache/jevtest/` shows `App animations complete notification not received` when this happens.

**What's different on an iPhone:** everything works except `grant:` (iOS can't pre-grant permissions: have the test tap the prompt, e.g. `do: Allow camera access`) and `network:`.

## Leaving the phone as it was

jevtest never changes a device's settings to make testing easier: animations stay on, nothing is disabled. The only changes are the ones a step asks for, and those go back at the end of the run:

| Step | Put back at the end |
|---|---|
| `rotate:` | Android: auto-rotate and orientation. iOS: orientation. |
| `dark_mode:` | Android and iOS: the appearance it had. |
| `network:` | Android: Wi-Fi and mobile data as they were. |
| `autofill:` | Android: the autofill service it had (Google's, say), or none. |
| `location:` | iOS simulator and iPhone: the simulated location is cleared, so the device uses its actual location again. The Android emulator keeps the last one set: it can't report where it was, so there's nothing to put back. |
