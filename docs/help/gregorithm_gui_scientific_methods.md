# SparkBox Scientific Methods

Physical definitions and their numerical implementation in gregorithm

Edition 16 November 2026. This document explains the scientific transformations used by the current SparkBox interface and its installed gregorithm 0.1.0 engine. Each method separates the continuous or theoretical definition from the calculation actually performed on recorded samples. The GUI sends validated inputs to gregorithm and displays the results it saves; it does not implement a second battery-analysis engine.

## 1 Scope and notation

The processing chain is source inspection and import, canonical cleaning and unit conversion, half-cycle/cycle segmentation, step classification, cumulative integration, Capacity and C-rate, DCIR, optional cycle correction, dQ/dV with optional voltage reconstruction, and GITT/ICI diffusion coefficients with their OCV and IR-drop products. Data Explorer and Comparison display existing quantities and select points for plotting; they do not add a chemical model.

| Symbol | Meaning | Canonical unit |
|---|---|---|
| t | Elapsed measurement time | s |
| I | Measured current, positive for charge | A |
| V | Measured cell voltage | V |
| Q | Capacity | Ah |
| E | Energy | Wh |
| R | DC internal resistance magnitude | Ω |
| m | User-supplied active material mass | mg in the request |
| A | User-supplied electrode area | cm² |
| ε | Rest-current threshold | A |
| r | CC relative tolerance | Dimensionless fraction |
| δV | CV absolute tolerance | V |

An index i denotes a retained measurement in time order. An index h denotes a half-cycle, c a cycle, k a CC segment and j a derivative grid point or histogram bin. Positive and negative subscripts below describe directional contributions, not automatic relabelling of an experiment.

Current sign is fixed: positive means charge and negative means discharge. A source that uses the opposite convention must be converted explicitly before import. Neither step names nor the apparent shape of a voltage curve reverse the current.

Displayed h, mA, mAh, mWh and Ω (or mΩ when chosen) are scale conversions at the interface boundary. Scientific calculations use the canonical units above. A screen value rounded to four significant digits is not the stored numerical value.

## 2 Source import and cleaning

### 2.1 Mathematical unit conversion

A mapped numerical source value is multiplied by its declared unit factor. For example:

$$
t_{\mathrm{s}}=3600\,t_{\mathrm{h}},\qquad I_{\mathrm{A}}=I_{\mathrm{mA}}/1000,\qquad V_{\mathrm{V}}=V_{\mathrm{mV}}/1000.
$$

The importer writes float64 time/current/voltage columns. Source time units are s, ms, min or h; current units are A, mA or uA in gregorithm, of which Manual Mapping offers A and mA; voltage units are V or mV. The GUI's Manual Mapping translates selected column indices back to the exact source column names required by gregorithm.

Elapsed duration text H:MM:SS.fraction is parsed as follows, with hours unrestricted above 23:

$$
t_{\mathrm{s}}=3600H+60M+S+f.
$$

Minutes and seconds must be in their accepted 00–59 fields. The fractional part is not truncated. This is elapsed time, not time of day or a timestamp with a timezone.

### 2.2 Actual cleaning order

1. Discard a row if current or voltage is missing, non-numeric or non-finite.
2. Validate time only for the remaining rows. Invalid or missing time aborts the import.
3. If a time equals the previous retained time, keep the first surviving row and discard the later occurrence with a warning.
4. If time is less than the previous retained time, abort. The importer does not sort or split the experiment.
5. Require at least two retained rows.

This order matters. An invalid-current row cannot reserve its timestamp or cause a decreasing-time error. A later valid row can become the first surviving occurrence of that time.

The canonical table contains only `time_s`, `current_a` and `voltage_v` in retained order. A downstream `source_row` is the zero-based row of this canonical table, not necessarily the physical line number of the original CSV. GUI table row numbers are generally shown starting at 1.

Removed rows do not exist for later numerical integration. Their retained neighbours become adjacent, even across a long time gap. The engine has no automatic gap-duration rejection or interpolation of missing measurements. Consequently a large missing interval can materially affect an integral and must be reviewed by the researcher.

Negative finite voltage survives import. It later makes energy unavailable for the affected half-cycle. Import warnings belong to the import report and are not propagated indiscriminately as quality flags on every later result.

## 3 Rest and half-cycle segmentation

### 3.1 Rest predicate

The classification rule is:

$$
\mathrm{Rest}_i\ \Longleftrightarrow\ |I_i|\leq\varepsilon,\qquad\varepsilon\geq0.
$$

The GUI base threshold is 0 mA; its request conversion is mA divided by 1000. At that threshold, only exactly zero current is Rest. Changing the threshold changes labels, not measured current. A small nonzero current classified as Rest still contributes to the numerical integrals.

### 3.2 Discrete confirmation rule

Leading Rest rows have half-cycle 0 and cycle 0. The first non-rest row starts half-cycle 1 immediately. A direction reversal requires three consecutive non-rest samples of the opposite sign. Once confirmed, the new half-cycle starts at the first of those three samples. Rest resets the pending reversal count. A shorter opposite-sign excursion stays in the existing half-cycle and keeps its measured current.

This is a sample-count rule. Three samples separated by unequal time intervals still satisfy the same rule; there is no hidden three-second requirement. Rest after active current keeps the current half-cycle and never starts a new one.

For example, `+ + + − − 0 −` does not confirm a discharge. In `+ + + − − 0 − − −`, the final three negative samples start the new half-cycle at the first of those three negatives.

The streaming implementation carries undecided rows across block boundaries. It must not decide differently merely because a three-point reversal crosses a Parquet batch boundary.

## 4 Cycle assignment

The selected Cycle Order determines which direction is the first phase of a cycle. Let h be a positive half-cycle index. When the direction of half-cycle 1 agrees with that selected first direction:

$$
c(h)=\left\lfloor\frac{h+1}{2}\right\rfloor.
$$

Otherwise:

