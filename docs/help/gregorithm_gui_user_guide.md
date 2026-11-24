# SparkBox User Guide

Step by step battery data analysis with SparkBox

Edition 16 November 2026. This guide describes the current SparkBox interface and its installed scientific engine, gregorithm 0.1.0. It is intended for battery researchers using the open application. Start with the source-file checks below, then follow the import and analysis steps. Read the companion Scientific Methods document (**Help → Scientific Methods**) when you need the mathematical definition behind a result.

## Quick start

1. Open a file: **File → Open File → Auto Mapping…** (`Ctrl+O`), the **+** after the dataset tabs, or **Open File** in Data.
2. In **Data**, check the valid-row count, discarded rows and duration.
3. In **Data Elaboration**, check Cycle Order and the thresholds, then click **Process**.
4. Look at the signals in **Data Explorer**.
5. Run the analyses you need: **Capacity → Analyze**; **DCIR → Detect**, review the pulses, then **Apply & Calculate**; **GITT / ICI**: complete Setup and **Proceed**, then review IR Drop, OCV and Pulse Fitting; **dQ/dV**: enter the cycles, then **Analyze**.
6. Overlay two or more files in **Comparison**. Save figures with **Save Image** and tables with **Export Table…**.

Settings and profiles are under **Settings → Settings…**. `F1` opens this guide.

## 1 Before importing

Prepare a CSV, TXT or Parquet file containing elapsed time, current and voltage. CSV and TXT files must have column names in their first row. The current GUI does not support headerless text files. Additional columns can be present, but the import uses only the three mapped quantities.

Confirm the instrument convention before importing. Positive current must mean charge; negative current must mean discharge. The application does not automatically reverse current. If your instrument uses the opposite convention, prepare a separate converted source file and keep the original.

The default display units are hours, milliamperes, volts, milliampere-hours, milliwatt-hours and ohms. The scientific engine stores seconds, amperes, volts, ampere-hours, watt-hours and ohms. Changing display units does not change stored measurements or recalculate an analysis.

### 1.1 Understand the window

The left sidebar selects Data, Data Elaboration, Data Explorer, Capacity, DCIR, GITT / ICI, dQ/dV or Comparison. Sections become available when their required input exists. An unavailable section is a dependency message, not evidence that the file is unusable.

Dataset tabs run across the top in one horizontally scrolling row. Click a tab to make that dataset active. A tab is as wide as its file name; only very long names are shortened, and you can hover to read the full name. Close a tab with its internal × button. Each dataset keeps its section and local settings during the session.

Only the active dataset works. The other open datasets are on standby: their files and results stay open, but a calculation started on one of them pauses when you select another tab and resumes when you select it again, so one dataset never slows down the one you are working on. The Comparison section lets every dataset run. Going back to a dataset, or to a section you have not opened for it yet, can therefore take a moment to load. Comparison is opened from the sidebar and has no dataset tab.

The compact bottom sidebar control collapses or expands navigation. Use **Advanced Settings** at the right of the dataset tabs to open the current section's settings panel. The **+** after the last tab opens another file. Expand a panel group using its chevron and scroll vertically to reach additional fields.

The top menus are File, Settings, About and Help. Application settings are under **Settings → Settings…**. **Help** opens this guide (`F1`) and the Scientific Methods.

### 1.2 Follow the dependency order

1. Import a file in Data.
2. Run Data Elaboration to identify cycles and steps and calculate cumulative quantities.
3. Inspect Data Explorer and run Capacity as required.
4. Detect and review DCIR pulses, calculate the measurements with Apply & Calculate, and optionally correct cycles.
5. For a GITT or ICI experiment, analyse one cycle or half-cycle in GITT / ICI.
6. Run dQ/dV for explicitly selected cycles.
7. Use Comparison after at least two datasets have the required results.

For a pulse-containing experiment, decide whether DCIR exclusions and Cycle Correction are needed before making final dQ/dV figures. A later change to an input result can leave an earlier analysis Outdated.

## 2 Import a file and inspect Data

### 2.1 Choose Auto Mapping

1. In Data, click **Open File**. The whole button opens a menu.
2. Choose **Auto Mapping…**. The same menu is under File → Open File and on the **+** next to the dataset tabs (`Ctrl+O`).
3. Select the source file.
4. Wait for the real import progress. The GUI asks for Manual Mapping if it cannot identify an unambiguous assignment.
5. After success, verify the dataset name, valid-row count, discarded-row count and duration.

The recognized header combinations include `time_s,current_a,voltage_v` and `time_s,current_mA,voltage_V`. Auto Mapping is recognition of supported names and units; it is not a scientific inference of unknown units.

### 2.2 Choose Manual Mapping

1. Choose **Manual Mapping…** from Open File, File → Open File or the **+** next to the tabs.
2. Inspect the column names and eight-row preview.
3. Assign exactly one column to **Time**, one to **Current** and one to **Voltage**. Set unrelated columns to **Not used**.
4. Select the actual source unit for each assigned column.
5. For numeric elapsed time, select its numerical unit. For durations such as `12:34:56.789`, select **hh:mm:ss.fff**. Hours may exceed 23; this is elapsed duration, not a calendar time.
6. Leave **First row contains column names** checked. It is intentionally disabled because headerless files are not supported.
7. Confirm the mapping and start the import.

| Source quantity | Supported source units or format |
|---|---|
| Time | s, ms, min, h, or hh:mm:ss.fff |
| Current | A, mA |
| Voltage | V, mV |

The mapping dialog reports the detected delimiter and encoding; it is not a general text-format editor. The application reads UTF-8 text, detects a delimiter among comma, semicolon, tab and vertical bar, and expects a period as decimal separator. If the preview is parsed incorrectly, prepare a separate compatible text file rather than choosing false column units.

### 2.3 Read the import report

Open **Import Report** to see the original columns, used roles and source units. Hover over the discarded-row box to read the reasons reported by the importer.

The importer first removes rows with invalid current or voltage. Among the surviving rows, invalid time aborts import, repeated timestamps retain the first surviving occurrence, and decreasing time aborts import. It does not sort a faulty time sequence. At least two rows must survive. The original file is not edited.

If import fails, read the exact error and correct the source or mapping. Cancel stops the operation cooperatively; a cancelled import must not be treated as a usable dataset. No elaboration starts automatically after a successful import.

### 2.4 Navigate and copy the table

