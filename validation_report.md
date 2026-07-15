# Validation report

Pipeline predictions (`data/predictions.csv`) scored against the hand-labelled gold set (`data/ground_truth.csv`). Only projects present in both files are scored. The gold set is **validation data, never training data** — the zero-shot agent has never seen it.

## Headline

- **Projects scored:** 11
- **Overall accuracy:** 55% (6/11)
- **Macro-averaged F1:** 0.54

## Precision / recall per status

| status | precision | recall | f1 | support |
| --- | --- | --- | --- | --- |
| completed | 1.00 | 1.00 | 1.00 | 1 |
| delayed | 0.00 | 0.00 | 0.00 | 1 |
| on_track | 0.80 | 0.57 | 0.67 | 7 |
| rescoped | 0.50 | 0.50 | 0.50 | 2 |

_Support is the number of gold-set projects with that true status. With a small sample, a single miss moves these numbers a lot — read them as directional, not definitive._

## Confusion matrix

Rows = human truth, columns = pipeline prediction.

| status_true | completed | delayed | on_track | rescoped |
| --- | --- | --- | --- | --- |
| completed | 1 | 0 | 0 | 0 |
| delayed | 0 | 0 | 1 | 0 |
| on_track | 0 | 2 | 4 | 1 |
| rescoped | 0 | 1 | 0 | 1 |

## Accuracy by predicted confidence

Is the pipeline's own confidence calibrated — are `high`-confidence calls actually more often right than `low`-confidence ones?

| confidence_pred | n | accuracy |
| --- | --- | --- |
| high | 11 | 0.55 |

## Per-project agreement

Disagreements (`✗`) are listed first for inspection.

| project_name | truth | predicted | conf | match |
| --- | --- | --- | --- | --- |
| Eden Project North | rescoped | delayed | high | ✗ |
| Liverpool City Council Docks Cultural Regeneration | on_track | delayed | high | ✗ |
| Multiversity | delayed | on_track | high | ✗ |
| Radcliffe (Civic and Enterprise Hub Development) | on_track | delayed | high | ✗ |
| Woodside (Woodside WaterFront Visitor and Gyratory Reconfiguration) | on_track | rescoped | high | ✗ |
| Ashton (Town Centre Regeneration) | on_track | on_track | high | ✓ |
| Barrow-in-Furness Town Centre | rescoped | rescoped | high | ✓ |
| Colne Town Centre (Investment) | completed | completed | high | ✓ |
| Haigh Hall | on_track | on_track | high | ✓ |
| Salford Rise (Innovation Zone) | on_track | on_track | high | ✓ |
| Transforming Ellesmere Port Town Centre | on_track | on_track | high | ✓ |
