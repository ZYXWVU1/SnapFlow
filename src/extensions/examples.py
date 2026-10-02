"""Minimal trusted providers; nothing is registered or loaded automatically."""
from . import ActionDefinition, ContextSource, PermissionMetadata, Skill


class ExampleActionProvider:
    metadata = PermissionMetadata('example_actions', 'clipboard', ('copy_selected_text',))

    def actions(self):
        return (ActionDefinition('copy_example_title', 'Copy example title', 'copy',
            lambda result: str(result.data.get('title') or ''), risk_level='clipboard'),)


class ExampleSkill(Skill):
    id, title = 'native_example', 'Native example'
    schema = {'title': 'string or null'}
    action_ids = ('copy_text',)

    def normalize(self, raw):
        title = raw.get('title')
        return {'title': title[:1000] if isinstance(title, str) else None}, []


class ExampleSkillProvider:
    metadata = PermissionMetadata('example_skills', 'read_only', ('visual_skill',))

    def skills(self):
        return (ExampleSkill(),)


class ExampleContextProvider:
    metadata = PermissionMetadata('example_context', 'read_only', ('selected_context',))

    async def retrieve(self, selection, cancel):
        if cancel.is_set():
            raise PermissionError('Request cancelled.')
        return ContextSource('external', 'trusted_extension', 'Selected example', 'Explicitly selected example text.',
            connection_id=self.metadata.id, resource_uri=selection, mime_type='text/plain')
