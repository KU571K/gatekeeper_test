import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gatekeeper.catalogs import project_type_by_code, theme_by_code
from gatekeeper.errors import RuleError
from gatekeeper.schemas import (
    ABSTRACTION_QUESTION_AT,
    CRITERIA_MAX,
    CRITERIA_MIN,
    FIELD_KEYS,
    TYPE_MAX,
    TYPE_MIN,
    QUESTION_MAX,
    QUESTION_MIN,
    ROLE_MAX_LENGTH,
    CommentTrace,
    FieldAssessment,
    Layout,
    ProjectRole,
    ResultCriterion,
    SufficiencyTrace,
)

_EMPTY = frozenset({
    '',
    '-',
    '—',
    'нет',
    'не указано',
    'неизвестно',
    'не определено',
    'n/a',
    'none',
    'null',
})
_THINK = re.compile(r'<think>.*?</think>', re.IGNORECASE | re.DOTALL)


class _AssessmentBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    theme_code: str | None = None
    technology: str | None = None
    project_types: list[str] | str | None = None
    themes: list[str] | str | None = None
    result_image: str | None = None
    result_criteria: list[Any] | None = None
    enough: bool
    role_clear: bool
    abstraction_level: int = Field(ge=0, le=100)
    assessment: dict[str, FieldAssessment]
    questions: list[str] = Field(default_factory=list)


class _LayoutBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    theme_code: str
    technology: str
    project_types: list[str] | str
    themes: list[str] | str
    result_image: str
    result_criteria: list[Any]


class _CommentBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    valid: bool
    concrete: bool
    reason: str
    abstraction_level: int = Field(ge=0, le=100)
    questions: list[str] = Field(default_factory=list)
    theme_code: str | None = None
    technology: str | None = None
    project_types: list[str] | str | None = None
    themes: list[str] | str | None = None
    result_image: str | None = None
    result_criteria: list[Any] | None = None


class _RoleItem(BaseModel):
    model_config = ConfigDict(extra='ignore')

    title: str
    why: str = ''
    filled_by_starter: bool = False


class _RolesBody(BaseModel):
    model_config = ConfigDict(extra='ignore')

    roles: list[_RoleItem]


