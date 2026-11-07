"""Print a complete V0.1 report for a local synthetic example or user score."""
import argparse
from composerlint.service import analyze_score

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("path", help="local uncompressed MusicXML or MIDI path")
parser.add_argument("--no-quantize", action="store_true", help="preserve exact MIDI ticks")
args = parser.parse_args()
print(analyze_score(args.path, {"midi": {"quantize_post": not args.no_quantize}}).canonical_json())