1. Scroll the read-only Data table. It fetches blocks rather than loading the whole file into the table widget.
2. Enter a **Row** and press its Go button to jump to that retained row. Visible row numbers start at 1.
3. Enter a **Time** in the displayed unit and press Go. The application reports an exact or nearest-row match.
4. Select cells and use `Ctrl+C` to copy them.

Most measurements show at most four significant digits. Time uses additional precision to distinguish nearby measurements. Copy uses full-precision numerical values in the display units; screen rounding does not alter the stored values. Clipboard copying of Data and elaboration tables is limited to 200,000 selected rows; use smaller selections rather than trying to copy a huge dataset at once.

Use **Re-map Columns…** only when the mapping needs changing. A new import interpretation makes the previous elaboration unavailable for that dataset; subsequent analyses use the newly imported data.

## 3 Run Data Elaboration

Click **Go to Data Elaboration**, or choose the section in the sidebar. This stage identifies rest points, half-cycles, cycles and CC/CV/Other steps and integrates capacity and energy.

### 3.1 Set the scientific parameters

Open **Review** or the Advanced panel and check the following fields before Process.

| Control | Base value | Meaning |
|---|---|---|
| Cycle Order | Charge → Discharge | Which phase is first when half-cycles are paired |
| Rest Current Threshold | 0 mA | Points with absolute current at or below this threshold are Rest |
| CC Relative Tolerance | 1% | Allowed current spread relative to the segment mean for CC classification |
| CV Absolute Tolerance | 1 mV | Allowed voltage spread when confirming CV |
| Active Material Mass | Not set | Positive mass in mg for later mass-normalized results |
| Surface Area | Not set | Positive area in cm² for later area-normalized results |

Use the information buttons beside parameters for local explanations. The threshold controls classification, not a replacement of small measured currents by zero. Cycle Order can change every cycle-based result. The scientific guide explains the three-point confirmation rules.

Enter active material mass, not total cell mass, when that is the basis intended for your publication. The software cannot verify that the physical mass or area entered is the correct experimental basis. Missing mass or area leaves the corresponding normalized Capacity fields unavailable; absolute results remain useful.

### 3.2 Process and inspect the result

1. Click **Process**.
2. Follow the phase/progress display. Use Cancel if necessary and wait for acknowledgement.
3. At 100%, wait for **Saving result…** to finish. The result is current only once it has been saved.
4. Review the summary of cycles, half-cycles, segments and rows.
5. Open the full data table and switch between **Points** and **Segments**.

Points contain measurements, cumulative quantities and labels. Segments summarize detected runs of equal step type. The first columns remain frozen while you scroll sideways. Copy follows the display units at full precision. Data Elaboration deliberately has no graph; use Data Explorer to examine the signals.

**Export Data…**, next to View Elaborated Data and Processing Details, saves the elaborated points or the segments as CSV or Parquet, with every column of the table at full precision in the display units. Choose the table and the format, then the file name; an existing file is never overwritten.

### 3.3 Change parameters or sample information

Changing elaboration parameters creates pending changes and can mark an existing result Outdated. Run **Reprocess** explicitly to calculate a new result. A failed or cancelled attempt keeps the previous result. **Reset to User Defaults** restores the current user defaults rather than rewriting the source.

Sample mass and area (**Sample Informations**, both optional) are metadata for normalized Capacity results. Changing them does not integrate the raw data again by itself; reanalyze Capacity to calculate results with the new normalization basis. Review the status indicators in downstream sections after any change.

## 4 Inspect Data Explorer

Data Explorer becomes available after a successful elaboration. It shows the measurements and cumulative values of that elaboration.

### 4.1 Select axes and navigate

1. Choose **X**, **Y1** and, if useful, **Y2** above the plot. Select None for Y2 to use one vertical axis.
2. Use **Box Zoom** and drag a rectangle within the plot to select a region.
3. Select the four-arrow **Pan** tool and drag, or hold Shift while dragging.
4. Use Reset View to fit the data of the current filter.
5. To change one axis, use the axis band rather than the centre of the plot. The central plot's wheel is inactive; axis navigation is handled separately.

The counter **Rendered / Total** compares the points drawn with all the points available for the view. Interactive rendering may show a subset of real rows. It is suitable for navigation; use Render All Points before judging fine structure that might fall between selected points.

### 4.2 Use every Advanced group

| Group or control | How to use it | Effect on science |
|---|---|---|
| Cycle Filter | Enter `1`, `1-20` or `1-5,9,12-20`; press Apply Filter; Clear removes it | Display selection only |
| Point Tracking | Turn Track points on hover on or off | Display only |
| Plot Colors | Pick the Y1 and Y2 colours; Reset restores their base colours | Display only |
| Legend | Show or hide curve labels | Display only |
| Axis Ranges | Enter a finite minimum below its maximum, then Apply Ranges; reset for automatic fit | Display only |
| Plot Style | Select Lines, Markers or Lines + Markers | Display only |
| Units… | Open application or dataset unit settings | Display conversion only |

Cycle filters refer to the actual cycle numbers of the result. They do not renumber data. An absent cycle produces no matching curve. Invalid filter or range text must be corrected before applying it.

### 4.3 Render all points and save an image

1. Set the axes, cycle filter and desired region.
2. Choose **Render All Points** to draw every eligible point for that view. Follow completed/total progress; cancel if necessary.
3. Return to interactive mode before changing controls that are locked during full rendering.
4. Use **Save Image** to save the figure. Explorer image saving renders all relevant points rather than saving only the interactive sample.

The saved image is generated at a minimum 300 dpi. DPI describes image rendering, not instrument sampling resolution. The numerical data and null values are unchanged. Connecting finite vertices with a line is a display convention; the engine does not manufacture additional measured rows.

## 5 Analyze Capacity

Capacity calculates per-cycle quantities from the current elaboration. It uses all applicable phase contributions, including CV and rest contributions, rather than integrating only the plotted CC points.

### 5.1 Analyze and interpret the table

1. Open **Capacity** after elaboration.
2. Check **Reference Cycle**, whose base value is 2. The second phase of that cycle supplies the C-rate reference.
3. Check Cycle Order, Active Material Mass and Surface Area. The latter fields are read-only here; use the Data Elaboration links to edit their source values.
4. Click **Analyze** and wait until the result is saved.
5. Review the graph and table. Hover empty cells for their diagnostic code and explanation.

