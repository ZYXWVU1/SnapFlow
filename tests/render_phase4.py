"""Render native Phase 4 dialogs and generate only synthetic manual samples."""
import json
from pathlib import Path
import tempfile
from PySide6.QtGui import QImage, QPainter, QFont, QColor
from PySide6.QtWidgets import QApplication
from src.skills.custom.models import CustomFieldDefinition as F, CustomSkillDefinition as D
from src.skills.custom.storage import CustomSkillStorage
from src.skills.custom.import_export import export_skill
from src.skills.custom.runtime_skill import RuntimeCustomSkill
from src.ui.skills.skill_manager import SkillManager
from src.ui.skills.skill_editor import SkillEditor
from src.ui.skills.teach_by_example import TeachByExample
from src.ui.result_window import ResultWindow


def definitions():
    return [
        D('internship_tracker', 'Internship Tracker', 'Track internship opportunities.',
          'A job or internship posting with a company and role.',
          [F('company', 'Company', 'string'), F('position', 'Position', 'string', True), F('location', 'Location', 'string'),
           F('salary', 'Salary', 'string'), F('requirements', 'Requirements', 'list_string'), F('deadline', 'Deadline', 'date'), F('url', 'URL', 'url')],
          ['copy_json', 'copy_markdown', 'save_csv', 'ask_ai'], False),
        D('receipt_tracker', 'Receipt Tracker', 'Track purchase receipts.', 'A store receipt or purchase confirmation.',
          [F('merchant', 'Merchant', 'string', True), F('date', 'Date', 'date'), F('total', 'Total', 'number'),
           F('tax', 'Tax', 'number'), F('order_id', 'Order ID', 'string')], ['copy_json', 'copy_text', 'save_csv', 'ask_ai'], False),
        D('package_tracker', 'Package Tracker', 'Track package delivery.', 'A shipping or package tracking page.',
          [F('carrier', 'Carrier', 'string'), F('tracking_number', 'Tracking number', 'string', True),
           F('status', 'Status', 'string'), F('estimated_delivery', 'Estimated delivery', 'date')], ['copy_json', 'copy_markdown', 'ask_ai'], False),
    ]


def main():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    samples = definitions()
    texts = [
        'Northstar Labs\nSoftware Engineering Intern\nBoston, MA · Summer 2027\nCompensation: $35/hour\nRequirements: Python, Git, teamwork\nApply by 2026-12-15\nhttps://example.com/careers/intern',
        'CORNER MARKET\nPurchase Receipt\nDate: 2026-09-22\nOrder: DEMO-00123\nSubtotal: $20.00\nTax: $1.60\nTotal paid: $21.60',
        'PARCEL DEMO\nTracking: DEMO-000123\nStatus: In transit\nEstimated delivery: 2026-09-25',
    ]
    root = Path('tests/manual_samples/custom')
    for skill, text in zip(samples, texts):
        folder = root / skill.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / (skill.id + '.aiskill')).write_text(export_skill(skill), encoding='utf-8')
        image = QImage(850, 500, QImage.Format.Format_RGB32)
        image.fill(QColor('#f5f7fb'))
        painter = QPainter(image)
        painter.setPen(QColor('#14233b'))
        painter.setFont(QFont('Segoe UI', 17))
        painter.drawText(35, 30, 780, 440, 0, text)
        painter.end()
        image.save(str(folder / 'synthetic.png'))
    output = Path('docs/phase4-ui')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        storage = CustomSkillStorage(Path(folder) / 'skills.json')
        for skill in samples:
            storage.create_skill(skill)
        manager = SkillManager(storage, None)
        manager.custom.setCurrentRow(0)
        editor = SkillEditor(samples[0])
        result = ResultWindow('smart', False)
        result.set_skill_result(RuntimeCustomSkill(samples[0]).parse(json.dumps(dict(company='Northstar Labs', position='Software Engineering Intern', location='Boston, MA', requirements=['Python', 'Git', 'Teamwork']))))
        teaching = TeachByExample(None)
        teaching.purpose.setPlainText('Track receipts and extract merchant, total and date.')
        for name, window in [('manager', manager), ('editor', editor), ('result', result), ('teaching', teaching)]:
            window.show()
            app.processEvents()
            window.grab().save(str(output / (name + '.png')))
            window.close()
    print(output.resolve())


if __name__ == '__main__':
    main()
