"""M1 library example: emit canonical normalized JSON without exporting a score."""
import argparse
from composerlint.engine.parser import load_score

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("path", help="local MusicXML or MIDI path")
parser.add_argument("--no-quantize", action="store_true", help="preserve exact MIDI ticks")
args = parser.parse_args()
print(load_score(args.path, {"midi": {"quantize_post": not args.no_quantize}}).canonical_json())
