"""
connectors/renpho.py
────────────────────
Parses a Renpho "Body Composition Analysis Report" PDF and extracts
all relevant metrics for the weekly scoring engine.

Usage:
    # From a file path
    data = parse_pdf("path/to/Body scan 4_3.pdf")

    # From an uploaded file object (e.g. email attachment bytes)
    data = parse_pdf_bytes(pdf_bytes, participant_id="kam")

The parser targets the exact layout of the Renpho report:
    - Body Composition Analysis table
    - Other Indicators section
    - Body Sore score
    - Target to optimal weight section
"""

import re
from pathlib import Path
import pdfplumber


# ── Regex patterns matching Renpho PDF field names ──────────────────────────

PATTERNS = {
    # Body Composition table
    "weight_lb":              r"Weight\s+([\d.]+)",
    "body_fat_mass_lb":       r"Body Fat Mass\s+([\d.]+)",
    "bone_mass_lb":           r"Bone Mass\s+([\d.]+)",
    "protein_mass_lb":        r"Protein Mass\s+([\d.]+)",
    "body_water_mass_lb":     r"Body Water Mass\s+([\d.]+)",
    "muscle_mass_lb":         r"Muscle Mass\s+([\d.]+)",
    "skeletal_muscle_mass_lb":r"Skeletal Muscle\s*Mass\s+([\d.]+)",

    # Obesity / body fat
    "bmi":                    r"BMI[:\s]+\(kg/m[²2]\)?[:\s]*([\d.]+)",
    "body_fat_pct":           r"Body Fat Percentage\s*[:\s]*([\d.]+)%",
    "obesity_assessment_pct": r"Obesity Assessment\s*[:\s]*([\d.]+)%",

    # Other Indicators
    "visceral_fat":           r"Visceral Fat\s+([\d.]+)",
    "bmr_kcal":               r"BMR\s+([\d.]+)\s*kcal",
    "fat_free_mass_lb":       r"Fat-free Mass\s+([\d.]+)",
    "subcutaneous_fat_pct":   r"Subcutaneous Fat\s+([\d.]+)%?",
    "smi":                    r"SMI\s+([\d.]+)\s*kg",
    "metabolic_age":          r"Metabolic Age\s+([\d.]+)",
    "whr":                    r"WHR\s*\(Waist-to-Hip Ratio\)\s+([\d.]+)",

    # Body Sore score (headline number)
    "body_sore_score":        r"([\d.]+)\s*/\s*100\s*Points",

    # Target to optimal
    "optimal_weight_lb":      r"Optimal Weight\s+([\d.]+)\s*lb",
    "target_weight_delta_lb": r"Target to optimal weight\s+([+-]?[\d.]+)\s*lb",
}

# Header fields for participant context
HEADER_PATTERNS = {
    "gender":  r"Gender\s*:\s*(\w+)",
    "age":     r"Age\s*:\s*(\d+)",
    "height":  r"Height\s*:\s*([\d'\s\"]+)",
    "scan_date": r"Test Date\s*:\s*([\w\s,:/]+\d{4}[\d:\s]+[AP]M)",
}


def _extract_text(pdf_path: str) -> str:
    """Extract full text from a PDF file using pdfplumber."""
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )


def _extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """Extract full text from PDF bytes (e.g. email attachment)."""
    import io
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return "\n".join(
            page.extract_text() or "" for page in pdf.pages
        )


def _parse_text(text: str, participant_id: str = None) -> dict:
    """Apply all regex patterns to extracted text and return clean metrics dict."""
    metrics = {"participant_id": participant_id}

    for field, pattern in PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                metrics[field] = float(match.group(1).replace(",", ""))
            except (ValueError, IndexError):
                metrics[field] = match.group(1).strip()
        else:
            metrics[field] = None

    for field, pattern in HEADER_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        metrics[field] = match.group(1).strip() if match else None

    # Derived: how far is body_fat_pct from optimal (10-20% for males)
    if metrics.get("body_fat_pct"):
        optimal_max = 20.0  # simplified; could be gender/age aware
        metrics["body_fat_above_optimal_pct"] = round(
            max(0.0, metrics["body_fat_pct"] - optimal_max), 1
        )

    return metrics


def parse_pdf(pdf_path: str, participant_id: str = None) -> dict:
    """
    Parse a Renpho PDF file.

    Args:
        pdf_path:       Path to the PDF file.
        participant_id: Optional participant ID to tag the data.

    Returns:
        dict of extracted metrics.
    """
    text = _extract_text(pdf_path)
    result = _parse_text(text, participant_id)
    result["source_file"] = Path(pdf_path).name
    return result


def parse_pdf_bytes(pdf_bytes: bytes, participant_id: str = None) -> dict:
    """
    Parse a Renpho PDF from raw bytes (e.g. email attachment).

    Args:
        pdf_bytes:      Raw PDF bytes.
        participant_id: Optional participant ID to tag the data.

    Returns:
        dict of extracted metrics.
    """
    text = _extract_text_from_bytes(pdf_bytes)
    result = _parse_text(text, participant_id)
    result["source_file"] = "email_attachment"
    return result


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else "data/weekly/latest_scan.pdf"
    result = parse_pdf(path, participant_id="kam")
    print(json.dumps(result, indent=2))