| Column in the app | Meaning | Name in exported files |
|---|---|---|
| Cycle | Cycle number | cycle |
| Charge Capacity | Charge phase net capacity (capacity display unit) | charge_capacity_ah |
| Discharge Capacity | Discharge phase net capacity (capacity display unit) | discharge_capacity_ah |
| Coulombic Efficiency [%] | Order-dependent capacity ratio | coulombic_efficiency_pct |
| Charge Energy | Charge phase net energy (energy display unit) | charge_energy_wh |
| Discharge Energy | Discharge phase net energy (energy display unit) | discharge_energy_wh |
| Energy Efficiency [%] | Order-dependent energy ratio | energy_efficiency_pct |
| Charge C-rate [C] | Charge CC current divided by the reference capacity | charge_c_rate |
| Discharge C-rate [C] | Discharge CC current divided by the reference capacity | discharge_c_rate |
| Charge Specific Capacity [mAh/g] | Charge capacity per active material mass | charge_capacity_mah_g |
| Discharge Specific Capacity [mAh/g] | Discharge capacity per active material mass | discharge_capacity_mah_g |
| Charge Areal Capacity [mAh/cm²] | Charge capacity per electrode area | charge_capacity_mah_cm2 |
| Discharge Areal Capacity [mAh/cm²] | Discharge capacity per electrode area | discharge_capacity_mah_cm2 |
| Charge Specific Energy [Wh/kg] | Charge energy per active material mass | charge_energy_wh_kg |
| Discharge Specific Energy [Wh/kg] | Discharge energy per active material mass | discharge_energy_wh_kg |
| Charge Areal Energy [mWh/cm²] | Charge energy per electrode area | charge_energy_mwh_cm2 |
| Discharge Areal Energy [mWh/cm²] | Discharge energy per electrode area | discharge_energy_mwh_cm2 |

Absolute capacity and energy columns are shown in your selected display units; exported names keep the stored unit in their suffix. Fixed specific/areal units remain as labelled. An empty value is unavailable, not zero. A partial first or last cycle can have one available phase and one missing phase. All-null quantities may be omitted from axis menus. Specific (per gram) columns appear only when Active Material Mass was entered in Data Elaboration (Sample), and areal (per cm²) columns only when Surface Area was entered: otherwise they are left out of the table, the axis menus and Export Table…, and a short tip above the chart reminds you how to get them.

### 5.2 Adjust the Capacity view

Select X, Y1 and optional Y2 above the graph. The Advanced panel contains Analysis, Cycle Filter, Plot Colors, Legend, Axis Ranges and Plot Style. These groups use the same filter/range syntax as Explorer.

**Show Preliminary (cycle 0)** controls its visibility. **Y2 uses the Y1 scale** places the two series on the same vertical scale; use it only for quantities whose common units and ranges make this meaningful. It does not normalize either series. **Analysis Details** exposes the result ID, reference capacity and parameters used for the result.

Changes to Reference Cycle or normalization basis require **Reanalyze**. Colours, filters, axis ranges, legends and line style do not require an analysis.

### 5.3 Export Capacity

1. Open Export from the Capacity data view.
2. Read the row-scope explanation in the dialog. Without an applied filter, export includes all cycles, including cycle 0 even when it is hidden from the graph. With a filter, it exports the selected cycles.
3. Choose CSV or Parquet and the desired columns. Cycle is always included.
4. Select **Also export diagnostics** if the reasons for empty cells must accompany the table.
5. Choose a new destination filename and wait for success.

The export preserves full numerical precision in the display units. A diagnostics companion uses the same basename with ` - diagnostics`. If an existing destination prevents export, choose a new name; do not assume the file was replaced. Use Save Image for a figure rather than a numerical table.

## 6 Detect and measure DCIR

DCIR follows Detection, Review, Results and optional Correction. A detected pulse is a candidate; it is not an automatically approved measurement.

### 6.1 Configure and run Detection

| Parameter | GUI base value | Guidance |
|---|---|---|
| Pulse Duration | 30 s | Nominal duration of the CC pulse |
| Pulse Duration Tolerance | 20% | Inclusive duration band around the nominal pulse |
| Rest Before | 600 s | Nominal immediately preceding rest; 0 disables this requirement |
| Rest Before Tolerance | 20% | Relative duration tolerance |
| Rest After | 600 s | Nominal immediately following rest; 0 disables this requirement |
| Rest After Tolerance | 20% | Relative duration tolerance |
| Polarity | Both | Charge, Discharge or Both |

1. Choose parameters that describe the actual pulse protocol. The base values are starting values, not instrument-specific recommendations.
2. Click **Detect**.
3. After completion, review the candidate count and parameter summary.
4. If detecting again, read the confirmation: Accepted events are retained; other candidate decisions may be replaced by the new detection.

DCIR uses the CC classification of the current Data Elaboration. Unexpected candidates can originate from unsuitable elaboration thresholds, not only from DCIR durations.

### 6.2 Review pulses and measurement times

1. Open **Review** and select a pulse from the list.
2. Examine current, voltage, rest zones and the reference/measurement markers.
3. Read the **V1 at** time in seconds after V0 of the selected pulse. The base value is 30 s, the end of the default 30 s pulse.
4. Read the **I1 at** time: empty uses the third pulse sample; a positive time after V0 chooses the nearest real current sample.
5. To change the times, type them and choose **Apply** (this pulse only) or **Apply to All** (every pulse). A pulse with its own times shows "own times" in the list and "this pulse" above the fields. Its measurement changes, so it returns to **Pending** and must be accepted again before Apply & Calculate. Apply to All removes every pulse's own times and measures the accepted pulses again; they stay accepted.
6. Read the actual sampled times, resistance, diagnostic messages and optional SOC.
7. Accept or Reject the event. A and R are shortcuts within the Review area; arrow keys navigate pulses.

V0 is the last recorded sample before the pulse, not the first pulse sample. The current and voltage measurement samples may therefore be at different instants. No sample interpolation occurs. A tie chooses the later sample. A request beyond the last observed pulse sample uses that last sample and reports a warning.

State, Polarity and Source filters determine the visible list. After Accept/Reject, the next visible pulse is selected according to those filters. An event hidden by a filter has not been deleted. An accepted event can still have an unavailable resistance, for example when its current difference is exactly zero.

The review value is a preview until **Apply & Calculate** successfully calculates and saves the accepted set. Do not use a preview as though it were a final result.

### 6.3 Add a manual measurement

1. Choose **Add Manual Measurement**.
2. Supply I0, V0, I1 and V1. For each value, choose Typed or Data point.
3. When using Data point, activate Pick on Chart… and select a real recorded sample. Use the manual dialog's time/cycle navigation to find the event.
4. Enter typed numbers in the units displayed. Typed values are explicitly identified as not linked to a data point.
5. If the event should support Cycle Correction, select two real boundary samples for its optional correction span.
6. Review the preview and confirm the measurement, then save it with Apply & Calculate.