$$
c(h)=1+\left\lfloor\frac{h}{2}\right\rfloor.
$$

Thus matching first directions pair half-cycles (1,2), (3,4), and so on. With a mismatch, half-cycle 1 is an incomplete cycle 1; (2,3) form cycle 2. A final unpaired half-cycle can also be incomplete. Cycle 0 is reserved for leading rest.

A reported cycle number is a deterministic pairing label. It is not an independent conclusion about whether the experimental protocol finished, and it is not automatically an instrument-provided cycle number. Charge → Discharge is the GUI base order.

## 5 Classification of Rest CC CV and Other

Classification proceeds within uninterrupted non-rest regions of a single half-cycle, in acquisition order. Stored step codes are 0 Rest, 1 CC, 2 CV and 3 Other.

### 5.1 Constant current

For a candidate segment with n samples and arithmetic mean current, CC requires:

$$
\bar I=\frac{1}{n}\sum_{i=1}^{n}I_i,\qquad \max(I_i)-\min(I_i)\leq r\,|\bar I|.
$$

The first three unassigned points are tested. When CC is found, the segment grows until the range/mean test over the entire growing segment would fail. The first violating point remains available for a new search. If no three-point start qualifies, the search advances by one sample; unassigned samples become Other.

The GUI tolerance is a percentage, converted to a fraction in the request: 1% becomes 0.01. CC uses arithmetic sample means, not a time-weighted average of each individual current observation.

### 5.2 Constant voltage

The voltage test on a three-sample candidate is:

$$
\max(V_i)-\min(V_i)\leq\delta V.
$$

Voltage stability alone is insufficient. With a preceding CC segment of mean current Iref, every current in the three-point CV candidate must lie outside the CC tolerance band around that reference:

$$
|I_i-I_{\mathrm{ref}}|>r\,|I_{\mathrm{ref}}|\quad\mathrm{for\ each\ candidate\ point}.
$$

Without a preceding CC reference, the candidate currents must fail the CC stability test. CC has priority when both starts might otherwise qualify. Once CV is confirmed, it is latched through the rest of that uninterrupted non-rest region. Therefore an apparent CC → CV → CC sequence without a rest is classified CC → CV → CV.

The GUI base CV tolerance is 1 mV, converted to 0.001 V. CC and CV both require at least three consecutive samples. C-rate, DCIR and dQ/dV use these stored CC labels rather than inventing independent CC definitions.

## 6 Capacity and energy integration

### 6.1 Continuous definitions

The signed charge transported over time, expressed in Ah, is:

$$
Q(t)=\frac{1}{3600}\int_{t_0}^{t} I(\tau)\,\mathrm{d}\tau.
$$

Electrical energy in Wh is the integral of terminal power:

$$
E(t)=\frac{1}{3600}\int_{t_0}^{t} V(\tau)I(\tau)\,\mathrm{d}\tau.
$$

The factor 1/3600 converts seconds to hours. These definitions do not specify how the unmeasured waveform behaves between samples; that is the numerical approximation described next.

### 6.2 Implemented interval rule without a current sign crossing

For adjacent retained samples, define Δt as their positive elapsed-time difference. The engine applies the trapezoidal rule to current and to endpoint power:

$$
\Delta Q_i=\frac{(t_{i+1}-t_i)(I_i+I_{i+1})}{7200}.
$$

$$
\Delta E_i=\frac{(t_{i+1}-t_i)(V_iI_i+V_{i+1}I_{i+1})}{7200}.
$$

The capacity rule corresponds to linear current between the two endpoints. The energy rule trapezoidally integrates the endpoint products V×I; it is not an exact analytical integration of the product of independently interpolated linear voltage and current.

Positive signed contributions enter charge accumulators; negative contributions enter discharge accumulators with positive magnitude. The engine stores four directional cumulative columns, reset at every half-cycle: charge capacity, discharge capacity, charge energy and discharge energy. Net quantities are their directional differences.

Measured current is integrated at Rest, CC, CV and Other points. Segmentation thresholds do not zero the input and short sign excursions are not discarded from the integrals.

### 6.3 Implemented rule when current crosses zero

If the two endpoint currents have opposite signs, the engine locates the fractional crossing:

$$
\alpha=\frac{|I_i|}{|I_i|+|I_{i+1}|},\qquad \Delta t_{\mathrm{L}}=\alpha\Delta t,\qquad \Delta t_{\mathrm{R}}=(1-\alpha)\Delta t.
$$

It assigns the two triangular capacity magnitudes to the respective endpoint signs:

$$
\Delta Q_{\mathrm{L}}=\frac{\alpha\Delta t\,|I_i|}{7200},\qquad \Delta Q_{\mathrm{R}}=\frac{(1-\alpha)\Delta t\,|I_{i+1}|}{7200}.
$$

The corresponding energy magnitudes are endpoint-power triangles:

$$
\Delta E_{\mathrm{L}}=\frac{\alpha\Delta t\,|V_iI_i|}{7200},\qquad \Delta E_{\mathrm{R}}=\frac{(1-\alpha)\Delta t\,|V_{i+1}I_{i+1}|}{7200}.
$$

No synthetic zero-crossing row is inserted. When the crossing is also a half-cycle boundary, only the right contribution belongs to the first real point of the new half-cycle. The left contribution is retained in the previous half-cycle's compact final totals. This is why a phase total must be obtained from the stored half-cycle totals rather than reconstructed casually from the last visible cumulative point alone.

A half-cycle boundary without a sign crossing is handled by the reset/grouping rule: the interval increment is assigned to the new half-cycle. In particular, rest-to-active transitions can give a nonzero cumulative value at the first active point.

### 6.4 Negative voltage and precision

If any measured voltage in a half-cycle is negative, the energy of that entire half-cycle is unavailable. Capacity remains calculable. This is an engine rule and should be considered before applying these cell-voltage methods to measurements of another physical meaning.

