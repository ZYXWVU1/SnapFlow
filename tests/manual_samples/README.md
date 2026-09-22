# Synthetic Smart classification samples

Generate five PNGs per category with `python -m scripts.generate_smart_samples`.
All text is fictional and defined in that script. Generated PNGs are ignored by git.
Do not add private screenshots here.

`python -m scripts.evaluate_classifier` inventories samples without network calls.
`python -m scripts.evaluate_classifier --live` sends those PNGs to your configured provider
and reports predicted type, confidence, route, per-category accuracy and overall accuracy.
Use `--limit-per-category 1` for a five-request smoke evaluation.

These clean, text-heavy examples establish a reproducible smoke dataset, not real-world
accuracy. Add intentionally synthetic screenshots with mixed layouts, photos, ambiguous
deadlines, tiny text and multiple categories before comparing prompt revisions.
# Phase 3 evaluation

Run `python -m scripts.evaluate_skills` for an offline inventory, or add `--live` to send
samples for classification and structured extraction. The default limit is one per category.
Add `01.expected.json` beside `01.png`, containing expected normalized fields such as
`{"title":"Homework 4","due_date":null}`, to score factual field correctness.
Include absent-field nulls to detect invented data. Without a sidecar, parsing success is reported
but extraction quality is not scored. Review correctness and completeness against each image.
