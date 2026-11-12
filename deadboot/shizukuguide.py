"""The Shizuku + client setup guide, shared by the F-Droid Setup tab and the recipe step.

Setting names inside the client apps are the ones those apps use as of this build. If a client
renames a setting, this file is the one place to change.
"""

from __future__ import annotations

CLIENT_LABELS = {"droidify": "Droid-ify", "obtainium": "Obtainium"}

# Where each client keeps its Shizuku switch.
CLIENT_SETTING = {
    "droidify": "Settings, Installer, choose Shizuku",
    "obtainium": "Settings, turn on \"Use Shizuku or Sui to install\"",
}


def connect_steps(client: str) -> str:
    """One line for the log: how to point the client at Shizuku."""
    name = CLIENT_LABELS.get(client, client)
    return (f"open {name}, {CLIENT_SETTING.get(client, 'Settings, choose Shizuku as the installer')}, then tap "
            f"Allow when {name} asks for Shizuku access.")


INTRO = (
    "<b>What this sets up.</b> Shizuku is a small helper app that lets other apps do things normally only adb "
    "can do, like installing apps without a confirmation prompt for each one. An app store client then uses "
    "Shizuku to install and update apps on the phone by itself, no computer needed. Droid-ify installs apps "
    "from F-Droid. Obtainium installs apps straight from their GitHub, GitLab or Codeberg releases."
    "<br><br>"
    "<b>You do not need Wireless debugging or pairing for setup.</b> Shizuku's service has to be started by "
    "adb, and deADBoot does that over the USB cable in step 3. Wireless debugging is only for restarting "
    "Shizuku after a reboot when you are away from the computer (bottom of this page).")

STEP_HELP = {
    "shizuku": "Phones with the Play Store: Shizuku's Play listing opens on the phone. Tap Install there; "
               "deADBoot waits and says when it is installed. Phones without Play get the latest GitHub release.",
    "client": "Installs the client picked above. If the client is already on the phone (its GitHub build or its "
              "F-Droid build), it is left alone, so you never end up with two copies.",
    "start": "Starts Shizuku over the cable. Then press Open Shizuku: the top card should say Shizuku is "
             "running. Shizuku also shows Start via Wireless debugging and Pairing buttons. Ignore them; they "
             "are the cable-free way of doing this same step.",
    "status": "Shows what is installed (and which build), whether Shizuku is running, and whether Play is present.",
}

CONNECT = (
    "<b>4. Connect the client to Shizuku</b> (on the phone; deADBoot can't press these for you). "
    "Press Open client, then:<ul>"
    "<li><b>Droid-ify:</b> " + CLIENT_SETTING["droidify"] + ".</li>"
    "<li><b>Obtainium:</b> " + CLIENT_SETTING["obtainium"] + ".</li></ul>"
    "The client then asks <i>Allow (client) to access Shizuku?</i> Tap Allow. That is the whole connection. "
    "Apps only appear in Shizuku's list of authorized apps after they ask, so an empty list before this step "
    "is normal. You never add apps from inside Shizuku itself. If the client says Shizuku isn't running, "
    "press 3. Start Shizuku again.")

USING = (
    "<b>5. Install apps</b> (on the phone)<ul>"
    "<li><b>Droid-ify:</b> search or browse, tap Install. With Shizuku there is no \"Do you want to install "
    "this app?\" prompt. Updates show in the Updates tab.</li>"
    "<li><b>Obtainium:</b> tap Add App, paste the app's project page (a GitHub link like "
    "https://github.com/owner/app works for most), tap Add, then Install. Obtainium checks for new releases "
    "on a schedule. With Shizuku, the updates install without a prompt.</li></ul>")

REBOOTS = (
    "<b>After a reboot</b><ul>"
    "<li>Without root, Shizuku's service stops at every reboot. The client keeps working for browsing, but "
    "installs fail until Shizuku runs again. Plug in and press 3. Start Shizuku, or use the cable-free restart "
    "below.</li>"
    "<li>Rooted phones: pick Root mode above. Shizuku then starts itself at boot.</li>"
    "<li>Wireless debugging needs Android 11 or newer. Older phones restart Shizuku over the cable only.</li>"
    "</ul>")

WIRELESS = (
    "<b>Optional: cable-free restarts (Android 11+)</b><br>"
    "One-time pairing:<ol>"
    "<li>Connect the phone to a Wi-Fi network you trust.</li>"
    "<li>Settings, System, Developer options, turn on Wireless debugging.</li>"
    "<li>Open Shizuku and tap Pairing (under Start via Wireless debugging). Shizuku posts a notification "
    "that it is looking for the pairing service. Allow notifications for Shizuku if asked.</li>"
    "<li>Go back to Developer options, Wireless debugging, tap Pair device with pairing code. A 6-digit code "
    "shows, and Shizuku's notification changes to offer a code field. Pull down the notification shade, "
    "type the code there and send it.</li>"
    "<li>Open Shizuku again and tap Start under Start via Wireless debugging.</li></ol>"
    "After each reboot: turn Wireless debugging back on if it is off (Android turns it off when Wi-Fi drops), "
    "open Shizuku, tap Start under Start via Wireless debugging. No re-pairing.")