Stored floating-point quantities use float64. Streaming processing carries prior-sample and half-cycle state across blocks; resetting a batch is not a physical reset. Intermediate accumulations can use extended NumPy precision where implemented, whose benefit depends on the platform. No bitwise cross-platform guarantee or hard process-memory guarantee follows from that fact.

### 6.5 Worked integration examples

At 0, 10 and 30 s, measured currents of 1, 1 and 2 A give interval capacities 10/3600 Ah and 60/7200 Ah, totaling 0.011111111… Ah. At constant 3.6 V, the corresponding energy is 0.04 Wh. Unequal time spacing is explicitly used; the engine does not multiply an average current by a guessed constant sampling period.

For a 10 s interval from +2 A to −1 A, α is 2/3. The positive triangle contributes 0.00185185185… Ah and the negative triangle contributes 0.000462962963… Ah. They are retained separately before the phase net is formed. Treating the whole interval as one signed trapezoid would lose that directional distinction.

## 7 Per-cycle Capacity and efficiency

### 7.1 Phase totals

Within a charge-labelled half-cycle, the reported phase capacity is the final positive directional capacity minus the final negative directional capacity. The sign is reversed for a discharge-labelled half-cycle:

$$
Q_{\mathrm{charge}}=Q_{+}^{(h)}-Q_{-}^{(h)},\qquad Q_{\mathrm{discharge}}=Q_{-}^{(h)}-Q_{+}^{(h)}.
$$

Energy uses the analogous directional difference. The cycle's designated phases are taken from the half-cycle indexes and their compact final totals, including the boundary contribution above. No CC-only filter is applied to Capacity.

Missing phase is null; a present phase with a zero net is numerical zero. An unusual signed net is not clipped into a physically convenient range. A brief opposite-sign excursion that did not form a new half-cycle can reduce the net capacity of that phase.

### 7.2 Efficiency ratios

For Charge → Discharge order:

$$
\eta_Q=100\frac{Q_{\mathrm{discharge}}}{Q_{\mathrm{charge}}},\qquad \eta_E=100\frac{E_{\mathrm{discharge}}}{E_{\mathrm{charge}}}.
$$

For Discharge → Charge order, charge is divided by discharge instead. A missing phase, unavailable energy or exactly zero denominator makes the corresponding result null. A computable efficiency below 0% or above 100% is reported as computed, not clipped to [0,100].

Cycle 0 reports its directional rest contributions, normally zero at the base threshold, and has unavailable efficiencies and C-rates. An incomplete cycle can still have valid values for its present phase.

## 8 Mass and area normalization

The physical definitions are quantity divided by active mass or electrode area. With the request's mass m in mg, area A in cm², Q in Ah and E in Wh, the implementation uses:

$$
Q_{\mathrm{specific}}=\frac{10^6Q}{m}\;[\mathrm{mAh/g}],\qquad Q_{\mathrm{areal}}=\frac{10^3Q}{A}\;[\mathrm{mAh/cm^2}].
$$

$$
E_{\mathrm{specific}}=\frac{10^6E}{m}\;[\mathrm{Wh/kg}],\qquad E_{\mathrm{areal}}=\frac{10^3E}{A}\;[\mathrm{mWh/cm^2}].
$$

For example, 0.001 Ah with 5 mg gives 200 mAh/g. A positive finite normalization basis must be supplied by the user; its scientific suitability is not inferred from a filename or instrument channel.

The core uses exact rational arithmetic for these transformations of the supplied finite float values and converts the final result back to float64. This avoids avoidable intermediate overflow but cannot represent a final value outside float64 range. Such a final cell becomes null with `NUMERIC_RESULT_UNREPRESENTABLE`. A missing basis causes its own normalization diagnostic; independent absolute quantities are retained.

## 9 C-rate

The conventional rate is current divided by a reference capacity:

$$
\mathcal{C}=\frac{|I_{\mathrm{CC}}|}{Q_{\mathrm{ref}}}.
$$

With A divided by Ah, the dimension is h⁻¹, conventionally displayed as C. In the implemented definition, Qref is the net capacity of the second phase of the selected Reference Cycle, whose GUI base value is 2. It is the discharge phase for Charge → Discharge order and the charge phase for the reverse order. There is no fallback to another cycle. A missing or nonpositive reference makes all affected C-rates unavailable.

For each CC segment, first take the arithmetic sample mean and its first-to-last-sample duration. Combine segment means using duration weights:

$$
\bar I_k=\frac{1}{n_k}\sum I_{k,i},\qquad D_k=t_{k,\mathrm{last}}-t_{k,\mathrm{first}},\qquad I_{\mathrm{CC}}=\frac{\sum_kD_k\bar I_k}{\sum_kD_k}.
$$

This is not a current average over every sample in the whole phase and not an integral-based rate including CV. A phase without usable CC segments has null C-rate. One ampere for 10 min and 0.5 A for 20 min give a weighted current of 2/3 A; with a 2 Ah reference, the rate is 1/3 C.

## 10 DCIR detection

A candidate is a CC segment of the current elaboration whose polarity and nominal duration/rest requirements match the request. For nominal T and tolerance p percent, the inclusive duration band is:

$$
T(1-p/100)\leq D\leq T(1+p/100).
$$

The GUI starts with a 30 s pulse, 600 s Rest Before and 600 s Rest After, each with 20% tolerance, and Both polarities. These GUI values are deliberately distinct from examples or defaults elsewhere that may use another protocol. A nominal rest of zero disables that specific rest check.

For detection, segment duration runs from its first sample to the first sample of the next segment. A final rest can end at the last available sample. A CC segment at the end of the record cannot supply the required following boundary and is ineligible. This detection duration differs from the first-to-last-sample duration used for C-rate weighting. One rest can support two neighbouring candidate pulses. There is no extra hidden current-jump or pulse-separation threshold.

Detection saves Pending candidates. Explicit user review makes an event Accepted or Rejected. Redetection preserves Accepted events and avoids duplicating them. A candidate without a preceding real reference sample cannot be accepted for automatic measurement.

