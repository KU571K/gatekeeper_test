r"""Экран привратника.

Запуск из этой папки в PowerShell:
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    Copy-Item .env.example .env
    streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os

from dotenv import load_dotenv

load_dotenv(ROOT / '.env')

import pandas as pd
import streamlit as st

_SECRET_KEYS = ('POLZA_API_KEY', 'POLZA_BASE_URL', 'POLZA_MODEL')


def _secrets_into_env() -> None:
    """Локально ключ берётся из .env. На Streamlit Cloud — из secrets.toml."""
    try:
        secrets = st.secrets
    except Exception:
        return
    for key in _SECRET_KEYS:
        if os.environ.get(key, '').strip():
            continue
        try:
            value = secrets[key]
        except Exception:
            continue
        text = str(value).strip()
        if text:
            os.environ[key] = text


from gatekeeper.catalogs import PROJECT_TYPES, THEMES
from gatekeeper.compliance import rule_checks
from gatekeeper.errors import GatekeeperError, ModelResponseError, PolzaError
from gatekeeper.llm import model_from_env
from gatekeeper.schemas import (
    COMMENT_MAX_LENGTH,
    DESCRIPTION_MAX_LENGTH,
    ROLE_MAX_LENGTH,
    CommentStatus,
    Layout,
    Phase,
    RuleStatus,
)
from gatekeeper.service import Gatekeeper

st.set_page_config(page_title='Привратник', layout='wide')

_EXAMPLES = (
    (
        'Мало данных',
        'Хотим сделать что-то полезное для первокурсников.',
    ),
    (
        'Технологический',
        'Нужно разработать мобильное приложение на Kotlin: карточки для изучения английских слов, '
        'повтор по забытым словам и короткий тест. Результат — установленный прототип и инструкция для преподавателя.',
    ),
    (
        'Заказчик',
        'Компания «Северная логистика» поставила задачу сделать чат-бота службы поддержки по своему техническому заданию. '
        'Бот отвечает на вопросы о статусе доставки, сценарии задаёт заказчик. '
        'Результат — бот, принятый по их чек-листу.',
    ),
)


def main() -> None:
    _secrets_into_env()
    model = model_from_env()
    st.title('Привратник')
    st.caption(
        'Описание и одна роль → расклад с критериями → ОК или один комментарий → нужные роли → промокод. '
        'Слева то, что видит студент. Справа проверка правил, включая скрытый след.'
    )
    if model is None:
        st.error(
            'Нет POLZA_API_KEY. Локально впишите его в .env. '
            'На Streamlit Cloud — в Settings → Secrets, по образцу .streamlit/secrets.toml.example.'
        )

    gate = _gate(model)
    student, audit = st.columns((1.35, 0.9), gap='large')
    with student:
        st.subheader('Экран студента')
        _student(gate, model is not None)
    with audit:
        st.subheader('Проверка правил')
        _audit(gate)


def _gate(model) -> Gatekeeper:  # noqa: ANN001
    stored = st.session_state.get('gate')
    if isinstance(stored, Gatekeeper):
        if model is not None:
            stored.replace_model(model)
        return stored
    return Gatekeeper(model or _ClosedModel())


def _student(gate: Gatekeeper, model_ready: bool) -> None:
    view = gate.student_view()
    if view.phase == Phase.AWAITING_DESCRIPTION:
        _description_form(gate, model_ready)
        return
    st.markdown('**Описание**')
    st.write(view.description)
    st.markdown('**Роль**')
    st.write(view.starter_role)
    if view.needs_clarification:
        st.markdown('**Уточнения**')
        for index, question in enumerate(view.questions, start=1):
            st.write(f'{index}. {question}')
        if view.phase == Phase.AWAITING_ANSWERS:
            _answers_form(gate)
            _reset()
            return
        for index, answer in enumerate(view.answers, start=1):
            st.caption(f'Ответ {index}. {answer}')
    elif view.needs_clarification is False:
        st.caption('Уточнения не понадобились: описания и роли хватило для расклада.')

    shown = view.final_layout or view.proposed_layout
    if shown is not None:
        st.markdown('**Расклад**')
        changed = _changed_titles(view.proposed_layout, view.final_layout)
        _show_layout(shown, changed)
    if view.phase == Phase.AWAITING_CONFIRMATION:
        _confirmation(gate)
    elif view.phase == Phase.AWAITING_COMMENT_ANSWERS:
        st.caption(f'Комментарий: {view.comment_text}')
        st.markdown('**Нужно назвать конкретно**')
        _comment_answers_form(gate)
    elif view.phase == Phase.AWAITING_ROLES:
        if view.comment_text:
            st.caption(f'Комментарий: {view.comment_text}')
        _show_comment_answers(view.comment_questions, view.comment_answers)
        _roles_form(gate)
    elif view.phase == Phase.DONE:
        st.success(_done_text(view.comment_status))
        if view.comment_text:
            st.caption(f'Комментарий: {view.comment_text}')
        _show_comment_answers(view.comment_questions, view.comment_answers)
        st.markdown('**Роли проекта**')
        for role in view.final_roles:
            mark = ' · ваша роль' if role.filled_by_starter else ''
            st.write(f'{role.title}{mark}')
        st.markdown('**Промокод**')
        st.code(view.promo_code or '')
    _reset()


def _description_form(gate: Gatekeeper, model_ready: bool) -> None:
    st.write('Опишите проект как угодно и укажите одну свою роль. Привратник сам разложит описание по полям.')
    for title, text in _EXAMPLES:
        st.button(title, key=f'example-{title}', on_click=_use_example, args=(text,))
    with st.form('description'):
        role = st.text_input(
            'Ваша роль',
            max_chars=ROLE_MAX_LENGTH,
            placeholder='например, фронтендер',
        )
        description = st.text_area(
            'Описание проекта',
            height=180,
            max_chars=DESCRIPTION_MAX_LENGTH,
            key='description_draft',
            placeholder='Что делаете, зачем и чем это должно закончиться',
        )
        submitted = st.form_submit_button('Разобрать', type='primary', disabled=not model_ready)
    if submitted:
        st.session_state.gate = gate
        _run(lambda: gate.submit_description(description, role))


def _answers_form(gate: Gatekeeper) -> None:
    with st.form('answers'):
        answers = [
            st.text_area(question, key=f'answer-{index}', height=80)
            for index, question in enumerate(gate.student_view().questions)
        ]
        submitted = st.form_submit_button('Ответить', type='primary')
    if submitted:
        _run(lambda: gate.submit_answers(answers))


def _comment_answers_form(gate: Gatekeeper) -> None:
    with st.form('comment-answers'):
        answers = [
            st.text_area(question, key=f'comment-answer-{index}', height=80)
            for index, question in enumerate(gate.student_view().comment_questions)
        ]
        submitted = st.form_submit_button('Уточнить', type='primary')
    if submitted:
        _run(lambda: gate.submit_comment_answers(answers))


def _roles_form(gate: Gatekeeper) -> None:
    st.markdown('**Нужные роли**')
    st.caption('Список можно исправить: переименовать, добавить или убрать.')
    frame = pd.DataFrame({'Роль': [role.title for role in gate.proposed_roles]})
    edited = st.data_editor(
        frame,
        num_rows='dynamic',
        key='roles_editor',
        use_container_width=True,
        hide_index=True,
    )
    if st.button('Роли готовы', type='primary', key='roles_ready'):
        _run(lambda: gate.confirm_roles(_role_titles(edited)))


def _confirmation(gate: Gatekeeper) -> None:
    st.markdown('**Дальше одно из двух:** нажать ОК или оставить один комментарий.')
    if st.button('ОК', type='primary', key='confirm'):
        _run(gate.confirm)
        return
    with st.form('comment'):
        comment = st.text_area(
            f'Комментарий, до {COMMENT_MAX_LENGTH} символов',
            height=90,
            max_chars=COMMENT_MAX_LENGTH,
        )
        submitted = st.form_submit_button('Отправить комментарий')
    if submitted:
        _run(lambda: gate.submit_comment(comment))


def _show_layout(layout: Layout, changed: set[str]) -> None:
    for title, value in layout.rows():
        mark = ' · исправлено' if title in changed else ''
        st.markdown(f'**{title}**{mark}')
        st.write(value)
    for goal in layout.project_type_goals:
        st.caption(goal)


def _show_comment_answers(questions: list[str], answers: list[str]) -> None:
    if not answers:
        return
    for index, (question, answer) in enumerate(zip(questions, answers, strict=True), start=1):
        st.caption(f'Уточнение {index}. {question}')
        st.caption(answer)


def _audit(gate: Gatekeeper) -> None:
    for check in rule_checks(gate):
        st.markdown(f'{_mark(check.status)} **{check.title}**')
        st.caption(check.detail)
    with st.expander('Справочники, из которых выбирает привратник', expanded=False):
        st.markdown('**Тематики из документа «Предметные области»**')
        for theme in THEMES:
            st.write(f'{theme.code} — {theme.title}')
            st.caption(theme.includes)
        st.markdown('**Типы**')
        for project_type in PROJECT_TYPES:
            st.write(f'{project_type.title}. {project_type.goal}')
        st.caption(
            'Тематика — одна область из этого списка. Тем внутри неё от 1 до 3. '
            'Критерии образа результата модель предлагает сама.'
        )
    with st.expander('Цифровой след — студенту не показывается', expanded=gate.sufficiency_trace is not None):
        if gate.sufficiency_trace is None:
            st.write('Появится после разбора описания и останется только в этой колонке.')
        else:
            st.markdown('**Уровень абстракции текста проекта**')
            st.json(_abstraction_log(
                gate.sufficiency_trace.abstraction_level,
                gate.sufficiency_trace.abstraction_level_after,
            ))
            st.markdown('**Оценка полноты описания**')
            st.json(gate.sufficiency_trace.model_dump())
        if gate.comment_trace is not None:
            st.markdown('**Уровень абстракции комментария**')
            st.json(_abstraction_log(
                gate.comment_trace.abstraction_level,
                gate.comment_trace.abstraction_level_after,
            ))
            st.markdown('**Оценка комментария**')
            st.json(gate.comment_trace.model_dump())
        if gate.proposed_roles:
            st.markdown('**Предложенные роли**')
            st.json([role.model_dump() for role in gate.proposed_roles])
        if gate.final_roles:
            st.markdown('**Роли после правки**')
            st.json([role.model_dump() for role in gate.final_roles])


def _abstraction_log(level: int, after: int | None) -> dict[str, int]:
    payload = {'abstraction_level': level}
    if after is not None:
        payload['abstraction_level_after'] = after
    return payload


def _run(action) -> None:  # noqa: ANN001
    try:
        with st.spinner('Привратник читает текст…'):
            action()
    except (GatekeeperError, ModelResponseError, PolzaError) as exc:
        message = exc.user_message if isinstance(exc, PolzaError) else str(exc)
        st.error(message)
        return
    st.rerun()


def _reset() -> None:
    if st.button('Разобрать другое описание', key='reset'):
        st.session_state.pop('gate', None)
        st.session_state.description_draft = ''
        st.session_state.pop('roles_editor', None)
        st.rerun()


def _use_example(text: str) -> None:
    st.session_state.description_draft = text


def _role_titles(edited: object) -> list[str]:
    if isinstance(edited, pd.DataFrame) and 'Роль' in edited.columns:
        return ['' if value is None else str(value) for value in edited['Роль'].tolist()]
    if isinstance(edited, list):
        return [str(row.get('Роль', '')) for row in edited if isinstance(row, dict)]
    return []


def _changed_titles(before: Layout | None, after: Layout | None) -> set[str]:
    if before is None or after is None:
        return set()
    return {title for (title, left), (_, right) in zip(before.rows(), after.rows(), strict=True) if left != right}


def _done_text(status: CommentStatus) -> str:
    if status == CommentStatus.ACCEPTED:
        return 'Расклад исправлен по комментарию. Промокод готов.'
    if status == CommentStatus.REJECTED:
        return 'Комментарий не изменил расклад. Промокод готов.'
    return 'Расклад подтверждён. Промокод готов.'


def _mark(status: RuleStatus) -> str:
    return {
        RuleStatus.PASSED: '✓',
        RuleStatus.WAITING: '·',
        RuleStatus.SKIPPED: '–',
        RuleStatus.FAILED: '✗',
    }[status]


class _ClosedModel:
    def complete(self, *, system: str, user: str) -> str:
        raise PolzaError('Сначала добавьте POLZA_API_KEY в .env или в секреты Streamlit')


main()
