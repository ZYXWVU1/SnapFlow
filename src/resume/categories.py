"""Ordered resume category registry shared by extraction, editing and matching."""

RECORD_KINDS = ('experience', 'education', 'project', 'certification', 'award',
                'publication', 'volunteering', 'additional')

# Normal dict insertion order is the display and extraction order.
CATEGORIES = {
    'person.full_name': 'Full name', 'person.given_name': 'Given name',
    'person.family_name': 'Family name', 'person.summary': 'Professional summary',
    'contact.email': 'Email', 'contact.phone': 'Phone',
    'address.street': 'Street address', 'address.city': 'City',
    'address.region': 'State / region', 'address.postal_code': 'Postal code', 'address.country': 'Country',
    'links.linkedin': 'LinkedIn', 'links.github': 'GitHub', 'links.portfolio': 'Portfolio',
    'links.website': 'Website', 'links.professional': 'Professional links',
    'skills.items': 'Skills', 'languages.items': 'Languages',
    'experience.employer': 'Work experience / Employer',
    'experience.title': 'Work experience / Job title', 'experience.location': 'Work experience / Location',
    'experience.start_date': 'Work experience / Start date', 'experience.end_date': 'Work experience / End date',
    'experience.description': 'Work experience / Description',
    'experience.employment_type': 'Work experience / Employment type',
    'experience.department': 'Work experience / Department',
    'experience.achievements': 'Work experience / Achievements',
    'education.institution': 'Education / Institution', 'education.degree': 'Education / Degree',
    'education.field_of_study': 'Education / Field of study', 'education.location': 'Education / Location',
    'education.start_date': 'Education / Start date', 'education.end_date': 'Education / End date',
    'education.gpa': 'Education / GPA', 'education.coursework': 'Education / Coursework',
    'education.honors': 'Education / Honors', 'education.description': 'Education / Description',
    'project.name': 'Project / Name', 'project.role': 'Project / Role',
    'project.organization': 'Project / Organization', 'project.description': 'Project / Description',
    'project.start_date': 'Project / Start date', 'project.end_date': 'Project / End date',
    'project.technologies': 'Project / Technologies', 'project.url': 'Project / URL',
    'certification.name': 'Certification / Name', 'certification.issuer': 'Certification / Issuer',
    'certification.issue_date': 'Certification / Issue date', 'certification.expiry_date': 'Certification / Expiry date',
    'certification.credential_id': 'Certification / Credential ID', 'certification.url': 'Certification / URL',
    'certification.description': 'Certification / Description',
    'award.name': 'Award / Name', 'award.issuer': 'Award / Issuer',
    'award.date': 'Award / Date', 'award.description': 'Award / Description',
    'publication.title': 'Publication / Title', 'publication.publisher': 'Publication / Publisher',
    'publication.authors': 'Publication / Authors', 'publication.date': 'Publication / Date',
    'publication.url': 'Publication / URL', 'publication.description': 'Publication / Description',
    'volunteering.organization': 'Volunteering / Organization', 'volunteering.role': 'Volunteering / Role',
    'volunteering.location': 'Volunteering / Location', 'volunteering.start_date': 'Volunteering / Start date',
    'volunteering.end_date': 'Volunteering / End date', 'volunteering.description': 'Volunteering / Description',
    'additional.title': 'Additional information / Title', 'additional.description': 'Additional information / Description',
    'additional.start_date': 'Additional information / Start date', 'additional.end_date': 'Additional information / End date',
    'additional.items': 'Additional information / Items', 'additional.url': 'Additional information / URL',
}
LIST_CATEGORIES = frozenset({'skills.items', 'languages.items', 'links.professional',
    'experience.achievements', 'education.coursework', 'education.honors',
    'project.technologies', 'publication.authors', 'additional.items'})
DATE_CATEGORIES = frozenset(key for key in CATEGORIES if key.endswith(('.start_date', '.end_date'))) | frozenset({
    'certification.issue_date', 'certification.expiry_date', 'award.date', 'publication.date'})
URL_CATEGORIES = frozenset(key for key in CATEGORIES if key.startswith('links.') or key.endswith('.url'))


def category_kind(category):
    """Return the repeated record kind, or None for a personal category."""
    prefix = category.split('.', 1)[0]
    return prefix if prefix in RECORD_KINDS else None


CATEGORY_METADATA = {key: {'kind': category_kind(key), 'repeated': category_kind(key) is not None,
                           'list': key in LIST_CATEGORIES} for key in CATEGORIES}