A manual resistance can be valid without a correction span. It cannot exclude a pulse from cycle segmentation unless authenticated boundary samples define the span.

### 6.4 Calculate and inspect Results

1. Finish the relevant accept/reject decisions and check measurement times.
2. Click **Apply & Calculate** and wait until the results are saved.
3. Open **Results** to read the results table and resistance plot.
4. Use Export for CSV/Parquet or Save Image for a figure.

The Advanced panel controls Shade Zones for the inspection chart and Point Color/Reset Color for the results chart. Its Detection group summarizes the detection request and links to Edit in Detection; the measurement-time summary belongs to the Review strip. Detection parameters and measurement times are also editable through global Settings → Advanced Settings → DCIR. Without an open dataset, those global DCIR fields set locally remembered defaults for future imports; they do not edit Default or an existing dataset.

After Cycle Correction, fields may be edited as pending inputs, but new Detection and automatic measurement remain blocked. Read the explanation and process the original data again before starting a fresh DCIR workflow. A corrected elaboration, including the one created by Remove Correction, is never treated as an original elaboration. Applying a profile that changes DCIR inputs after correction is also guarded.

## 7 Optionally correct cycle labels around pulses

1. First calculate the Accepted DCIR measurements with Apply & Calculate.
2. Select **Go to Correction** or **3 Correction**.
3. Inspect the list and highlighted spans. Events without usable boundaries are identified and will not be excluded from cycle segmentation.
4. Check the rest-current threshold, shown in your selected current unit, and the Cycle Order/Capacity settings summary.
5. Choose **Apply Cycle Correction…**, read the confirmation and proceed only if its scope is intended.
6. Wait until the corrected elaboration is saved and review the report. Use Reapply after a relevant change, or Remove Correction to restore the uncorrected cycle labels.

Correction excludes pulse spans only while deciding half-cycle boundaries. It keeps the measured samples, integrates their contributions in the corrected half-cycles and saves a new, corrected elaboration. It does not erase pulses from the raw data or repeat step classification.

Data Explorer, Capacity and DCIR then use the corrected elaboration. An existing dQ/dV result keeps its values and only receives the new cycle labels; this is not a fresh dQ/dV calculation. Inspect the labels and provenance before combining corrected and uncorrected figures. A failed/cancelled correction retains the previous result. Removing the correction saves another elaboration; earlier results are not deleted.

## 8 Analyze differential capacity and voltage reconstruction

### 8.1 Choose cycles and a method

1. Open **dQ/dV** after elaboration.
2. Enter the **Cycles to Analyze**, for example `1,5,10-20`. Cycle 0 is not an analyzable dQ/dV cycle.
3. Choose a Method. The current build exposes the three methods below; numerical finite differences are part of the classic interpolated-gradient implementation, not an additional menu method.
4. Enter relevant settings. The form shows method-specific fields.
5. Review the **DCIR pulses** summary. Exclusions come from the Accepted pulses of the current DCIR result; this is not an independently editable dQ/dV checkbox.
6. Click **Analyze** and wait until the result is saved. Use **Reanalyze** after changing a scientific parameter.

| Setting | Base value | Applies to |
|---|---|---|
| E min and E max | Empty | Every method; entered in V; empty means the measured limit |
| Interpolation Points | 10% | Interpolated gradient; enter a percentage or an integer count such as 200 |
| Edge Trim | 5 mV | Interpolated gradient; 0 disables trimming |
| Bin Size | 1 mV | Histogram and Weighted Histogram |
| Smoothing | Off | Every method; filter type depends on the method |
| SG Window | 5 points | Interpolated gradient with smoothing; positive odd integer |
| SG Polynomial Order | 2 | Interpolated gradient with smoothing; below the window length |
| Gaussian Width | 1 bin | Histogram methods with smoothing; positive |
| Voltage Reconstruction | On | Rebuild voltage and report per-curve APE/RMSE |

**Interpolated Gradient (Classic)** sorts eligible points by voltage, interpolates Q onto a uniform voltage grid and differentiates that curve. Use it for essentially monotonic half-cycles; sorting can fold non-monotonic behaviour onto one voltage axis.

**Histogram** estimates capacity per voltage bin from point counts. It assumes similar capacity increments per measurement. **Weighted Histogram** weights adjacent eligible measurement intervals by their capacity increments; it better reflects non-uniform sampling or changing current within the selected CC data. The scientific guide gives their exact equations and limits.

### 8.2 Explore Raw and Smoothed results

Use Phase to select charge, discharge or both. Use Plot to select Raw, Smoothed or Both. Smoothed values require an analysis run with smoothing enabled; otherwise the effective curve is raw. **Cycles shown** is a display filter within the cycles already analyzed. It cannot calculate a missing cycle.

The dQ/dV axis remains Ah/V or mAh/V according to the capacity display unit. Changing the horizontal voltage axis to mV does not make the derivative per mV. Read both axis labels. Negative discharge dQ/dV is the mathematical sign of increasing cumulative capacity with falling voltage; it is not automatically reversed for visual symmetry.

Use Box Zoom, Pan/Shift-drag, Reset View and Save Image as in other graphs. Distinct curve pieces, when supplied by the engine, are not joined by the GUI. The installed engine currently produces one piece per curve; the classic method does not promise a separate reconstruction of every monotonic branch.

### 8.3 Use every dQ/dV Advanced group

| Group | Controls |
|---|---|
| Analysis | Cycle selection, method, voltage window, interpolation or bin settings, smoothing, reconstruction, Analyze/Reanalyze |
| Trace Colors | Same colour for charge/discharge; a colour per cycle and optionally per phase; Reset Colors |
| Line Style | Charge and discharge dash style; line width 0.5–6 px; Raw Opacity 10–100% when Both is selected |
| Legend | Show or hide the legend |
| Axis Ranges | Explicit X/Y limits; Apply or Automatic |
| Plot Style | Lines, Markers or Lines + Markers |

The base line width is 1.6 px. In Both, raw curves are thinner and initially use 50% opacity. Appearance changes affect presentation, not the calculated derivative.

### 8.4 Read and export the eight-column table

