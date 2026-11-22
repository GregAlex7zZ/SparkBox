# Mascot

A small animated lab assistant sits to the right of the **SparkBox** name in the
sidebar header. It is decorative. It watches what the application shows and never
takes part in it.

## Behaviour

| What happens on screen | Reaction |
|---|---|
| Nothing in particular | Works at the desk (typing, sipping coffee) |
| Import, processing, calculation, export or saved image succeeds | Thumbs up (about 65 %) or lifts the battery (about 35 %) |
| Something fails: a failed status message, an export or save error, a GITT/ICI error, a warning or error dialog | Explosion |
| Every 20 animations (desk-work loops and reactions alike) | Goes to the bathroom |
| Sidebar collapsed | Hidden, and its timer stops |

Right-click the mascot to preview each animation.

## Removing the mascot

- Right-click the mascot and choose **Remove Mascot**, or turn off **Show Mascot** in Settings › Appearance.
- Turn **Show Mascot** back on in Settings › Appearance to bring it back.
- The choice is saved (`mascot/enabled` in the app's preferences), so it holds for the next launches.
- A removed mascot is hidden, its timer stops and it reacts to nothing, so no animation is queued for later.

## Design

- `app/mascot/director.py`: Qt-free frame sequencing. Every event starts and ends on the
  same neutral pose, and the desk loop is left only from its resting frame, so frames
  never jump.
- `app/mascot/widget.py`: the fixed-size `Mascot` widget. It
  loads frames from `assets/mascot` and scales them for HiDPI screens.
- `app/mascot/observer.py`: `MascotObserver`. It connects read-only to existing public
  signals: the status bar, controller notices, `runFinished`, `exportFinished`,
  `exportError`, `savedImage`, `saveFailed`, GITT/ICI `changed` and window focus (to
  detect warning dialogs). Several notifications about the same outcome within 3 s count
  as one reaction. A success is not celebrated right after a failure.
- `app/mascot/placement.py`: `BrandSlot` sizes the mascot to the room between the end of
  the brand text and the sidebar column's edge (about 61 × 51 px with Inter, at most the
  56 px row height). It re-measures on theme or font changes and never covers the text.
- `main_window.py` has a single integration point: the brand row and the observer.
  Removing them restores the previous behaviour exactly.

## Limits

- Free-text messages are classified by wording (`classify_message`). New wording that is
  not recognised is ignored. It is never reported as a failure.
- The art comes from the original drawings and has no new intermediate poses.
- Frames and animations are listed in `assets/mascot/manifest.json`: the scene size (`world`), the frames with
  their file and offset (`frames`) and the animations with their frame durations in ms (`clips`).

## Origin

The mascot frames in `assets/mascot/` were generated with AI image tools and adapted for SparkBox (see
`THIRD_PARTY_NOTICES.md`).
