"""Local, opt-in evaluation; prints labels/metrics only, never screenshot text."""
import argparse
from pathlib import Path
from dotenv import load_dotenv
from src.config import ROOT, load_config
from src.llm_client import LLMClient
from src.smart.classifier import ScreenshotClassifier
from src.smart.models import SUPPORTED_CONTENT_TYPES
from src.smart.router import SmartRouter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, default=ROOT / 'tests/manual_samples')
    parser.add_argument('--live', action='store_true', help='Send sample PNGs to the configured vision provider')
    parser.add_argument('--limit-per-category', type=int, default=5)
    args = parser.parse_args()
    if args.limit_per_category < 1:
        parser.error('--limit-per-category must be positive')
    samples = [(kind, path) for kind in sorted(SUPPORTED_CONTENT_TYPES)
               for path in sorted((args.samples / kind).glob('*.png'))[:args.limit_per_category]]
    if not samples:
        parser.error('No PNG samples found; run python -m scripts.generate_smart_samples first')
    if not args.live:
        print(f'{len(samples)} samples found. Add --live to send them to the configured provider.')
        return
    load_dotenv(ROOT / '.env')
    classifier = ScreenshotClassifier(LLMClient())
    router = SmartRouter(load_config().smart_classification_threshold)
    correct = total = failures = 0
    for kind in sorted(SUPPORTED_CONTENT_TYPES):
        hits = count = 0
        for expected, path in samples:
            if expected != kind:
                continue
            result = classifier.classify(path.read_bytes())
            failed = result.confidence == 0 and result.reasoning in (
                'Classification unavailable.', 'Unable to parse classifier response.')
            failures += int(failed)
            hit = not failed and result.content_type == expected
            hits += int(hit)
            count += 1
            print(f'{expected}/{path.name}: {result.content_type} confidence={result.confidence:.2f} '
                  f'route={router.route(result)} correct={hit} failure={failed}', flush=True)
        correct += hits
        total += count
        print(f'{kind}: {hits}/{count} correct', flush=True)
    print(f'Overall: {correct}/{total} ({correct / total:.0%}); request/parse failures: {failures}', flush=True)


if __name__ == '__main__':
    main()