Open **View dQ/dV Data**. The table contains Cycle, Half-Cycle, Phase, Anchor Source Row, Segment, Voltage, dQ/dV raw and dQ/dV smoothed. The canonical names are documented in the scientific guide. Despite the legacy Voltage (midpoint) heading, classic-method coordinates are grid nodes; histogram coordinates are bin centres.

Hover a numerical cell for its full precision. Export offers CSV/Parquet and selectable columns. Rows follow the cycles and phase shown; both raw and smoothed columns can be exported independently of which trace is currently visible. Missing smoothed values remain empty, not zero.

### 8.5 Inspect Voltage Reconstruction

Open the **Voltage Reconstruction** tab. Choose Measured, Reconstructed or Both, the phase, and the cycles shown. Use its independent plot navigation.

The table **Reconstruction Quality** reports APE of the capacity range and voltage RMSE for each curve. Lower values indicate closer reconstruction of the eligible measured curve under those definitions. They do not establish instrument accuracy or validate a chemical model. An unavailable metric remains empty. These metrics compare the dQ/dV representation with the eligible CC data, not necessarily the whole-cycle Capacity total.

Reconstruction display choice and axis ranges do not run a new analysis. Turning the scientific reconstruction parameter on requires Reanalyze if the current result has no reconstruction data.

## 9 Analyze GITT and ICI

The GITT / ICI section becomes available after Data Elaboration. It uses the current elaboration only; a new elaboration clears its impulses and results. Like DCIR, it is a sequence of steps shown as buttons at the top: **1 Setup › 2 IR Drop › 3 OCV › 4 Pulse Fitting › 5 Diffusion Results**. Steps 2 to 5 unlock once Setup has recognised the impulses. You can go back to any step without losing your work: every step reads the same analysis, and a change made in one step updates the others. While a calculation runs, its progress and **Cancel** appear next to the title; Cancel keeps the previous results.

### 9.1 Setup

- **Protocol**: choose **GITT** or **ICI**; it is never inferred from pulse durations. Changing it restores the methods and OCV method preselected for that protocol.
- **Cycle or half-cycle**: choose **Cycle** or **Half-cycle**, then one number from those of the current elaboration. Exactly one element is analysed; choosing a half-cycle clears the cycle and vice versa.
- **Calculation methods**: GITT VA, GITT Δ, ICI and Delacourt. Several can be checked to compare their D. With the GITT protocol all four are available, so ICI and Delacourt can be added to a GITT analysis. With the ICI protocol only ICI is available: Delacourt needs a relaxed, uniform state at the start of the pulse, which the short ICI interruptions do not provide. Disabled methods say why. Delacourt uses a fixed offset, whose IR correction is the pre-pulse sample.
- **OCV methods**: select one or more of **Sampled OCV**, **Pseudo OCV** and **Extrapolated OCV** (a ✓ marks the selected ones). Each selected OCV method gives every compatible calculation method its own D series, so they can be compared. Pseudo OCV is the intercept of consecutive ICI interruptions: it is enabled only when ICI is checked and is used only by ICI; every other checked method needs Sampled or Extrapolated OCV too. Sampled and extrapolated OCV never overwrite each other.
- **Physical Inputs**: Active Volume, Effective Area and Particle Radius, each with its unit and a Required or Optional mark. V_act and A_eff are required by GITT VA, GITT Δ and ICI; r_p by Delacourt and by physically guided windows. They start empty for every dataset and are never copied from another one. V_act is the active-phase volume, not automatically the coating or molar volume; A_eff is the effective diffusion area, not automatically the geometric, BET or electrochemical area.
- **Automatic analysis**: when ticked, every impulse is calculated right after recognition with the chosen window method (statistical search, suggested window, or physically guided with a D source and the physical coefficient). The physical coefficient starts at 0.0032, a theoretical reference value from the literature (Chien, supplementary material) that holds only under specific assumptions; edit it if your conditions differ. When it is off, Setup only recognises the impulses and you calculate them in Pulse Fitting.
- **Advanced settings**: the GITT Δ IR correction (none by default), the pre-pulse and post-pulse IR sample times (1 s; the post-pulse time follows the pre-pulse one unless set) and the advisory indicator thresholds.

Every item has an (i) button that explains it. The box at the bottom lists every condition still to fix. **Proceed** stays disabled until the list is empty. It then applies the settings, recognises the impulses of the chosen element, measures their IR drops and rest OCV, calculates them if the automatic analysis is on, and opens IR Drop. If the element contains no impulse of the protocol, a message says so.

In the following steps, a bar at the bottom offers **‹ Back** and **Continue to … ›**. Continue becomes a filled button once the step is complete (no rest period or impulse fit left to review); next to it, the number still to review is shown. You can continue anyway.

### 9.2 IR Drop

The pulse list shows every impulse with its pre-pulse and post-pulse IR drop, ΔV in mV, in separate columns. **Valid** shows whether R could be measured on each side (pre, then post: ✓ or ✗); hover a row for the reasons. A ✎ marks an impulse with a manual sample. Select an impulse to update both charts and every value below them.

- **Pre-pulse (R_on)**: the reference is the last sample of the rest before the pulse; the measured sample is the last pulse sample at or before the pre-pulse sample time.
- **Post-pulse (R_off)**: the reference is the last sample under current; the measured sample is the last sample of the following rest at or before the post-pulse sample time.

Each chart is zoomed on its transition and shows the real samples. The square is the reference and the diamond the measured sample used by gregorithm. The dashed line marks the reference, the shaded band the switching interval up to the first sample after the switch, the dotted line the sample time T, and the vertical segment the computed ΔV. Use the mouse wheel and drag to zoom and pan; **Reset view** returns to the transition. Below each chart: rows, times and voltages of the two samples, ΔV, both currents, ΔI, R = |ΔV/ΔI| and how the sample was chosen. When R cannot be measured, the reason is shown and no value is invented.

**Sample times** works as the measurement times of DCIR: its Pre-pulse and Post-pulse fields show the times the selected impulse uses, and the label next to the title says whose they are (*all impulses*, or *this impulse* when it has its own). **Apply** keeps the typed times for this impulse only (an empty field, or the global time, means the global time of that side); **Apply to All** makes them the times of every impulse (the same settings as Setup → Advanced settings), keeping the times chosen for single impulses. Click a blue sample to use exactly that row; **Use global times**, shown when the impulse has its own times or samples, goes back to the global times. The pre-pulse sample is also the sample of the selected-sample IR corrections: the Delacourt fixed offset, and GITT Δ when its correction is *Selected sample under current*. Only those results of that impulse are recalculated. A post-pulse sample changes no D.

### 9.3 OCV

