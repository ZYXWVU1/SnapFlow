"""Opt-in screenshot evaluation. Reports metrics without printing extracted private data.

Add a sibling .expected.json file containing expected normalized fields to each PNG
to measure factual field correctness, including nulls for fields not visible.
Without expected fields, parsing success alone does not establish extraction quality.
"""
import argparse
import json
from pathlib import Path
from dotenv import load_dotenv
from src.config import ROOT, load_config
from src.llm_client import LLMClient, AnalysisError
from src.modes import ResponseFormatError
from src.skills.registry import SKILLS
from src.smart.classifier import ScreenshotClassifier
from src.smart.router import SmartRouter


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=Path, default=ROOT / 'tests/manual_samples')
    parser.add_argument('--live', action='store_true', help='Send sample screenshots for classification and extraction')
    parser.add_argument('--limit-per-category', type=int, default=1)
    args = parser.parse_args()
    if args.limit_per_category < 1:
        parser.error('--limit-per-category must be positive')
    samples = [(kind, path) for kind in (*SKILLS, 'unknown')
               for path in sorted((args.samples / kind).glob('*.png'))[:args.limit_per_category]]
    if not samples:
        parser.error('No PNG samples found. Run python -m scripts.generate_smart_samples first.')
    if not args.live:
        print(f'{len(samples)} samples found. No requests sent. Add --live to evaluate classification and extraction.')
        return
    load_dotenv(ROOT / '.env')
    client = LLMClient()
    classifier, router = ScreenshotClassifier(client), SmartRouter(load_config().smart_classification_threshold)
    for kind, path in samples:
        image = path.read_bytes()
        classification = classifier.classify(image)
        route = router.route(classification)
        correct = route == kind if kind != 'unknown' else route == 'ask'
        print(f'{kind}/{path.name}: routing_correct={correct}', flush=True)
        if route not in SKILLS:
            continue
        try:
            result = SKILLS[route].extract(client, image, classification.confidence)
        except (AnalysisError, ResponseFormatError):
            print('  extraction_failed=True', flush=True)
            continue
        expected_path = path.with_suffix('.expected.json')
        if expected_path.exists():
            expected = json.loads(expected_path.read_text(encoding='utf-8'))
            if not isinstance(expected, dict) or not expected:
                parser.error('Expected-field sidecars must be nonempty JSON objects.')
            hits = sum(key in result.data and result.data[key] == value for key, value in expected.items())
            print(f'  core_fields_correct={hits}/{len(expected)} warnings={len(result.warnings)}', flush=True)
        else:
            print(f'  parsed=True warnings={len(result.warnings)}; factual quality not scored (no expected sidecar).', flush=True)


if __name__ == '__main__':
    main()
