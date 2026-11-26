"""Проверка, что ход привратника совпадает с правилами заказчика.

Это экран для показа, не данные студента. Сами тексты следа остаются
на объекте Gatekeeper и в студентский вид не входят.
"""

from gatekeeper.schemas import CRITERIA_MAX, CRITERIA_MIN, CommentStatus, Phase, RuleCheck, RuleStatus
from gatekeeper.service import Gatekeeper


def rule_checks(gate: Gatekeeper) -> list[RuleCheck]:
    trace = gate.sufficiency_trace
    layout = gate.final_layout or gate.proposed_layout
    described = bool(gate.description)
    classified = layout is not None
    return [
        RuleCheck(
            code='description',
            title='Описание принимается в свободной форме',
            status=RuleStatus.PASSED if described else RuleStatus.WAITING,
            detail=f'{len(gate.description)} символов' if described else 'Описание ещё не отправлено',
        ),
        _starter_role_rule(gate),
        RuleCheck(
            code='theme',
            title='Тематика одна, из справочника предметных областей',
            status=_filled(classified),
            detail=_theme_detail(layout),
        ),
        RuleCheck(
            code='project_type',
            title='Типов от 1 до 3, каждый из семи фиксированных',
            status=_filled(layout is not None and 1 <= len(layout.project_types) <= 3),
            detail='; '.join(layout.project_type_titles) if layout else 'Типы появятся в раскладе',
        ),
        RuleCheck(
            code='subject',
            title='Тем от 1 до 3, тематика одна',
            status=_filled(layout is not None and 1 <= len(layout.themes) <= 3),
            detail='; '.join(layout.themes) if layout else 'Темы появятся внутри одной тематики',
        ),
        RuleCheck(
            code='technology_result',
            title='Технология и образ результата сформулированы',
            status=_filled(classified),
            detail='Методы работы и конкретный итог' if classified else 'Появятся вместе с раскладом',
        ),
        _criteria_rule(layout),
        _clarification_rule(gate),
        _abstraction_rule(gate),
        RuleCheck(
            code='trace',
            title='Цифровой след сохранён и студенту не показан',
            status=RuleStatus.PASSED if trace is not None else RuleStatus.WAITING,
            detail=(
                'Оценка полноты лежит отдельно от экрана студента'
                if trace is not None
                else 'След появится после первого разбора'
            ),
        ),
        RuleCheck(
            code='comment_once',
            title='ОК или один комментарий до 400 символов',
            status=_comment_rule_status(gate),
            detail=_comment_rule_detail(gate),
        ),
        _rejection_rule(gate),
        _roles_rule(gate),
        _promo_rule(gate),
    ]


def _starter_role_rule(gate: Gatekeeper) -> RuleCheck:
    if not gate.starter_role:
        return RuleCheck(
            code='starter_role',
            title='На старте ровно одна роль',
            status=RuleStatus.WAITING,
            detail='Одно поле роли, без списка команды',
        )
    return RuleCheck(
        code='starter_role',
        title='На старте ровно одна роль',
        status=RuleStatus.PASSED,
        detail=gate.starter_role,
    )


def _abstraction_rule(gate: Gatekeeper) -> RuleCheck:
    project = gate.sufficiency_trace
    if project is None:
        return RuleCheck(
            code='abstraction',
            title='Уровень абстракции текста и комментария, каждый от 0 до 100',
            status=RuleStatus.WAITING,
            detail='Два отдельных числа появятся в цифровом следе. 0 — всё названо, 100 — категории без состава',
        )
    detail = f'текст проекта: {project.abstraction_level} из 100'
    if project.abstraction_level_after is not None:
        detail += f', после уточнения {project.abstraction_level_after} из 100'
    comment = gate.comment_trace
    if comment is not None:
        detail += f'; комментарий: {comment.abstraction_level} из 100'
        if comment.abstraction_level_after is not None:
            detail += f', после уточнения {comment.abstraction_level_after} из 100'
    return RuleCheck(
        code='abstraction',
        title='Уровень абстракции текста и комментария, каждый от 0 до 100',
        status=RuleStatus.PASSED,
        detail=detail,
    )


def _criteria_rule(layout) -> RuleCheck:  # noqa: ANN001
    if layout is None:
        return RuleCheck(
            code='result_criteria',
            title='У образа результата 2–4 критерия или метрики',
            status=RuleStatus.WAITING,
            detail='Модель предложит их вместе с итогом, без закрытого справочника',
        )
    count = len(layout.result_criteria)
    ready = CRITERIA_MIN <= count <= CRITERIA_MAX
    names = '; '.join(item.name for item in layout.result_criteria)
    return RuleCheck(
        code='result_criteria',
        title='У образа результата 2–4 критерия или метрики',
        status=RuleStatus.PASSED if ready else RuleStatus.FAILED,
        detail=names if names else 'Список критериев пуст',
    )