The list shows every rest period with its sampled and extrapolated OCV and its state: **Pending**, **Accepted**, **Modified** (accepted, then changed) or **Invalid** (a value it needs is missing). Select a rest to plot V against the time from the last sample under current. The diamond is the sampled OCV.

Below the chart, two cards work as the measurement times of DCIR: their fields show the values the selected rest
uses, and the label next to each title says whose they are (*all rests*, or *this rest* when it has its own).
**Apply** keeps the typed values for this rest only; **Apply to All** makes them the values of every rest without its
own choice; the reset button, shown only when the rest has its own choice, removes it.

- **Sampled OCV**: the last real rest sample at or before **Sampled at** (empty = the last sample of the rest). Click
  another sample to use exactly that row; **Use global rule** restores the rule. Only the results using that rest's
  sampled OCV are recalculated.
- **Extrapolated OCV**: **Extrapolate OCV** fits V = α + β·ln(t) over the last part of every rest (from the **Fit
  start** fraction, 0.2 = last 80 %) and extrapolates it to the **Target**, 10 800 s by default. The box applies at
  once to every rest; it is ticked from the start when Extrapolated OCV is one of the OCV methods, and cannot be
  turned off then. A rest can have its own target and fit start (**Apply**, **Use global settings**); **Apply to All**
  never overwrites them. The plot then shows the fit interval (shaded), the fit curve, the extrapolated point (star)
  and extends to the target; tick **Logarithmic time** for long targets. A target longer than the observed rest is
  allowed and reported with its ratio. The fit values below the cards are explained in
  section 9.7 Fit statistics.
- The **Pseudo OCV** of ICI appears as a dotted line labelled as not equilibrium when ICI results exist.

**Accept** confirms the OCV values of the selected rest exactly as shown; **Accept all valid** does it for every rest with a sampled OCV (a missing extrapolated OCV is shown with its reason and only blocks the series that use it); **Return to Pending** withdraws it. A later change to that rest's values makes it Modified until you accept it again. A D result is used only when every rest it takes its OCV from is accepted.

### 9.4 Pulse Fitting

The list shows every impulse with the state of its fits and its problems (failed, no D, out of date, to check). The chart shows the segment fitted by the series chosen in **Fit of**, V(t) above and V(√t) below: the pulse for GITT VA and Delacourt, the interruption after the pulse for ICI. The highlighted samples are the ones inside the window gregorithm fitted. **Overlay the other fits** adds the fits of the other checked methods of the impulse, each labelled: a √t line and Delacourt's spherical model are different fits.

**Fit of** lists one entry per distinct fit: GITT VA (pulse), ICI (interruption) and Delacourt (whole pulse). GITT Δ has no fit of its own; with the GITT-intercept IR correction it reuses the GITT VA pulse window, so it is listed only when GITT VA is not checked. The OCV source does not change a √t fit: it enters only ΔU and therefore D. Series with an identical fit are merged and shown once; the OCV source is named only when the fits really differ, as with Delacourt fixed offset (its offset η uses the OCV of the rest before the pulse) or with a physically guided window (it depends on D). The table below the chart still lists every series, because D changes with the OCV source.

Every canonical sample of the segment is drawn as a point, and no fit ever skips samples: a fit uses all the samples inside its window. **Suggested window, fixed** fits the whole segment. Drag the shaded window in either plot: the fit is recalculated once, when you release it. You can also type the bounds and choose **Apply window**. Edits made while a calculation runs are queued, and only the latest one is applied. **Re-apply automatic to this impulse** and **Calculate all impulses** use the automatic windows again; they drop manual windows and manual IR samples. GITT Δ needs no fit, and Delacourt has no editable window.

Below the chart, each series of the impulse shows D, slope, intercept, R², samples and indicators, or the reason why D is missing; section 9.7 Fit statistics explains every value. When the table is wider than the window, drag it sideways with the pointer, or use its scroll bar, Shift + wheel or a touchpad. **Accept** confirms the fits of the impulse, **Accept all valid** those of every impulse that is not Invalid, and **Return to Pending** reopens one. A recalculation that changes a fit makes the impulse Modified. An impulse whose methods need no fit shows *No fit needed*; a failed fit of one series is shown and never used, without blocking the others.

### 9.5 Diffusion Results

The chart draws only the D results in use: current, with a finite D, with an accepted pulse fit when the method needs one, and with every rest it uses accepted. Each excluded result is counted below the chart with its reason. Accepting and revoking in OCV and Pulse Fitting update this chart and gregorithm's acceptance used by the export.

- **X axis**: Capacity [mAh] or [Ah], Specific capacity [mAh/g] (needs the active material mass entered in Data Elaboration) or SoC [%] (100 · Q / Q_end of the half-cycle, 100 − p in discharge). Unavailable choices are disabled and say why.
- **Y1** shows the diffusion coefficients, with a linear or logarithmic scale; a logarithmic scale draws only D > 0. Tick or untick each method and OCV series.
- **Y2** shows the Sampled, Pseudo and Extrapolated OCV curves, one point per rest at the capacity of the state it represents, connected only within one source and one half-cycle.
- **Y3** shows the pre-pulse and post-pulse resistances from IR Drop, the same values.

- **Branches**: show charge and discharge together, charge only or discharge only, on all three axes.
- **Colour by branch** (default): charge in red and discharge in blue on every axis; each D series is told by its marker, shown next to its name, each OCV by its line style, R_on (◇) and R_off (✚) by their marker. **Colour by method** gives each D series its own colour and tells the branch by the marker (● charge, ▼ discharge).

D and R are drawn as markers joined by lines by default; Advanced Settings → Results Chart switches to lines only or markers only and sets the marker size.

The options panel is also the legend: each box shows the colour or the marker of its series. Hover a point for its provenance; click a D point to open its impulse in Pulse Fitting, an R point to open it in IR Drop. **Save image…** writes the chart as PNG.

### 9.6 Export accepted results

**Export Table…** in Diffusion Results writes one row per complete impulse, as CSV or Parquet, with the columns you tick in the dialog: impulse, cycle and half-cycle; capacity, specific capacity and SoC at the start of the pulse (where D is placed); pre- and post-pulse IR drops with R_on and R_off; sampled, extrapolated and ICI pseudo-OCV of the rests around the pulse; the √t slope of GITT VA and ICI; and one D column per series in use, in the D unit chosen in Diffusion Results. By default charge and discharge go to separate files. Only D in use (accepted and current) is written: any other cell stays empty, never zero, and columns that are empty for every impulse are left out. When no D is in use, a message is shown and no file is created. An existing file is never overwritten; choose a new name. Every export also publishes the analysed snapshot as an immutable gregorithm generation, so the file stays traceable to its inputs.


