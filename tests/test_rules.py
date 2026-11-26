import json
import re
import unittest
from pathlib import Path

from gatekeeper.catalogs import PROJECT_TYPES, THEMES
from gatekeeper.compliance import rule_checks
from gatekeeper.errors import GatekeeperError, RuleError
from gatekeeper.parsing import extract_json, parse_comment, parse_layout
from gatekeeper.prompts import classify_system
from gatekeeper.schemas import CommentStatus, Phase, RuleStatus
from gatekeeper.service import Gatekeeper

_REASON = 'СЛЕД-ПРИЧИНА-9981'
_ROLE_WHY = 'ПОЯСНЕНИЕ-РОЛИ-7742'
_PROMO = re.compile(r'^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$')
_ROOT = Path(__file__).resolve().parents[1]
_STARTER = 'фронтендер'


class _FakeModel:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        if not self.replies:
            raise AssertionError('у фиктивной модели кончились ответы')
        return self.replies.pop(0)


def _criteria() -> list[dict[str, str]]:
    return [
        {'name': 'Полнота прототипа', 'how_to_tell': 'приложение ставится и проходит короткий тест'},
        {'name': 'Инструкция', 'how_to_tell': 'преподаватель проводит занятие по тексту'},
    ]


def _assessment(
    *,
    enough: bool,
    questions: list[str] | None = None,
    technology: str | None = 'Kotlin и карточки',
    role_clear: bool = True,
    abstraction_level: int | None = None,
) -> dict:
    asked = questions or []
    filled = enough
    return {
        'theme_code': '06' if filled or technology else None,
        'technology': technology if filled else None,
        'project_types': ['technological'] if filled else None,
        'themes': ['мобильная разработка'] if filled else None,
        'result_image': 'прототип приложения и инструкция' if filled else None,
        'result_criteria': _criteria() if filled else None,
        'enough': enough,
        'role_clear': role_clear,
        'abstraction_level': (15 if enough else 75) if abstraction_level is None else abstraction_level,
        'assessment': {
            key: {'enough': filled, 'reason': _REASON}
            for key in ('theme', 'technology', 'project_type', 'themes', 'result_image')
        },
        'questions': asked,
    }


def _layout(**overrides: object) -> dict:
    payload: dict[str, object] = {
        'theme_code': '06',
        'technology': 'Kotlin и карточки',
        'project_types': ['technological'],
        'themes': ['мобильная разработка', 'изучение лексики'],
        'result_image': 'прототип приложения и инструкция',
        'result_criteria': _criteria(),
        'abstraction_level': 20,
    }
    payload.update(overrides)
    return payload


def _roles(*titles: str) -> dict:
    names = titles or (_STARTER, 'бэкенд-разработчик')
    return {
        'roles': [
            {
                'title': title,
                'why': _ROLE_WHY,
                'filled_by_starter': title == _STARTER,
            }
            for title in names
        ]
    }