def extract_json(raw: str) -> dict[str, Any]:
    cleaned = _THINK.sub('', raw).strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
    cleaned = re.sub(r'\s*```$', '', cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        if start < 0 or end <= start:
            raise RuleError('ответ не содержит JSON') from None
        try:
            payload = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RuleError('JSON не разбирается') from exc
    if not isinstance(payload, dict):
        raise RuleError('JSON должен быть объектом')
    return payload


def parse_assessment(payload: dict[str, Any]) -> tuple[SufficiencyTrace, Layout | None]:
    payload = _align_fields(payload)
    try:
        body = _AssessmentBody.model_validate(payload)
    except ValidationError as exc:
        raise RuleError(_short_validation(exc)) from exc

    missing = [key for key in FIELD_KEYS if key not in body.assessment]
    if missing:
        raise RuleError(f'в assessment нет полей: {", ".join(missing)}')

    questions = [_required_text(item, 'вопрос', max_length=300) for item in body.questions]
    draft_type = _project_types(body.project_types, required=False)
    draft_theme = _optional_theme(body.theme_code)
    draft = {
        'theme': draft_theme,
        'technology': _optional_text(body.technology, max_length=2_000),
        'project_types': draft_type,
        'themes': _themes(body.themes),
        'result_image': _optional_text(body.result_image, max_length=2_000),
        'result_criteria': _criteria(body.result_criteria, required=False),
    }
    field_enough = {key: body.assessment[key].enough for key in FIELD_KEYS}
    level = body.abstraction_level
    vague = level >= ABSTRACTION_QUESTION_AT
    ready = body.enough and body.role_clear and not vague

    if ready:
        if questions:
            raise RuleError('при enough=true, ясной роли и уровне абстракции ниже 40 список questions должен быть пустым')
        if not all(field_enough.values()):
            raise RuleError('при enough=true все пять оценок в assessment тоже должны быть enough=true')
        draft['project_types'] = _project_types(body.project_types, required=True)
        draft['result_criteria'] = _criteria(body.result_criteria, required=True)
        layout = _layout_from_parts(draft)
        if layout is None:
            raise RuleError('при enough=true все пять полей расклада и критерии должны быть заполнены')
    else:
        if not QUESTION_MIN <= len(questions) <= QUESTION_MAX:
            raise RuleError(f'нужно от {QUESTION_MIN} до {QUESTION_MAX} вопросов')
        if not body.enough and all(field_enough.values()):
            raise RuleError('при enough=false хотя бы одно поле в assessment должно быть enough=false')
        layout = None

    trace = SufficiencyTrace(
        enough=body.enough,
        assessment=body.assessment,
        questions=questions,
        draft_theme_code=draft['theme'].code if draft['theme'] else None,
        draft_technology=draft['technology'],
        draft_project_types=[item.code for item in draft['project_types'] or []],
        draft_themes=draft['themes'] or [],
        draft_result_image=draft['result_image'],
        role_clear=body.role_clear,
        abstraction_level=level,
    )
    return trace, layout


def parse_layout(payload: dict[str, Any]) -> Layout:
    payload = _align_fields(payload)
    try:
        body = _LayoutBody.model_validate(payload)
    except ValidationError as exc:
        raise RuleError(_short_validation(exc)) from exc
    layout = _layout_from_parts(
        {
            'theme': _optional_theme(body.theme_code),
            'technology': _optional_text(body.technology, max_length=2_000),
            'project_types': _project_types(body.project_types, required=True),
            'themes': _themes(body.themes),
            'result_image': _optional_text(body.result_image, max_length=2_000),
            'result_criteria': _criteria(body.result_criteria, required=True),
        },
    )
    if layout is None:
        raise RuleError('в итоговом раскладе должны быть заполнены все пять полей и критерии, коды только из справочников')
    return layout


def parse_comment(payload: dict[str, Any], *, comment: str, previous: Layout) -> tuple[CommentTrace, Layout, bool]:
    payload = _align_fields(payload)
    try:
        body = _CommentBody.model_validate(payload)
    except ValidationError as exc:
        raise RuleError(_short_validation(exc)) from exc
    reason = _required_text(body.reason, 'reason', max_length=500)
    level = body.abstraction_level
    vague = level >= ABSTRACTION_QUESTION_AT
    if not body.valid:
        trace = CommentTrace(
            comment=comment,
            valid=False,
            concrete=True,
            reason=reason,
            questions=[],
            abstraction_level=level,
        )
        return trace, previous.model_copy(deep=True), False
    questions = [_required_text(item, 'вопрос', max_length=300) for item in body.questions]
    if vague and body.concrete:
        raise RuleError(
            'при уровне абстракции 40 и выше комментарий не закрывается: '
            'concrete=false и вопрос, который называет состав'
        )
    if not vague and not body.concrete:
        raise RuleError('при уровне абстракции ниже 40 комментарий конкретный, вопросы не задаются')
    if body.concrete:
        if questions:
            raise RuleError('при concrete=true список questions должен быть пустым')
        layout = parse_layout(
            {
                'theme_code': body.theme_code,
                'technology': body.technology,
                'project_types': body.project_types,
                'themes': body.themes,
                'result_image': body.result_image,
                'result_criteria': body.result_criteria,
            },
        )
        trace = CommentTrace(
            comment=comment,
            valid=True,
            concrete=True,
            reason=reason,
            questions=[],
            abstraction_level=level,
        )
        return trace, layout, False
    if not QUESTION_MIN <= len(questions) <= QUESTION_MAX:
        raise RuleError(f'при неконкретном комментарии нужно от {QUESTION_MIN} до {QUESTION_MAX} вопросов')
    trace = CommentTrace(
        comment=comment,
        valid=True,
        concrete=False,
        reason=reason,
        questions=questions,
        abstraction_level=level,
    )
    return trace, previous.model_copy(deep=True), True


def parse_settled_layout(payload: dict[str, Any]) -> tuple[Layout, int]:
    if 'abstraction_level' not in payload:
        raise RuleError('нужен уровень абстракции от 0 до 100')
    level = _abstraction_level(payload.get('abstraction_level'))
    return parse_layout(payload), level


def _abstraction_level(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError('уровень абстракции — целое число от 0 до 100')
    if isinstance(value, float) and not value.is_integer():
        raise RuleError('уровень абстракции — целое число от 0 до 100')
    level = int(value)
    if not 0 <= level <= 100:
        raise RuleError('уровень абстракции — целое число от 0 до 100')
    return level


def parse_roles(payload: dict[str, Any]) -> list[ProjectRole]:
    try:
        body = _RolesBody.model_validate(payload)
    except ValidationError as exc:
        raise RuleError(_short_validation(exc)) from exc
    cleaned: list[ProjectRole] = []
    seen: set[str] = set()
    for item in body.roles:
        title = _optional_text(item.title, max_length=ROLE_MAX_LENGTH)
        if title is None or title.casefold() in seen:
            continue
        seen.add(title.casefold())
        why = _optional_text(item.why, max_length=300) or ''
        cleaned.append(ProjectRole(title=title, why=why, filled_by_starter=item.filled_by_starter))
    if not 1 <= len(cleaned) <= 6:
        raise RuleError('нужно от 1 до 6 ролей')
    return cleaned


def _layout_from_parts(parts: dict[str, Any]) -> Layout | None:
    theme = parts['theme']
    project_types = parts['project_types']
    technology = parts['technology']
    themes = parts['themes']
    result_image = parts['result_image']
    criteria = parts['result_criteria']
    if theme is None or not project_types or not technology or not themes or not result_image or not criteria:
        return None
    return Layout(
        theme_code=theme.code,
        theme_title=theme.title,
        technology=technology,
        project_types=[item.code for item in project_types],
        project_type_titles=[item.title for item in project_types],
        project_type_goals=[item.goal for item in project_types],
        themes=themes,
        result_image=result_image,
        result_criteria=criteria,
    )


def _align_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Модель иногда пишет subject_area вместо themes и project_type вместо списка."""
    data = dict(payload)
    if 'themes' not in data and 'subject_area' in data:
        data['themes'] = data.pop('subject_area')
    if 'project_types' not in data and 'project_type' in data:
        data['project_types'] = data.pop('project_type')
    assessment = data.get('assessment')
    if isinstance(assessment, dict) and 'themes' not in assessment and 'subject_area' in assessment:
        assessment = dict(assessment)
        assessment['themes'] = assessment.pop('subject_area')
        data['assessment'] = assessment
    return data


def _themes(value: list[str] | str | None) -> list[str] | None:
    if value is None:
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise RuleError('темы должны быть списком от 1 до 3')
    cleaned: list[str] = []
    for item in items:
        text = _optional_text(str(item), max_length=120)
        if text is None or text in cleaned:
            continue
        cleaned.append(text)
    if not cleaned:
        return None
    if len(cleaned) > 3:
        raise RuleError('тем может быть не больше трёх, тематика при этом одна')
    return cleaned


def _criteria(value: Any, *, required: bool) -> list[ResultCriterion] | None:
    if value is None:
        if required:
            raise RuleError(f'нужны от {CRITERIA_MIN} до {CRITERIA_MAX} критериев образа результата')
        return None
    if not isinstance(value, list):
        raise RuleError('критерии образа результата должны быть списком')
    cleaned: list[ResultCriterion] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise RuleError('критерий должен содержать name и how_to_tell')
        name = _optional_text(str(item.get('name', '')), max_length=80)
        how = _optional_text(str(item.get('how_to_tell', '')), max_length=240)
        if name is None or how is None or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        cleaned.append(ResultCriterion(name=name, how_to_tell=how))
    if len(cleaned) > CRITERIA_MAX:
        cleaned = cleaned[:CRITERIA_MAX]
    if len(cleaned) < CRITERIA_MIN:
        if required:
            raise RuleError(f'нужны от {CRITERIA_MIN} до {CRITERIA_MAX} критериев образа результата')
        return None
    return cleaned


def _optional_theme(code: str | None) -> Any:
    cleaned = _optional_text(code, max_length=8)
    if cleaned is None:
        return None
    theme = theme_by_code(cleaned)
    if theme is None:
        raise RuleError(f'тематики «{cleaned}» нет в справочнике предметных областей')
    return theme


def _project_types(value: list[str] | str | None, *, required: bool) -> list[Any] | None:
    if value is None:
        if required:
            raise RuleError(f'нужно от {TYPE_MIN} до {TYPE_MAX} типов проекта')
        return None
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        raise RuleError(f'типы проекта должны быть списком от {TYPE_MIN} до {TYPE_MAX}')
    cleaned: list[Any] = []
    seen: set[str] = set()
    for item in items:
        project_type = _optional_type(None if item is None else str(item))
        if project_type is None or project_type.code in seen:
            continue
        seen.add(project_type.code)
        cleaned.append(project_type)
    if len(cleaned) > TYPE_MAX:
        raise RuleError(f'типов проекта может быть не больше {TYPE_MAX}')
    if len(cleaned) < TYPE_MIN:
        if required:
            raise RuleError(f'нужно от {TYPE_MIN} до {TYPE_MAX} типов проекта')
        return None
    return cleaned


def _optional_type(code: str | None) -> Any:
    cleaned = _optional_text(code, max_length=40)
    if cleaned is None:
        return None
    project_type = project_type_by_code(cleaned.casefold())
    if project_type is None:
        raise RuleError(f'тип «{cleaned}» нет в списке из семи типов')
    return project_type


def _optional_text(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    text = ' '.join(str(value).split())
    if text.casefold() in _EMPTY:
        return None
    if len(text) > max_length:
        raise RuleError(f'поле длиннее {max_length} символов')
    return text


def _required_text(value: str, label: str, *, max_length: int) -> str:
    text = _optional_text(value, max_length=max_length)
    if text is None:
        raise RuleError(f'«{label}» не заполнено')
    return text


def _short_validation(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:4]:
        loc = '.'.join(str(item) for item in error['loc'])
        parts.append(f'{loc}: {error["msg"]}')
    return '; '.join(parts) or 'JSON не совпал со схемой'