def _clarification_rule(gate: Gatekeeper) -> RuleCheck:
    trace = gate.sufficiency_trace
    title = 'Абстракции и пробелы снимаются вопросом'
    if trace is None:
        return RuleCheck(
            code='clarification',
            title=title,
            status=RuleStatus.WAITING,
            detail='Вопрос появится, если поля не заполнить или в тексте безымянная категория',
        )
    comment_pending = gate.phase is Phase.AWAITING_COMMENT_ANSWERS
    if comment_pending:
        return RuleCheck(
            code='clarification',
            title=title,
            status=RuleStatus.WAITING,
            detail=f'Комментарий неконкретный. Вопросов: {len(gate.comment_questions)}',
        )
    if gate.comment_answers:
        return RuleCheck(
            code='clarification',
            title=title,
            status=RuleStatus.PASSED,
            detail=f'После комментария снята абстракция, ответов: {len(gate.comment_answers)}',
        )
    if trace.enough and trace.role_clear:
        return RuleCheck(
            code='clarification',
            title=title,
            status=RuleStatus.SKIPPED,
            detail='Описания и роли хватило, вопросы не задавались',
        )
    if gate.phase is Phase.AWAITING_ANSWERS:
        return RuleCheck(
            code='clarification',
            title=title,
            status=RuleStatus.WAITING,
            detail=f'Задано вопросов: {len(gate.questions)}. Расклад студенту ещё не показан',
        )
    return RuleCheck(
        code='clarification',
        title=title,
        status=RuleStatus.PASSED,
        detail=f'После {len(gate.questions)} вопросов собран расклад',
    )


def _roles_rule(gate: Gatekeeper) -> RuleCheck:
    if gate.phase is Phase.DONE and gate.final_roles:
        return RuleCheck(
            code='roles',
            title='Нужные роли можно исправить',
            status=RuleStatus.PASSED,
            detail='; '.join(role.title for role in gate.final_roles),
        )
    if gate.phase is Phase.AWAITING_ROLES and gate.proposed_roles:
        return RuleCheck(
            code='roles',
            title='Нужные роли можно исправить',
            status=RuleStatus.WAITING,
            detail='Список предложен. Его можно поправить и подтвердить',
        )
    return RuleCheck(
        code='roles',
        title='Нужные роли можно исправить',
        status=RuleStatus.WAITING,
        detail='Список появится, когда расклад будет зафиксирован',
    )


def _promo_rule(gate: Gatekeeper) -> RuleCheck:
    if gate.promo_code:
        return RuleCheck(
            code='promo',
            title='Промокод проекта выдан',
            status=RuleStatus.PASSED,
            detail=gate.promo_code,
        )
    return RuleCheck(
        code='promo',
        title='Промокод проекта выдан',
        status=RuleStatus.WAITING,
        detail='Появится после подтверждения ролей',
    )


def _rejection_rule(gate: Gatekeeper) -> RuleCheck:
    if gate.comment_status is not CommentStatus.REJECTED:
        waiting = gate.phase in {
            Phase.AWAITING_DESCRIPTION,
            Phase.AWAITING_ANSWERS,
            Phase.AWAITING_CONFIRMATION,
            Phase.AWAITING_COMMENT_ANSWERS,
        }
        return RuleCheck(
            code='rejection',
            title='Ерунда в комментарии не меняет расклад',
            status=RuleStatus.WAITING if waiting else RuleStatus.SKIPPED,
            detail='Сработает, только если комментарий не принят',
        )
    same = gate.final_layout == gate.proposed_layout
    return RuleCheck(
        code='rejection',
        title='Ерунда в комментарии не меняет расклад',
        status=RuleStatus.PASSED if same else RuleStatus.FAILED,
        detail='Расклад оставлен как был' if same else 'Расклад изменился, хотя комментарий не принят',
    )


def _comment_rule_status(gate: Gatekeeper) -> RuleStatus:
    if gate.phase in {Phase.AWAITING_ROLES, Phase.DONE}:
        return RuleStatus.PASSED
    if gate.phase in {Phase.AWAITING_CONFIRMATION, Phase.AWAITING_COMMENT_ANSWERS}:
        return RuleStatus.WAITING
    return RuleStatus.WAITING


def _comment_rule_detail(gate: Gatekeeper) -> str:
    if gate.comment_status is CommentStatus.CONFIRMED:
        return 'Студент нажал ОК, комментария нет'
    if gate.phase is Phase.AWAITING_COMMENT_ANSWERS:
        return 'Комментарий по делу, но в нём осталась абстракция'
    if gate.comment_text is not None:
        return f'Комментарий один, {len(gate.comment_text)} из 400 символов'
    return 'После расклада можно нажать ОК или оставить один комментарий'


def _filled(ready: bool) -> RuleStatus:
    return RuleStatus.PASSED if ready else RuleStatus.WAITING


def _theme_detail(layout) -> str:  # noqa: ANN001
    if layout is None:
        return 'Код подставится только из документа «Предметные области»'
    return f'{layout.theme_code} — {layout.theme_title}'