class GatekeeperRulesTest(unittest.TestCase):
    def test_enough_description_goes_straight_to_layout(self) -> None:
        model = _FakeModel([json.dumps(_assessment(enough=True)), json.dumps(_roles())])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение для английских слов.', _STARTER)
        view = gate.student_view()
        self.assertEqual(view.phase, Phase.AWAITING_CONFIRMATION)
        self.assertFalse(view.needs_clarification)
        self.assertEqual(view.questions, [])
        self.assertEqual(view.starter_role, _STARTER)
        self.assertIsNotNone(view.proposed_layout)
        self.assertEqual(len(view.proposed_layout.result_criteria), 2)
        self.assertIsNone(view.promo_code)
        self.assertNotIn(_REASON, view.model_dump_json())
        self.assertTrue(gate.sufficiency_trace is not None and gate.sufficiency_trace.enough)
        gate.confirm()
        self.assertEqual(gate.phase, Phase.AWAITING_ROLES)
        self.assertEqual(gate.student_view().comment_status, CommentStatus.CONFIRMED)
        self.assertIsNone(gate.comment_trace)
        self.assertNotIn(_ROLE_WHY, gate.student_view().model_dump_json())
        gate.confirm_roles([_STARTER, 'методист'])
        done = gate.student_view()
        self.assertEqual(done.phase, Phase.DONE)
        self.assertEqual([role.title for role in done.final_roles], [_STARTER, 'методист'])
        self.assertTrue(done.final_roles[0].filled_by_starter)
        self.assertFalse(done.final_roles[1].filled_by_starter)
        self.assertIsNotNone(done.promo_code)
        self.assertRegex(done.promo_code or '', _PROMO)
        self.assertNotIn(_REASON, done.model_dump_json())
        self.assertNotIn(_ROLE_WHY, done.model_dump_json())

    def test_thin_description_asks_before_showing_layout(self) -> None:
        questions = ['Чем проект должен закончиться?']
        thin = _assessment(enough=False, questions=questions, technology=None)
        for item in thin['assessment'].values():
            item['enough'] = False
        model = _FakeModel([json.dumps(thin), json.dumps(_layout())])
        gate = Gatekeeper(model)
        gate.submit_description('Хотим сделать что-то полезное.', _STARTER)
        view = gate.student_view()
        self.assertEqual(view.phase, Phase.AWAITING_ANSWERS)
        self.assertIsNone(view.proposed_layout)
        self.assertEqual(view.questions, questions)
        self.assertNotIn(_REASON, view.model_dump_json())
        gate.submit_answers(['Прототип приложения и инструкция для преподавателя.'])
        self.assertEqual(gate.sufficiency_trace.abstraction_level, 75)
        self.assertEqual(gate.sufficiency_trace.abstraction_level_after, 20)
        self.assertEqual(gate.phase, Phase.AWAITING_CONFIRMATION)
        self.assertEqual(gate.proposed_layout.themes, ['мобильная разработка', 'изучение лексики'])
        self.assertEqual(len(gate.proposed_layout.result_criteria), 2)

    def test_unclear_role_asks_before_layout(self) -> None:
        question = 'Какая одна роль у вас в проекте?'
        payload = _assessment(enough=True, questions=[question], role_clear=False)
        model = _FakeModel([json.dumps(payload)])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение для английских слов.', 'специалист')
        view = gate.student_view()
        self.assertEqual(view.phase, Phase.AWAITING_ANSWERS)
        self.assertIsNone(view.proposed_layout)
        self.assertEqual(view.questions, [question])
        self.assertTrue(view.needs_clarification)
        self.assertTrue(gate.sufficiency_trace.enough)
        self.assertFalse(gate.sufficiency_trace.role_clear)

    def test_empty_role_does_not_call_the_model(self) -> None:
        model = _FakeModel([])
        gate = Gatekeeper(model)
        with self.assertRaises(GatekeeperError):
            gate.submit_description('Делаем приложение.', '   ')
        self.assertEqual(model.calls, 0)
        self.assertEqual(gate.phase, Phase.AWAITING_DESCRIPTION)

    def test_old_subject_area_key_is_accepted_as_themes(self) -> None:
        payload = _assessment(enough=True)
        payload['subject_area'] = payload.pop('themes')
        payload['assessment']['subject_area'] = payload['assessment'].pop('themes')
        model = _FakeModel([json.dumps(payload)])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение для английских слов.', _STARTER)
        self.assertEqual(gate.proposed_layout.themes, ['мобильная разработка'])

    def test_nonsense_comment_keeps_layout_and_hides_reason(self) -> None:
        rejected = {
            'valid': False,
            'concrete': True,
            'reason': _REASON,
            'abstraction_level': 10,
            'questions': ['это не должно стать вопросом'],
            'theme_code': '02',
            'technology': 'это не должно примениться',
            'project_types': ['creative'],
            'themes': ['театр'],
            'result_image': 'спектакль',
            'result_criteria': _criteria(),
        }
        model = _FakeModel([
            json.dumps(_assessment(enough=True)),
            json.dumps(rejected),
            json.dumps(_roles()),
        ])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение.', _STARTER)
        before = gate.proposed_layout.model_copy(deep=True)
        gate.submit_comment('абракадабра купи слона')
        self.assertEqual(gate.comment_status, CommentStatus.REJECTED)
        self.assertEqual(gate.final_layout, before)
        self.assertEqual(gate.phase, Phase.AWAITING_ROLES)
        self.assertEqual(gate.comment_trace.reason, _REASON)
        self.assertEqual(gate.comment_questions, [])
        self.assertEqual(gate.sufficiency_trace.abstraction_level, 15)
        self.assertEqual(gate.comment_trace.abstraction_level, 10)
        self.assertNotIn('abstraction_level', gate.student_view().model_dump_json())
        self.assertNotIn(_REASON, gate.student_view().model_dump_json())
        rejection = next(item for item in rule_checks(gate) if item.code == 'rejection')
        self.assertEqual(rejection.status, RuleStatus.PASSED)
        self.assertNotIn(_REASON, ''.join(item.detail for item in rule_checks(gate)))

    def test_abstract_comment_asks_before_changing_layout(self) -> None:
        pending = {
            'valid': True,
            'concrete': False,
            'reason': _REASON,
            'abstraction_level': 80,
            'questions': ['Каких именно двух факультетов?'],
            'theme_code': None,
            'technology': None,
            'project_types': None,
            'themes': None,
            'result_image': None,
            'result_criteria': None,
        }
        updated = _layout(result_image='программа для факультетов права и экономики')
        model = _FakeModel([
            json.dumps(_assessment(enough=True)),
            json.dumps(pending),
            json.dumps(updated),
            json.dumps(_roles()),
        ])
        gate = Gatekeeper(model)
        gate.submit_description('Готовим программу по заказу деканата.', _STARTER)
        before = gate.proposed_layout.model_copy(deep=True)
        gate.submit_comment('Совместная подготовка была строго в пределах двух факультетов.')
        self.assertEqual(gate.phase, Phase.AWAITING_COMMENT_ANSWERS)
        self.assertIsNone(gate.final_layout)
        self.assertEqual(gate.proposed_layout, before)
        self.assertEqual(gate.student_view().comment_questions, ['Каких именно двух факультетов?'])
        self.assertEqual(gate.sufficiency_trace.abstraction_level, 15)
        self.assertEqual(gate.comment_trace.abstraction_level, 80)
        self.assertNotIn('abstraction_level', gate.student_view().model_dump_json())
        self.assertNotIn(_REASON, gate.student_view().model_dump_json())
        gate.submit_comment_answers(['Факультеты права и экономики.'])
        self.assertEqual(gate.sufficiency_trace.abstraction_level, 15)
        self.assertIsNone(gate.sufficiency_trace.abstraction_level_after)
        self.assertEqual(gate.comment_trace.abstraction_level, 80)
        self.assertEqual(gate.comment_trace.abstraction_level_after, 20)
        self.assertEqual(gate.comment_status, CommentStatus.ACCEPTED)
        self.assertEqual(gate.phase, Phase.AWAITING_ROLES)
        self.assertIn('права', gate.final_layout.result_image)
        self.assertEqual(model.calls, 4)

    def test_valid_comment_updates_fields_once(self) -> None:
        accepted = {
            'valid': True,
            'concrete': True,
            'reason': _REASON,
            'abstraction_level': 15,
            'questions': [],
            **_layout(technology='Kotlin, Room и повторение по кривой забывания'),
        }
        model = _FakeModel([
            json.dumps(_assessment(enough=True)),
            json.dumps(accepted),
            json.dumps(_roles()),
        ])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение.', _STARTER)
        gate.submit_comment('Технология ещё включает локальную базу Room и повторение по кривой забывания.')
        self.assertEqual(gate.comment_status, CommentStatus.ACCEPTED)
        self.assertIn('Room', gate.final_layout.technology)
        self.assertEqual(gate.phase, Phase.AWAITING_ROLES)
        with self.assertRaises(GatekeeperError):
            gate.submit_comment('второй комментарий')
        self.assertEqual(model.calls, 3)

    def test_long_comment_does_not_call_the_model(self) -> None:
        model = _FakeModel([json.dumps(_assessment(enough=True))])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение.', _STARTER)
        with self.assertRaises(GatekeeperError):
            gate.submit_comment('а' * 401)
        self.assertEqual(model.calls, 1)
        self.assertEqual(gate.phase, Phase.AWAITING_CONFIRMATION)

    def test_bad_catalog_code_is_repaired(self) -> None:
        broken = _assessment(enough=True)
        broken['theme_code'] = '99'
        model = _FakeModel([json.dumps(broken), json.dumps(_assessment(enough=True))])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение для слов.', _STARTER)
        self.assertEqual(gate.proposed_layout.theme_code, '06')
        self.assertEqual(model.calls, 2)

    def test_missing_starter_role_is_added(self) -> None:
        model = _FakeModel([
            json.dumps(_assessment(enough=True)),
            json.dumps(_roles('аналитик')),
        ])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение.', _STARTER)
        gate.confirm()
        self.assertEqual(gate.proposed_roles[0].title, _STARTER)
        self.assertTrue(gate.proposed_roles[0].filled_by_starter)
        self.assertEqual(gate.proposed_roles[1].title, 'аналитик')
        self.assertFalse(gate.proposed_roles[1].filled_by_starter)

    def test_empty_role_list_is_rejected(self) -> None:
        model = _FakeModel([json.dumps(_assessment(enough=True)), json.dumps(_roles())])
        gate = Gatekeeper(model)
        gate.submit_description('Делаем приложение.', _STARTER)
        gate.confirm()
        with self.assertRaises(GatekeeperError):
            gate.confirm_roles(['', '   '])
        self.assertEqual(gate.phase, Phase.AWAITING_ROLES)
        self.assertIsNone(gate.promo_code)

    def test_prompts_carry_customer_lists(self) -> None:
        system = classify_system()
        for project_type in PROJECT_TYPES:
            self.assertIn(project_type.code, system)
            self.assertIn(project_type.title, system)
        for theme in THEMES:
            self.assertIn(f'{theme.code} — {theme.title}', system)
        self.assertIn('abstraction_level', system)
        self.assertIn('одна роль', system)
        self.assertIn('Команду целиком не собирай', system)
        self.assertIn('два факультета', system.casefold())
        types_file = (_ROOT / 'prompts' / 'project_types.md').read_text(encoding='utf-8')
        self.assertIn('конкретного заказчика', types_file)
        self.assertIn('программы акселерации', types_file)

    def test_opening_rules_wait_for_one_role(self) -> None:
        gate = Gatekeeper(_FakeModel([]))
        codes = {item.code: item.status for item in rule_checks(gate)}
        self.assertNotIn('team', codes)
        self.assertEqual(codes['starter_role'], RuleStatus.WAITING)
        self.assertEqual(codes['roles'], RuleStatus.WAITING)
        self.assertEqual(codes['promo'], RuleStatus.WAITING)
        self.assertEqual(codes['result_criteria'], RuleStatus.WAITING)
        self.assertEqual(codes['abstraction'], RuleStatus.WAITING)

    def test_high_abstraction_asks_even_when_fields_are_filled(self) -> None:
        question = 'Каких именно двух факультетов?'
        payload = _assessment(enough=True, questions=[question], abstraction_level=80)
        model = _FakeModel([json.dumps(payload)])
        gate = Gatekeeper(model)
        gate.submit_description('Совместная подготовка была строго в пределах двух факультетов.', _STARTER)
        self.assertEqual(gate.phase, Phase.AWAITING_ANSWERS)
        self.assertIsNone(gate.proposed_layout)
        self.assertEqual(gate.questions, [question])
        self.assertEqual(gate.sufficiency_trace.abstraction_level, 80)
        self.assertNotIn('abstraction_level', gate.student_view().model_dump_json())

    def test_several_project_types_stay_in_the_layout(self) -> None:
        payload = _layout(project_types=['external_customer', 'technological', 'technological'])
        layout = parse_layout(extract_json(json.dumps(payload)))
        self.assertEqual(layout.project_types, ['external_customer', 'technological'])
        self.assertEqual(
            layout.project_type_titles,
            ['Проект по запросу внешнего заказчика', 'Технологический проект'],
        )

    def test_single_type_string_is_accepted(self) -> None:
        payload = _layout()
        payload['project_type'] = payload.pop('project_types')[0]
        layout = parse_layout(extract_json(json.dumps(payload)))
        self.assertEqual(layout.project_types, ['technological'])

    def test_more_than_three_types_are_rejected(self) -> None:
        payload = _layout(project_types=['technological', 'research', 'creative', 'social'])
        with self.assertRaises(RuleError):
            parse_layout(extract_json(json.dumps(payload)))

    def test_comment_accepts_criteria_written_as_lines(self) -> None:
        accepted = {
            'valid': True,
            'concrete': True,
            'reason': _REASON,
            'abstraction_level': 12,
            'questions': [],
            'theme_code': '06',
            'technology': 'сценарии заказчика и ответы о статусе доставки',
            'project_types': ['technological', 'external_customer'],
            'themes': ['чат-бот службы поддержки'],
            'result_image': 'бот, принятый школой английского языка',
            'result_criteria': [
                'Ответы о доставке — бот отвечает на вопросы о статусе',
                'Приёмка — заказчик принимает бота по своему чек-листу',
            ],
        }
        model = _FakeModel([
            json.dumps(_assessment(enough=True)),
            json.dumps(accepted),
            json.dumps(_roles()),
        ])
        gate = Gatekeeper(model)
        gate.submit_description(
            'Компания «Северная логистика» заказала чат-бота по своему чек-листу.',
            _STARTER,
        )
        gate.submit_comment(
            'Забыл сказать, что приложение заказала школа английского языка, которая сотрудничает с нашей кафедрой'
        )
        self.assertEqual(gate.comment_status, CommentStatus.ACCEPTED)
        self.assertEqual(gate.final_layout.result_criteria[0].name, 'Ответы о доставке')
        self.assertEqual(
            gate.final_layout.result_criteria[0].how_to_tell,
            'бот отвечает на вопросы о статусе',
        )
        self.assertEqual(gate.final_layout.result_criteria[1].name, 'Приёмка')
        self.assertEqual(model.calls, 3)

    def test_criteria_aliases_and_plain_phrase_are_accepted(self) -> None:
        payload = _layout(result_criteria=[
            {'название': 'Приёмка', 'описание': 'заказчик принимает бота по чек-листу'},
            'бот отвечает на вопросы о статусе доставки',
        ])
        layout = parse_layout(extract_json(json.dumps(payload)))
        self.assertEqual(layout.result_criteria[0].name, 'Приёмка')
        self.assertEqual(layout.result_criteria[0].how_to_tell, 'заказчик принимает бота по чек-листу')
        self.assertEqual(layout.result_criteria[1].name, 'бот отвечает на вопросы о статусе доставки')
        self.assertEqual(layout.result_criteria[1].how_to_tell, layout.result_criteria[1].name)

    def test_concrete_comment_keeps_previous_criteria_when_model_omits_them(self) -> None:
        previous = parse_layout(extract_json(json.dumps(_layout())))
        payload = {
            'valid': True,
            'concrete': True,
            'reason': _REASON,
            'abstraction_level': 10,
            'questions': [],
            'theme_code': previous.theme_code,
            'technology': previous.technology,
            'project_types': previous.project_types,
            'themes': previous.themes,
            'result_image': 'бот для школы английского языка',
            'result_criteria': None,
        }
        _trace, layout, pending = parse_comment(payload, comment='школа заказала приложение', previous=previous)
        self.assertFalse(pending)
        self.assertEqual(layout.result_image, 'бот для школы английского языка')
        self.assertEqual(layout.result_criteria, previous.result_criteria)

    def test_json_is_taken_from_think_block(self) -> None:
        raw = '<think>служебное</think>\n```json\n' + json.dumps(_layout()) + '\n```'
        layout = parse_layout(extract_json(raw))
        self.assertEqual(layout.theme_code, '06')
        self.assertEqual(layout.result_criteria[0].name, 'Полнота прототипа')
        self.assertNotIn('служебное', layout.technology)


if __name__ == '__main__':
    unittest.main()
