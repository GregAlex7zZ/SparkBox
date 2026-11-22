# Settings and profiles

Open **Settings → Settings…** (`Ctrl+,`). The window follows the Apple Human Interface Guidelines
for settings windows: a sidebar shows every pane at once (Appearance, Units, New Dataset Defaults,
Advanced Settings) and the **profile card** at the top of the sidebar names the profile in use and
opens the Profiles pane. Every change takes effect immediately; **Done** only closes the window.
An invalid number is highlighted and never saved: the last valid value stays in use until it is
fixed.

## Advanced Settings

Choose Data Elaboration, Data Explorer, Capacity, DCIR, dQ/dV, or Comparison. The dialog hosts the
same live panels used at the right of the main window. Editing either location changes the same
state. Controls scroll vertically in smaller windows. Dataset-specific sections require an open
dataset, except DCIR starting settings and Comparison preferences. Without a file, those sections show a
"No file open" state with an **Open File** menu (Auto Mapping… / Manual Mapping…) instead of a disabled
panel, which previously could be neither scrolled nor edited.
Data Import has no Advanced panel.

Changes take effect immediately and do not start a scientific calculation. Existing
Analyze/Reanalyze controls keep their usual meaning.
DCIR detection parameters and measurement times can also be edited in this dialog. Existing
Cycle Correction restrictions remain enforced.

DCIR remains editable without an open dataset: valid values are saved locally and become the
starting settings for future imports. They can be saved into a favourite profile. The immutable
Default profile retains its original 600 s rests; applying Default restores those values.

With Cycle Correction active or previously removed, DCIR fields remain editable as pending inputs.
New Detection and automatic measurements stay blocked until the original data are processed again.
Editing these fields does not alter the already-published DCIR or correction result. Running
operations temporarily disable editing and display an explicit reason.

## Profiles

- **Default** is read-only and restores the application baseline, including DCIR Rest Before =
  600 s and Rest After = 600 s.
- A fresh install offers only Default; profiles saved by earlier versions are kept.
- The pane opens with a three-step "How it works" box, then the profile list. The profile in use is
  bold and marked "in use"; the sidebar card shows the same name.
- **Create New Profile…** (above the list) saves the current settings as a new profile; a name such
  as "My Profile 1" is suggested.
- **Delete…** deletes the selected profile after confirmation. Deleting the profile in use keeps its
  values as the starting settings for new datasets and marks Default as in use.
- **Rename…** changes the selected profile's name. Names must be unique; Default is reserved.
- **Update with Current Settings** replaces the selected profile with the active dataset's
  parameters and plot settings, Comparison preferences, appearance, app units, and dataset-unit
  overrides, after confirmation.
- **Use Profile** (or a double-click) loads the selected profile; selecting it alone does not.

Profiles are stored locally through QSettings and persist after restarting the app. Applying a
profile changes the active dataset and Comparison and selects the starting settings for future
imports. Other open datasets keep their section preferences. Global app units and theme still
affect all applicable views, as in the existing Settings dialog.

Profiles contain parameters, axis assignments/ranges, filters, colours, line styles, legends,
tracking and reconstruction display preferences. Sample mass/area, dataset/source paths, dataset
selection, dataset-specific Comparison colours, DCIR decisions, source generations, published results,
and processing state are excluded.
Dataset-specific sample metadata remain editable in Data Elaboration.

Applying a profile never starts Process, Analyze, Detection, Apply & Calculate, or Cycle Correction.
Scientific publications remain on disk. Changed parameters use the existing pending/Outdated
indicators until the relevant analysis is explicitly run. Applying is blocked while processing,
rendering, importing or exporting; a profile that changes DCIR detection/measurement inputs is
rejected after Cycle Correction. No preference edits mutate gregorithm.

## Verification

`tests/test_settings_profiles.py` covers independent/default profiles, naming, restart persistence,
corrupt input, plot ranges and tuple colour keys, preservation of publications and sample metadata,
the six shared panels, repeated close/reopen, modal Units routing, busy/correction guards, the
sidebar profile card, immediate commits (invalid defaults never saved), and new/use/delete flows.
