# Synthetic custom skill acceptance samples

From the project root run `python -c "import runpy; runpy.run_path('tests/render_phase4.py', run_name='__main__')"`.
This regenerates synthetic PNGs and disabled `.aiskill` examples for internship, receipt and package tracking.
No real person, company, order, tracking number, private image or API call is used.

1. Import Internship Tracker, enable it, and capture its synthetic image in Smart mode.
2. Teach From Screenshot with the receipt sample and purpose "Track receipts and extract merchant, total and date".
3. Review/edit the proposed fields, test, enable and save. Confirm definitions survive restart.
4. Disable Receipt Tracker and confirm Smart ignores it (a confident built-in may still match first).
5. Export a skill, delete it after confirmation, import it again; repeat import to verify copy behavior.
6. Test partial screenshots: optional missing fields disappear; missing required fields show Not detected.
7. Close teaching/testing during an AI request and confirm no late result reopens a window.

The offline test suite uses mocked model responses. These manual AI acceptance checks require a configured provider.
