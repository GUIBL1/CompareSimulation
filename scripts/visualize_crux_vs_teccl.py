from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from simulator.metrics import generate_crux_teccl_comparison_visuals


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate comparison figures for one CRUX result dir and one TE-CCL result dir.")
    parser.add_argument("--crux-result", required=True, help="Result directory for the CRUX run.")
    parser.add_argument("--teccl-result", required=True, help="Result directory for the TE-CCL run.")
    parser.add_argument("--output-dir", required=True, help="Directory to write figures and summary JSON.")
    parser.add_argument("--title", default="CRUX vs TE-CCL Comparison", help="Plot title prefix.")
    args = parser.parse_args()

    outputs = generate_crux_teccl_comparison_visuals(
        crux_result_dir=Path(args.crux_result),
        teccl_result_dir=Path(args.teccl_result),
        output_dir=Path(args.output_dir),
        title=args.title,
    )
    print(json.dumps(outputs, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()