### 9.7 Fit statistics

Every value below is computed by gregorithm; SparkBox only shows it. The colours are advisory: they never filter the
lists, block **Accept** or change data. The limits marked *provisional* are those of the current gregorithm version.

**√t fits (GITT VA pulse, ICI interruption).** The samples inside the window are fitted by least squares, with equal
weights and no smoothing, to V(t) = a + s·√t.

| Value | Meaning |
|---|---|
| **Slope** s [V/√s] | Slope of V against √t; it enters D. |
| **Intercept** a [V] | Voltage extrapolated to t = 0 (√t = 0). With the GITT-intercept IR correction it gives the IR jump. |
| **R²** | 1 − SSE / SST: the fraction of the voltage variance inside the window explained by the line. Close to 1 is good, but a long window can reach a high R² while bending, so read it together with q_L. Not available when the voltage is constant. |
| **Samples** | Number of canonical samples inside the window. At least two distinct times are needed for a fit, at least three for q_L. |
| **q_L** (linearity) [%] | RMSE / (\|s\| · (√t_end − √t_start)): the residual scatter relative to the voltage change the line spans in the window. Small means straight. Green ≤ 2 %, orange ≤ 5 %, red above (provisional). |
| **q_S** (stability) [%] | Median of \|s′ − s\| / \|s\| over neighbouring windows, obtained by moving each window end by about 10 % of its samples. Small means the slope, and therefore D, does not depend on the exact window. Needs at least two valid neighbours. Green ≤ 5 %, orange ≤ 15 %, red above (provisional). |
| **Statistical** | The worse of the q_L and q_S colours. N/D when the slope is not distinguishable from noise (\|s\| ≤ 2 standard errors) or one metric cannot be estimated: then no colour is given rather than a false green. |
| **Physical** | Whether the window ends within the useful time t_useful = k · r_p² / D, where the semi-infinite √t model holds: green up to t_useful, orange up to 1.5 · t_useful, red beyond. N/D without r_p or a guiding D, which is not a failure. |
| **To check** | An automatic window whose statistical colour is not green or whose physical colour is orange or red. The reason is in the Note column. |

**Delacourt (spherical model, whole pulse).** The model is fitted to the whole pulse; there is no √t line, so q_L and
q_S do not apply. Its two indicators are fit fidelity and identifiability:

- **Fit fidelity**: the worse of the R² colour (green ≥ 0.99, orange ≥ 0.95, red below) and of the residual structure,
  the lag-1 autocorrelation of the residuals (green ≤ 0.5, orange ≤ 0.8, red above). An autocorrelation close to 1
  means the residuals follow a pattern: the model misses a systematic shape of the transient. All limits provisional.
- **Identifiability**: red when D is not identifiable or lies on a search boundary, orange when D is identifiable but
  its uncertainty cannot be estimated, green otherwise. RMSE and the relative uncertainty of D are recorded with the
  result.

**GITT Δ** has no fit. Its only check is the sign consistency of the method: under a current of sign s, both the OCV
step ΔU and the corrected response ΔE_τ must move in the direction of s (red otherwise); a zero ΔU gives D = 0 and is
orange because it cannot be drawn on a logarithmic axis.

**OCV extrapolation (LOG_TIME_2P).** Each rest is fitted from its fit start to its end with V = α + β·ln(t / 1 s):

| Value | Meaning |
|---|---|
| **α** [V] | Voltage of the fit at t = 1 s. |
| **β** [V] | Change of voltage per natural-log unit of time: the relaxation rate. |
| **Fit interval, samples** | Time range of the fitted samples and their number. |
| **RMSE** [mV] | √(SSE / n): typical distance between the samples and the curve. |
| **R²** | As above, for the log-time fit. |
| **Target ratio** | Target time / observed rest. Above 1 the OCV is extrapolated beyond the data: allowed and reported, but less certain the larger the ratio. |

## 10 Compare datasets

1. Import and elaborate at least two datasets.
2. For Capacity or dQ/dV comparison, first analyze each selected dataset in its own section.
3. Open **Comparison** from the sidebar and open its Advanced panel.
4. Select at least two drawable datasets and choose distinct colours beside their names.
5. Choose Data Explorer, Capacity or dQ/dV above the chart. DCIR and GITT / ICI are not Comparison modes.
6. Set the common cycle filter using the original cycle IDs. Press Enter to apply it.
7. Choose axes or phase/Raw/Smoothed as appropriate to the mode and inspect any Outdated or missing-cycle note.

| Mode | Available choices |
|---|---|
| Data Explorer | One X, one Y and common cycle IDs; real elaboration points |
| Capacity | Cycle on X and one Capacity quantity on Y |
| dQ/dV | Voltage versus dQ/dV; phase and Raw/Smoothed/Both choices |

Comparison always uses app-level units, even if a dataset has a local override. It never computes differences, ratios, cross-dataset averages or interpolation between samples. A missing cycle contributes no curve and is not matched to a different cycle. An Outdated result can remain visible with its status so that its age is not hidden.

Advanced controls cover Datasets and colours, lighter tones for later cycles, line width, Legend, Axis Ranges and Plot Style. The base line width is 1.6 px. Each mode remembers its own filter and viewport during the session. Dataset identities and Comparison dataset selections are not part of a reusable profile.

For Data Explorer comparison, the Rendered/Total count uses distinct source rows per dataset rather than counting a row repeatedly for each curve. With a non-time X axis, the visible region is filtered before display selection. Render All Points and Save Image include every eligible point of the selected datasets for the requested view, with progress and cancellation. Capacity and dQ/dV already plot their calculated rows; they do not use that raw-data sampling workflow. There is no Comparison numerical table/export; export each dataset's own table.

## 11 Manage settings and profiles

Open **Settings → Settings…**. The left sidebar lists Appearance, Units, New Dataset Defaults and Advanced Settings. Click the **Profile: …** card above that list to open Profiles. Valid changes take effect immediately. **Done** closes the window; there is no general Apply step and closing the window does not undo valid edits.

### 11.1 Appearance and defaults

Appearance offers Light, Dark and System. Click a choice to change the theme immediately. System follows the operating system: light or dark, and its accent colour (Windows: Settings → Personalization → Colors) for buttons, selections and the active section. Light and Dark use the SparkBox colours. Charts keep their colours in every mode. Theme changes affect only the appearance.

