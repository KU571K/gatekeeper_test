import secrets

from gatekeeper.errors import GatekeeperError, ModelResponseError, RuleError
from gatekeeper.parsing import extract_json, parse_assessment, parse_comment, parse_roles, parse_settled_layout
from gatekeeper.ports import LanguageModel
from gatekeeper.prompts import (
    classify_system,
    classify_user,
    comment_finalize_system,
    comment_finalize_user,
    finalize_system,
    finalize_user,
    repair_user,
    review_system,
    review_user,
    roles_system,
    roles_user,
)
from gatekeeper.schemas import (
    COMMENT_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    ROLE_MAX_LENGTH,
    ROLES_MAX,
    CommentStatus,
    Layout,
    Phase,
    ProjectRole,
    RoleView,
    StudentView,
    SufficiencyTrace,
    CommentTrace,
)

_PROMO_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'


class Gatekeeper:
    """Сценарий привратника без UI и без базы.

    Состояние живёт в объекте. Streamlit держит его в session_state,
    позже тот же объект можно сохранять по полям следа и расклада.
    """

    def __init__(self, model: LanguageModel) -> None:
        self._model = model
        self.phase = Phase.AWAITING_DESCRIPTION
        self.description = ''
        self.starter_role = ''
        self.questions: list[str] = []
        self.answers: list[str] = []
        self.proposed_layout: Layout | None = None
        self.final_layout: Layout | None = None
        self.comment_status = CommentStatus.NONE
        self.comment_text: str | None = None
        self.comment_questions: list[str] = []
        self.comment_answers: list[str] = []
        self.proposed_roles: list[ProjectRole] = []
        self.final_roles: list[ProjectRole] = []
        self.promo_code: str | None = None
        self.sufficiency_trace: SufficiencyTrace | None = None
        self.comment_trace: CommentTrace | None = None

    def replace_model(self, model: LanguageModel) -> None:
        if self.phase is Phase.AWAITING_DESCRIPTION:
            self._model = model

    def submit_description(self, description: str, starter_role: str) -> None:
        if self.phase is not Phase.AWAITING_DESCRIPTION:
            raise GatekeeperError('Описание уже принято')
        text = _clean_input(description, max_length=DESCRIPTION_MAX_LENGTH, empty_message='Введите описание проекта')
        role = _clean_input(starter_role, max_length=ROLE_MAX_LENGTH, empty_message='Укажите одну роль')
        trace, layout = self._ask(
            system=classify_system(),
            user=classify_user(text, role),
            parse=parse_assessment,
        )
        self.description = text
        self.starter_role = role
        self.sufficiency_trace = trace
        self.questions = list(trace.questions)
        if layout is None:
            self.phase = Phase.AWAITING_ANSWERS
            return
        self.proposed_layout = layout
        self.phase = Phase.AWAITING_CONFIRMATION

    def submit_answers(self, answers: list[str]) -> None:
        if self.phase is not Phase.AWAITING_ANSWERS:
            raise GatekeeperError('Сейчас привратник не ждёт ответов')
        if len(answers) != len(self.questions):
            raise GatekeeperError('Нужен ответ на каждый вопрос')
        cleaned = [
            _clean_input(answer, max_length=2_000, empty_message=f'Пустой ответ на вопрос {index}')
            for index, answer in enumerate(answers, start=1)
        ]
        layout, level = self._ask(
            system=finalize_system(),
            user=finalize_user(
                description=self.description,
                starter_role=self.starter_role,
                questions=self.questions,
                answers=cleaned,
            ),
            parse=parse_settled_layout,
        )
        self.answers = cleaned
        self.proposed_layout = layout
        if self.sufficiency_trace is not None:
            self.sufficiency_trace = self.sufficiency_trace.model_copy(update={'abstraction_level_after': level})
        self.phase = Phase.AWAITING_CONFIRMATION

    def confirm(self) -> None:
        if self.phase is not Phase.AWAITING_CONFIRMATION or self.proposed_layout is None:
            raise GatekeeperError('Подтверждать пока нечего')
        self.final_layout = self.proposed_layout.model_copy(deep=True)
        self.comment_status = CommentStatus.CONFIRMED
        self._propose_roles()

    def submit_comment(self, comment: str) -> None:
        if self.phase is not Phase.AWAITING_CONFIRMATION or self.proposed_layout is None:
            raise GatekeeperError('Комментарий принимается один раз, после показа расклада')
        if self.comment_trace is not None:
            raise GatekeeperError('Комментарий уже был')
        text = _clean_input(
            comment,
            max_length=COMMENT_MAX_LENGTH,
            empty_message='Комментарий пустой',
            too_long_message=f'Комментарий длиннее {COMMENT_MAX_LENGTH} символов',
        )
        previous = self.proposed_layout
        trace, layout, pending = self._ask(
            system=review_system(),
            user=review_user(
                description=self.description,
                starter_role=self.starter_role,
                layout_text=_layout_text(previous),
                comment=text,
            ),
            parse=lambda payload: parse_comment(payload, comment=text, previous=previous),
        )
        self.comment_text = text
        self.comment_trace = trace
        if pending:
            self.comment_questions = list(trace.questions)
            self.phase = Phase.AWAITING_COMMENT_ANSWERS
            return
        self.final_layout = layout
        self.comment_status = CommentStatus.ACCEPTED if trace.valid else CommentStatus.REJECTED
        self._propose_roles()

    def submit_comment_answers(self, answers: list[str]) -> None:
        if self.phase is not Phase.AWAITING_COMMENT_ANSWERS or self.proposed_layout is None:
            raise GatekeeperError('Сейчас привратник не ждёт ответов на комментарий')
        if len(answers) != len(self.comment_questions):
            raise GatekeeperError('Нужен ответ на каждый вопрос')
        cleaned = [
            _clean_input(answer, max_length=2_000, empty_message=f'Пустой ответ на вопрос {index}')
            for index, answer in enumerate(answers, start=1)
        ]
        layout, level = self._ask(
            system=comment_finalize_system(),
            user=comment_finalize_user(
                description=self.description,
                starter_role=self.starter_role,
                layout_text=_layout_text(self.proposed_layout),
                comment=self.comment_text or '',
                questions=self.comment_questions,
                answers=cleaned,
            ),
            parse=parse_settled_layout,
        )
        self.comment_answers = cleaned
        self.final_layout = layout
        if self.comment_trace is not None:
            self.comment_trace = self.comment_trace.model_copy(update={'abstraction_level_after': level})
        self.comment_status = CommentStatus.ACCEPTED
        self._propose_roles()

    def confirm_roles(self, titles: list[str]) -> None:
        if self.phase is not Phase.AWAITING_ROLES:
            raise GatekeeperError('Сначала нужно получить список ролей')
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in titles:
            title = ' '.join(str(raw).split())
            if not title or title.casefold() in {'nan', 'none', 'null'}:
                continue
            if len(title) > ROLE_MAX_LENGTH:
                raise GatekeeperError(f'Название роли длиннее {ROLE_MAX_LENGTH} символов')
            if title.casefold() in seen:
                continue
            seen.add(title.casefold())
            cleaned.append(title)
        if not cleaned:
            raise GatekeeperError('Нужна хотя бы одна роль')
        if len(cleaned) > ROLES_MAX:
            raise GatekeeperError(f'Ролей может быть не больше {ROLES_MAX}')
        known = {role.title.casefold(): role for role in self.proposed_roles}
        starter = self.starter_role.casefold()
        final: list[ProjectRole] = []
        for title in cleaned:
            previous = known.get(title.casefold())
            why = previous.why if previous is not None else ''
            final.append(ProjectRole(title=title, why=why, filled_by_starter=title.casefold() == starter))
        self.final_roles = final
        self.promo_code = _promo_code()
        self.phase = Phase.DONE

    def student_view(self) -> StudentView:
        needs: bool | None = None
        if self.sufficiency_trace is not None:
            needs = not self.sufficiency_trace.enough or not self.sufficiency_trace.role_clear
        show_roles = self.phase in {Phase.AWAITING_ROLES, Phase.DONE}
        done = self.phase is Phase.DONE
        return StudentView(
            phase=self.phase,
            description=self.description,
            starter_role=self.starter_role,
            needs_clarification=needs,
            questions=list(self.questions) if needs else [],
            answers=list(self.answers) if needs else [],
            proposed_layout=self.proposed_layout,
            final_layout=self.final_layout,
            comment_status=self.comment_status,
            comment_text=self.comment_text,
            comment_questions=list(self.comment_questions),
            comment_answers=list(self.comment_answers),
            proposed_roles=[_role_view(role) for role in self.proposed_roles] if show_roles else [],
            final_roles=[_role_view(role) for role in self.final_roles] if done else [],
            promo_code=self.promo_code if done else None,
        )

    def _propose_roles(self) -> None:
        if self.final_layout is None:
            raise GatekeeperError('Расклад ещё не зафиксирован')
        roles = self._ask(
            system=roles_system(),
            user=roles_user(
                description=self.description,
                starter_role=self.starter_role,
                layout_text=_layout_text(self.final_layout),
            ),
            parse=parse_roles,
        )
        self.proposed_roles = _ensure_starter(roles, self.starter_role)
        self.phase = Phase.AWAITING_ROLES

    def _ask(self, *, system: str, user: str, parse):  # noqa: ANN001, ANN202
        last_error = 'пустой ответ'
        prompt = user
        for _ in range(2):
            raw = self._model.complete(system=system, user=prompt)
            try:
                return parse(extract_json(raw))
            except (RuleError, ValueError) as exc:
                last_error = str(exc)
                prompt = f'{user}\n\n{repair_user(last_error)}'
        raise ModelResponseError(last_error)


def _clean_input(value: str, *, max_length: int, empty_message: str, too_long_message: str | None = None) -> str:
    text = value.strip()
    if not text:
        raise GatekeeperError(empty_message)
    if len(text) > max_length:
        raise GatekeeperError(too_long_message or f'Текст длиннее {max_length} символов')
    return text


def _layout_text(layout: Layout) -> str:
    return '\n'.join(f'{title}: {value}' for title, value in layout.rows())


def _role_view(role: ProjectRole) -> RoleView:
    return RoleView(title=role.title, filled_by_starter=role.filled_by_starter)


def _ensure_starter(roles: list[ProjectRole], starter: str) -> list[ProjectRole]:
    starter_key = starter.casefold()
    fixed: list[ProjectRole] = []
    found = False
    for role in roles:
        same = role.title.casefold() == starter_key
        found = found or same
        if role.filled_by_starter == same:
            fixed.append(role)
        else:
            fixed.append(role.model_copy(update={'filled_by_starter': same}))
    if found:
        return fixed
    return [ProjectRole(title=starter, why='Роль, с которой проект начался', filled_by_starter=True), *fixed]


def _promo_code() -> str:
    return ''.join(secrets.choice(_PROMO_ALPHABET) for _ in range(6))
