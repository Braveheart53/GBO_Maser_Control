# MHM2010 Analysis Review

Version 0.1.0. Offline advisory output; no instrument commands were sent.
Use analysis.json for complete precision, assumptions and source hashes.

Channel: MicroSem -> CNS_GPS1. Input: FITS.
Fit: order 2; 9600 samples; RMS 5.405 ns.
Reference epoch: MJD 60977.999763492808 UTC.

## Warnings
- Fit uncertainty is conditional IID-OLS only; GPS, colored noise and systematics are not included.
- Constant phase offset is not corrected by the DDS/drift fields.

## Correction candidate
```json
{
  "application_epoch_mjd_utc": 60992.998201057395,
  "residual_fractional_frequency": 5.838444178725387e-15,
  "residual_frequency_drift_per_day": -6.60833065550308e-16,
  "conditional_frequency_sigma": 2.9491579969249224e-16,
  "conditional_drift_sigma_per_day": 1.9038592212810412e-17,
  "current_external_dds": "5751.6772565",
  "delta_dds_counts_unrounded": 82.92959692308136,
  "new_external_dds": "5751.6772648",
  "quantization_residual_fractional_frequency": -4.956546877e-18,
  "current_drift_field": 975.0,
  "existing_drift_enabled": true,
  "drift_units": "pp18/day",
  "positive_pp18_convention": "output",
  "delta_drift_field": 660.8330655503041,
  "new_drift_field": 1635.8330655503041,
  "new_drift_field_rounded_integer": 1636,
  "both_drift_units": {
    "DDS_units_per_day": {
      "current": -13.848956078853254,
      "increment": -9.38651087206179,
      "new": -23.235466950915043,
      "new_nearest_integer": -23
    },
    "parts_in_1e18_per_day": {
      "current": 975.0000000000001,
      "increment": 660.8330655503042,
      "new": 1635.8330655503044,
      "new_nearest_integer": 1636
    },
    "fractional_drift_residual_if_integer_DDS_per_day": -1.6577442793159332e-17,
    "fractional_drift_residual_if_integer_pp18_per_day": 1.6693444969564553e-19,
    "manufacturer_drift_range": "Not specified in Rev. J; verify installed software limits."
  },
  "fractional_frequency_per_dds_count": -7.040241838074584e-17,
  "phase_sign_multiplier": 1,
  "status": "HISTORICAL / UNREVIEWED. Settings and signs are explicit CLI assumptions, not current recommendations.",
  "operator_confirmed": false,
  "operating_state_reviewed": false
}
```

## Stable32
If present, import raw.PHD as phase seconds or raw.FRD as fractional frequency. Set tau to the value in the sidecar. Fit-residual files are separately labeled. No gaps were filled; actual zeros are represented by 1e-99.

## References

| Ref. | IEEE bibliographic entry | URL |
|---|---|---|
| [1] | Microsemi, MHM-2010 User’s Guide, 75666-201, rev. J, Oct. 2014. Accessed Nov. 3, 2026. | https://www.gb.nrao.edu/~fghigo/timer/MSMmaser/Maser_UG_75666-201_Rev_J.PDF |
| [2] | W. J. Riley, Handbook of Frequency Stability Analysis, NIST SP 1065, Jul. 2008. | https://tf.nist.gov/general/pdf/2220.pdf |
| [3] | W. J. Riley, Stable32 User Manual, ver. 1.54, 2008. Accessed Nov. 3, 2026. | http://www.stable32.com/Manual154.pdf |

Equations and sign derivations: see the supplied bookmarked user manual, Appendix A.
