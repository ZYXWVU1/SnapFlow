from src.skills.custom.models import CustomFieldDefinition as Field, CustomSkillDefinition as Definition


def sample(**changes):
    values = dict(id='receipt_tracker', name='Receipt Tracker', description='Track receipts',
                  detection_prompt='A purchase receipt', fields=[Field('merchant', 'Merchant', 'string', True),
                  Field('total', 'Total paid', 'number')], actions=['copy_json', 'copy_markdown', 'copy_text', 'save_csv', 'ask_ai'])
    return Definition(**(values | changes))
