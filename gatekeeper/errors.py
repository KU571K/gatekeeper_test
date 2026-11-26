class GatekeeperError(Exception):
    """Нарушение шага сценария: пустой ввод, второй комментарий, не тот этап."""


class RuleError(Exception):
    """Ответ модели не соблюдает справочники или схему. Можно попросить исправление."""


class ModelResponseError(Exception):
    """Модель так и не вернула ответ, который проходит правила."""


class PolzaError(Exception):
    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message