New Dataset Defaults supplies the four Data Elaboration starting parameters for newly imported datasets. Existing datasets keep their values. Edit Cycle Order, Rest Current Threshold, CC Relative Tolerance and CV Absolute Tolerance; valid edits are saved immediately. Restore Factory Defaults resets these starting fields. Invalid text is highlighted and the last valid saved values remain in use. These controls neither edit a source file nor launch an analysis.

### 11.2 Units

Choose **All Datasets** for app units or **This Dataset** for a session-specific override. Enable Use custom units for this dataset before editing its overrides. Each choice takes effect immediately. **Use App Units** removes the dataset override; **Restore Factory Defaults** resets app units in All Datasets mode. Changing units can reset plot ranges so the old numeric bounds are not misread in new units.

| Quantity | Display options |
|---|---|
| Time | s, min, h |
| Current | A, mA, µA |
| Voltage | V, mV |
| Capacity | Ah, mAh |
| Energy | Wh, mWh |
| Resistance | Ω, mΩ |

Mass/area-normalized units stay fixed as labelled. Time source mapping also supports ms, but ms is not a display-unit option in this Settings page.

### 11.3 Advanced Settings

Choose **Advanced Settings**, then the section from its dropdown. These are the actual Advanced panels, kept synchronized with the main window. Their edits are live; Done does not undo them. Use each panel's explicit range/filter controls to apply text inputs. Scientific parameters still require Process, Analyze, Detect or Apply & Calculate as appropriate before a new result is calculated; changing a field alone does not recalculate it.

Without a dataset, DCIR starting settings and Comparison preferences are available; the other sections show **No File Open** with an **Open File** menu. DCIR starting values are validated, saved locally and used by subsequent imports. Invalid text must be fixed before it can be captured in a profile.

### 11.4 Create and use profiles

1. Configure the active dataset's parameters, plots and units. With no dataset, set the available starting preferences.
2. Check that numerical fields are valid. Appearance, unit and New Dataset Defaults changes are already live; there is no separate Apply button.
3. Click the **Profile: …** card above the Settings sidebar to open Profiles.
4. Click **Create New Profile…**, enter a meaningful unique name and confirm. This saves the current settings as a new profile. Create as many profiles as you need, for example one per instrument or protocol.
5. To replace a saved setup, select the profile, click **Update with Current Settings** and confirm. Use **Rename…** to change its name. **Delete…** removes the selected profile after confirmation; deleting it does not change the currently configured controls.
6. To restore a set, select it and click **Use Profile**, or double-click its list entry. Selecting a name once alone does not apply it. The profile card and the list's “in use” note identify the selected profile.

Default is protected and cannot be renamed or overwritten. It restores the base settings, including 600 s DCIR rests. Applying a profile updates the active dataset, Comparison, app appearance/units and the starting settings for future imports; other open datasets retain their section settings. A saved dataset-unit override travels with the profile so its ranges keep the same meaning.

Profiles preserve parameter choices and display settings, not source files, sample mass/area, scientific results, accepted/rejected decisions or dataset selections. Per-dataset Comparison colours stay session-specific. A profile is not an analysis result. Applying it is guarded during import, processing, rendering or export and when a different DCIR input set is incompatible with correction history.

## 12 Export everything at once

The **Export** button at the bottom of the section list opens one window with every table export of the active dataset: elaborated data and segments, Capacity table, DCIR measurements, dQ/dV table and GITT / ICI diffusion results. Exports whose section has a result can be ticked; the others are locked and say which section must be run first. Clicking a locked export repeats that explanation with a button that opens the section.

Tick the exports, choose CSV or Parquet and the folder, then **Export**. Each export is written as a separate file named "<dataset> - <export>", with every row and every column in the display units, exactly as the section's own export writes them (GITT / ICI: one file per branch). An existing file is never overwritten: a free name such as "… (2)" is used. The window then lists the files written and any export that failed. Chart images are saved from each section with Save Image.

## 13 Recognize result states and handle problems

| State or symptom | What to do |
|---|---|
| Not processed or not analyzed | Run the prerequisite operation explicitly |
| Modified or Changes not applied | Review the pending input; run the relevant operation to apply it |
| Outdated | Read the old result as belonging to its recorded inputs; reprocess/reanalyze when a current result is required |
| 100% with Saving result… | Wait until the operation returns successfully |
| Cancel requested | Wait for acknowledgement; do not use an unfinished result |
| Empty cell or missing curve | Read its diagnostic; do not substitute a numerical zero |
| No DCIR candidates | Check CC classification, pulse/rest durations, tolerances and polarity |
| No GITT / ICI impulse found | Check the protocol, the chosen cycle or half-cycle and the Data Elaboration thresholds |
| Missing D points in Diffusion Results | Read the excluded-result reasons below the chart; check V_act and A_eff (r_p for Delacourt) and accept the rests and pulse fits |
| No dQ/dV curve | Check explicit cycle IDs, CC data, voltage window, trim and bin/grid settings |
| Empty normalized Capacity axes | Supply the correct positive mass/area and reanalyze |
| No Comparison curves | Select two drawable datasets and confirm their results and common filter |
| Destination exists | Choose a new export filename and wait for a success receipt |

The source is kept unchanged. Saved results normally live in the current user's `.gregorithm-gui/results` folder. They are kept on disk; closing a tab releases its session resources but is not an approved disk-cleanup operation. The current GUI does not automatically restore open dataset tabs on restart. Profiles persist independently.

For reproducible research, record the source identifier, engine version, parameters, result ID, fingerprints, diagnostics and exported checksums alongside the figures. Numerical exports are data files, not a substitute for all of that provenance. Keep private source files and stores outside material intended for public publication.

## 14 Shortcut reference and final review

| Shortcut or gesture | Action |
|---|---|
| Ctrl+O | Auto Mapping |
| Ctrl+C | Copy the selected supported table cells |
| A or R in DCIR Review | Accept or Reject the selected event |
| Up or Down in DCIR Review | Previous or next visible pulse |
| Shift and drag on a plot | Pan |
| F1 | Open this Help |

Before reporting a result in a paper or report, confirm units and current sign, import diagnostics, cycle interpretation, normalization basis, DCIR measurement times, GITT / ICI protocol, physical inputs, OCV methods and accepted fits, dQ/dV method/filter parameters, result status and the exact cycle/phase scope of each exported table. Use the Scientific Methods companion to explain how the reported numbers were obtained.
