"""Step 4 -- validate pipeline predictions against the hand-labelled gold set.

This is the project's honesty check. The ReAct agent (Step 3) produces a status
for every project; here we compare those predictions to ``data/ground_truth.csv``
-- the 11 human-verified labels -- and report how often they agree.

We report:
* overall accuracy on the labelled subset;
* precision / recall / F1 per status (via scikit-learn);
* a confusion matrix (which statuses get mixed up -- e.g. rescoped vs delayed);
* accuracy stratified by the pipeline's own confidence (is ``high`` really more
  reliable than ``low``? i.e. is the confidence signal calibrated?);
* a per-project agreement table, so disagreements are inspectable, not hidden.

Note: ``ground_truth.csv`` is a *validation / gold set*, never training data --
the zero-shot agent has never seen it. Only the projects present in BOTH files
are scored. The report is written to ``validation_report.md`` at the repo root.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
GROUND_TRUTH_CSV = DATA_DIR / "ground_truth.csv"
PREDICTIONS_CSV = DATA_DIR / "predictions.csv"
REPORT_MD = REPO_ROOT / "validation_report.md"

# The six real statuses plus the pipeline-only "unknown" (predictions can be
# unknown; hand labels never are). Kept explicit so metrics list every class.
_GT_STATUSES = ["on_track", "delayed", "stalled", "rescoped", "cancelled", "completed"]
_CONF_ORDER = ["high", "med", "low"]


class ValidationError(RuntimeError):
    """Raised when the inputs needed for validation are missing."""


def _load() -> pd.DataFrame:
    """Join predictions to ground truth on project_name (inner join = scored set)."""
    if not GROUND_TRUTH_CSV.exists():
        raise ValidationError(f"Missing {GROUND_TRUTH_CSV}. Run Step 2 first.")
    if not PREDICTIONS_CSV.exists():
        raise ValidationError(
            f"Missing {PREDICTIONS_CSV}. Run the pipeline first: python -m pipeline.run"
        )

    gt = pd.read_csv(GROUND_TRUTH_CSV).rename(
        columns={"status": "status_true", "confidence": "confidence_true"}
    )
    pred = pd.read_csv(PREDICTIONS_CSV).rename(
        columns={"status": "status_pred", "confidence": "confidence_pred"}
    )

    merged = gt.merge(
        pred[["project_name", "status_pred", "confidence_pred"]],
        on="project_name",
        how="inner",
    )
    if merged.empty:
        raise ValidationError(
            "No overlap between ground_truth.csv and predictions.csv on project_name."
        )
    merged["correct"] = merged["status_true"] == merged["status_pred"]
    return merged


def _per_status_metrics(merged: pd.DataFrame) -> pd.DataFrame:
    """Precision / recall / F1 / support per status, using scikit-learn."""
    from sklearn.metrics import precision_recall_fscore_support

    # Score over every status that appears as a true or predicted label, so a
    # spurious predicted class (e.g. an unexpected "stalled") is still visible.
    labels = sorted(set(merged["status_true"]) | set(merged["status_pred"]))
    precision, recall, f1, support = precision_recall_fscore_support(
        merged["status_true"],
        merged["status_pred"],
        labels=labels,
        zero_division=0,
    )
    return pd.DataFrame(
        {
            "status": labels,
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }
    )


def _confusion(merged: pd.DataFrame) -> pd.DataFrame:
    """Confusion matrix: rows = true status, cols = predicted status."""
    return pd.crosstab(
        merged["status_true"], merged["status_pred"], dropna=False
    )


def _confidence_breakdown(merged: pd.DataFrame) -> pd.DataFrame:
    """Accuracy grouped by the pipeline's predicted confidence (calibration check)."""
    rows = []
    for conf in _CONF_ORDER:
        subset = merged[merged["confidence_pred"] == conf]
        if len(subset):
            rows.append(
                {
                    "confidence_pred": conf,
                    "n": len(subset),
                    "accuracy": subset["correct"].mean(),
                }
            )
    return pd.DataFrame(rows)


def _fmt_df(df: pd.DataFrame, floatfmt: str = "{:.2f}") -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table."""
    disp = df.copy()
    for col in disp.columns:
        if pd.api.types.is_float_dtype(disp[col]):
            disp[col] = disp[col].map(lambda v: floatfmt.format(v))
    header = "| " + " | ".join(str(c) for c in disp.columns) + " |"
    sep = "| " + " | ".join("---" for _ in disp.columns) + " |"
    body = [
        "| " + " | ".join(str(v) for v in row) + " |"
        for row in disp.itertuples(index=False)
    ]
    return "\n".join([header, sep, *body])


def build_report() -> str:
    """Compute all metrics and return the Markdown report as a string."""
    merged = _load()
    n = len(merged)
    accuracy = merged["correct"].mean()

    metrics = _per_status_metrics(merged)
    macro_f1 = metrics["f1"].mean()
    confusion = _confusion(merged)
    conf_break = _confidence_breakdown(merged)

    # Per-project agreement table (sorted so disagreements surface at the top).
    agreement = merged[
        ["project_name", "status_true", "status_pred", "confidence_pred", "correct"]
    ].sort_values(["correct", "project_name"])
    agreement = agreement.rename(
        columns={
            "status_true": "truth",
            "status_pred": "predicted",
            "confidence_pred": "conf",
            "correct": "match",
        }
    )
    agreement["match"] = agreement["match"].map({True: "✓", False: "✗"})

    lines: list[str] = []
    lines.append("# Validation report")
    lines.append("")
    lines.append(
        "Pipeline predictions (`data/predictions.csv`) scored against the "
        "hand-labelled gold set (`data/ground_truth.csv`). Only projects present "
        "in both files are scored. The gold set is **validation data, never "
        "training data** — the zero-shot agent has never seen it."
    )
    lines.append("")
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **Projects scored:** {n}")
    lines.append(f"- **Overall accuracy:** {accuracy:.0%} ({merged['correct'].sum()}/{n})")
    lines.append(f"- **Macro-averaged F1:** {macro_f1:.2f}")
    lines.append("")
    lines.append("## Precision / recall per status")
    lines.append("")
    lines.append(_fmt_df(metrics))
    lines.append("")
    lines.append(
        "_Support is the number of gold-set projects with that true status. With "
        "a small sample, a single miss moves these numbers a lot — read them as "
        "directional, not definitive._"
    )
    lines.append("")
    lines.append("## Confusion matrix")
    lines.append("")
    lines.append("Rows = human truth, columns = pipeline prediction.")
    lines.append("")
    lines.append(_fmt_df(confusion.reset_index()))
    lines.append("")
    lines.append("## Accuracy by predicted confidence")
    lines.append("")
    lines.append(
        "Is the pipeline's own confidence calibrated — are `high`-confidence "
        "calls actually more often right than `low`-confidence ones?"
    )
    lines.append("")
    lines.append(_fmt_df(conf_break))
    lines.append("")
    lines.append("## Per-project agreement")
    lines.append("")
    lines.append("Disagreements (`✗`) are listed first for inspection.")
    lines.append("")
    lines.append(_fmt_df(agreement))
    lines.append("")
    return "\n".join(lines)


def run() -> Path:
    """Build the report and write it to validation_report.md."""
    report = build_report()
    REPORT_MD.write_text(report, encoding="utf-8")
    print(f"Wrote validation report to {REPORT_MD}")
    # Echo the headline to the console for a quick read.
    for line in report.splitlines():
        if line.startswith("- **") or line.startswith("# "):
            print(line.replace("**", "").replace("- ", "  "))
    return REPORT_MD


def main() -> None:
    run()


if __name__ == "__main__":
    main()