## 11 DCIR measurement and relative SOC

### 11.1 Physical definition and sign

The finite-step resistance estimate is the magnitude of the voltage change divided by the current change:

$$
\Delta V=V_1-V_0,\qquad\Delta I=I_1-I_0,\qquad R=\left|\frac{\Delta V}{\Delta I}\right|.
$$

The core retains signed ΔV and ΔI but reports R as a nonnegative magnitude in Ω. This is a protocol- and sampling-dependent DCIR value, not a universal separation of ohmic, charge-transfer and diffusion resistances.

### 11.2 Actual automatic sample selection

V0 and I0 are the voltage/current of the real sample immediately preceding the pulse. Its time is the common origin. The default I1 is the third real pulse sample. Alternatively, an explicit current time selects the nearest real pulse sample to that time after V0. Voltage V1 is the real pulse sample nearest the requested voltage time after V0: 30 s by default in the GUI (the engine's own default is 10 s).

$$
i_{\mathrm{V1}}=\mathrm{argmin}_{i\ \mathrm{in\ pulse}}\left|t_i-(t_{\mathrm{V0}}+\tau_{\mathrm{requested}})\right|.
$$

A tie selects the later sample. If the requested current or voltage time exceeds the last observed pulse time relative to V0, the last sample is used with `DCIR_TIME_AFTER_LAST_SAMPLE`. Neither signal is interpolated. The engine records actual times and sample provenance and authenticates them against the elaboration when saving the result.

For pulse samples at +9 and +11 s relative to V0, a +10 s request selects +11 s. With 1 s acquisition, the first pulse sample is normally at +1 s, not at zero. A pulse whose last sample is at +30 s can serve a 30 s request without the beyond-last-sample warning.

An exactly zero ΔI produces a null resistance with `DCIR_ZERO_CURRENT_DIFFERENCE`, even for an Accepted event. Stable rational arithmetic prevents avoidable intermediate overflow; an unrepresentable final resistance is unavailable. No tolerance substitutes an arbitrary small current denominator.

Example: I0 = 0 A, V0 = 3.700 V, I1 = −1 A and V1 = 3.650 V give ΔV = −0.050 V, ΔI = −1 A and R = 0.050 Ω = 50 mΩ. The current and voltage sampling instants still need to be reported alongside that number.

### 11.3 Manual measurement

The same equation is used for four explicitly typed or sample-linked values. Typed entries are retained as typed inputs; a sample-linked entry is checked against its real row rather than a visually interpolated chart position. Two optional real boundary samples supply correction bounds. A resistance without bounds can remain valid while being ineligible for cycle correction.

### 11.4 Capacity-based relative SOC

A general physical SOC is stored charge divided by a defined usable charge capacity. The implemented optional estimate is more specific: when a pulse is its own half-cycle between two unambiguous same-direction half-cycles, let their directional capacities be Q1 and Q2 and the pulse capacity Qp. The engine forms:

$$
f=\frac{Q_1}{Q_1+Q_2-Q_p}.
$$

For charge-direction flanks it reports SOC = 100f; for discharge-direction flanks it reports SOC = 100(1−f). It derives this context from the actual half-cycle/segment indexes. Ambiguous topology, missing values or an unusable denominator make SOC null with `DCIR_SOC_UNAVAILABLE`, independently of resistance.

For Q1 = 0.40 Ah, Q2 = 0.65 Ah and Qp = 0.05 Ah, f = 0.40. The respective reported values are 40% or 60%, depending on flank direction. This is a relative capacity estimate, not an instrument-calibrated absolute SOC. The formula is not a coulomb counter initialized from a known battery SOC, and the implementation does not clamp its mathematical result to [0,100].

## 12 Cycle correction

An opposite-sign DCIR pulse long enough to satisfy the three-point rule can create an unwanted extra half-cycle. Correction is an explicit new interpretation of cycle labels using authenticated Accepted-event spans.

1. Automatic events supply their pulse row range. Manual events need two valid sample-linked boundary rows. Pending, Rejected and unbounded events do not contribute a span.
2. Inclusive overlapping or adjacent spans are merged.
3. Half-cycle segmentation is rerun on rows outside those spans with the selected rest threshold.
4. Retained rows inside each span are assigned neighbouring labels; when neighbouring directions differ, the core determines the split from the first point matching the following direction.
5. The full point record is retained. Cumulative quantities and Capacity are recomputed for the resulting half-cycles. Pulse charge is still counted.
6. Step classification is not rerun. DCIR is not redetected and stored resistance values are retained; cycle context and applicable SOC are refreshed.
7. Existing dQ/dV rows and voltage-reconstruction rows can have cycle/half-cycle labels mapped through the curve anchor. Their derivative/reconstruction numbers are not recomputed by this relabelling.

Therefore correction is neither deletion of pulse measurements nor reprocessing all analysis stages. A relabelled dQ/dV table is not a new differential analysis with changed smoothing or binning. The GUI propagates the composed generation and preserves the correction lineage. Remove Correction saves an uncorrected (enabled = false) interpretation; it does not erase the history or automatically permit a new DCIR detection as though the generation were original.

## 13 Differential capacity

### 13.1 Continuous definition and eligible measurements

Differential capacity is:

$$
g(V)=\frac{\mathrm{d}Q}{\mathrm{d}V}.
$$

The engine processes explicitly selected positive cycles and their charge/discharge half-cycles. It keeps CC points outside Accepted DCIR spans of the matching saved DCIR result. Charge curves use cumulative charge capacity; discharge curves use cumulative discharge capacity. No mass/area normalization is added. The voltage window applies to every method.

Values are in Ah/V. The GUI can scale the capacity numerator to mAh, but the denominator stays V even when the horizontal axis is displayed in mV. Derivatives retain their mathematical sign. Falling voltage with increasing discharge capacity normally produces negative dQ/dV.

The current installed engine exposes three methods: interpolated_gradient, histogram and histogram_weighted. It produces one piece per curve, with segment_index = 0. It does not promise to preserve every non-monotonic branch as a separately differentiated piece. Too little eligible data yields `DQDV_NO_VALID_SPAN`, not a fabricated zero curve.

### 13.2 Interpolated gradient

Theoretical numerical idea: approximate Q as a function of voltage on a regular grid, then take a finite difference of that approximation.

Implementation sequence:

1. With a nonzero Edge Trim, keep only CC voltages strictly more than that distance from both eligible voltage extremes. With trim 0, retain them all at this stage.
2. Choose the grid count from the number M of retained points after trimming. Percentage mode uses Python round(pM/100), including its ties-to-even rule; count mode uses the specified count. Clamp to 3 through the core limit 5000. The GUI does not expose the 5000 cap as a separate control.
3. Remove non-finite curve values, stable-sort by voltage and retain the first occurrence of each equal voltage.
4. Intersect the measured voltage domain with E min/E max and build an evenly spaced grid including its endpoints.
5. Use `numpy.interp` for piecewise-linear Q(V), then `numpy.gradient` with voltage coordinates. Interior differences are second-order central differences; the default edge differences are first-order one-sided.

For a uniform grid, the interior expression is:

$$
g_j\simeq\frac{\widehat Q_{j+1}-\widehat Q_{j-1}}{V_{j+1}-V_{j-1}}.
$$

The first edge uses (Q1−Q0)/(V1−V0), and the last edge the analogous backward difference. Values live at the grid nodes, despite the table's legacy `voltage_mid_v` name.

Sorting folds non-monotonic observations onto a single voltage axis. It can change the interpretation of hysteretic or locally reversing paths. The method should be interpreted as a representation of essentially monotonic half-cycles, not as proof that every path has a unique Q(V). There is no additional fourth finite-difference menu method in this build; the finite differences above are this method's derivative step.

### 13.3 Unweighted voltage histogram

When equal measurement counts correspond approximately to equal capacity increments, the probability density of observed voltages, scaled by total measured capacity range, estimates dQ/dV.

With n eligible points, c_j counts in bin j, actual width w and eligible capacity range ΔQ:

$$
\rho_j=\frac{c_j}{nw},\qquad g_j=s\,\rho_j\,\Delta Q,\qquad \Delta Q=\max(q_i)-\min(q_i).
$$

The implementation sets the bin count to floor((Vmax−Vmin)/requested_bin × (1+10⁻¹²)). It requires at least one bin, divides the measured voltage range into that many equal bins, and uses the resulting actual width w. Thus actual bin width can differ slightly from the requested mV value. The last upper edge is inclusive.

The sign s is +1 when the last eligible voltage exceeds the first and −1 otherwise. When endpoints are equal, phase direction supplies the sign. Values are at bin centres. Empty bins legitimately contain zero by the histogram definition. That is different from an unavailable entire curve or a null result cell.

The assumptions are important: unequal logging intervals, changing current or a removed middle span can make equal point counts correspond to unequal capacity. The unweighted method does not automatically compensate for those effects.

### 13.4 Capacity-weighted histogram

Instead of counting every observation equally, each truly adjacent eligible interval contributes its measured cumulative-capacity increment to the bin containing its midpoint voltage:

$$
g_j=\frac{s}{w}\sum_{i\in B_j}(q_{i+1}-q_i),\qquad V_{i,\mathrm{mid}}=\frac{V_i+V_{i+1}}{2}.
$$

Adjacency is checked using canonical source-row positions. If filtering removed non-CC points, a DCIR span or window-excluded points between two remaining measurements, that broken interval contributes zero weight rather than bridging the gap. The code uses the signed cumulative-capacity difference as the weight, not an invented constant Δt.

This weighting accommodates unequal capacities per measurement interval. It still depends on eligible data, bin width and the midpoint allocation rule. It does not restore measurements absent from the source or reconstruct capacity through an excluded pulse interval.

### 13.5 Savitzky–Golay and Gaussian smoothing

For the classic method, Savitzky–Golay fits a polynomial within a moving window and evaluates it at the target point. In least-squares notation:

$$
\widehat a=\mathop{\mathrm{argmin}}_a\sum_{k\in W}(g_k-p_a(x_k))^2,\qquad \widetilde g_j=p_{\widehat a}(x_j).
$$

The NumPy implementation uses a Legendre polynomial basis on a scaled window, an odd window length and polynomial order below that length. At edges it uses the nearest complete window. If the curve is shorter than the window, it returns a copy of the raw values; a requested smooth column can therefore equal raw numerically.

Histogram smoothing convolves the bin values with a normalized Gaussian kernel:

$$
K_k=\frac{\exp(-k^2/(2\sigma^2))}{\sum_{r=-L}^{L}\exp(-r^2/(2\sigma^2))},\qquad \widetilde g_j=\sum_{k=-L}^{L}K_k g_{j-k}.
$$

Sigma is measured in bins. The radius is int(4σ+0.5), and edges are extended symmetrically including the endpoint. The engine uses NumPy, not a GUI filter. The raw derivative/histogram remains stored beside the smoothed values. Selecting Raw in the GUI changes presentation; it does not change a reconstruction that the core already calculated from the smoothed curve.

## 14 Voltage reconstruction and numerical quality measures

### 14.1 Continuous relation and discrete integration

The inverse consistency check starts from the integral of the derivative:

$$
q(V)=q_0+\int_{V_0}^{V}g(u)\,\mathrm{d}u.
$$

The core uses the stored curve's smoothed values when present, otherwise raw values. Along ascending voltage coordinates it forms a cumulative trapezoid:

$$
F_j=F_{j-1}+\frac{(V_j-V_{j-1})(g_j+g_{j-1})}{2},\qquad F_0=0.
$$

The starting capacity q0 is the minimum eligible measured cumulative capacity. If the total integral is nonnegative, reconstructed capacity is q0+F and the curve runs from low to high voltage. For a negative integral, the core offsets by its final integral and reverses order so the curve starts at the high-voltage end and cumulative capacity grows in measurement order.

The reconstruction table records measured and reconstructed curves. The reference measurements are those eligible for the method, including voltage-window and classic edge-trim restrictions. They are not necessarily the entire phase used by Capacity.

### 14.2 Absolute percentage error of capacity range

Let ΔQm and ΔQr be max-minus-min measured and reconstructed capacity ranges:

$$
\mathrm{APE}=100\frac{|\Delta Q_{\mathrm{m}}-\Delta Q_{\mathrm{r}}|}{\Delta Q_{\mathrm{m}}}.
$$

A nonpositive measured range makes the metric unavailable. APE concerns the total eligible capacity range, not a pointwise relative voltage error.

### 14.3 Voltage root mean square error

At reconstructed capacities inside the common capacity interval, interpolate the measured voltage curve linearly and calculate:

$$
\mathrm{RMSE}_V=\sqrt{\frac{1}{N}\sum_{j=1}^{N}\left[V_{\mathrm{measured,interp}}(q_j)-V_{\mathrm{reconstructed}}(q_j)\right]^2}.
$$

The average weights these comparison points equally. It is not weighted by experimental time. No overlap gives an unavailable RMSE; missing metrics carry `DQDV_RECONSTRUCTION_UNAVAILABLE`. GUI RMSE is often displayed in mV for readability, while the core records V.

Low APE and RMSE demonstrate agreement under this particular reconstruction test. They do not validate a battery chemistry, the calibration of the instrument, or the correctness of the selected cycle boundaries.

### 14.4 Why a correct histogram can have nonzero reconstruction error

For a linear measured curve V = 3 + q/2 with q from 0 to 2 Ah, the exact derivative is 2 Ah/V. With weighted histogram bins of 1/64 V, integrating only between bin centres misses half a bin at each end. The reconstructed capacity range is 1.96875 Ah, giving APE = 1.5625%; the voltage offset gives RMSE = 1/128 V = 7.8125 mV. Nonzero metrics can therefore include a discretization/end-coordinate effect rather than only measurement noise.

## 15 GITT and ICI diffusion coefficients

The GUI delegates event association, OCV products, square-root-time fits, the four diffusion methods, quality indicators and review transitions to gregorithm. It reads only the current canonical elaboration generation and never reimports, deduplicates, resegments or decimates samples. The protocol, GITT or ICI, is chosen by the operator; it is never inferred from pulse durations.

### 15.1 Inputs, units and symbols

V_act is the active-phase volume and A_eff the effective diffusion area; r_p is the particle radius used by the spherical Delacourt fit and by physically guided windows. They are entered directly with their units, converted to SI by gregorithm, constant for every impulse of the analysis and never inferred. Without V_act and A_eff, GITT VA, GITT Δ and ICI keep their intermediates but D is unavailable with a reason; Delacourt needs only r_p. D is stored in m²/s and shown in cm²/s by default.

τ is the duration of the constant-current pulse measured from the time origin, ΔU the OCV step across the pulse for the chosen OCV source f, and E the voltage under current. Each OCV source, sampled, extrapolated or ICI pseudo-OCV, produces its own result series; sources are never mixed between the two ends of a pair.

### 15.2 GITT VA

The pulse voltage is regressed on the square root of the time from the last sample of the rest before the pulse, inside one window in seconds:

$$
D_{VA}=\frac{4}{\pi}\left(\frac{V_\mathrm{act}}{A_\mathrm{eff}}\right)^2\left[\frac{\Delta U^{(f)}}{\tau\,(dE/d\sqrt{t})_\mathrm{pulse}}\right]^2
$$

A constant current is required. The window is suggested from the real segment duration and can be edited; the first automatic pass searches statistically inside it and prefers the longest adequate window with at least five distinct times.

**Derivation.** GITT VA is the last of three algebraically equivalent forms; only GITT VA is offered, because it needs no stoichiometry. The classic composition form, with molar volume V_m, current I, charge number z, Faraday constant F and composition step Δx caused by the charge q passed in a material of mass m and molar mass M, is

$$
D_X=\frac{4}{\pi}\left[\frac{V_m I}{zFA_\mathrm{eff}}\,\frac{\Delta U^{(f)}/\Delta x}{(dE/d\sqrt{t})_\mathrm{pulse}}\right]^2,\qquad \Delta x=\frac{qM}{zFm}
$$

With consistent molar and volumetric bases, Δx = V_m Δc, so the same result is written with the concentration step Δc of the active volume:

$$
D_C=\frac{4}{\pi}\left[\frac{I}{zFA_\mathrm{eff}}\,\frac{\Delta U^{(f)}/\Delta c}{(dE/d\sqrt{t})_\mathrm{pulse}}\right]^2,\qquad \Delta c=\frac{q}{zFV_\mathrm{act}}
$$

At constant current q = Iτ, so I/Δc = zF·V_act/τ and z, F and I cancel: D_C becomes the GITT VA expression above, which needs only V_act, A_eff, τ, ΔU and the √t slope.

### 15.3 GITT Δ

GITT Δ uses samples instead of a regression and therefore has no square-root-time window or linearity indicator:

$$
D_\Delta=\frac{4}{\pi\tau}\left(\frac{V_\mathrm{act}}{A_\mathrm{eff}}\right)^2\left(\frac{\Delta U^{(f)}}{\Delta E_\tau}\right)^2
$$

$$
\Delta E_\tau=E_\mathrm{end}-V_0-c_\mathrm{IR}
$$

V_0 is the last canonical sample of the rest before the pulse and E_end the last sample under current. The constant IR correction c_IR is zero by default; when another mode is chosen it is applied once, and the detail chart can show V − c next to the raw voltage. It coincides with GITT VA only in ideal conditions.

### 15.4 ICI

The rest after an interruption is regressed on the square root of the time from the last sample under current:

$$
E_\mathrm{rest}(t)=a_\mathrm{off}+s_\mathrm{off}\sqrt{t}
$$

$$
D_\mathrm{ICI}=\frac{4}{\pi}\left(\frac{V_\mathrm{act}}{A_\mathrm{eff}}\right)^2\left[\frac{\Delta U_j^{(f)}}{\tau_{\mathrm{on},j}\,s_{\mathrm{off},j}}\right]^2
$$

With the default pseudo-OCV, U_j is the intercept a_off of interruption j and the pair is the previous interruption in the same branch; τ_on,j is the time under current between the two references. The first interruption of a branch has no previous pseudo-OCV, so its D is unavailable with the reason shown in the list. With the sampled or extrapolated OCV the pair is the same pulse and τ_on is its duration. ICI may also be added to a GITT analysis.

### 15.5 Delacourt analytical spherical fit

The whole constant-current pulse is fitted with the analytical solution for a sphere; there is no sub-window to optimise:

$$
V(t)=R_A i_n+\frac{U_0[y_1-y_s(t)]+U_1[y_s(t)-y_0]}{y_1-y_0}
$$

$$
y_s(t)=y_0-\frac{i_n r_p}{F D c_\mathrm{max}}\,f(Dt/r_p^2)
$$

$$
f(\theta)=3\theta+\frac{1}{5}-2\sum_{j\geq 1}\frac{e^{-\lambda_j^2\theta}}{\lambda_j^2},\qquad \tan\lambda_j=\lambda_j
$$

SparkBox uses the fixed offset, taken from the measured initial voltage jump. gregorithm also implements a free-offset variant, which the GUI does not offer. Delacourt is available only with the GITT protocol: the analytical model needs a relaxed, uniform state at the start of the pulse, which short ICI interruptions do not provide. Its indicators describe fit fidelity and identifiability of D, not square-root-time linearity. The fixed offset has a known sampling-dependent bias that remains documented.

### 15.6 OCV sources and targets

The sampled OCV is the last real sample of each rest, without averaging, or the exact sample chosen for that rest. The extrapolated OCV fits E(t) = α + β ln(t / 1 s) on the rest time after the fit start fraction (0.2 by default, the last 80 %) and evaluates it at a target time. Sampled and extrapolated OCV are separate products of the same rest: one never replaces the other. Each OCV method selected in Setup gives every compatible method its own D series. The target is global, 10 800 s by default, and can be overridden for a single rest; only results that depend on that rest become stale. A target beyond the observed rest is allowed and reported with the ratio target / observed duration, because the logarithm has no finite equilibrium limit.

### 15.7 Windows, indicators and review

Windows are manual or automatic. Opening an impulse enters manual mode and does not start a fit; a window dragged on V(t) or V(√t) is recalculated only when released or applied. Re-applying the automatic windows to one impulse recalculates every checked method of that impulse; Calculate all impulses recalculates every impulse in scope and drops manual windows. Physically guided windows limit the search to a time derived from r_p and one D source chosen for the whole run, which may be a proposed result; the coefficient starts at 0.0032, a theoretical literature value (Chien, supplementary material) valid only under specific assumptions, and is editable.

The statistical indicator combines the relative linearity q_L and slope stability q_S; their limits, 2 %/5 % and 5 %/15 %, are provisional. The physical indicator is N/D when r_p or a guiding D is missing, which is not a failure. Colours are advisory: they never filter the list, block acceptance or change data. A recalculated result replaces the previous one of the same impulse, method, variant and OCV source; a recalculation that fails leaves an error record instead of the old D; a cancelled run publishes nothing and keeps the previous results.

Acceptance is given to the intermediate results, not to each D. A rest period is accepted with the exact OCV values shown (sampled, extrapolated when active, pseudo-OCV when present); a pulse is accepted with the exact fits of its methods (GITT VA pulse window, ICI interruption window, Delacourt fit, and GITT Δ only when its IR correction is the GITT fit intercept). Each acceptance is stored with a fingerprint of those values; if a later change alters them, the acceptance no longer counts until it is given again. A D result is used, and accepted in gregorithm, only when it is current, has a finite D, its pulse is accepted when its method needs a fit, and every rest whose OCV it uses is accepted: both rests of the pulse for sampled and extrapolated OCV, the two interruptions for the ICI pseudo-OCV. An unchanged recalculation keeps the acceptance.

### 15.8 Results chart and export

Only D results in use (section 15.7) are drawn and exported. Each D is placed at the canonical capacity at the start of its constant-current pulse; specific capacity divides that capacity by the active material mass of the sample. The SoC alternative is p = 100 · Q / Q_end with Q_end the branch capacity at the last sample of the half-cycle, and 100 − p in discharge; it is unavailable when Q_end is zero. OCV products are placed at the capacity of the state they represent: the rest before a pulse at its start, the rest after at its end, and the ICI pseudo-OCV at the end of the pulse that precedes the interruption. Connecting OCV points is a visual aid, not a thermodynamic model. A logarithmic D axis omits values that are not finite and positive instead of shifting them.

The export publishes the current snapshot as an immutable gregorithm generation and writes one row per complete impulse (charge and discharge in separate files by default), with one D column per series in use; only accepted, current D values are written and other cells stay empty. CSV and Parquet share the same columns. An empty export is refused before any file is created.

### 15.9 Pre-pulse and post-pulse IR drops

An IR drop pairs two real samples; nothing is fitted or interpolated. Pre-pulse: V₀, I₀ is the last sample of the rest before the pulse and V₁, I₁ the last pulse sample with t ≤ T_on after it. Post-pulse: V₋, I₋ is the last sample under current and Vᵣ, Iᵣ the last sample of the following rest with t ≤ T_off. ΔV and ΔI keep their sign and R = |ΔV / ΔI|; ΔI = 0, a missing rest or no sample within the time leaves R unavailable with its reason. T_on is 1 s by default and T_off equals T_on unless set separately. A manual choice keeps the exact row of a real sample of the same segment.

The reference is the last sample before the switch, not the exact switching instant, and a sample at 1 s may already contain diffusion and charge-transfer response: R is a sampled resistance, not a purely ohmic one. Measuring R never corrects a voltage. The selected-sample IR correction (the Delacourt fixed-offset default, and GITT Δ when chosen) uses the same pre-pulse sample, so a manual pre-pulse sample makes exactly those results stale and recalculates them. R_off feeds no D result.

## 16 Plotting point selection and Comparison

Interactive Data Explorer requests real rows from the stored point table using the core window API and a point budget, with stored real-point overviews where appropriate. The GUI adapter counts distinct canonical source rows, not separate copies of a row for Y1 and Y2. With a non-time X range, region filtering precedes point selection. Lines between plotted vertices are a rendering convention and do not add recorded samples.

Render All Points streams all eligible points for the selected dataset/filter/region and draws the full data layer. Image export uses at least 300 dpi rendering. Neither sampling nor DPI changes any Capacity, resistance or derivative result.

Comparison overlays only already-calculated Data Explorer, Capacity or dQ/dV results. It uses one Y and common app units, original cycle IDs and explicit availability/Outdated status. It does not calculate cross-dataset differences, ratios, averages, cycle matching or interpolation between datasets. Any interpolation performed within the core dQ/dV method or reconstruction metric is a distinct scientific operation, not a Comparison operation.

## 17 Stored tables diagnostics and provenance

| Stored table | Scientific contents |
|---|---|
| Canonical points | Retained time/current/voltage in s/A/V |
| Elaborated points and indexes | Labels, step codes, directional cumulatives, half-cycle totals and segment boundaries |
| Capacity table | 17 per-cycle fields including efficiency, normalization and C-rate |
| DCIR event and measurement tables | Decisions, actual sample references, time choices, signed changes, resistance and optional SOC |
| dQ/dV table | cycle, half_cycle, phase, anchor_source_row, segment_index, voltage_mid_v, dqdv_raw_ah_per_v, dqdv_smoothed_ah_per_v |
| Reconstruction table and report | Measured/reconstructed curves, APE/RMSE and unavailable reasons |
| GITT/ICI analysis | Impulses, IR drops, OCV products, fits, D series with their OCV source, acceptances and the exported snapshot |

A null value denotes unavailable science or an unrepresentable result. Zero is retained only where it is a calculable value, including an empty histogram bin. Diagnostics attach a stable cause to a field or curve, while independent fields can remain available.

Each successful scientific operation saves a new immutable result (a *generation* in gregorithm's terms) with parameters, dependency fingerprints, engine version and file checksums. A failed or cancelled job must not replace the previous result. Progress reaching 100% does not itself prove that the result has been saved.

Changing colours, viewport or display units does not change the scientific fingerprint. Changing scientific parameters requires an explicit new operation. An embedded corrected dQ/dV table has correction lineage and relabelled indices; it must not be advertised as a new derivative calculation.

CSV/Parquet exports contain data at full precision in the selected display units and omit internal store sidecars. Keep version, parameters, fingerprints, diagnostics and export checksums separately for reproducibility. Neither checksum integrity nor software regression agreement constitutes independent validation of an experimental instrument or laboratory protocol.

## 18 Source map and method attribution

The definitions above were checked against the installed engine used by the GUI, not inferred solely from interface labels. Relevant implementation locations are:

| Topic | gregorithm implementation |
|---|---|
| Mapping cleaning and unit conversion | importing.py, inspect_source and import_source |
| Half-cycles and steps | elaboration.py, segment_half_cycles, classify_steps, _S3Stream and _S4Stream |
| Interval integration and boundary totals | elaboration.py, _interval_contributions, integrate_cumulatives and _S5Stream |
| Phase Capacity normalization and C-rate | capacity.py, capacity_rows_from_halves and analyze_capacity |
| Real-sample DCIR and SOC | dcir.py, detect_dcir, measure_automatic_from_generation, _soc and publish_dcir_events |
| Corrected labels and composed results | correction.py, accepted_correction_spans and recalculate_cycles |
| Interpolation histograms smoothing and reconstruction | dqdv.py, compute_dqdv, interpolated_gradient, dqdv_histogram, savgol_interp, gaussian_smooth, reconstruct_voltage_curve and reconstruction_metrics |
| GITT/ICI events, IR drops, OCV, windows, fits, diffusion and review | gitt_ici package: events.py, ir_drop.py, ocv.py, windows.py, fits.py, kernels.py, delacourt.py, quality.py, review.py and export.py |
| Immutable storage and data export | storage.py and exporting.py |

Private helper names in this source map identify implementation locations; they are not recommended as stable public APIs. The GUI adapter is `app/gregorithm_backend.py`; settings/profile code does not replace these algorithms.

The installed engine attributes the histogram methods and reconstruction metrics to E. Flores and S. Clark, “Simple and Comparable Computation of Incremental Capacity Curves”, Journal of The Electrochemical Society 173, 120520 (2026), DOI [10.1149/1945-7111/ae7e5c](https://doi.org/10.1149/1945-7111/ae7e5c). This bibliographic identity is the reference declared by the engine. Check the publisher record and journal citation requirements before submitting a paper; the numerical descriptions in this document are verified against the implementation.

Savitzky–Golay denotes the local polynomial least-squares smoothing method associated with A. Savitzky and M. J. E. Golay, Analytical Chemistry 36 (1964), 1627–1639. The implementation described here uses its own NumPy Legendre-basis fit and does not import SciPy code.

When reporting a study, state the integration rule, current sign, segmentation thresholds and order, C-rate reference, DCIR timing/sample rule, dQ/dV method and its grid/bin/filter settings, exclusion/correction status and quality metrics. Those choices are part of the numerical definition of the reported result.
