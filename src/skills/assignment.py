from .base import Skill
from .validation import date_value, fields, number, time_value, url


class AssignmentSkill(Skill):
    id, title = 'assignment', 'Assignment'
    schema = dict.fromkeys(('course', 'title', 'assignment_type', 'due_date', 'due_time', 'timezone',
                            'points', 'instructions_summary', 'submission_method', 'url'), 'string or null')
    schema = {**schema, 'points': 'number or null'}
    instructions = ('Use YYYY-MM-DD dates and HH:MM 24-hour times. If year is absent or ambiguous, use null; '
                    'never infer timezone from location. Summarize instructions in 1-3 sentences. '
                    'assignment_type: homework, project, quiz, exam, discussion, paper, lab, reading, other.')
    action_ids = ('create_ics', 'copy_details', 'copy_markdown', 'ask_ai')
    presentation = (('course', 'Course'), ('title', 'Title'), ('due_date', 'Due date'), ('due_time', 'Due time'),
                    ('timezone', 'Timezone'), ('points', 'Points'), ('instructions_summary', 'Summary'),
                    ('submission_method', 'Submission'), ('url', 'URL'), ('assignment_type', 'Type'))

    def normalize(self, raw):
        data, warnings = fields(raw, self.schema, {'due_date': date_value, 'due_time': time_value,
                                                  'points': number, 'url': url})
        kind = data['assignment_type']
        if kind:
            kind = kind.lower()
            if kind not in ('homework', 'project', 'quiz', 'exam', 'discussion', 'paper', 'lab', 'reading', 'other'):
                kind = 'other'
            data['assignment_type'] = kind
        return data, warnings
