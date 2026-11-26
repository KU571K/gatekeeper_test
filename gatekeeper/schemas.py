from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Phase(StrEnum):
    AWAITING_DESCRIPTION = 'awaiting_description'
    AWAITING_ANSWERS = 'awaiting_answers'
    AWAITING_CONFIRMATION = 'awaiting_confirmation'
    AWAITING_COMMENT_ANSWERS = 'awaiting_comment_answers'
    AWAITING_ROLES = 'awaiting_roles'
    DONE = 'done'


class CommentStatus(StrEnum):
    NONE = 'none'
    CONFIRMED = 'confirmed'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'


FIELD_KEYS: tuple[str, ...] = (
    'theme',
    'technology',
    'project_type',
    'themes',
    'result_image',
)

COMMENT_MAX_LENGTH = 400
DESCRIPTION_MAX_LENGTH = 8_000
ROLE_MAX_LENGTH = 120
QUESTION_MIN = 1
QUESTION_MAX = 4
CRITERIA_MIN = 2
CRITERIA_MAX = 4
TYPE_MIN = 1
TYPE_MAX = 3
ABSTRACTION_QUESTION_AT = 40
ROLES_MIN = 1
ROLES_MAX = 8


class FieldAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enough: bool
    reason: str = Field(min_length=1, max_length=500)


class ResultCriterion(BaseModel):
    """Наблюдаемая примета образа результата. Справочника нет: формулирует модель."""

    model_config = ConfigDict(extra='forbid')

    name: str
    how_to_tell: str


class ProjectRole(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str
    why: str = ''
    filled_by_starter: bool = False


class RoleView(BaseModel):
    """Роль на экране студента: без пояснения модели."""

    model_config = ConfigDict(extra='forbid')

    title: str
    filled_by_starter: bool = False


class Layout(BaseModel):
    model_config = ConfigDict(extra='forbid')

    theme_code: str
    theme_title: str
    technology: str
    project_types: list[str]
    project_type_titles: list[str]
    project_type_goals: list[str]
    themes: list[str]
    result_image: str
    result_criteria: list[ResultCriterion]

    def rows(self) -> list[tuple[str, str]]:
        criteria = '\n'.join(
            item.name if item.name == item.how_to_tell else f'{item.name} — {item.how_to_tell}'
            for item in self.result_criteria
        )
        return [
            ('Тематика', f'{self.theme_code} — {self.theme_title}'),
            ('Технология', self.technology),
            ('Тип', '; '.join(self.project_type_titles)),
            ('Темы', '; '.join(self.themes)),
            ('Образ результата', self.result_image),
            ('Критерии и метрики', criteria),
        ]


class SufficiencyTrace(BaseModel):
    """Служебная оценка полноты описания. Студенту не отдаётся."""

    model_config = ConfigDict(extra='forbid')

    enough: bool
    assessment: dict[str, FieldAssessment]
    questions: list[str]
    draft_theme_code: str | None
    draft_technology: str | None
    draft_project_types: list[str]
    draft_themes: list[str]
    draft_result_image: str | None
    role_clear: bool
    abstraction_level: int = Field(ge=0, le=100)
    abstraction_level_after: int | None = Field(default=None, ge=0, le=100)


class CommentTrace(BaseModel):
    """Служебная оценка единственного комментария. Студенту не отдаётся."""

    model_config = ConfigDict(extra='forbid')

    comment: str
    valid: bool
    concrete: bool
    reason: str
    questions: list[str]
    abstraction_level: int = Field(ge=0, le=100)
    abstraction_level_after: int | None = Field(default=None, ge=0, le=100)


class StudentView(BaseModel):
    """То, что можно показать студенту. Следа здесь нет."""

    model_config = ConfigDict(extra='forbid')

    phase: Phase
    description: str
    starter_role: str
    needs_clarification: bool | None
    questions: list[str]
    answers: list[str]
    proposed_layout: Layout | None
    final_layout: Layout | None
    comment_status: CommentStatus
    comment_text: str | None
    comment_questions: list[str]
    comment_answers: list[str]
    proposed_roles: list[RoleView]
    final_roles: list[RoleView]
    promo_code: str | None


class RuleStatus(StrEnum):
    WAITING = 'waiting'
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPPED = 'skipped'


class RuleCheck(BaseModel):
    code: str
    title: str
    status: RuleStatus
    detail: str
